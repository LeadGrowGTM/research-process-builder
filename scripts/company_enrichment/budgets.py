from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import threading
import time
from typing import Iterator

from .contracts import canonical_json


class BudgetExhausted(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Reservation:
    reservation_id: str
    scope_id: str
    idempotency_key: str
    amount: Decimal


_LOCKS_GUARD = threading.Lock()
_PATH_LOCKS: dict[Path, threading.RLock] = {}


def _path_lock(path: Path) -> threading.RLock:
    resolved = path.resolve()
    with _LOCKS_GUARD:
        return _PATH_LOCKS.setdefault(resolved, threading.RLock())


def _amount(value: str | Decimal, name: str) -> Decimal:
    try:
        amount = Decimal(value)
    except (InvalidOperation, TypeError) as error:
        raise ValueError(f"{name} must be a decimal amount") from error
    if not amount.is_finite() or amount < 0:
        raise ValueError(f"{name} must be finite and non-negative")
    return amount


class BudgetLedger:
    def __init__(self, path: Path, caps: dict[str, str]) -> None:
        self.path = Path(path)
        if not caps:
            raise ValueError("at least one budget cap is required")
        self._caps = {
            scope_id: _amount(cap, f"cap for {scope_id}")
            for scope_id, cap in caps.items()
        }
        if any(not isinstance(scope_id, str) or not scope_id for scope_id in caps):
            raise ValueError("scope IDs must be non-empty text")
        self._thread_lock = _path_lock(self.path)
        self._lock_path = self.path.with_suffix(self.path.suffix + ".lock")

    def reserve(
        self,
        scope_id: str,
        idempotency_key: str,
        estimated_cost: str | Decimal,
    ) -> Reservation:
        estimate = _amount(estimated_cost, "estimated_cost")
        self._validate_scope_and_key(scope_id, idempotency_key)
        with self._locked():
            reservations, actuals = self._replay()
            identity = (scope_id, idempotency_key)
            existing = reservations.get(identity)
            if existing is not None:
                if existing.amount != estimate:
                    raise ValueError("idempotency key was already used with a different amount")
                return existing

            outstanding = sum(
                (
                    item.amount
                    for item in reservations.values()
                    if item.scope_id == scope_id
                    and item.reservation_id not in actuals
                ),
                Decimal("0"),
            )
            spent = sum(
                (
                    actual
                    for reservation_id, actual in actuals.items()
                    if self._reservation_by_id(reservations, reservation_id).scope_id
                    == scope_id
                ),
                Decimal("0"),
            )
            if spent + outstanding + estimate > self._caps[scope_id]:
                raise BudgetExhausted(
                    f"budget_exhausted: {scope_id} cap is {self._caps[scope_id]}"
                )

            reservation = Reservation(
                reservation_id=self._reservation_id(scope_id, idempotency_key),
                scope_id=scope_id,
                idempotency_key=idempotency_key,
                amount=estimate,
            )
            self._append(
                {
                    "amount": str(estimate),
                    "idempotency_key": idempotency_key,
                    "kind": "reserve",
                    "reservation_id": reservation.reservation_id,
                    "scope_id": scope_id,
                }
            )
            return reservation

    def reconcile(
        self, reservation: Reservation, actual_cost: str | Decimal
    ) -> None:
        actual = _amount(actual_cost, "actual_cost")
        with self._locked():
            reservations, actuals = self._replay()
            stored = reservations.get((reservation.scope_id, reservation.idempotency_key))
            if stored != reservation:
                raise ValueError("reservation is not present in this ledger")
            if actual > stored.amount:
                raise ValueError("actual cost cannot exceed the reserved maximum")
            prior = actuals.get(stored.reservation_id)
            if prior is not None:
                if prior != actual:
                    raise ValueError("reservation was already reconciled to a different amount")
                return
            self._append(
                {
                    "actual_cost": str(actual),
                    "kind": "reconcile",
                    "reservation_id": stored.reservation_id,
                    "scope_id": stored.scope_id,
                }
            )

    def reserved(self, scope_id: str) -> Decimal:
        self._validate_scope(scope_id)
        with self._locked():
            reservations, actuals = self._replay()
            return sum(
                (
                    item.amount
                    for item in reservations.values()
                    if item.scope_id == scope_id
                    and item.reservation_id not in actuals
                ),
                Decimal("0"),
            )

    def spent(self, scope_id: str) -> Decimal:
        self._validate_scope(scope_id)
        with self._locked():
            reservations, actuals = self._replay()
            ids = {
                item.reservation_id
                for item in reservations.values()
                if item.scope_id == scope_id
            }
            return sum(
                (amount for reservation_id, amount in actuals.items() if reservation_id in ids),
                Decimal("0"),
            )

    def _validate_scope(self, scope_id: str) -> None:
        if scope_id not in self._caps:
            raise ValueError(f"unknown budget scope: {scope_id}")

    def _validate_scope_and_key(self, scope_id: str, idempotency_key: str) -> None:
        self._validate_scope(scope_id)
        if not isinstance(idempotency_key, str) or not idempotency_key:
            raise ValueError("idempotency_key must be non-empty text")

    @staticmethod
    def _reservation_id(scope_id: str, idempotency_key: str) -> str:
        material = canonical_json(
            {"idempotency_key": idempotency_key, "scope_id": scope_id}
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    @staticmethod
    def _reservation_by_id(
        reservations: dict[tuple[str, str], Reservation], reservation_id: str
    ) -> Reservation:
        for reservation in reservations.values():
            if reservation.reservation_id == reservation_id:
                return reservation
        raise ValueError("budget ledger contains an orphan reconciliation")

    def _replay(
        self,
    ) -> tuple[dict[tuple[str, str], Reservation], dict[str, Decimal]]:
        reservations: dict[tuple[str, str], Reservation] = {}
        actuals: dict[str, Decimal] = {}
        if not self.path.exists():
            return reservations, actuals
        for line_number, line in enumerate(
            self.path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            try:
                event = json.loads(line)
                kind = event["kind"]
                if kind == "reserve":
                    reservation = Reservation(
                        reservation_id=event["reservation_id"],
                        scope_id=event["scope_id"],
                        idempotency_key=event["idempotency_key"],
                        amount=_amount(event["amount"], "stored reservation"),
                    )
                    expected_id = self._reservation_id(
                        reservation.scope_id, reservation.idempotency_key
                    )
                    if reservation.reservation_id != expected_id:
                        raise ValueError("reservation ID mismatch")
                    key = (reservation.scope_id, reservation.idempotency_key)
                    if key in reservations:
                        raise ValueError("duplicate reservation")
                    reservations[key] = reservation
                elif kind == "reconcile":
                    reservation_id = event["reservation_id"]
                    if reservation_id in actuals:
                        raise ValueError("duplicate reconciliation")
                    actuals[reservation_id] = _amount(
                        event["actual_cost"], "stored actual cost"
                    )
                else:
                    raise ValueError(f"unknown event kind: {kind}")
            except (KeyError, TypeError, json.JSONDecodeError, ValueError) as error:
                raise ValueError(
                    f"invalid budget ledger event on line {line_number}"
                ) from error
        for reservation_id, actual in actuals.items():
            reservation = self._reservation_by_id(reservations, reservation_id)
            if actual > reservation.amount:
                raise ValueError("actual cost exceeds its reservation")
        return reservations, actuals

    def _append(self, event: dict[str, str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(canonical_json(event) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    @contextmanager
    def _locked(self) -> Iterator[None]:
        with self._thread_lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            deadline = time.monotonic() + 10
            descriptor: int | None = None
            while descriptor is None:
                try:
                    descriptor = os.open(
                        self._lock_path,
                        os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    )
                except FileExistsError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("timed out waiting for budget ledger lock")
                    time.sleep(0.01)
            try:
                os.write(descriptor, str(os.getpid()).encode("ascii"))
                yield
            finally:
                os.close(descriptor)
                self._lock_path.unlink(missing_ok=True)
