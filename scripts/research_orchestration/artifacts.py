"""Local, canonical, fail-closed persistence for autoresearch run artifacts."""

from __future__ import annotations
from contextlib import contextmanager
import functools
from threading import Lock, RLock

from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping

from .budgets import BudgetLimits, BudgetUsage
from .contracts import (
    CompletedRoleRecord,
    CompletedRoleResult,
    Experiment,
    Role,
    RoleEnvelope,
    RunRequest,
    RunSummary,
    SCHEMA_VERSION,
    SchemaError,
    StateEventRecord,
    StateProjection,
)


_FORBIDDEN_FIELD_PARTS = (
    "secret", "token", "password", "authorization", "transcript", "api_key", "credential",
)
_STAGES = tuple(role.value for role in Role)
_GATE_STAGE = "gate"
_NEXT_STAGE = dict(zip(("start",) + _STAGES, _STAGES + (_GATE_STAGE,)))
_LOCKS_GUARD = Lock()
_RUN_LOCKS: dict[str, RLock] = {}



class ArtifactHaltForReview(RuntimeError):
    """A persisted-state ambiguity which must be reviewed rather than repaired."""

    def __init__(self, reason_code: str):
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class ResumeCursor:
    """The next unrecorded role and work already made idempotent by artifacts."""

    cycle: int
    stage: str
    completed_idempotency_keys: frozenset[str]


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ArtifactHaltForReview("noncanonical_artifact") from error


