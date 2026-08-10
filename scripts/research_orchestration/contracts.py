"""Immutable, bounded, provider-neutral values at the orchestration seam."""

from __future__ import annotations

from dataclasses import dataclass, fields
from enum import StrEnum
from hashlib import sha256
import json
import math
from types import MappingProxyType
from typing import Any, ClassVar, Mapping
from uuid import uuid4


SCHEMA_VERSION = "1.0"
MAX_TEXT_CHARS = 4_000
MAX_LIST_ITEMS = 100
_FORBIDDEN_FIELD_PARTS = ("secret", "token", "password", "authorization", "transcript")


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
class RunRequest(CanonicalContract):
    schema_version: str
    run_id: str
    brief: str
    constraints: tuple[str, ...]
    baseline: Mapping[str, float]
    budget_limits: Any
    approval_threshold: float

    def __post_init__(self) -> None:
        _require_schema_version(self.schema_version)
        _require_text(self.run_id, "run_id")
        _require_text(self.brief, "brief")
        frozen_constraints = _freeze(self.constraints, field_name="constraints")
        if not all(isinstance(item, str) for item in frozen_constraints):
            raise SchemaError("constraints must contain text")
        frozen_baseline = _freeze(self.baseline, field_name="baseline")
        if not all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in frozen_baseline.values()):
            raise SchemaError("baseline values must be finite numbers")
        if not hasattr(self.budget_limits, "to_canonical_dict"):
            raise SchemaError("budget_limits must be a canonical contract")
        _require_finite(self.approval_threshold, "approval_threshold")
        if not 0 < self.approval_threshold <= 1:
            raise SchemaError("approval_threshold must be between zero and one")
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
        _require_text(self.final_action, "final_action")
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


@dataclass(frozen=True, slots=True)
class RoleEnvelope(CanonicalContract):
    schema_version: str
    role: Role
    invocation_id: str
    payload: Mapping[str, Any]

    @classmethod
    def create(cls, role: Role, payload: Mapping[str, Any]) -> "RoleEnvelope":
        return cls(
            schema_version=SCHEMA_VERSION,
            role=role,
            invocation_id=str(uuid4()),
            payload=payload,
        )

    def __post_init__(self) -> None:
        _require_schema_version(self.schema_version)
        if not isinstance(self.role, Role):
            raise SchemaError("role must be a declared role")
        _require_text(self.invocation_id, "invocation_id")
        if not isinstance(self.payload, Mapping):
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
