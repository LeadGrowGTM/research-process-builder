"""Behavior contracts for secret-free autoresearch orchestration values."""

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))

import pytest

from research_orchestration.contracts import (
    BudgetCharge,
    CheckerResult,
    EvaluationResult,
    Evidence,
    Experiment,
    Role,
    RoleEnvelope,
    RunRequest,
    RunSummary,
    SchemaError,
)
from research_orchestration.budgets import BudgetLimits


def test_budget_charge_is_frozen_schema_versioned_nonnegative_and_bounded():
    charge = BudgetCharge(calls=1, queries=2, cost=0.25, stages=1)
    assert charge.to_canonical_dict() == {
        "calls": 1, "cost": 0.25, "llm_calls": 0, "queries": 2,
        "schema_version": "1.0", "scrapes": 0, "stages": 1,
    }
    with pytest.raises((AttributeError, TypeError)):
        charge.calls = 2
    with pytest.raises(TypeError):
        BudgetCharge(retries=1)
    with pytest.raises(SchemaError, match="non-negative"):
        BudgetCharge(calls=-1)
    with pytest.raises(SchemaError, match="bounded"):
        BudgetCharge(queries=1_000_001)
    with pytest.raises(SchemaError, match="unsupported schema"):
        BudgetCharge(schema_version="2.0")


@pytest.mark.parametrize("action", ("accept", "stop", "", "ADVANCE"))
def test_run_summary_rejects_actions_outside_gate_vocabulary(action):
    with pytest.raises(SchemaError, match="invalid final_action"):
        RunSummary("1.0", "run-001", action, "reason", 0)

def test_experiment_key_is_stable_for_equivalent_content():
    """Would fail if a key incorporated invocation order or random state."""
    first = Experiment(
        schema_version="1.0",
        flow_id="search-flow",
        hypothesis="A narrower query improves relevance.",
        proposed_change="Add the industry term.",
    )
    second = Experiment(
        schema_version="1.0",
        flow_id="search-flow",
        hypothesis="A narrower query improves relevance.",
        proposed_change="Add the industry term.",
    )

    assert first.key == second.key
    assert len(first.key) == 64


def test_contracts_are_frozen_and_serialize_canonically():
    """Would fail if callers could alter persisted run inputs or key ordering changed."""
    request = RunRequest(
        schema_version="1.0",
        run_id="run-001",
        brief="Find accurate source candidates.",
        constraints=("read_only", "no_secrets"),
        baseline={"recall": 0.84, "precision": 0.88},
        budget_limits=BudgetLimits(max_calls=2, max_stages=3),
        approval_threshold=0.9,
    )

    with pytest.raises((AttributeError, TypeError)):
        request.brief = "altered"  # type: ignore[misc]

    assert request.to_canonical_json() == (
        '{"approval_threshold":0.9,"baseline":{"precision":0.88,"recall":0.84},'
        '"brief":"Find accurate source candidates.","budget_limits":{"max_calls":2,'
        '"max_cost":0.0,"max_llm_calls":0,"max_queries":0,"max_retries":0,'
        '"max_scrapes":0,"max_stages":3,"schema_version":"1.0"},"constraints":["read_only","no_secrets"],'
        '"run_id":"run-001","schema_version":"1.0"}'
    )


@pytest.mark.parametrize("threshold", (0.89, 0.9000001, 1.0))
def test_run_request_requires_the_gate_threshold_exactly(threshold):
    with pytest.raises(SchemaError, match="approval_threshold must be exactly 0.90"):
        RunRequest(
            schema_version="1.0", run_id="run-001", brief="brief", constraints=(),
            baseline={}, budget_limits=BudgetLimits(), approval_threshold=threshold,
        )

