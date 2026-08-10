"""Behavior contracts for local, tamper-evident autoresearch artifacts."""

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import sys
from threading import Barrier

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import pytest

from research_orchestration.budgets import BudgetLimits, BudgetUsage
from research_orchestration.contracts import (
    CheckerResult, CompletedRoleRecord, EvaluationResult, Evidence, Experiment,
    Role, RoleEnvelope, RunRequest, RunSummary, SchemaError,
)
from research_orchestration.artifacts import ArtifactHaltForReview, ArtifactStore


def _request() -> RunRequest:
    return RunRequest(
        schema_version="1.0",
        run_id="run-001",
        brief="Find accurate source candidates.",
        constraints=("read_only", "no_secrets"),
        baseline={"precision": 0.88},
        budget_limits=BudgetLimits(max_calls=2, max_stages=5),
        approval_threshold=0.9,
    )


def _inventor_envelope() -> RoleEnvelope:
    return RoleEnvelope.create(
        Role.INVENTOR,
        {
            "run_brief": "Find accurate source candidates.",
            "baseline": {"precision": 0.88},
            "prior_decisions": (),
            "budget_remaining": {"max_calls": 2},
        },
    )


def _bounds_envelope() -> RoleEnvelope:
    return RoleEnvelope.create(
        Role.IN_BOUNDS_CHECKER,
        {"constraints": ("read_only",), "experiment": "experiment-0"},
    )


def _experiment() -> Experiment:
    return Experiment("1.0", "search-flow", "Narrow improves.", "Add domain term.")


def _evidence() -> tuple[Evidence, ...]:
    return (Evidence("1.0", "https://example.test/a", "fact", "2026-08-10T00:00:00Z"),)


@pytest.mark.parametrize(
    ("role", "envelope", "result", "result_type"),
    (
        (Role.INVENTOR, _inventor_envelope, _experiment, "experiment"),
        (Role.IN_BOUNDS_CHECKER, _bounds_envelope, lambda: CheckerResult("1.0", "in_bounds", True, "accepted"), "checker_result"),
        (Role.NOVELTY_CHECKER, lambda: RoleEnvelope.create(Role.NOVELTY_CHECKER, {"experiment": "experiment-0", "prior_fingerprints": ()}), lambda: CheckerResult("1.0", "novelty", True, "novel"), "checker_result"),
        (Role.EXECUTOR, lambda: RoleEnvelope.create(Role.EXECUTOR, {"experiment": "experiment-0", "execution_inputs": {}}), _evidence, "evidence"),
        (Role.EVALUATOR, lambda: RoleEnvelope.create(Role.EVALUATOR, {"rubric": "accuracy", "experiment": "experiment-0", "evidence": ("fact",)}), lambda: EvaluationResult("1.0", True, 0.9, "validated"), "evaluation_result"),
    ),
)
def test_completed_role_artifact_round_trips_typed_result(tmp_path, role, envelope, result, result_type):
    store = ArtifactStore(tmp_path)
    store.create_run(_request())
    expected = result()
    artifact_hash = store.put_completed_role_artifact(0, role, envelope(), expected, f"{role.value}-0")
    assert store.load_role_result(0, role) == expected
    stored = json.loads((tmp_path / "objects" / f"{artifact_hash}.json").read_text(encoding="utf-8"))
    assert stored["result_type"] == result_type
    assert set(stored) == {"envelope", "result", "result_type", "schema_version"}


def test_completed_role_record_rejects_role_result_mismatch_and_empty_evidence():
    with pytest.raises(SchemaError, match="role result mismatch"):
        CompletedRoleRecord.create(_inventor_envelope(), EvaluationResult("1.0", True, 0.9, "validated"))
    executor = RoleEnvelope.create(Role.EXECUTOR, {"experiment": "experiment-0", "execution_inputs": {}})
    with pytest.raises(SchemaError, match="evidence must not be empty"):
        CompletedRoleRecord.create(executor, ())


def test_completed_role_record_rejects_checker_identity_and_schema_version_mismatch():
    with pytest.raises(SchemaError, match="checker must match role"):
        CompletedRoleRecord.create(_bounds_envelope(), CheckerResult("1.0", "novelty", True, "novel"))
    with pytest.raises(SchemaError, match="schema version"):
        CompletedRoleRecord.rehydrate(schema_version="2.0", envelope=_inventor_envelope().to_canonical_dict(), result_type="experiment", result=_experiment().to_canonical_dict())