def _require_safe_value(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if not isinstance(key, str):
                raise ArtifactHaltForReview("invalid_artifact")
            normalized = key.casefold().replace("-", "_")
            if any(part in normalized for part in _FORBIDDEN_FIELD_PARTS):
                raise ArtifactHaltForReview("sensitive_content")
            _require_safe_value(nested)
    elif isinstance(value, list):
        for nested in value:
            _require_safe_value(nested)
    elif isinstance(value, (str, int, float, bool)) or value is None:
        return
    else:
        raise ArtifactHaltForReview("invalid_artifact")



def _locked_method(method):
    @functools.wraps(method)
    def wrapped(self, *args, **kwargs):
        with self._locked():
            return method(self, *args, **kwargs)

    return wrapped

class ArtifactStore:
    """A deliberately small filesystem seam; it has no remote or execution behavior."""

    def __init__(self, root: str | Path):
        self._root = Path(root)

    @contextmanager
    def _locked(self):
        """Serialize local writers in-process and across processes for this run root."""
        try:
            key = str(self._root.absolute())
        except OSError as error:
            raise ArtifactHaltForReview("lock_io_failed") from error
        with _LOCKS_GUARD:
            lock = _RUN_LOCKS.setdefault(key, RLock())
        with lock:
            handle = None
            acquired = False
            active_error = None
            try:
                try:
                    self._root.mkdir(parents=True, exist_ok=True)
                    lock_path = self._root / "run.lock"
                    if lock_path.exists() and (not lock_path.is_file() or lock_path.is_symlink()):
                        raise ArtifactHaltForReview("lock_io_failed")
                    handle = lock_path.open("a+b")
                    handle.seek(0)
                    if not handle.read(1):
                        handle.seek(0)
                        handle.write(b"0")
                        handle.flush()
                    if os.name == "nt":
                        import msvcrt

                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                    acquired = True
                except ArtifactHaltForReview:
                    raise
                except OSError as error:
                    raise ArtifactHaltForReview("lock_io_failed") from error
                yield
            except BaseException as error:
                active_error = error
                raise
            finally:
                teardown_error = None
                if acquired:
                    try:
                        if os.name == "nt":
                            handle.seek(0)
                            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                        else:
                            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                    except ArtifactHaltForReview as error:
                        teardown_error = (error, None)
                    except OSError as error:
                        teardown_error = (ArtifactHaltForReview("lock_io_failed"), error)
                if handle is not None:
                    try:
                        handle.close()
                    except ArtifactHaltForReview as error:
                        if teardown_error is None:
                            teardown_error = (error, None)
                    except OSError as error:
                        if teardown_error is None:
                            teardown_error = (ArtifactHaltForReview("lock_io_failed"), error)
                if active_error is None and teardown_error is not None:
                    halt, cause = teardown_error
                    if cause is None:
                        raise halt
                    raise halt from cause

    @property
    def _run_path(self) -> Path:
        return self._root / "run.json"

    @property
    def _journal_path(self) -> Path:
        return self._root / "journal.jsonl"

    @property
    def _state_path(self) -> Path:
        return self._root / "state.jsonl"

    def run_exists(self) -> bool:
        return self._run_path.is_file()

    def create_run(self, request: RunRequest) -> Path:
        with self._locked():
            return self._create_run(request)

    def _create_run(self, request: RunRequest) -> Path:
        if not isinstance(request, RunRequest):
            raise ArtifactHaltForReview("invalid_run_request")
        self._root.mkdir(parents=True, exist_ok=True)
        (self._root / "objects").mkdir(exist_ok=True)
        (self._root / "cycles").mkdir(exist_ok=True)
        payload = request.to_canonical_dict()
        self._write_immutable(self._run_path, _canonical_bytes(payload), "run_collision")
        return self._root

    @_locked_method
    def put_role_artifact(
        self,
        cycle: int,
        role: Role,
        envelope: RoleEnvelope | Mapping[str, Any],
        idempotency_key: str,
    ) -> str:
        self._require_initialized()
        self._require_cycle(cycle)
        self._require_idempotency_key(idempotency_key)
        if isinstance(envelope, Mapping):
            _require_safe_value(envelope)
            raise ArtifactHaltForReview("invalid_role_artifact")
        if not isinstance(envelope, RoleEnvelope) or not isinstance(role, Role) or envelope.role is not role:
            raise ArtifactHaltForReview("invalid_role_artifact")
        payload = envelope.to_canonical_dict()
        _require_safe_value(payload)
        object_bytes = _canonical_bytes(payload)
        artifact_hash = sha256(object_bytes).hexdigest()
        cycle_path = self._cycle_path(cycle)
        cycle_path.mkdir(parents=True, exist_ok=True)
        reference_path = cycle_path / f"{role.value}.json"
        if reference_path.exists():
            existing = self._read_json(reference_path, "invalid_cycle_reference")
            if existing.get("idempotency_key") != idempotency_key or existing.get("stage") != role.value:
                raise ArtifactHaltForReview("idempotency_collision")
            existing_hash = existing.get("artifact_hash")
            if not isinstance(existing_hash, str):
                raise ArtifactHaltForReview("invalid_cycle_reference")
            self._validate_object(existing_hash)
            return existing_hash
        for existing in self._all_references():
            if existing["idempotency_key"] == idempotency_key:
                raise ArtifactHaltForReview("idempotency_collision")
        self._write_immutable(self._object_path(artifact_hash), object_bytes, "artifact_collision")
        reference = {
            "artifact_hash": artifact_hash,
            "cycle": cycle,
            "idempotency_key": idempotency_key,
            "schema_version": SCHEMA_VERSION,
            "stage": role.value,
        }
        self._write_immutable(reference_path, _canonical_bytes(reference), "artifact_reference_collision")
        return artifact_hash

    def put_completed_role_artifact(
        self,
        cycle: int,
        role: Role,
        envelope: RoleEnvelope,
        result: CompletedRoleResult,
        idempotency_key: str,
    ) -> str:
        with self._locked():
            return self._put_completed_role_artifact(
                cycle, role, envelope, result, idempotency_key
            )

    def _put_completed_role_artifact(
        self,
        cycle: int,
        role: Role,
        envelope: RoleEnvelope,
        result: CompletedRoleResult,
        idempotency_key: str,
    ) -> str:
        self._require_initialized()
        self._require_cycle(cycle)
        self._require_idempotency_key(idempotency_key)
        if not isinstance(role, Role) or not isinstance(envelope, RoleEnvelope) or envelope.role is not role:
            raise ArtifactHaltForReview("invalid_role_artifact")
        try:
            record = CompletedRoleRecord.create(envelope, result)
        except SchemaError as error:
            raise ArtifactHaltForReview("invalid_role_result") from error
        payload = record.to_canonical_dict()
        _require_safe_value(payload)
        object_bytes = _canonical_bytes(payload)
        artifact_hash = sha256(object_bytes).hexdigest()
        cycle_path = self._cycle_path(cycle)
        cycle_path.mkdir(parents=True, exist_ok=True)
        reference_path = cycle_path / f"{role.value}.json"
        if reference_path.exists():
            existing = self._read_json(reference_path, "invalid_cycle_reference")
            if existing.get("idempotency_key") != idempotency_key or existing.get("stage") != role.value:
                raise ArtifactHaltForReview("idempotency_collision")
            existing_hash = existing.get("artifact_hash")
            if not isinstance(existing_hash, str):
                raise ArtifactHaltForReview("invalid_cycle_reference")
            existing_value = self._validate_object(existing_hash)
            if not isinstance(existing_value, CompletedRoleRecord):
                raise ArtifactHaltForReview("missing_role_result")
            if existing_hash != artifact_hash:
                raise ArtifactHaltForReview("idempotency_collision")
            return existing_hash
        for existing in self._all_references():
            if existing["idempotency_key"] == idempotency_key:
                raise ArtifactHaltForReview("idempotency_collision")
        self._write_immutable(self._object_path(artifact_hash), object_bytes, "artifact_collision")
        reference = {
            "artifact_hash": artifact_hash,
            "cycle": cycle,
            "idempotency_key": idempotency_key,
            "schema_version": SCHEMA_VERSION,
            "stage": role.value,
        }
        self._write_immutable(reference_path, _canonical_bytes(reference), "artifact_reference_collision")
        return artifact_hash

    def load_role_result(self, cycle: int, role: Role) -> CompletedRoleResult:
        self._require_initialized()
        self._require_cycle(cycle)
        if not isinstance(role, Role):
            raise ArtifactHaltForReview("invalid_stage")
        reference_path = self._cycle_path(cycle) / f"{role.value}.json"
        if not reference_path.is_file():
            raise ArtifactHaltForReview("missing_role_result")
        reference = self._read_json(reference_path, "invalid_cycle_reference")
        self._validate_reference(reference)
        if reference["cycle"] != cycle or reference["stage"] != role.value:
            raise ArtifactHaltForReview("invalid_cycle_reference")
        value = self._validate_object(reference["artifact_hash"])
        if not isinstance(value, CompletedRoleRecord):
            raise ArtifactHaltForReview("missing_role_result")
        if value.envelope.role is not role:
            raise ArtifactHaltForReview("role_artifact_mismatch")
        return value.result

    def load_prior_inventor_results(self, before_cycle: int) -> tuple[tuple[int, Experiment], ...]:
        """Return validated completed Inventor results strictly before a cycle."""
        self._require_cycle(before_cycle)
        self.load_and_validate()
        results: list[tuple[int, Experiment]] = []
        for reference in self._all_references():
            if reference["cycle"] >= before_cycle or reference["stage"] != Role.INVENTOR.value:
                continue
            result = self.load_role_result(reference["cycle"], Role.INVENTOR)
            if not isinstance(result, Experiment):
                raise ArtifactHaltForReview("invalid_inventor_history")
            results.append((reference["cycle"], result))
        return tuple(sorted(results, key=lambda item: item[0]))

    def load_prior_gate_decisions(self, before_cycle: int) -> tuple[StateEventRecord, ...]:
        """Return validated Gate decisions strictly before a cycle."""
        self._require_cycle(before_cycle)
        self.load_and_validate()
        return tuple(
            row for row in self._state_rows(require_determinate=True)
            if row.event == "gate_decided" and row.cycle < before_cycle
        )
    def append_transition(
        self,
        cycle: int,
        from_stage: str,
        to_stage: str,
        artifact_hash: str,
        idempotency_key: str,
    ) -> Mapping[str, Any]:
        with self._locked():
            return self._append_transition(
                cycle, from_stage, to_stage, artifact_hash, idempotency_key
            )

    def _append_transition(
        self,
        cycle: int,
        from_stage: str,
        to_stage: str,
        artifact_hash: str,
        idempotency_key: str,
    ) -> Mapping[str, Any]:
        self._require_initialized()
        self._require_cycle(cycle)
        self._require_stage(from_stage, allow_start=True)
        self._require_stage(to_stage)
        self._require_idempotency_key(idempotency_key)
        rows = self._journal_rows()
        cycle_rows = [row for row in rows if row["cycle"] == cycle]
        if from_stage != (cycle_rows[-1]["to_stage"] if cycle_rows else "start"):
            raise ArtifactHaltForReview("invalid_transition")
        if _NEXT_STAGE.get(from_stage) != to_stage:
            raise ArtifactHaltForReview("invalid_transition")
        matches = [
            reference for reference in self._all_references()
            if reference.get("cycle") == cycle and reference.get("stage") == to_stage
            and reference.get("artifact_hash") == artifact_hash and reference.get("idempotency_key") == idempotency_key
        ]
        if len(matches) != 1:
            raise ArtifactHaltForReview("journal_reference_mismatch")
        if any(row["idempotency_key"] == idempotency_key for row in self._journal_rows()):
            raise ArtifactHaltForReview("idempotency_collision")
        self._validate_object(artifact_hash)
        row = {
            "artifact_hash": artifact_hash,
            "cycle": cycle,
            "from_stage": from_stage,
            "idempotency_key": idempotency_key,
            "schema_version": SCHEMA_VERSION,
            "sequence": len(rows) + 1,
            "to_stage": to_stage,
        }
        self._journal_path.parent.mkdir(parents=True, exist_ok=True)
        with self._journal_path.open("a", encoding="utf-8", newline="\n") as journal:
            journal.write(_canonical_bytes(row).decode("utf-8") + "\n")
            journal.flush()
            os.fsync(journal.fileno())
        return row

    def append_state_event(
        self,
        cycle: int,
        stage: str,
        event: str,
        reason_code: str,
        budget_usage: BudgetUsage,
        retry_count: int,
        *,
        action: str | None = None,
        invocation_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> StateEventRecord:
        with self._locked():
            return self._append_state_event(
                cycle, stage, event, reason_code, budget_usage, retry_count,
                action=action, invocation_id=invocation_id, idempotency_key=idempotency_key,
            )

    def reserve_role_attempt(
        self,
        cycle: int,
        role: Role,
        envelope: RoleEnvelope,
        idempotency_key: str,
        budget_usage: BudgetUsage,
        retry_count: int,
    ) -> StateEventRecord:
        if not isinstance(role, Role) or not isinstance(envelope, RoleEnvelope) or envelope.role is not role:
            raise ArtifactHaltForReview("invalid_role_artifact")
        return self.append_state_event(
            cycle, role.value, "role_reserved", "reserved", budget_usage, retry_count,
            invocation_id=envelope.invocation_id, idempotency_key=idempotency_key,
        )

    def complete_role_attempt(
        self,
        cycle: int,
        role: Role,
        envelope: RoleEnvelope,
        result: CompletedRoleResult,
        idempotency_key: str,
        budget_usage: BudgetUsage,
        retry_count: int,
    ) -> str:
        with self._locked():
            rows = self._state_rows(require_determinate=False)
            matching = [
                row for row in rows
                if row.event == "role_reserved" and row.cycle == cycle
                and row.stage == role.value and row.invocation_id == envelope.invocation_id
                and row.idempotency_key == idempotency_key
            ]
            if len(matching) != 1:
                raise ArtifactHaltForReview("missing_role_reservation")
            if any(
                row.event in {"role_completed", "role_failed"}
                and row.invocation_id == envelope.invocation_id
                and row.idempotency_key == idempotency_key
                for row in rows
            ):
                raise ArtifactHaltForReview("state_event_collision")
            artifact_hash = self._put_completed_role_artifact(
                cycle, role, envelope, result, idempotency_key
            )
            cycle_rows = [row for row in self._journal_rows() if row["cycle"] == cycle]
            from_stage = cycle_rows[-1]["to_stage"] if cycle_rows else "start"
            self._append_transition(cycle, from_stage, role.value, artifact_hash, idempotency_key)
            self._append_state_event(
                cycle, role.value, "role_completed", "completed", budget_usage, retry_count,
                action=None, invocation_id=envelope.invocation_id, idempotency_key=idempotency_key,
            )
            return artifact_hash

    def load_state(self) -> StateProjection:
        rows = self._state_rows(require_determinate=True)
        if rows:
            return StateProjection(rows[-1].budget_usage, rows[-1].retry_count, rows[-1], tuple(rows))
        return StateProjection(BudgetUsage(), 0, None, ())

    def _reconstruct_summary(self) -> RunSummary | None:
        rows = self._state_rows(require_determinate=True)
        completed = [row for row in rows if row.event == "run_completed"]
        if not completed:
            return None
        terminal = completed[-1]
        run = self._read_json(self._run_path, "invalid_run")
        return RunSummary(SCHEMA_VERSION, run["run_id"], terminal.action, terminal.reason_code, terminal.cycle + 1)

    def load_summary(self) -> RunSummary:
        expected = self._reconstruct_summary()
        if expected is None:
            raise ArtifactHaltForReview("missing_terminal_state")
        path = self._root / "summary.json"
        if path.exists():
            value = self._read_json(path, "invalid_summary")
            try:
                persisted = RunSummary(**value)
            except (TypeError, SchemaError) as error:
                raise ArtifactHaltForReview("invalid_summary") from error
            if persisted != expected:
                raise ArtifactHaltForReview("summary_state_mismatch")
        return expected

    def write_summary(self, summary: RunSummary) -> Path:
        if not isinstance(summary, RunSummary):
            raise ArtifactHaltForReview("invalid_summary")
        run = self._read_json(self._run_path, "invalid_run")
        if summary.run_id != run.get("run_id"):
            raise ArtifactHaltForReview("summary_run_mismatch")
        expected = self._reconstruct_summary()
        if expected is not None and summary != expected:
            raise ArtifactHaltForReview("summary_state_mismatch")
        path = self._root / "summary.json"
        if path.exists() and "cycles" in self._read_json(path, "invalid_summary"):
            raise ArtifactHaltForReview("summary_conflict")
        self._atomic_replace(path, _canonical_bytes(summary.to_canonical_dict()))
        return path

    def load_and_validate(self) -> Mapping[str, Any]:
        self._require_initialized()
        self._validate_run_tree()
        run = self._read_json(self._run_path, "invalid_run")
        self._validate_schema(run)
        _require_safe_value(run)
        self._validate_run(run)
        references = self._all_references()
        object_hashes = self._validate_all_objects()
        if object_hashes != {reference["artifact_hash"] for reference in references}:
            raise ArtifactHaltForReview("unreferenced_object")
        for reference in references:
            self._validate_reference(reference)
            self._validate_object(reference["artifact_hash"])
        rows = self._journal_rows()
        self._validate_relationships(references, rows)
        self._state_rows(require_determinate=True)
        summary_path = self._root / "summary.json"
        if summary_path.exists():
            summary_value = self._read_json(summary_path, "invalid_summary")
            if "final_action" in summary_value:
                self.load_summary()
        return run

    def resume_cursor(self) -> ResumeCursor:
        self.load_and_validate()
        references = self._all_references()
        completed = frozenset(reference["idempotency_key"] for reference in references)
        by_cycle: dict[int, set[str]] = {}
        for reference in references:
            by_cycle.setdefault(reference["cycle"], set()).add(reference["stage"])
        highest_cycle = max(by_cycle, default=0)
        for cycle in range(highest_cycle + 1):
            completed_stages = by_cycle.get(cycle, set())
            for stage in _STAGES:
                if stage not in completed_stages:
                    return ResumeCursor(cycle, stage, completed)
        return ResumeCursor(highest_cycle + 1, _STAGES[0], completed)

    def project_summary(self) -> Mapping[str, Any]:
        summary_path = self._root / "summary.json"
        if summary_path.exists() and "final_action" in self._read_json(summary_path, "invalid_summary"):
            raise ArtifactHaltForReview("summary_conflict")
        run = self.load_and_validate()
        cycles: list[dict[str, Any]] = []
        for cycle in sorted({reference["cycle"] for reference in self._all_references()}):
            artifacts = [
                {
                    "artifact_hash": reference["artifact_hash"],
                    "idempotency_key": reference["idempotency_key"],
                    "stage": reference["stage"],
                }
                for reference in self._cycle_references(cycle)
            ]
            cycles.append({"artifacts": artifacts, "cycle": cycle})
        summary = {
            "cycles": cycles,
            "journal_sequences": [row["sequence"] for row in self._journal_rows()],
            "run_id": run["run_id"],
            "schema_version": SCHEMA_VERSION,
        }
        self._atomic_replace(self._root / "summary.json", _canonical_bytes(summary))
        return summary

    def _validate_run_tree(self) -> None:
        allowed = {"run.json", "journal.jsonl", "state.jsonl", "summary.json", "objects", "cycles", "run.lock"}
        for item in self._root.iterdir():
            normalized = item.name.casefold().replace("-", "_")
            if any(part in normalized for part in _FORBIDDEN_FIELD_PARTS):
                raise ArtifactHaltForReview("sensitive_content")
            if item.name not in allowed:
                raise ArtifactHaltForReview("unexpected_artifact_file")
        if not (self._root / "objects").is_dir() or not (self._root / "cycles").is_dir():
            raise ArtifactHaltForReview("invalid_run_tree")
        lock_path = self._root / "run.lock"
        if lock_path.exists() and (not lock_path.is_file() or lock_path.is_symlink()):
            raise ArtifactHaltForReview("unsafe_lock_path")

    def _require_initialized(self) -> None:
        if not self._run_path.is_file():
            raise ArtifactHaltForReview("missing_run")

    @staticmethod
    def _require_cycle(cycle: int) -> None:
        if isinstance(cycle, bool) or not isinstance(cycle, int) or cycle < 0:
            raise ArtifactHaltForReview("invalid_cycle")

    @staticmethod
    def _require_idempotency_key(value: str) -> None:
        if not isinstance(value, str) or not value or len(value) > 4_000:
            raise ArtifactHaltForReview("invalid_idempotency_key")

    @staticmethod
    def _require_stage(value: str, *, allow_start: bool = False) -> None:
        if value == "start" and allow_start:
            return
        if value not in _STAGES:
            raise ArtifactHaltForReview("invalid_stage")

    def _cycle_path(self, cycle: int) -> Path:
        return self._root / "cycles" / str(cycle)

    def _object_path(self, artifact_hash: str) -> Path:
        if not isinstance(artifact_hash, str) or len(artifact_hash) != 64 or any(char not in "0123456789abcdef" for char in artifact_hash):
            raise ArtifactHaltForReview("invalid_artifact_hash")
        return self._root / "objects" / f"{artifact_hash}.json"

    def _atomic_replace(self, path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        temp_path = Path(temp_name)
        try:
            with os.fdopen(descriptor, "wb") as temp_file:
                temp_file.write(content)
                temp_file.flush()
                os.fsync(temp_file.fileno())
            os.replace(temp_path, path)
        except OSError as error:
            raise ArtifactHaltForReview("atomic_write_failed") from error
        finally:
            if temp_path.exists():
                temp_path.unlink()

    def _write_immutable(self, path: Path, content: bytes, collision_reason: str) -> None:
        if path.exists():
            try:
                current = path.read_bytes()
            except OSError as error:
                raise ArtifactHaltForReview("artifact_read_failed") from error
            if current != content:
                raise ArtifactHaltForReview(collision_reason)
            return
        self._atomic_replace(path, content)

    def _read_json(self, path: Path, reason: str) -> dict[str, Any]:
        try:
            decoded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ArtifactHaltForReview(reason) from error
        if not isinstance(decoded, dict):
            raise ArtifactHaltForReview(reason)
        if _canonical_bytes(decoded) != path.read_bytes():
            raise ArtifactHaltForReview(reason)
        return decoded

    @staticmethod
    def _validate_schema(value: Mapping[str, Any]) -> None:
        if value.get("schema_version") != SCHEMA_VERSION:
            raise ArtifactHaltForReview("unsupported_schema_version")

    @staticmethod
    def _validate_run(value: Mapping[str, Any]) -> None:
        try:
            budget_limits = BudgetLimits(**value["budget_limits"])
            run = RunRequest(
                schema_version=value["schema_version"],
                run_id=value["run_id"],
                brief=value["brief"],
                constraints=tuple(value["constraints"]),
                baseline=value["baseline"],
                budget_limits=budget_limits,
                execution_inputs=value.get('execution_inputs', {}),
                rubric=value.get('rubric', 'ground_truth_accuracy'),
                approval_threshold=value["approval_threshold"],
            )
        except (KeyError, TypeError, SchemaError) as error:
            raise ArtifactHaltForReview("invalid_run") from error
        if run.to_canonical_dict() != value:
            raise ArtifactHaltForReview("invalid_run")

    def _validate_all_objects(self) -> set[str]:
        object_root = self._root / "objects"
        object_hashes: set[str] = set()
        if not object_root.is_dir():
            raise ArtifactHaltForReview("missing_objects")
        for item in object_root.iterdir():
            if not item.is_file() or item.suffix != ".json":
                raise ArtifactHaltForReview("invalid_object_path")
            self._validate_object(item.stem)
            object_hashes.add(item.stem)
        return object_hashes

    def _validate_object(self, artifact_hash: str) -> RoleEnvelope | CompletedRoleRecord:
        path = self._object_path(artifact_hash)
        try:
            content = path.read_bytes()
            value = json.loads(content.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ArtifactHaltForReview("missing_or_invalid_object") from error
        if sha256(content).hexdigest() != artifact_hash:
            raise ArtifactHaltForReview("artifact_hash_mismatch")
        if not isinstance(value, dict) or _canonical_bytes(value) != content:
            raise ArtifactHaltForReview("noncanonical_artifact")
        _require_safe_value(value)
        self._validate_schema(value)
        try:
            if set(value) == {"envelope", "result", "result_type", "schema_version"}:
                return CompletedRoleRecord.rehydrate(
                    schema_version=value["schema_version"],
                    envelope=value["envelope"],
                    result_type=value["result_type"],
                    result=value["result"],
                )
            return RoleEnvelope.rehydrate(
                schema_version=value["schema_version"], role=Role(value["role"]),
                invocation_id=value["invocation_id"], payload=value["payload"],
            )
        except (KeyError, TypeError, ValueError, SchemaError) as error:
            raise ArtifactHaltForReview("invalid_role_artifact") from error

    def _cycle_references(self, cycle: int) -> list[dict[str, Any]]:
        path = self._cycle_path(cycle)
        if not path.exists():
            return []
        if not path.is_dir():
            raise ArtifactHaltForReview("invalid_cycle_reference")
        references: list[dict[str, Any]] = []
        for item in sorted(path.iterdir(), key=lambda entry: entry.name):
            normalized = item.name.casefold().replace("-", "_")
            if any(part in normalized for part in _FORBIDDEN_FIELD_PARTS):
                raise ArtifactHaltForReview("sensitive_content")
            if not item.is_file() or item.suffix != ".json" or item.stem not in _STAGES:
                raise ArtifactHaltForReview("unexpected_artifact_file")
            reference = self._read_json(item, "invalid_cycle_reference")
            if reference.get("cycle") != cycle or reference.get("stage") != item.stem:
                raise ArtifactHaltForReview("invalid_cycle_reference")
            references.append(reference)
        return references

    def _all_references(self) -> list[dict[str, Any]]:
        cycles_root = self._root / "cycles"
        if not cycles_root.exists() or not cycles_root.is_dir():
            raise ArtifactHaltForReview("missing_cycles")
        references: list[dict[str, Any]] = []
        for directory in sorted(cycles_root.iterdir(), key=lambda item: item.name):
            if not directory.is_dir() or not directory.name.isdecimal() or directory.name != str(int(directory.name)):
                raise ArtifactHaltForReview("invalid_cycle_reference")
            references.extend(self._cycle_references(int(directory.name)))
        return references

    def _validate_reference(self, reference: Mapping[str, Any]) -> None:
        self._validate_schema(reference)
        expected = {"artifact_hash", "cycle", "idempotency_key", "schema_version", "stage"}
        if set(reference) != expected:
            raise ArtifactHaltForReview("invalid_cycle_reference")
        self._require_cycle(reference["cycle"])
        self._require_stage(reference["stage"])
        self._require_idempotency_key(reference["idempotency_key"])
        self._object_path(reference["artifact_hash"])

    def _validate_relationships(self, references: list[dict[str, Any]], rows: list[dict[str, Any]]) -> None:
        by_key: dict[str, dict[str, Any]] = {}
        for reference in references:
            key = reference["idempotency_key"]
            if key in by_key:
                raise ArtifactHaltForReview("idempotency_collision")
            value = self._validate_object(reference["artifact_hash"])
            object_role = value.envelope.role if isinstance(value, CompletedRoleRecord) else value.role
            if object_role.value != reference["stage"]:
                raise ArtifactHaltForReview("role_artifact_mismatch")
            by_key[key] = reference
        seen_rows: set[str] = set()
        expected_from: dict[int, str] = {}
        for row in rows:
            key = row["idempotency_key"]
            if key in seen_rows:
                raise ArtifactHaltForReview("idempotency_collision")
            seen_rows.add(key)
            if row["from_stage"] != expected_from.get(row["cycle"], "start"):
                raise ArtifactHaltForReview("invalid_transition")
            expected_from[row["cycle"]] = row["to_stage"]
            if _NEXT_STAGE.get(row["from_stage"]) != row["to_stage"]:
                raise ArtifactHaltForReview("invalid_transition")
            reference = by_key.get(key)
            if reference is None:
                raise ArtifactHaltForReview("journal_reference_mismatch")
            if any(reference[field] != row[field] for field in ("cycle", "stage", "artifact_hash") if field != "stage") or reference["stage"] != row["to_stage"]:
                raise ArtifactHaltForReview("journal_reference_mismatch")
        if set(by_key) != seen_rows:
            raise ArtifactHaltForReview("unlinked_artifact_reference")

    def _append_state_event(
        self,
        cycle: int,
        stage: str,
        event: str,
        reason_code: str,
        budget_usage: BudgetUsage,
        retry_count: int,
        *,
        action: str | None,
        invocation_id: str | None,
        idempotency_key: str | None,
    ) -> StateEventRecord:
        self._require_initialized()
        self._require_cycle(cycle)
        if isinstance(reason_code, str):
            normalized = reason_code.casefold().replace("-", "_")
            if any(part in normalized for part in _FORBIDDEN_FIELD_PARTS):
                raise ArtifactHaltForReview("sensitive_content")
        if isinstance(idempotency_key, str):
            normalized_key = idempotency_key.casefold().replace("-", "_")
            if any(part in normalized_key for part in _FORBIDDEN_FIELD_PARTS):
                raise ArtifactHaltForReview("sensitive_content")
        rows = self._state_rows(require_determinate=False)
        explicit_action = action is not None
        if event in {"gate_decided", "run_completed"} and action is None:
            action = "halt_for_review" if reason_code == "halt_for_review" else "advance"
        if event.startswith("role_"):
            existing = [
                row for row in rows
                if (row.cycle, row.stage, row.event, row.invocation_id, row.idempotency_key)
                == (cycle, stage, event, invocation_id, idempotency_key)
            ]
        else:
            existing = [
                row for row in rows if (row.cycle, row.stage, row.event) == (cycle, stage, event)
            ]
        if existing:
            prior = existing[0]
            if (prior.reason_code, prior.action, prior.budget_usage, prior.retry_count,
                    prior.invocation_id, prior.idempotency_key) == (
                    reason_code, action, budget_usage, retry_count, invocation_id, idempotency_key):
                return prior
            raise ArtifactHaltForReview("state_event_collision")
        if rows and rows[-1].event == "run_completed":
            raise ArtifactHaltForReview("run_already_completed")
        pending = [row for row in rows if row.event == "role_reserved" and not any(
            outcome.event in {"role_completed", "role_failed"}
            and outcome.invocation_id == row.invocation_id
            and outcome.idempotency_key == row.idempotency_key for outcome in rows)]
        if event == "role_reserved" and pending:
            raise ArtifactHaltForReview("active_role_attempt")
        if event == "gate_decided" and pending:
            raise ArtifactHaltForReview("indeterminate_role_attempt")
        if event == "run_completed" and explicit_action:
            gates = [row for row in rows if row.event == "gate_decided" and row.cycle == cycle]
            if not gates:
                raise ArtifactHaltForReview("missing_gate_decision")
            if (gates[-1].action, gates[-1].reason_code) != (action, reason_code):
                raise ArtifactHaltForReview("terminal_decision_mismatch")
        previous_hash = rows[-1].row_hash if rows else "0" * 64
        seed = {
            "action": action,
            "budget_usage": budget_usage.to_canonical_dict() if isinstance(budget_usage, BudgetUsage) else budget_usage,
            "cycle": cycle,
            "event": event,
            "idempotency_key": idempotency_key,
            "invocation_id": invocation_id,
            "previous_hash": previous_hash,
            "reason_code": reason_code,
            "retry_count": retry_count,
            "schema_version": SCHEMA_VERSION,
            "sequence": len(rows) + 1,
            "stage": stage,
        }
        row_hash = sha256(_canonical_bytes(seed)).hexdigest()
        try:
            candidate = StateEventRecord(
                row_hash=row_hash,
                **{key: value for key, value in seed.items() if key != "budget_usage"},
                budget_usage=budget_usage,
            )
        except (TypeError, SchemaError) as error:
            raise ArtifactHaltForReview("invalid_state_event") from error
        if event in {"role_completed", "role_failed"}:
            reserved = any(
                row.event == "role_reserved" and row.cycle == cycle and row.stage == stage
                and row.invocation_id == invocation_id and row.idempotency_key == idempotency_key
                for row in rows
            )
            if not reserved:
                raise ArtifactHaltForReview("missing_role_reservation")
        self._validate_cumulative_state(rows, candidate)
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        with self._state_path.open("a", encoding="utf-8", newline="\n") as state_file:
            state_file.write(_canonical_bytes(candidate.to_canonical_dict()).decode("utf-8") + "\n")
            state_file.flush()
            os.fsync(state_file.fileno())
        return candidate

    @staticmethod
    def _validate_cumulative_state(rows: list[StateEventRecord], candidate: StateEventRecord) -> None:
        if not rows:
            return
        prior = rows[-1]
        usage_fields = ("calls", "queries", "scrapes", "llm_calls", "retries", "cost", "stages")
        if any(getattr(candidate.budget_usage, name) < getattr(prior.budget_usage, name) for name in usage_fields):
            raise ArtifactHaltForReview("noncumulative_budget_usage")
        if candidate.retry_count < prior.retry_count:
            raise ArtifactHaltForReview("noncumulative_retry_count")

    def _state_rows(self, *, require_determinate: bool) -> list[StateEventRecord]:
        if not self._state_path.exists():
            return []
        try:
            content = self._state_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            raise ArtifactHaltForReview("invalid_state_journal") from error
        if content and not content.endswith("\n"):
            raise ArtifactHaltForReview("truncated_state_journal")
        rows: list[StateEventRecord] = []
        previous_hash = "0" * 64
        expected_fields = {
            "action", "budget_usage", "cycle", "event", "idempotency_key", "invocation_id",
            "previous_hash", "reason_code", "retry_count", "row_hash", "schema_version",
            "sequence", "stage",
        }
        for expected_sequence, line in enumerate(content.splitlines(), start=1):
            try:
                value = json.loads(line)
            except json.JSONDecodeError as error:
                raise ArtifactHaltForReview("invalid_state_journal") from error
            if not isinstance(value, dict) or set(value) != expected_fields or _canonical_bytes(value).decode("utf-8") != line:
                raise ArtifactHaltForReview("invalid_state_journal")
            if value.get("schema_version") != SCHEMA_VERSION:
                raise ArtifactHaltForReview("unsupported_state_version")
            if value.get("sequence") != expected_sequence:
                raise ArtifactHaltForReview("invalid_state_sequence")
            if value.get("previous_hash") != previous_hash:
                raise ArtifactHaltForReview("invalid_state_chain")
            unhashed = {key: item for key, item in value.items() if key != "row_hash"}
            if sha256(_canonical_bytes(unhashed)).hexdigest() != value.get("row_hash"):
                raise ArtifactHaltForReview("state_hash_mismatch")
            try:
                row = StateEventRecord(
                    budget_usage=BudgetUsage(**value["budget_usage"]),
                    **{key: item for key, item in value.items() if key != "budget_usage"},
                )
            except (TypeError, SchemaError) as error:
                raise ArtifactHaltForReview("invalid_state_event") from error
            self._validate_cumulative_state(rows, row)
            rows.append(row)
            previous_hash = row.row_hash
        if require_determinate:
            outcomes = {
                (row.invocation_id, row.idempotency_key)
                for row in rows if row.event in {"role_completed", "role_failed"}
            }
            if any(
                row.event == "role_reserved" and (row.invocation_id, row.idempotency_key) not in outcomes
                for row in rows
            ):
                raise ArtifactHaltForReview("indeterminate_role_attempt")
        return rows

    def _journal_rows(self) -> list[dict[str, Any]]:
        if not self._journal_path.exists():
            return []
        try:
            lines = self._journal_path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError) as error:
            raise ArtifactHaltForReview("invalid_journal") from error
        raw = self._journal_path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise ArtifactHaltForReview("truncated_journal")
        rows: list[dict[str, Any]] = []
        for expected_sequence, line in enumerate(lines, start=1):
            try:
                row = json.loads(line)
            except json.JSONDecodeError as error:
                raise ArtifactHaltForReview("truncated_journal") from error
            if not isinstance(row, dict) or _canonical_bytes(row).decode("utf-8") != line:
                raise ArtifactHaltForReview("invalid_journal")
            expected_fields = {
                "artifact_hash", "cycle", "from_stage", "idempotency_key", "schema_version", "sequence", "to_stage",
            }
            if set(row) != expected_fields or row["sequence"] != expected_sequence:
                raise ArtifactHaltForReview("invalid_journal_sequence")
            self._validate_schema(row)
            self._require_cycle(row["cycle"])
            self._require_stage(row["from_stage"], allow_start=True)
            self._require_stage(row["to_stage"])
            self._require_idempotency_key(row["idempotency_key"])
            self._validate_object(row["artifact_hash"])
            rows.append(row)
        return rows