@pytest.mark.parametrize(
    ("factory", "expected"),
    [
        (
            lambda: RunRequest(
                schema_version="2.0",
                run_id="run-001",
                brief="brief",
                constraints=(),
                baseline={},
                budget_limits=BudgetLimits(),
                approval_threshold=0.9,
            ),
            "unsupported schema version",
        ),
        (
            lambda: Evidence(
                schema_version="1.0",
                source_url="https://example.test/a",
                excerpt="x" * 4001,
                observed_at="2026-08-10T00:00:00Z",
            ),
            "text exceeds",
        ),
        (
            lambda: RunRequest(
                schema_version="1.0",
                run_id="run-001",
                brief="brief",
                constraints=tuple(str(index) for index in range(101)),
                baseline={},
                budget_limits=BudgetLimits(),
                approval_threshold=0.9,
            ),
            "list exceeds",
        ),
    ],
)
def test_contracts_reject_wrong_version_and_unbounded_values(factory, expected):
    """Would fail if incompatible or unbounded data could become an artifact."""
    with pytest.raises(SchemaError, match=expected):
        factory()


def test_dataclass_constructor_rejects_missing_and_extra_fields():
    """Would fail if strict artifact constructors silently accepted shape drift."""
    with pytest.raises(TypeError):
        RunSummary(schema_version="1.0", run_id="run-001", final_action="advance")
    with pytest.raises(TypeError):
        Evidence(
            schema_version="1.0",
            source_url="https://example.test/a",
            excerpt="observed fact",
            observed_at="2026-08-10T00:00:00Z",
            provider="leak",
        )


def test_envelopes_are_unique_and_expose_only_the_role_allowlist():
    """Would fail if role context leaked or two invocations shared an identity."""
    payload = {
        "run_brief": "brief",
        "baseline": {"accuracy": 0.8},
        "prior_decisions": ("retry",),
        "budget_remaining": {"max_calls": 1},
    }
    first = RoleEnvelope.create(Role.INVENTOR, payload)
    second = RoleEnvelope.create(Role.INVENTOR, payload)

    assert first.invocation_id != second.invocation_id
    assert set(first.payload) == {
        "run_brief",
        "baseline",
        "prior_decisions",
        "budget_remaining",
    }
    assert first.to_canonical_dict()["payload"] == {
        "baseline": {"accuracy": 0.8},
        "budget_remaining": {"max_calls": 1},
        "prior_decisions": ["retry"],
        "run_brief": "brief",
    }


@pytest.mark.parametrize(
    ("role", "payload"),
    [
        (Role.IN_BOUNDS_CHECKER, {"constraints": (), "experiment": "key"}),
        (Role.NOVELTY_CHECKER, {"experiment": "key", "prior_fingerprints": ()}),
        (Role.EXECUTOR, {"experiment": "key", "execution_inputs": {}}),
        (Role.EVALUATOR, {"rubric": "accuracy", "experiment": "key", "evidence": ()}),
    ],
)
def test_each_role_accepts_its_exact_declared_fields(role, payload):
    """Would fail if a role could receive another role's context."""
    envelope = RoleEnvelope.create(role, payload)

    assert set(envelope.payload) == set(payload)
    with pytest.raises(SchemaError, match="not allowed"):
        RoleEnvelope.create(role, {**payload, "raw_transcript": "must not cross"})


def test_canonical_serialization_rejects_secrets_and_raw_transcripts():
    """Would fail if sensitive prompt history were written into a portable artifact."""
    with pytest.raises(SchemaError, match="forbidden"):
        RoleEnvelope.create(
            Role.EXECUTOR,
            {"experiment": "key", "execution_inputs": {"api_token": "secret"}},
        )


def test_checker_and_evaluator_results_are_provider_neutral_and_bounded():
    """Would fail if decision artifacts depended on a provider or accepted non-finite scores."""
    checker = CheckerResult(schema_version="1.0", checker="novelty", accepted=True, reason_code="novel")
    evaluation = EvaluationResult(
        schema_version="1.0",
        passed=True,
        score=0.91,
        reason_code="improved",
    )

    assert json.loads(checker.to_canonical_json())["checker"] == "novelty"
    assert json.loads(evaluation.to_canonical_json())["score"] == 0.91
    with pytest.raises(SchemaError, match="finite"):
        EvaluationResult(schema_version="1.0", passed=True, score=float("nan"), reason_code="bad")