def test_public_history_reads_prior_inventor_results_and_gate_decisions(tmp_path):
    store = ArtifactStore(tmp_path)
    store.create_run(_request())
    for cycle in (0, 1):
        envelope = _inventor_envelope()
        artifact_hash = store.put_completed_role_artifact(
            cycle, Role.INVENTOR, envelope, _experiment(), f"invent-{cycle}"
        )
        store.append_transition(cycle, "start", Role.INVENTOR.value, artifact_hash, f"invent-{cycle}")
        store.append_state_event(cycle, "gate", "gate_decided", f"decision_{cycle}", BudgetUsage(stages=cycle + 1), 0,
                                 action="retry" if cycle == 0 else "advance")

    assert store.load_prior_inventor_results(1) == ((0, _experiment()),)
    decisions = store.load_prior_gate_decisions(1)
    assert len(decisions) == 1
    assert (decisions[0].cycle, decisions[0].action, decisions[0].reason_code) == (0, "retry", "decision_0")


def test_public_history_reads_validate_cycle_and_tamper(tmp_path):
    store = ArtifactStore(tmp_path)
    store.create_run(_request())
    with pytest.raises(ArtifactHaltForReview, match="invalid_cycle"):
        store.load_prior_inventor_results(-1)
    store.append_state_event(0, "gate", "gate_decided", "accepted", BudgetUsage(), 0, action="advance")
    path = tmp_path / "state.jsonl"
    path.write_text(path.read_text(encoding="utf-8").replace("accepted", "tampered"), encoding="utf-8")
    with pytest.raises(ArtifactHaltForReview, match="state_hash_mismatch"):
        store.load_prior_gate_decisions(1)

def test_load_role_result_halts_for_tampered_completed_record(tmp_path):
    store = ArtifactStore(tmp_path)
    store.create_run(_request())
    artifact_hash = store.put_completed_role_artifact(0, Role.INVENTOR, _inventor_envelope(), _experiment(), "invent-0")
    object_path = tmp_path / "objects" / f"{artifact_hash}.json"
    value = json.loads(object_path.read_text(encoding="utf-8"))
    value["result"]["hypothesis"] = "tampered"
    object_path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    with pytest.raises(ArtifactHaltForReview, match="artifact_hash_mismatch"):
        store.load_role_result(0, Role.INVENTOR)


def test_load_role_result_reports_typed_missing_result_for_legacy_artifact(tmp_path):
    store = ArtifactStore(tmp_path)
    store.create_run(_request())
    store.put_role_artifact(0, Role.INVENTOR, _inventor_envelope(), "invent-0")
    with pytest.raises(ArtifactHaltForReview, match="missing_role_result") as error:
        store.load_role_result(0, Role.INVENTOR)
    assert error.value.reason_code == "missing_role_result"


def test_completed_role_idempotency_rejects_different_result_for_existing_key(tmp_path):
    store = ArtifactStore(tmp_path)
    store.create_run(_request())
    envelope = _inventor_envelope()
    store.put_completed_role_artifact(0, Role.INVENTOR, envelope, _experiment(), "invent-0")
    changed = Experiment("1.0", "search-flow", "Different hypothesis.", "Add domain term.")

    with pytest.raises(ArtifactHaltForReview, match="idempotency_collision"):
        store.put_completed_role_artifact(0, Role.INVENTOR, envelope, changed, "invent-0")


def test_state_events_form_hash_chain_and_load_immutable_projection(tmp_path):
    store = ArtifactStore(tmp_path)
    store.create_run(_request())
    first_usage = BudgetUsage(calls=1, stages=1)
    final_usage = BudgetUsage(calls=2, retries=1, stages=2)

    envelope = _inventor_envelope()
    first = store.reserve_role_attempt(0, Role.INVENTOR, envelope, "invent-0", first_usage, 0)
    second = store.append_state_event(
        0, Role.INVENTOR.value, "role_failed", "provider_error", final_usage, 1,
        invocation_id=envelope.invocation_id, idempotency_key="invent-0",
    )
    projection = store.load_state()

    assert (first.sequence, second.sequence) == (1, 2)
    assert second.previous_hash == first.row_hash
    assert projection.budget_usage == final_usage
    assert projection.retry_count == 1
    assert projection.last_event == second
    assert projection.rows == (first, second)
    with pytest.raises(AttributeError):
        projection.rows = ()


