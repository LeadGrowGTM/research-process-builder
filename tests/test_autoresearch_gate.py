"""Behavior contracts for deterministic autoresearch transitions and budgets."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))

import pytest

from research_orchestration.budgets import BudgetExceeded, BudgetLedger, BudgetLimits
from research_orchestration.gate import GateAction, GateInput, decide_gate


@pytest.mark.parametrize(
    ("case", "gate_input", "action", "reason_code"),
    [
        (
            "accepted improvement",
            GateInput(checks_accepted=True, evaluation_passed=True, candidate_score=0.86, baseline_score=0.8),
            GateAction.ADVANCE,
            "accepted_improvement",
        ),
        (
            "bounded retryable failure",
            GateInput(retryable_failure=True, retries_remaining=1),
            GateAction.RETRY,
            "retryable_failure",
        ),
        (
            "regression with baseline",
            GateInput(
                checks_accepted=True,
                evaluation_passed=True,
                candidate_score=0.79,
                baseline_score=0.8,
                rollback_available=True,
            ),
            GateAction.ROLLBACK,
            "regression_with_baseline",
        ),
        (
            "threshold requires human review",
            GateInput(checks_accepted=True, evaluation_passed=True, candidate_score=0.9, baseline_score=0.8),
            GateAction.HALT_FOR_REVIEW,
            "threshold_requires_human_review",
        ),
        (
            "budget exhausted",
            GateInput(budget_exhausted=True),
            GateAction.HALT_FOR_REVIEW,
            "budget_exhausted",
        ),
        (
            "retry exhausted without rollback",
            GateInput(retryable_failure=True, retries_remaining=0, rollback_available=False),
            GateAction.HALT_FOR_REVIEW,
            "retry_exhausted_without_rollback",
        ),
        (
            "corrupt or version-invalid artifact",
            GateInput(artifacts_valid=False),
            GateAction.HALT_FOR_REVIEW,
            "invalid_artifact",
        ),
        (
            "unsafe ambiguity",
            GateInput(safe_to_proceed=False),
            GateAction.HALT_FOR_REVIEW,
            "unsafe_ambiguity",
        ),
    ],
)
def test_gate_emits_each_safe_deterministic_action(case, gate_input, action, reason_code):
    """Would fail if any outcome selected the wrong transition for its validated state."""
    decision = decide_gate(gate_input)

    assert decision.action is action
    assert decision.reason_code == reason_code
    assert decision.to_canonical_dict() == {
        "action": action.value,
        "reason_code": reason_code,
        "schema_version": "1.0",
    }


def test_gate_has_exactly_the_four_non_approval_actions():
    """Would fail if code introduced an automatic approval transition."""
    assert {action.value for action in GateAction} == {"advance", "retry", "rollback", "halt_for_review"}


def test_ledger_reserves_every_external_work_counter_before_a_call():
    """Would fail if a paid or external operation could proceed without a complete reservation."""
    ledger = BudgetLedger(
        BudgetLimits(
            max_calls=5,
            max_queries=1,
            max_scrapes=1,
            max_llm_calls=1,
            max_retries=1,
            max_cost=0.5,
            max_stages=2,
        )
    )

    usage = ledger.reserve(calls=1, queries=1, scrapes=1, llm_calls=1, retries=1, cost=0.5, stages=1)

    assert usage.to_canonical_dict() == {
        "calls": 1,
        "cost": 0.5,
        "llm_calls": 1,
        "queries": 1,
        "retries": 1,
        "scrapes": 1,
        "stages": 1,
    }


def test_ledger_fails_closed_without_partially_reserving_any_counter():
    """Would fail if a rejected reservation consumed an earlier counter in the same charge."""
    ledger = BudgetLedger(BudgetLimits(max_calls=1, max_queries=1, max_cost=0.25, max_stages=1))
    ledger.reserve(calls=1, stages=1)

    with pytest.raises(BudgetExceeded, match="queries"):
        ledger.reserve(queries=2, cost=0.5)

    assert ledger.usage.to_canonical_dict() == {
        "calls": 1,
        "cost": 0.0,
        "llm_calls": 0,
        "queries": 0,
        "retries": 0,
        "scrapes": 0,
        "stages": 1,
    }


@pytest.mark.parametrize("field", ["calls", "queries", "scrapes", "llm_calls", "retries", "cost", "stages"])
def test_ledger_rejects_each_counter_when_its_limit_is_exceeded(field):
    """Would fail if any named budget counter could be charged after its limit."""
    limits = BudgetLimits(
        max_calls=1,
        max_queries=1,
        max_scrapes=1,
        max_llm_calls=1,
        max_retries=1,
        max_cost=1.0,
        max_stages=1,
    )
    ledger = BudgetLedger(limits)

    with pytest.raises(BudgetExceeded, match=field):
        ledger.reserve(**{field: 2.0 if field == "cost" else 2})

    assert ledger.usage.to_canonical_dict()[field] == 0
