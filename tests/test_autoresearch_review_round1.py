"""Focused regressions from review round 1 for autoresearch contracts."""

from pathlib import Path
import sys
from threading import Barrier, Lock, Thread

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import pytest

from research_orchestration.budgets import BudgetExceeded, BudgetLedger, BudgetLimits, BudgetUsage
from research_orchestration.contracts import Role, RoleEnvelope, RunRequest, SchemaError
from research_orchestration.gate import GateAction, GateInput, decide_gate


def _request_fields() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "run_id": "run-001",
        "brief": "brief",
        "constraints": ("read_only",),
        "baseline": {"score": 0.8},
        "budget_limits": BudgetLimits(),
        "approval_threshold": 0.9,
    }


def test_gate_locks_approval_threshold_to_ninety_percent_and_requires_review():
    """Would fail if a caller could change the approval invariant or auto-advance at 90%."""
    with pytest.raises(SchemaError, match="fixed"):
        GateInput(approval_threshold=0.95)

    decision = decide_gate(GateInput(checks_accepted=True, evaluation_passed=True, candidate_score=0.90))

    assert decision.action is GateAction.HALT_FOR_REVIEW
    assert "human_review" in decision.reason_code


def test_budget_artifacts_have_canonical_schema_versions_and_reject_schema_drift():
    """Would fail if persisted budget state could not identify or reject its schema version."""
    limits = BudgetLimits(schema_version="1.0", max_calls=1)
    usage = BudgetUsage(schema_version="1.0", calls=1)

    assert limits.to_canonical_dict()["schema_version"] == "1.0"
    assert usage.to_canonical_dict()["schema_version"] == "1.0"
    with pytest.raises(SchemaError, match="unsupported schema version"):
        BudgetLimits(schema_version="2.0")
    with pytest.raises(TypeError):
        BudgetUsage(schema_version="1.0", unexpected=1)


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("constraints", "read_only", "constraints"),
        ("constraints", ["x" * 4001], "text exceeds"),
        ("baseline", [], "baseline"),
        ("baseline", {"api_key": 0.8}, "forbidden"),
        ("baseline", {"score": float("nan")}, "finite"),
        ("budget_limits", object(), "BudgetLimits"),
    ],
)
def test_run_request_rejects_malformed_boundary_values(field, value, expected):
    """Would fail if malformed run inputs escaped as AttributeError or crossed the seam."""
    fields = _request_fields()
    fields[field] = value

    with pytest.raises(SchemaError, match=expected):
        RunRequest(**fields)


def test_run_request_freezes_a_non_string_constraint_sequence():
    """Would fail if a caller could mutate validated constraints after construction."""
    constraints = ["read_only"]
    fields = _request_fields()
    fields["constraints"] = constraints
    request = RunRequest(**fields)
    constraints.append("later")

    assert request.constraints == ("read_only",)


def test_envelope_creation_is_fresh_while_rehydrate_validates_persisted_identity():
    """Would fail if callers could forge new IDs or valid persisted IDs could not resume."""
    payload = {
        "run_brief": "brief",
        "baseline": {"score": 0.8},
        "prior_decisions": (),
        "budget_remaining": {"calls": 1},
    }
    envelope = RoleEnvelope.create(Role.INVENTOR, payload)

    with pytest.raises(TypeError):
        RoleEnvelope(schema_version="1.0", role=Role.INVENTOR, invocation_id=envelope.invocation_id, payload=payload)
    restored = RoleEnvelope.rehydrate(
        schema_version="1.0",
        role=Role.INVENTOR,
        invocation_id=envelope.invocation_id,
        payload=payload,
    )

    assert restored.invocation_id == envelope.invocation_id
    with pytest.raises(SchemaError, match="UUID"):
        RoleEnvelope.rehydrate(
            schema_version="1.0",
            role=Role.INVENTOR,
            invocation_id="duplicate-not-a-uuid",
            payload=payload,
        )


def test_ledger_concurrent_reservations_admit_only_one_charge_with_one_slot():
    """Would fail if overlapping reservations both consumed the same final call slot."""
    ledger = BudgetLedger(BudgetLimits(max_calls=1))
    ready = Barrier(3)
    outcomes: list[object] = []
    outcomes_lock = Lock()

    def reserve_one() -> None:
        ready.wait()
        try:
            result: object = ledger.reserve(calls=1)
        except BudgetExceeded:
            result = "exhausted"
        with outcomes_lock:
            outcomes.append(result)

    workers = [Thread(target=reserve_one), Thread(target=reserve_one)]
    for worker in workers:
        worker.start()
    ready.wait()
    for worker in workers:
        worker.join()

    assert sum(result == "exhausted" for result in outcomes) == 1
    assert sum(getattr(result, "calls", 0) == 1 for result in outcomes) == 1
    assert ledger.usage.calls == 1