@pytest.mark.parametrize("event", ("role_failed", "gate_decided", "run_completed"))
def test_terminal_and_failure_state_events_append_idempotently_once(tmp_path, event):
    store = ArtifactStore(tmp_path)
    store.create_run(_request())
    stage = Role.EXECUTOR.value if event == "role_failed" else "gate" if event == "gate_decided" else "run"
    usage = BudgetUsage(calls=1, stages=1)

    identity = {}
    expected_rows = 1
    if event == "role_failed":
        envelope = RoleEnvelope.create(Role.EXECUTOR, {"experiment": "experiment-0", "execution_inputs": {}})
        store.reserve_role_attempt(0, Role.EXECUTOR, envelope, "execute-0", usage, 0)
        identity = {"invocation_id": envelope.invocation_id, "idempotency_key": "execute-0"}
        expected_rows = 2
    first = store.append_state_event(0, stage, event, "halt_for_review", usage, 0, **identity)
    repeated = store.append_state_event(0, stage, event, "halt_for_review", usage, 0, **identity)

    assert repeated == first
    assert len(store.load_state().rows) == expected_rows
    with pytest.raises(ArtifactHaltForReview, match="state_event_collision"):
        store.append_state_event(0, stage, event, "different_reason", usage, 0, **identity)


def test_state_event_rejects_sensitive_or_unbounded_reason_codes(tmp_path):
    store = ArtifactStore(tmp_path)
    store.create_run(_request())
    usage = BudgetUsage()

    with pytest.raises(ArtifactHaltForReview, match="invalid_state_event"):
        store.append_state_event(0, Role.INVENTOR.value, "provider transcript", "accepted", usage, 0)
    with pytest.raises(ArtifactHaltForReview, match="sensitive_content"):
        store.append_state_event(0, Role.INVENTOR.value, "role_failed", "api_token_leaked", usage, 0)


