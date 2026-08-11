"""Deep orchestration behavior, including interruption-safe resume."""
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import pytest
from research_orchestration.artifacts import ArtifactStore
from research_orchestration.budgets import BudgetLimits
from research_orchestration.contracts import BudgetCharge, CheckerResult, EvaluationResult, Evidence, Experiment, Role, RunRequest
from research_orchestration.orchestrator import AutoresearchOrchestrator, RoleRunners


def request(run_id="run"):
    return RunRequest("1.0", run_id, "Improve read-only research.", ("read_only", "no_network"),
                      {"accuracy": .88}, BudgetLimits(max_stages=5), .90)


def standard_charges():
    return {role: BudgetCharge(stages=1) for role in Role}

def results():
    exp = Experiment("1.0", "search-flow", "Narrow improves.", "Add domain term.")
    return [exp, CheckerResult("1.0", "in_bounds", True, "accepted"),
            CheckerResult("1.0", "novelty", True, "novel"),
            (Evidence("1.0", "https://example.test/a", "fact", "2026-08-10T00:00:00Z"),),
            EvaluationResult("1.0", True, .90, "validated")]


class RecordingRoles:
    def __init__(self, fail_after=None):
        self.values = results()
        self.calls = []
        self.fail_after = fail_after
    def runner(self, index):
        def run(envelope):
            self.calls.append(envelope)
            if self.fail_after == index:
                self.fail_after = None
                raise RuntimeError("interrupted")
            return self.values[index]
        return run
    def bundle(self, charges=None):
        return RoleRunners(*(self.runner(i) for i in range(5)), charges=charges or standard_charges())


def test_roles_receive_fresh_exact_context_and_threshold_halts_for_review(tmp_path):
    recording = RecordingRoles()
    summary = AutoresearchOrchestrator(ArtifactStore(tmp_path), recording.bundle()).run(request())
    assert [item.role for item in recording.calls] == list(Role)
    assert len({item.invocation_id for item in recording.calls}) == 5
    assert [set(item.payload) for item in recording.calls] == [
        {"run_brief", "baseline", "prior_decisions", "budget_remaining"},
        {"constraints", "experiment"}, {"experiment", "prior_fingerprints"},
        {"experiment", "execution_inputs"}, {"rubric", "experiment", "evidence"}]
    assert (summary.final_action, summary.reason_code) == ("halt_for_review", "human_review_required")



def test_rejected_checker_stops_before_executor(tmp_path):
    recording = RecordingRoles()
    recording.values[1] = CheckerResult("1.0", "in_bounds", False, "out_of_bounds")
    summary = AutoresearchOrchestrator(ArtifactStore(tmp_path), recording.bundle()).run(request())
    assert [item.role for item in recording.calls] == [Role.INVENTOR, Role.IN_BOUNDS_CHECKER]
    assert (summary.final_action, summary.reason_code) == ("rollback", "retry_exhausted_with_rollback")

def test_stage_budget_is_reserved_before_invoking_a_role(tmp_path):
    recording = RecordingRoles()
    zero_budget = RunRequest(
        "1.0",
        "run",
        "Improve read-only research.",
        ("read_only", "no_network"),
        {"accuracy": 0.88},
        BudgetLimits(max_stages=0),
        0.90,
    )
    summary = AutoresearchOrchestrator(ArtifactStore(tmp_path), recording.bundle()).run(zero_budget)
    assert recording.calls == []
    assert (summary.final_action, summary.reason_code) == ("halt_for_review", "budget_exhausted")


def test_duplicate_candidate_stops_before_executor(tmp_path):
    recording = RecordingRoles()
    recording.values[2] = CheckerResult("1.0", "novelty", False, "duplicate")
    summary = AutoresearchOrchestrator(ArtifactStore(tmp_path), recording.bundle()).run(request())
    assert [item.role for item in recording.calls] == [Role.INVENTOR, Role.IN_BOUNDS_CHECKER, Role.NOVELTY_CHECKER]
    assert summary.final_action == "rollback"


@pytest.mark.parametrize("index,bad", [(0, "not experiment"), (1, "not checker"), (2, None), (3, ()), (4, "not evaluation")])
def test_invalid_role_results_halt_typed_before_persistence(tmp_path, index, bad):
    recording = RecordingRoles()
    recording.values[index] = bad
    with pytest.raises(Exception, match="invalid_role_result"):
        AutoresearchOrchestrator(ArtifactStore(tmp_path), recording.bundle()).run(request())
    assert len(list((tmp_path / "cycles" / "0").glob("*.json"))) == index

