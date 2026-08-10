"""Immutable, bounded, provider-neutral values at the orchestration seam."""

from __future__ import annotations

from collections.abc import Mapping as MappingABC, Sequence
from dataclasses import dataclass, fields
from enum import StrEnum
from hashlib import sha256
import json
import math
import re
from types import MappingProxyType
from typing import Any, Mapping
from uuid import UUID, uuid4


SCHEMA_VERSION = "1.0"
MAX_TEXT_CHARS = 4_000
MAX_LIST_ITEMS = 100
_FORBIDDEN_FIELD_PARTS = ("secret", "token", "password", "authorization", "transcript", "api_key", "credential")


class SchemaError(ValueError):
    """Raised when a value cannot safely cross the orchestration seam."""


def _require_schema_version(schema_version: str) -> None:
    if schema_version != SCHEMA_VERSION:
        raise SchemaError(f"unsupported schema version: {schema_version!r}")


def _require_text(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value:
        raise SchemaError(f"{field_name} must be a non-empty string")
    if len(value) > MAX_TEXT_CHARS:
        raise SchemaError(f"text exceeds {MAX_TEXT_CHARS} characters: {field_name}")


def _require_finite(value: float, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise SchemaError(f"{field_name} must be finite")


def _check_safe_key(key: str) -> None:
    normalized = key.casefold().replace("-", "_")
    if any(part in normalized for part in _FORBIDDEN_FIELD_PARTS):
        raise SchemaError(f"forbidden sensitive field: {key}")


def _freeze(value: Any, *, field_name: str = "value") -> Any:
    """Validate recursive artifact values while making mutable inputs immutable."""
    if isinstance(value, CanonicalContract):
        return value
    if isinstance(value, StrEnum):
        return value
    if isinstance(value, Mapping):
        if len(value) > MAX_LIST_ITEMS:
            raise SchemaError(f"list exceeds {MAX_LIST_ITEMS} items: {field_name}")
        frozen: dict[str, Any] = {}
        for key, nested in value.items():
            if not isinstance(key, str):
                raise SchemaError(f"{field_name} keys must be strings")
            _check_safe_key(key)
            frozen[key] = _freeze(nested, field_name=key)
        return MappingProxyType(frozen)
    if isinstance(value, (tuple, list)):
        if len(value) > MAX_LIST_ITEMS:
            raise SchemaError(f"list exceeds {MAX_LIST_ITEMS} items: {field_name}")
        return tuple(_freeze(item, field_name=field_name) for item in value)
    if isinstance(value, str):
        _require_text(value, field_name)
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        _require_finite(value, field_name)
        return value
    if isinstance(value, bool) or value is None:
        return value
    raise SchemaError(f"unsupported canonical value for {field_name}: {type(value).__name__}")


def _canonical_value(value: Any) -> Any:
    if isinstance(value, CanonicalContract):
        return value.to_canonical_dict()
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, Mapping):
        return {key: _canonical_value(value[key]) for key in sorted(value)}
    if isinstance(value, tuple):
        return [_canonical_value(item) for item in value]
    if isinstance(value, float):
        _require_finite(value, "value")
    return value


@dataclass(frozen=True, slots=True)
class CanonicalContract:
    """Small common interface for strict values that may be persisted as JSON."""

    def to_canonical_dict(self) -> dict[str, Any]:
        return {item.name: _canonical_value(getattr(self, item.name)) for item in fields(self)}

    def to_canonical_json(self) -> str:
        return json.dumps(self.to_canonical_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False)


@dataclass(frozen=True, slots=True)
class BudgetCharge(CanonicalContract):
    """Complete bounded charge declared before one external execution."""

    schema_version: str = SCHEMA_VERSION
    calls: int = 0
    queries: int = 0
    scrapes: int = 0
    llm_calls: int = 0
    cost: float = 0.0
    stages: int = 0

    def __post_init__(self) -> None:
        _require_schema_version(self.schema_version)
        for name in ("calls", "queries", "scrapes", "llm_calls", "stages"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise SchemaError(f"{name} must be a non-negative integer")
            if value > 1_000_000:
                raise SchemaError(f"{name} must be bounded at 1000000")
        _require_finite(self.cost, "cost")
        if self.cost < 0:
            raise SchemaError("cost must be a non-negative finite number")
        if self.cost > 1_000_000_000:
            raise SchemaError("cost must be bounded at 1000000000")

@dataclass(frozen=True, slots=True)
class RunRequest(CanonicalContract):
    schema_version: str
    run_id: str
    brief: str
    constraints: tuple[str, ...]
    baseline: Mapping[str, float]
    budget_limits: "BudgetLimits"
    approval_threshold: float

    def __post_init__(self) -> None:
        from .budgets import BudgetLimits

        _require_schema_version(self.schema_version)
        _require_text(self.run_id, "run_id")
        _require_text(self.brief, "brief")
        if isinstance(self.constraints, (str, bytes)) or not isinstance(self.constraints, Sequence):
            raise SchemaError("constraints must be a non-string sequence")
        frozen_constraints = _freeze(tuple(self.constraints), field_name="constraints")
        if not all(isinstance(item, str) for item in frozen_constraints):
            raise SchemaError("constraints must contain text")
        if not isinstance(self.baseline, MappingABC):
            raise SchemaError("baseline must be a mapping")
        frozen_baseline = _freeze(self.baseline, field_name="baseline")
        if not all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in frozen_baseline.values()):
            raise SchemaError("baseline values must be finite numbers")
        if not isinstance(self.budget_limits, BudgetLimits):
            raise SchemaError("budget_limits must be BudgetLimits")
        _require_finite(self.approval_threshold, "approval_threshold")
        if self.approval_threshold != 0.90:
            raise SchemaError("approval_threshold must be exactly 0.90")
        object.__setattr__(self, "constraints", frozen_constraints)
        object.__setattr__(self, "baseline", frozen_baseline)

@dataclass(frozen=True, slots=True)
class RunSummary(CanonicalContract):
    schema_version: str
    run_id: str
    final_action: str
    reason_code: str
    cycles_completed: int = 0

    def __post_init__(self) -> None:
        _require_schema_version(self.schema_version)
        _require_text(self.run_id, "run_id")
        if not isinstance(self.final_action, str) or self.final_action not in {
            "advance", "retry", "rollback", "halt_for_review",
        }:
            raise SchemaError("invalid final_action")
        _require_text(self.reason_code, "reason_code")
        if isinstance(self.cycles_completed, bool) or not isinstance(self.cycles_completed, int) or self.cycles_completed < 0:
            raise SchemaError("cycles_completed must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class Experiment(CanonicalContract):
    schema_version: str
    flow_id: str
    hypothesis: str
    proposed_change: str
    key: str = ""

    def __post_init__(self) -> None:
        _require_schema_version(self.schema_version)
        _require_text(self.flow_id, "flow_id")
        _require_text(self.hypothesis, "hypothesis")
        _require_text(self.proposed_change, "proposed_change")
        expected_key = sha256(
            json.dumps(
                {
                    "flow_id": self.flow_id,
                    "hypothesis": self.hypothesis,
                    "proposed_change": self.proposed_change,
                    "schema_version": self.schema_version,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        if self.key and self.key != expected_key:
            raise SchemaError("experiment key must be content-derived")
        object.__setattr__(self, "key", expected_key)


@dataclass(frozen=True, slots=True)
class Evidence(CanonicalContract):
    schema_version: str
    source_url: str
    excerpt: str
    observed_at: str

    def __post_init__(self) -> None:
        _require_schema_version(self.schema_version)
        _require_text(self.source_url, "source_url")
        if not self.source_url.startswith(("https://", "http://")):
            raise SchemaError("source_url must be an absolute HTTP URL")
        _require_text(self.excerpt, "excerpt")
        _require_text(self.observed_at, "observed_at")


@dataclass(frozen=True, slots=True)
class CheckerResult(CanonicalContract):
    schema_version: str
    checker: str
    accepted: bool
    reason_code: str

    def __post_init__(self) -> None:
        _require_schema_version(self.schema_version)
        _require_text(self.checker, "checker")
        if not isinstance(self.accepted, bool):
            raise SchemaError("accepted must be a boolean")
        _require_text(self.reason_code, "reason_code")


@dataclass(frozen=True, slots=True)
class EvaluationResult(CanonicalContract):
    schema_version: str
    passed: bool
    score: float
    reason_code: str

    def __post_init__(self) -> None:
        _require_schema_version(self.schema_version)
        if not isinstance(self.passed, bool):
            raise SchemaError("passed must be a boolean")
        _require_finite(self.score, "score")
        if not 0 <= self.score <= 1:
            raise SchemaError("score must be between zero and one")
        _require_text(self.reason_code, "reason_code")


class Role(StrEnum):
    INVENTOR = "inventor"
    IN_BOUNDS_CHECKER = "in_bounds_checker"
    NOVELTY_CHECKER = "novelty_checker"
    EXECUTOR = "executor"
    EVALUATOR = "evaluator"


ROLE_FIELD_ALLOWLISTS: Mapping[Role, frozenset[str]] = MappingProxyType(
    {
        Role.INVENTOR: frozenset({"run_brief", "baseline", "prior_decisions", "budget_remaining"}),
        Role.IN_BOUNDS_CHECKER: frozenset({"constraints", "experiment"}),
        Role.NOVELTY_CHECKER: frozenset({"experiment", "prior_fingerprints"}),
        Role.EXECUTOR: frozenset({"experiment", "execution_inputs"}),
        Role.EVALUATOR: frozenset({"rubric", "experiment", "evidence"}),
    }
)


@dataclass(frozen=True, slots=True, init=False)
class RoleEnvelope(CanonicalContract):
    schema_version: str
    role: Role
    invocation_id: str
    payload: Mapping[str, Any]

    @classmethod
    def _build(
        cls,
        *,
        schema_version: str,
        role: Role,
        invocation_id: str,
        payload: Mapping[str, Any],
    ) -> "RoleEnvelope":
        envelope = object.__new__(cls)
        object.__setattr__(envelope, "schema_version", schema_version)
        object.__setattr__(envelope, "role", role)
        object.__setattr__(envelope, "invocation_id", invocation_id)
        object.__setattr__(envelope, "payload", payload)
        envelope.__post_init__()
        return envelope

    @classmethod
    def create(cls, role: Role, payload: Mapping[str, Any]) -> "RoleEnvelope":
        return cls._build(
            schema_version=SCHEMA_VERSION,
            role=role,
            invocation_id=str(uuid4()),
            payload=payload,
        )

    @classmethod
    def rehydrate(
        cls,
        *,
        schema_version: str,
        role: Role,
        invocation_id: str,
        payload: Mapping[str, Any],
    ) -> "RoleEnvelope":
        return cls._build(
            schema_version=schema_version,
            role=role,
            invocation_id=invocation_id,
            payload=payload,
        )

    def __post_init__(self) -> None:
        _require_schema_version(self.schema_version)
        if not isinstance(self.role, Role):
            raise SchemaError("role must be a declared role")
        _require_text(self.invocation_id, "invocation_id")
        try:
            parsed_invocation_id = UUID(self.invocation_id)
        except (AttributeError, ValueError) as error:
            raise SchemaError("invocation_id must be a valid UUID") from error
        if str(parsed_invocation_id) != self.invocation_id:
            raise SchemaError("invocation_id must be a canonical UUID")
        if not isinstance(self.payload, MappingABC):
            raise SchemaError("payload must be a mapping")
        allowed = ROLE_FIELD_ALLOWLISTS[self.role]
        payload_keys = frozenset(self.payload)
        if payload_keys != allowed:
            disallowed = payload_keys - allowed
            missing = allowed - payload_keys
            if disallowed:
                raise SchemaError(f"payload fields not allowed for {self.role.value}: {sorted(disallowed)}")
            raise SchemaError(f"payload fields missing for {self.role.value}: {sorted(missing)}")
        object.__setattr__(self, "payload", _freeze(self.payload, field_name="payload"))


CompletedRoleResult = Experiment | CheckerResult | tuple[Evidence, ...] | EvaluationResult


@dataclass(frozen=True, slots=True, init=False)
class CompletedRoleRecord(CanonicalContract):
    """A validated role invocation and its complete reconstructable result."""

    schema_version: str
    envelope: RoleEnvelope
    result_type: str
    result: CompletedRoleResult

    @classmethod
    def create(cls, envelope: RoleEnvelope, result: CompletedRoleResult) -> "CompletedRoleRecord":
        if not isinstance(envelope, RoleEnvelope):
            raise SchemaError("envelope must be a RoleEnvelope")
        result_type = cls._result_type(envelope.role, result)
        return cls._build(SCHEMA_VERSION, envelope, result_type, result)

    @classmethod
    def rehydrate(
        cls,
        *,
        schema_version: str,
        envelope: Mapping[str, Any],
        result_type: str,
        result: Any,
    ) -> "CompletedRoleRecord":
        _require_schema_version(schema_version)
        try:
            role_envelope = RoleEnvelope.rehydrate(
                schema_version=envelope["schema_version"],
                role=Role(envelope["role"]),
                invocation_id=envelope["invocation_id"],
                payload=envelope["payload"],
            )
            reconstructed = cls._rehydrate_result(result_type, result)
        except (KeyError, TypeError, ValueError) as error:
            raise SchemaError("invalid completed role record") from error
        return cls._build(schema_version, role_envelope, result_type, reconstructed)

    @classmethod
    def _build(
        cls,
        schema_version: str,
        envelope: RoleEnvelope,
        result_type: str,
        result: CompletedRoleResult,
    ) -> "CompletedRoleRecord":
        _require_schema_version(schema_version)
        expected_type = cls._result_type(envelope.role, result)
        if result_type != expected_type:
            raise SchemaError("role result mismatch")
        record = object.__new__(cls)
        object.__setattr__(record, "schema_version", schema_version)
        object.__setattr__(record, "envelope", envelope)
        object.__setattr__(record, "result_type", result_type)
        object.__setattr__(record, "result", result)
        return record

    @staticmethod
    def _result_type(role: Role, result: CompletedRoleResult) -> str:
        if role is Role.INVENTOR and isinstance(result, Experiment):
            return "experiment"
        if role in (Role.IN_BOUNDS_CHECKER, Role.NOVELTY_CHECKER) and isinstance(result, CheckerResult):
            expected_checker = "in_bounds" if role is Role.IN_BOUNDS_CHECKER else "novelty"
            if result.checker != expected_checker:
                raise SchemaError("checker must match role")
            return "checker_result"
        if role is Role.EXECUTOR and isinstance(result, tuple) and all(isinstance(item, Evidence) for item in result):
            if not result:
                raise SchemaError("evidence must not be empty")
            if len(result) > MAX_LIST_ITEMS:
                raise SchemaError(f"list exceeds {MAX_LIST_ITEMS} items: result")
            return "evidence"
        if role is Role.EVALUATOR and isinstance(result, EvaluationResult):
            return "evaluation_result"
        raise SchemaError("role result mismatch")

    @staticmethod
    def _rehydrate_result(result_type: str, value: Any) -> CompletedRoleResult:
        if result_type == "experiment":
            return Experiment(**value)
        if result_type == "checker_result":
            return CheckerResult(**value)
        if result_type == "evidence":
            if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
                raise SchemaError("evidence must be a sequence")
            return tuple(Evidence(**item) for item in value)
        if result_type == "evaluation_result":
            return EvaluationResult(**value)
        raise SchemaError("unsupported role result type")


STATE_EVENTS = frozenset({"role_reserved", "role_completed", "role_failed", "gate_decided", "run_completed"})
_STATE_STAGES = frozenset(role.value for role in Role) | {"gate", "run"}
_STATE_SLUG = re.compile(r"^[a-z0-9_]+$")
_ZERO_HASH = "0" * 64


@dataclass(frozen=True, slots=True)
class StateEventRecord(CanonicalContract):
    schema_version: str
    sequence: int
    cycle: int
    stage: str
    event: str
    reason_code: str
    action: str | None
    budget_usage: Any
    retry_count: int
    invocation_id: str | None
    idempotency_key: str | None
    previous_hash: str
    row_hash: str

    def __post_init__(self) -> None:
        from .budgets import BudgetUsage

        _require_schema_version(self.schema_version)
        for name, value in (("sequence", self.sequence), ("cycle", self.cycle), ("retry_count", self.retry_count)):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise SchemaError(f"{name} must be a non-negative integer")
        if self.sequence < 1:
            raise SchemaError("sequence must be positive")
        if self.stage not in _STATE_STAGES or self.event not in STATE_EVENTS:
            raise SchemaError("invalid state event")
        if not isinstance(self.reason_code, str) or not _STATE_SLUG.fullmatch(self.reason_code):
            raise SchemaError("invalid state event")
        _check_safe_key(self.reason_code)
        if self.event in {"gate_decided", "run_completed"}:
            if self.action not in {"advance", "rollback", "retry", "halt_for_review"}:
                raise SchemaError("terminal state requires gate action")
        elif self.action is not None:
            raise SchemaError("role state cannot contain gate action")
        if not isinstance(self.budget_usage, BudgetUsage):
            raise SchemaError("budget_usage must be BudgetUsage")
        if self.invocation_id is not None:
            _require_text(self.invocation_id, "invocation_id")
            try:
                if str(UUID(self.invocation_id)) != self.invocation_id:
                    raise ValueError
            except (AttributeError, ValueError) as error:
                raise SchemaError("invocation_id must be a canonical UUID") from error
        if self.idempotency_key is not None:
            _require_text(self.idempotency_key, "idempotency_key")
        for name, value in (("previous_hash", self.previous_hash), ("row_hash", self.row_hash)):
            if not isinstance(value, str) or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
                raise SchemaError(f"invalid {name}")
        is_role_event = self.event.startswith("role_")
        if is_role_event != (self.stage in {role.value for role in Role}):
            raise SchemaError("invalid state event")
        if is_role_event and (self.invocation_id is None or self.idempotency_key is None):
            raise SchemaError("role state requires invocation identity")
        if not is_role_event and (self.invocation_id is not None or self.idempotency_key is not None):
            raise SchemaError("terminal state cannot contain invocation identity")


@dataclass(frozen=True, slots=True)
class StateProjection:
    budget_usage: Any
    retry_count: int
    last_event: StateEventRecord | None
    rows: tuple[StateEventRecord, ...]

    def __post_init__(self) -> None:
        from .budgets import BudgetUsage

        if not isinstance(self.budget_usage, BudgetUsage):
            raise SchemaError("budget_usage must be BudgetUsage")
        if isinstance(self.retry_count, bool) or not isinstance(self.retry_count, int) or self.retry_count < 0:
            raise SchemaError("retry_count must be a non-negative integer")
        if not isinstance(self.rows, tuple) or not all(isinstance(row, StateEventRecord) for row in self.rows):
            raise SchemaError("rows must contain state records")
        if self.last_event != (self.rows[-1] if self.rows else None):
            raise SchemaError("last_event must match rows")
