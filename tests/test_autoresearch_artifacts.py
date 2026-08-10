"""Behavior contracts for local, tamper-evident autoresearch artifacts."""

import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import pytest

from research_orchestration.budgets import BudgetLimits
from research_orchestration.contracts import Role, RoleEnvelope, RunRequest
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

    first = store.append_transition(0, "start", Role.INVENTOR.value, artifact_hash, "invent-0")
    second = store.append_transition(0, Role.INVENTOR.value, Role.IN_BOUNDS_CHECKER.value, artifact_hash, "check-0")
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
    store.put_role_artifact(0, Role.INVENTOR, _inventor_envelope(), "invent-0")

    cursor = store.resume_cursor()

    assert cursor.cycle == 0
    assert cursor.stage == Role.IN_BOUNDS_CHECKER.value
    assert cursor.completed_idempotency_keys == frozenset({"invent-0"})
    assert not (tmp_path / "journal.jsonl").exists()


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