def test_state_replays_budget_and_completed_resume_is_idempotent(tmp_path):
    recording = RecordingRoles()
    summary = AutoresearchOrchestrator(ArtifactStore(tmp_path), recording.bundle()).run(request())
    rows = ArtifactStore(tmp_path).load_state().rows
    assert rows[0].event == "role_reserved"
    assert rows[0].budget_usage.stages == 1
    assert rows[-2].event == "gate_decided"
    assert rows[-1].event == "run_completed"
    assert json.loads((tmp_path / "summary.json").read_text()) == summary.to_canonical_dict()
    resumed = RecordingRoles()
    assert AutoresearchOrchestrator(ArtifactStore(tmp_path), resumed.bundle()).run(request()) == summary
    assert resumed.calls == []
    assert ArtifactStore(tmp_path).load_state().budget_usage.stages == 5


def test_exception_is_redacted_and_retry_starts_fresh_cycle(tmp_path):
    recording = RecordingRoles(fail_after=3)
    req = RunRequest("1.0", "run", "Improve read-only research.", ("read_only",), {"accuracy": .88},
                     BudgetLimits(max_stages=10, max_retries=1), .90)
    summary = AutoresearchOrchestrator(ArtifactStore(tmp_path), recording.bundle()).run(req)
    assert [call.role for call in recording.calls] == [
        Role.INVENTOR, Role.IN_BOUNDS_CHECKER, Role.NOVELTY_CHECKER, Role.EXECUTOR,
        Role.INVENTOR, Role.IN_BOUNDS_CHECKER, Role.NOVELTY_CHECKER, Role.EXECUTOR, Role.EVALUATOR,
    ]
    projection = ArtifactStore(tmp_path).load_state()
    failed = [row for row in projection.rows if row.event == "role_failed"]
    assert len(failed) == 1 and failed[0].reason_code == "runner_error"
    assert projection.retry_count == projection.budget_usage.retries == 1
    assert any(row.event == "gate_decided" and row.reason_code == "retryable_failure" for row in projection.rows)
    assert summary.reason_code == "human_review_required"


@pytest.mark.parametrize("baseline,action,reason", [
    (True, "rollback", "retry_exhausted_with_rollback"),
    (False, "halt_for_review", "retry_exhausted_without_rollback"),
])
def test_exhausted_runner_failure_uses_gate_and_finishes(tmp_path, baseline, action, reason):
    recording = RecordingRoles(fail_after=0)
    req = RunRequest("1.0", "run", "Improve read-only research.", ("read_only",),
                     {"accuracy": .88} if baseline else {}, BudgetLimits(max_stages=5), .90)
    summary = AutoresearchOrchestrator(ArtifactStore(tmp_path), recording.bundle()).run(req)
    assert (summary.final_action, summary.reason_code) == (action, reason)
    assert ArtifactStore(tmp_path).load_state().last_event.event == "run_completed"


def test_evaluator_receives_the_exact_executor_evidence(tmp_path):
    recording = RecordingRoles()
    AutoresearchOrchestrator(ArtifactStore(tmp_path), recording.bundle()).run(request())
    assert recording.calls[-1].payload["evidence"] == recording.values[3]

@pytest.mark.parametrize("score,expected_action,reason", [
    (.89, "advance", "accepted_improvement"),
    (.80, "rollback", "regression_with_baseline"),
])
def test_evaluation_routes_gate_actions_through_orchestrator(tmp_path, score, expected_action, reason):
    recording = RecordingRoles()
    recording.values[4] = EvaluationResult("1.0", True, score, "validated")
    summary = AutoresearchOrchestrator(ArtifactStore(tmp_path), recording.bundle()).run(request())
    assert (summary.final_action, summary.reason_code) == (expected_action, reason)
    gate = [row for row in ArtifactStore(tmp_path).load_state().rows if row.event == "gate_decided"][-1]
    assert (gate.action, gate.reason_code) == (expected_action, reason)


def test_reservation_identity_matches_exact_runner_envelope(tmp_path):
    recording = RecordingRoles()
    AutoresearchOrchestrator(ArtifactStore(tmp_path), recording.bundle()).run(request())
    reserved = [row for row in ArtifactStore(tmp_path).load_state().rows if row.event == "role_reserved"]
    assert [row.invocation_id for row in reserved] == [call.invocation_id for call in recording.calls]
    assert [row.idempotency_key for row in reserved] == [f"run:0:{role.value}" for role in Role]