def test_load_state_halts_for_row_hash_tampering(tmp_path):
    store = ArtifactStore(tmp_path)
    store.create_run(_request())
    store.append_state_event(0, "gate", "gate_decided", "accepted", BudgetUsage(), 0)
    path = tmp_path / "state.jsonl"
    row = json.loads(path.read_text(encoding="utf-8"))
    row["reason_code"] = "tampered"
    path.write_text(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")

    with pytest.raises(ArtifactHaltForReview, match="state_hash_mismatch"):
        store.load_state()


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    (("sequence", 1, "invalid_state_sequence"),
     ("previous_hash", "f" * 64, "invalid_state_chain"),
     ("schema_version", "2.0", "unsupported_state_version")),
)
def test_load_state_halts_for_duplicate_sequence_broken_chain_or_version(tmp_path, field, value, reason):
    store = ArtifactStore(tmp_path)
    store.create_run(_request())
    store.append_state_event(0, "gate", "gate_decided", "accepted", BudgetUsage(), 0)
    store.append_state_event(0, "run", "run_completed", "accepted", BudgetUsage(stages=1), 0)
    path = tmp_path / "state.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    rows[1][field] = value
    unhashed = {key: val for key, val in rows[1].items() if key != "row_hash"}
    rows[1]["row_hash"] = hashlib.sha256(json.dumps(unhashed, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    path.write_text("".join(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in rows), encoding="utf-8")

    with pytest.raises(ArtifactHaltForReview, match=reason):
        store.load_state()


def test_write_summary_persists_reconstructible_run_summary(tmp_path):
    store = ArtifactStore(tmp_path)
    store.create_run(_request())
    summary = RunSummary("1.0", "run-001", "halt_for_review", "human_review_required", 1)

    path = store.write_summary(summary)

    assert json.loads(path.read_text(encoding="utf-8")) == summary.to_canonical_dict()


def test_completed_summary_round_trips_or_reconstructs_from_terminal_state(tmp_path):
    store = ArtifactStore(tmp_path)
    store.create_run(_request())
    usage = BudgetUsage(stages=5)
    store.append_state_event(0, "gate", "gate_decided", "quality_improved", usage, 0, action="advance")
    store.append_state_event(0, "run", "run_completed", "quality_improved", usage, 0, action="advance")
    expected = RunSummary("1.0", "run-001", "advance", "quality_improved", 1)
    assert store.load_summary() == expected
    store.write_summary(expected)
    assert store.load_summary() == expected


def test_tampered_terminal_summary_halts_typed(tmp_path):
    store = ArtifactStore(tmp_path)
    store.create_run(_request())
    usage = BudgetUsage(stages=5)
    store.append_state_event(0, "gate", "gate_decided", "quality_improved", usage, 0, action="advance")
    store.append_state_event(0, "run", "run_completed", "quality_improved", usage, 0, action="advance")
    store.write_summary(RunSummary("1.0", "run-001", "advance", "quality_improved", 1))
    path = tmp_path / "summary.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["reason_code"] = "tampered"
    path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    with pytest.raises(ArtifactHaltForReview, match="summary_state_mismatch"):
        store.load_summary()


def test_project_summary_cannot_overwrite_terminal_summary(tmp_path):
    store = ArtifactStore(tmp_path)
    store.create_run(_request())
    store.write_summary(RunSummary("1.0", "run-001", "halt_for_review", "human_review_required", 1))
    with pytest.raises(ArtifactHaltForReview, match="summary_conflict"):
        store.project_summary()


def test_state_journal_enforces_reservation_and_terminal_order(tmp_path):
    store = ArtifactStore(tmp_path)
    store.create_run(_request())
    envelope = _inventor_envelope()
    usage = BudgetUsage(stages=1)
    store.reserve_role_attempt(0, Role.INVENTOR, envelope, "invent-0", usage, 0)
    with pytest.raises(ArtifactHaltForReview, match="active_role_attempt"):
        store.reserve_role_attempt(0, Role.INVENTOR, envelope, "invent-0b", usage, 0)
    with pytest.raises(ArtifactHaltForReview, match="indeterminate_role_attempt"):
        store.append_state_event(0, "gate", "gate_decided", "rejected", usage, 0, action="halt_for_review")


def test_state_journal_requires_gate_before_matching_run_completion(tmp_path):
    store = ArtifactStore(tmp_path)
    store.create_run(_request())
    usage = BudgetUsage()
    with pytest.raises(ArtifactHaltForReview, match="missing_gate_decision"):
        store.append_state_event(0, "run", "run_completed", "accepted", usage, 0, action="advance")
    store.append_state_event(0, "gate", "gate_decided", "accepted", usage, 0, action="advance")
    with pytest.raises(ArtifactHaltForReview, match="terminal_decision_mismatch"):
        store.append_state_event(0, "run", "run_completed", "different", usage, 0, action="advance")

def test_reserved_role_attempt_without_outcome_halts_as_indeterminate(tmp_path):
    store = ArtifactStore(tmp_path)
    store.create_run(_request())
    envelope = _inventor_envelope()

    reserved = store.reserve_role_attempt(
        0, Role.INVENTOR, envelope, "invent-0", BudgetUsage(calls=1, stages=1), 0
    )

    assert reserved.event == "role_reserved"
    assert reserved.invocation_id == envelope.invocation_id
    assert reserved.idempotency_key == "invent-0"
    with pytest.raises(ArtifactHaltForReview, match="indeterminate_role_attempt"):
        store.load_state()


def test_complete_role_attempt_atomically_persists_result_transition_and_outcome(tmp_path):
    store = ArtifactStore(tmp_path)
    store.create_run(_request())
    envelope = _inventor_envelope()
    usage = BudgetUsage(calls=1, stages=1)
    store.reserve_role_attempt(0, Role.INVENTOR, envelope, "invent-0", usage, 0)

    artifact_hash = store.complete_role_attempt(
        0, Role.INVENTOR, envelope, _experiment(), "invent-0", usage, 0
    )

    assert store.load_role_result(0, Role.INVENTOR) == _experiment()
    assert store.load_state().last_event.event == "role_completed"
    journal = json.loads((tmp_path / "journal.jsonl").read_text(encoding="utf-8"))
    assert journal["artifact_hash"] == artifact_hash
    assert journal["idempotency_key"] == "invent-0"


def test_completion_requires_matching_persisted_reservation(tmp_path):
    store = ArtifactStore(tmp_path)
    store.create_run(_request())

    with pytest.raises(ArtifactHaltForReview, match="missing_role_reservation"):
        store.complete_role_attempt(
            0, Role.INVENTOR, _inventor_envelope(), _experiment(), "invent-0", BudgetUsage(), 0
        )


def test_put_role_artifact_hashes_canonical_bytes_and_uses_atomic_replace(tmp_path, monkeypatch):
    """Would fail if key order changed a stored object or a write exposed a partial target."""
    store = ArtifactStore(tmp_path)
    store.create_run(_request())
    envelope = _inventor_envelope()
    replaced = []
    real_replace = __import__("os").replace

    def track_replace(source, destination):
        replaced.append((Path(source), Path(destination)))
        return real_replace(source, destination)

    monkeypatch.setattr("research_orchestration.artifacts.os.replace", track_replace)

    artifact_hash = store.put_role_artifact(0, Role.INVENTOR, envelope, "invent-0")
    object_path = tmp_path / "objects" / f"{artifact_hash}.json"

    expected = json.dumps(
        envelope.to_canonical_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    assert artifact_hash == hashlib.sha256(expected).hexdigest()
    assert object_path.read_bytes() == expected
    assert any(destination == object_path and source.parent == object_path.parent for source, destination in replaced)


def test_journal_sequences_and_references_persisted_artifact_and_idempotency_key(tmp_path):
    """Would fail if a transition could be journaled out of order or without replay-safe references."""
    store = ArtifactStore(tmp_path)
    store.create_run(_request())
    artifact_hash = store.put_role_artifact(0, Role.INVENTOR, _inventor_envelope(), "invent-0")
    bounds_hash = store.put_role_artifact(0, Role.IN_BOUNDS_CHECKER, _bounds_envelope(), "check-0")

    first = store.append_transition(0, "start", Role.INVENTOR.value, artifact_hash, "invent-0")
    second = store.append_transition(0, Role.INVENTOR.value, Role.IN_BOUNDS_CHECKER.value, bounds_hash, "check-0")
    rows = [json.loads(line) for line in (tmp_path / "journal.jsonl").read_text(encoding="utf-8").splitlines()]

    assert (first["sequence"], second["sequence"]) == (1, 2)
    assert [row["sequence"] for row in rows] == [1, 2]
    assert rows[0]["artifact_hash"] == artifact_hash
    assert rows[0]["idempotency_key"] == "invent-0"


def test_projection_reconstructs_cycles_from_artifact_references(tmp_path):
    """Would fail if summary projection omitted the role artifact behind a recorded transition."""
    store = ArtifactStore(tmp_path)
    store.create_run(_request())
    artifact_hash = store.put_role_artifact(0, Role.INVENTOR, _inventor_envelope(), "invent-0")
    store.append_transition(0, "start", Role.INVENTOR.value, artifact_hash, "invent-0")

    summary = store.project_summary()

    assert summary == {
        "cycles": [{"artifacts": [{"artifact_hash": artifact_hash, "idempotency_key": "invent-0", "stage": "inventor"}], "cycle": 0}],
        "journal_sequences": [1],
        "run_id": "run-001",
        "schema_version": "1.0",
    }
    assert json.loads((tmp_path / "summary.json").read_text(encoding="utf-8")) == summary


def test_resume_cursor_reports_first_missing_stage_and_completed_keys_without_work(tmp_path):
    """Would fail if resume replayed completed work or skipped the first unrecorded role."""
    store = ArtifactStore(tmp_path)
    store.create_run(_request())
    artifact_hash = store.put_role_artifact(0, Role.INVENTOR, _inventor_envelope(), "invent-0")
    store.append_transition(0, "start", Role.INVENTOR.value, artifact_hash, "invent-0")

    cursor = store.resume_cursor()

    assert cursor.cycle == 0
    assert cursor.stage == Role.IN_BOUNDS_CHECKER.value
    assert cursor.completed_idempotency_keys == frozenset({"invent-0"})
    assert (tmp_path / "journal.jsonl").is_file()


def test_repeated_idempotency_key_returns_existing_artifact_without_overwrite(tmp_path):
    """Would fail if a resume path performed the already-completed role a second time."""
    store = ArtifactStore(tmp_path)
    store.create_run(_request())
    first = store.put_role_artifact(0, Role.INVENTOR, _inventor_envelope(), "invent-0")

    second = store.put_role_artifact(0, Role.INVENTOR, _inventor_envelope(), "invent-0")

    assert second == first
    assert len(list((tmp_path / "cycles" / "0").glob("*.json"))) == 1


def test_load_halts_for_object_tampering_and_truncated_journal(tmp_path):
    """Would fail if corrupted local state were guessed-repaired instead of held for review."""
    store = ArtifactStore(tmp_path)
    store.create_run(_request())
    artifact_hash = store.put_role_artifact(0, Role.INVENTOR, _inventor_envelope(), "invent-0")
    (tmp_path / "objects" / f"{artifact_hash}.json").write_text('{"changed":true}', encoding="utf-8")

    with pytest.raises(ArtifactHaltForReview, match="artifact_hash_mismatch"):
        store.load_and_validate()

    store = ArtifactStore(tmp_path / "journal")
    store.create_run(_request())
    artifact_hash = store.put_role_artifact(0, Role.INVENTOR, _inventor_envelope(), "invent-0")
    store.append_transition(0, "start", Role.INVENTOR.value, artifact_hash, "invent-0")
    (tmp_path / "journal" / "journal.jsonl").write_text('{"sequence":1', encoding="utf-8")

    with pytest.raises(ArtifactHaltForReview, match="truncated_journal"):
        store.load_and_validate()


def test_load_halts_for_incompatible_schema_and_sensitive_content(tmp_path):
    """Would fail if incompatible or secret/transcript-shaped artifacts entered a resumable run."""
    store = ArtifactStore(tmp_path)
    store.create_run(_request())
    run = json.loads((tmp_path / "run.json").read_text(encoding="utf-8"))
    run["schema_version"] = "2.0"
    (tmp_path / "run.json").write_text(
        json.dumps(run, sort_keys=True, separators=(",", ":")), encoding="utf-8"
    )

    with pytest.raises(ArtifactHaltForReview, match="unsupported_schema_version"):
        store.load_and_validate()

    with pytest.raises(ArtifactHaltForReview, match="sensitive_content"):
        store.put_role_artifact(0, Role.INVENTOR, {"raw_transcript": "do not persist"}, "unsafe-0")


def test_load_halts_when_run_shape_or_unreferenced_object_is_invalid(tmp_path):
    """Would fail if malformed run state or a tampered orphan object escaped validation."""
    store = ArtifactStore(tmp_path)
    store.create_run(_request())
    run = json.loads((tmp_path / "run.json").read_text(encoding="utf-8"))
    run.pop("budget_limits")
    (tmp_path / "run.json").write_text(
        json.dumps(run, sort_keys=True, separators=(",", ":")), encoding="utf-8"
    )

    with pytest.raises(ArtifactHaltForReview, match="invalid_run"):
        store.load_and_validate()

    store = ArtifactStore(tmp_path / "orphan")
    store.create_run(_request())
    orphan = {"schema_version": "1.0"}
    orphan_bytes = json.dumps(orphan, sort_keys=True, separators=(",", ":")).encode("utf-8")


def test_load_rejects_unlinked_role_artifacts_and_mismatched_transition_links(tmp_path):
    """Would fail if a role object could be resumed without its matching legal journal transition."""
    store = ArtifactStore(tmp_path)
    store.create_run(_request())
    inventor_hash = store.put_role_artifact(0, Role.INVENTOR, _inventor_envelope(), "invent-0")
    bounds_hash = store.put_role_artifact(0, Role.IN_BOUNDS_CHECKER, _bounds_envelope(), "check-0")

    with pytest.raises(ArtifactHaltForReview, match="unlinked_artifact_reference"):
        store.load_and_validate()

    with pytest.raises(ArtifactHaltForReview, match="invalid_transition"):
        store.append_transition(0, "start", Role.IN_BOUNDS_CHECKER.value, bounds_hash, "check-0")

    with pytest.raises(ArtifactHaltForReview, match="journal_reference_mismatch"):
        store.append_transition(0, "start", Role.INVENTOR.value, inventor_hash, "check-0")


def test_idempotency_keys_are_unique_across_the_entire_run(tmp_path):
    """Would fail if the same completed-work identity could name different cycle artifacts."""
    store = ArtifactStore(tmp_path)
    store.create_run(_request())
    store.put_role_artifact(0, Role.INVENTOR, _inventor_envelope(), "invent-0")

    with pytest.raises(ArtifactHaltForReview, match="idempotency_collision"):
        store.put_role_artifact(1, Role.INVENTOR, _inventor_envelope(), "invent-0")


def test_load_rejects_noncanonical_cycle_directories_and_unexpected_or_sensitive_files(tmp_path):
    """Would fail if ignored filesystem state could alter or conceal a resumable run."""
    store = ArtifactStore(tmp_path / "leading-zero")
    store.create_run(_request())
    (tmp_path / "leading-zero" / "cycles" / "00").mkdir()

    with pytest.raises(ArtifactHaltForReview, match="invalid_cycle_reference"):
        store.load_and_validate()

    store = ArtifactStore(tmp_path / "unexpected")
    store.create_run(_request())
    (tmp_path / "unexpected" / "notes.txt").write_text("ignored", encoding="utf-8")

    with pytest.raises(ArtifactHaltForReview, match="unexpected_artifact_file"):
        store.load_and_validate()

    store = ArtifactStore(tmp_path / "sensitive-file")
    store.create_run(_request())
    (tmp_path / "sensitive-file" / "raw_transcript.txt").write_text("forbidden", encoding="utf-8")

    with pytest.raises(ArtifactHaltForReview, match="sensitive_content"):
        store.load_and_validate()


def test_concurrent_transitions_receive_unique_monotonic_sequences(tmp_path):
    """Would fail if simultaneous local writers could reuse a journal sequence number."""
    store = ArtifactStore(tmp_path)
    store.create_run(_request())
    artifacts = [
        store.put_role_artifact(cycle, Role.INVENTOR, _inventor_envelope(), f"invent-{cycle}")
        for cycle in range(4)
    ]
    barrier = Barrier(4)

    def append(cycle):
        barrier.wait()
        return store.append_transition(cycle, "start", Role.INVENTOR.value, artifacts[cycle], f"invent-{cycle}")

    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(append, range(4)))

    assert sorted(row["sequence"] for row in rows) == [1, 2, 3, 4]
    store.load_and_validate()


def test_journal_requires_each_cycle_to_begin_with_inventor_and_chain_contiguously(tmp_path):
    """Would fail if an otherwise legal edge could be appended before its predecessor."""
    store = ArtifactStore(tmp_path)
    store.create_run(_request())
    store.put_role_artifact(0, Role.INVENTOR, _inventor_envelope(), "invent-0")
    bounds_hash = store.put_role_artifact(0, Role.IN_BOUNDS_CHECKER, _bounds_envelope(), "check-0")

    with pytest.raises(ArtifactHaltForReview, match="invalid_transition"):
        store.append_transition(
            0, Role.INVENTOR.value, Role.IN_BOUNDS_CHECKER.value, bounds_hash, "check-0"
        )


def test_load_rejects_valid_but_unreferenced_object_envelopes(tmp_path):
    """Would fail if a valid object could disappear from the cycle projection without review."""
    store = ArtifactStore(tmp_path)
    store.create_run(_request())
    inventor_hash = store.put_role_artifact(0, Role.INVENTOR, _inventor_envelope(), "invent-0")
    store.append_transition(0, "start", Role.INVENTOR.value, inventor_hash, "invent-0")
    orphan = _bounds_envelope().to_canonical_dict()
    orphan_bytes = json.dumps(orphan, sort_keys=True, separators=(",", ":")).encode("utf-8")
    (tmp_path / "objects" / f"{hashlib.sha256(orphan_bytes).hexdigest()}.json").write_bytes(orphan_bytes)

    with pytest.raises(ArtifactHaltForReview, match="unreferenced_object"):
        store.load_and_validate()


def test_load_and_lock_acquisition_reject_unsafe_lock_paths(tmp_path):
    """Would fail if a lock directory surfaced raw filesystem errors or bypassed run safety checks."""
    store = ArtifactStore(tmp_path)
    store.create_run(_request())
    (tmp_path / "run.lock").unlink()
    (tmp_path / "run.lock").mkdir()

    with pytest.raises(ArtifactHaltForReview, match="unsafe_lock_path"):
        store.load_and_validate()

    other = ArtifactStore(tmp_path / "acquire")
    (tmp_path / "acquire").mkdir()
    (tmp_path / "acquire" / "run.lock").mkdir()
    with pytest.raises(ArtifactHaltForReview, match="lock_io_failed"):
        other.create_run(_request())


def test_load_rejects_globally_reordered_per_cycle_journal_chain(tmp_path):
    """Would fail if individually legal rows were accepted after sequence-order tampering."""
    store = ArtifactStore(tmp_path)
    store.create_run(_request())
    inventor_hash = store.put_role_artifact(0, Role.INVENTOR, _inventor_envelope(), "invent-0")
    bounds_hash = store.put_role_artifact(0, Role.IN_BOUNDS_CHECKER, _bounds_envelope(), "check-0")
    store.append_transition(0, "start", Role.INVENTOR.value, inventor_hash, "invent-0")
    store.append_transition(0, Role.INVENTOR.value, Role.IN_BOUNDS_CHECKER.value, bounds_hash, "check-0")
    rows = [json.loads(line) for line in (tmp_path / "journal.jsonl").read_text(encoding="utf-8").splitlines()]
    rows.reverse()
    for sequence, row in enumerate(rows, start=1):
        row["sequence"] = sequence
    (tmp_path / "journal.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )

    with pytest.raises(ArtifactHaltForReview, match="invalid_transition"):
        store.load_and_validate()


@pytest.mark.parametrize("failure_phase", ("read", "write", "flush", "close"))
def test_lock_file_seed_and_close_failures_halt_for_review(tmp_path, monkeypatch, failure_phase):
    """Would fail if lock-file seeding or close leaked raw filesystem errors."""
    class LockHandle:
        def __enter__(self):
            return self

        def __exit__(self, _exc_type, _exc_value, _traceback):
            self.close()

        @staticmethod
        def seek(_offset):
            return None

        def read(self, _length):
            if failure_phase == "read":
                raise PermissionError("seed read")
            return b""

        def write(self, _content):
            if failure_phase == "write":
                raise OSError("seed write")
            return 1

        def flush(self):
            if failure_phase == "flush":
                raise PermissionError("seed flush")

        @staticmethod
        def fileno():
            return 1

        def close(self):
            if failure_phase == "close":
                raise OSError("close")

    class SuccessfulLock:
        LK_LOCK = 1
        LK_UNLCK = 2

        @staticmethod
        def locking(_fd, _operation, _length):
            return None

    root = tmp_path / failure_phase
    root.mkdir()
    monkeypatch.setattr(Path, "open", lambda self, *_args, **_kwargs: LockHandle())
    monkeypatch.setitem(sys.modules, "msvcrt", SuccessfulLock)

    with pytest.raises(ArtifactHaltForReview, match="lock_io_failed"):
        ArtifactStore(root).create_run(_request())


def test_lock_root_mkdir_failure_halts_for_review(tmp_path, monkeypatch):
    """Would fail if lock setup leaked a root-creation filesystem error."""
    root = tmp_path / "mkdir"
    real_mkdir = Path.mkdir

    def fail_root_mkdir(path, *args, **kwargs):
        if path == root:
            raise PermissionError("mkdir")
        return real_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", fail_root_mkdir)

    with pytest.raises(ArtifactHaltForReview, match="lock_io_failed"):
        ArtifactStore(root).create_run(_request())


def test_lock_file_open_failure_halts_for_review(tmp_path, monkeypatch):
    """Would fail if lock setup leaked a lock-file open error."""
    root = tmp_path / "open"
    root.mkdir()
    real_open = Path.open

    def fail_lock_open(path, *args, **kwargs):
        if path == root / "run.lock":
            raise OSError("open")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_lock_open)

    with pytest.raises(ArtifactHaltForReview, match="lock_io_failed"):
        ArtifactStore(root).create_run(_request())


def test_failed_lock_acquisition_is_not_followed_by_release(tmp_path, monkeypatch):
    """Would fail if a failed acquisition entered teardown as though the lock were held."""
    class FailingLock:
        LK_LOCK = 1
        LK_UNLCK = 2

        @staticmethod
        def locking(_fd, operation, _length):
            if operation == FailingLock.LK_LOCK:
                raise PermissionError("acquire")
            raise AssertionError("released without acquisition")

    monkeypatch.setitem(sys.modules, "msvcrt", FailingLock)
    with pytest.raises(ArtifactHaltForReview, match="lock_io_failed"):
        ArtifactStore(tmp_path / "acquire").create_run(_request())


def test_lock_release_failure_halts_when_body_succeeds(tmp_path, monkeypatch):
    """Would fail if lock release errors escaped without a typed halt."""
    class ReleaseFailingLock:
        LK_LOCK = 1
        LK_UNLCK = 2

        @staticmethod
        def locking(_fd, operation, _length):
            if operation == ReleaseFailingLock.LK_UNLCK:
                raise OSError("release")

    monkeypatch.setitem(sys.modules, "msvcrt", ReleaseFailingLock)
    with pytest.raises(ArtifactHaltForReview, match="lock_io_failed"):
        ArtifactStore(tmp_path / "release").create_run(_request())


def test_lock_release_failure_preserves_active_body_exception(tmp_path, monkeypatch):
    """Would fail if unlock failure replaced the exception escaping the protected body."""
    class BodyFailure(RuntimeError):
        pass

    class ReleaseFailingLock:
        LK_LOCK = 1
        LK_UNLCK = 2

        @staticmethod
        def locking(_fd, operation, _length):
            if operation == ReleaseFailingLock.LK_UNLCK:
                raise PermissionError("release")

    monkeypatch.setitem(sys.modules, "msvcrt", ReleaseFailingLock)

    with pytest.raises(BodyFailure, match="body failure"):
        with ArtifactStore(tmp_path / "release-with-body")._locked():
            raise BodyFailure("body failure")
