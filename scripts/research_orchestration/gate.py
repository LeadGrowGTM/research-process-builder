"""Pure, deterministic transition selection for the autoresearch state machine."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import math

from .contracts import CanonicalContract, SCHEMA_VERSION, SchemaError


class GateAction(StrEnum):
    ADVANCE = "advance"
    RETRY = "retry"
    ROLLBACK = "rollback"
    HALT_FOR_REVIEW = "halt_for_review"


@dataclass(frozen=True, slots=True)
class GateInput(CanonicalContract):
    """Validated state needed to choose one safe next transition."""

    checks_accepted: bool = False
    evaluation_passed: bool = False
    candidate_score: float | None = None
    baseline_score: float | None = None
    approval_threshold: float = 0.9
    retryable_failure: bool = False
    retries_remaining: int = 0
    rollback_available: bool = False
    budget_exhausted: bool = False
    artifacts_valid: bool = True
    safe_to_proceed: bool = True
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise SchemaError(f"unsupported schema version: {self.schema_version!r}")
        for field_name in (
            "checks_accepted",
            "evaluation_passed",
            "retryable_failure",
            "rollback_available",
            "budget_exhausted",
            "artifacts_valid",
            "safe_to_proceed",
        ):
            if not isinstance(getattr(self, field_name), bool):
                raise SchemaError(f"{field_name} must be a boolean")
        if isinstance(self.retries_remaining, bool) or not isinstance(self.retries_remaining, int) or self.retries_remaining < 0:
            raise SchemaError("retries_remaining must be a non-negative integer")
        for field_name in ("candidate_score", "baseline_score"):
            value = getattr(self, field_name)
            if value is not None:
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                    raise SchemaError(f"{field_name} must be finite when supplied")
                if not 0 <= value <= 1:
                    raise SchemaError(f"{field_name} must be between zero and one")
        if isinstance(self.approval_threshold, bool) or not isinstance(self.approval_threshold, (int, float)) or not math.isfinite(self.approval_threshold):
            raise SchemaError("approval_threshold must be finite")
        if not 0 < self.approval_threshold <= 1:
            raise SchemaError("approval_threshold must be between zero and one")


@dataclass(frozen=True, slots=True)
class GateDecision(CanonicalContract):
    action: GateAction
    reason_code: str
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise SchemaError(f"unsupported schema version: {self.schema_version!r}")
        if not isinstance(self.action, GateAction):
            raise SchemaError("action must be a GateAction")
        if not isinstance(self.reason_code, str) or not self.reason_code or len(self.reason_code) > 4_000:
            raise SchemaError("reason_code must be bounded non-empty text")

    def to_canonical_dict(self) -> dict[str, str]:
        return {"action": self.action.value, "reason_code": self.reason_code, "schema_version": self.schema_version}


def decide_gate(gate_input: GateInput) -> GateDecision:
    """Return the single safe transition for validated state, without side effects."""
    if not isinstance(gate_input, GateInput):
        raise SchemaError("gate_input must be GateInput")
    if not gate_input.artifacts_valid:
        return GateDecision(GateAction.HALT_FOR_REVIEW, "invalid_artifact")
    if gate_input.budget_exhausted:
        return GateDecision(GateAction.HALT_FOR_REVIEW, "budget_exhausted")
    if not gate_input.safe_to_proceed:
        return GateDecision(GateAction.HALT_FOR_REVIEW, "unsafe_ambiguity")
    if gate_input.retryable_failure:
        if gate_input.retries_remaining > 0:
            return GateDecision(GateAction.RETRY, "retryable_failure")
        if gate_input.rollback_available:
            return GateDecision(GateAction.ROLLBACK, "retry_exhausted_with_rollback")
        return GateDecision(GateAction.HALT_FOR_REVIEW, "retry_exhausted_without_rollback")
    if not gate_input.checks_accepted or not gate_input.evaluation_passed:
        return GateDecision(GateAction.HALT_FOR_REVIEW, "unsafe_ambiguity")
    if gate_input.candidate_score is None:
        return GateDecision(GateAction.HALT_FOR_REVIEW, "unsafe_ambiguity")
    if gate_input.candidate_score >= gate_input.approval_threshold:
        return GateDecision(GateAction.HALT_FOR_REVIEW, "threshold_requires_human_review")
    if gate_input.baseline_score is not None and gate_input.candidate_score < gate_input.baseline_score:
        if gate_input.rollback_available:
            return GateDecision(GateAction.ROLLBACK, "regression_with_baseline")
        return GateDecision(GateAction.HALT_FOR_REVIEW, "unsafe_ambiguity")
    return GateDecision(GateAction.ADVANCE, "accepted_improvement")