def test_retry_count_and_fresh_cycle_survive_new_orchestrator_process(tmp_path):
    class StopAfterRetryGate(AutoresearchOrchestrator):
        def _record_gate(self, cycle, usage, retry_count, gate_input):
            decision = super()._record_gate(cycle, usage, retry_count, gate_input)
            if decision.action.value == "retry":
                raise RuntimeError("process_stopped_after_gate")
            return decision

    first = RecordingRoles(fail_after=3)
    req = RunRequest("1.0", "run", "Improve read-only research.", ("read_only",), {"accuracy": .88},
                     BudgetLimits(max_stages=10, max_retries=1), .90)
    with pytest.raises(RuntimeError, match="process_stopped_after_gate"):
        StopAfterRetryGate(ArtifactStore(tmp_path), first.bundle()).run(req)

    second = RecordingRoles(fail_after=0)
    summary = AutoresearchOrchestrator(ArtifactStore(tmp_path), second.bundle()).run(req)
    assert [call.role for call in second.calls] == [Role.INVENTOR]
    assert (summary.final_action, summary.reason_code) == ("rollback", "retry_exhausted_with_rollback")
    projection = ArtifactStore(tmp_path).load_state()
    assert projection.retry_count == projection.budget_usage.retries == 1
    assert [row.cycle for row in projection.rows if row.event == "gate_decided"] == [0, 1]

@pytest.mark.parametrize("field,value", [
    ("calls", 1), ("queries", 1), ("scrapes", 1), ("llm_calls", 1), ("cost", .01),
])
def test_declared_role_charge_exhaustion_blocks_before_runner(tmp_path, field, value):
    recording = RecordingRoles()
    charges = standard_charges()
    charges[Role.INVENTOR] = BudgetCharge(**{field: value})
    limits = dict(max_stages=5, max_calls=1, max_queries=1, max_scrapes=1, max_llm_calls=1, max_cost=1.0)
    limits[f"max_{field}"] = 0
    req = RunRequest("1.0", "run", "Improve.", ("read_only",), {"accuracy": .88},
                     BudgetLimits(**limits), .90)
    summary = AutoresearchOrchestrator(ArtifactStore(tmp_path), recording.bundle(charges)).run(req)
    assert recording.calls == []
    assert summary.reason_code == "budget_exhausted"


def test_role_runners_require_complete_validated_charge_declarations():
    recording = RecordingRoles()
    with pytest.raises(Exception, match="charges"):
        RoleRunners(*(recording.runner(i) for i in range(5)), charges={})
    with pytest.raises(Exception):
        BudgetCharge(cost=-1)


def test_checker_rejection_retries_with_persisted_prior_context(tmp_path):
    recording = RecordingRoles()
    recording.values[1] = CheckerResult("1.0", "in_bounds", False, "out_of_bounds")
    req = RunRequest("1.0", "run", "Improve.", ("read_only",), {"accuracy": .88},
                     BudgetLimits(max_stages=7, max_retries=1), .90)
    summary = AutoresearchOrchestrator(ArtifactStore(tmp_path), recording.bundle()).run(req)
    assert [call.role for call in recording.calls] == [
        Role.INVENTOR, Role.IN_BOUNDS_CHECKER, Role.INVENTOR, Role.IN_BOUNDS_CHECKER,
    ]
    assert recording.calls[2].payload["prior_decisions"] == ("retryable_failure",)
    assert recording.calls[2].payload["baseline"] == req.baseline
    assert summary.reason_code == "retry_exhausted_with_rollback"


def test_retry_context_rehydrates_prior_experiment_fingerprints_after_process_restart(tmp_path):
    class StopAfterRetryGate(AutoresearchOrchestrator):
        def _record_gate(self, cycle, usage, retry_count, gate_input):
            decision = super()._record_gate(cycle, usage, retry_count, gate_input)
            if decision.action.value == "retry":
                raise RuntimeError("restart")
            return decision

    first = RecordingRoles()
    first.values[2] = CheckerResult("1.0", "novelty", False, "duplicate")
    req = RunRequest("1.0", "run", "Improve.", ("read_only",), {"accuracy": .88},
                     BudgetLimits(max_stages=8, max_retries=1), .90)
    with pytest.raises(RuntimeError, match="restart"):
        StopAfterRetryGate(ArtifactStore(tmp_path), first.bundle()).run(req)
    prior_key = first.values[0].key

    second = RecordingRoles()
    second.values[2] = CheckerResult("1.0", "novelty", False, "duplicate")
    summary = AutoresearchOrchestrator(ArtifactStore(tmp_path), second.bundle()).run(req)
    inventor, in_bounds, novelty = second.calls
    assert inventor.payload["prior_decisions"] == ("retryable_failure",)
    assert novelty.payload["prior_fingerprints"] == (prior_key,)
    assert summary.reason_code == "retry_exhausted_with_rollback"
