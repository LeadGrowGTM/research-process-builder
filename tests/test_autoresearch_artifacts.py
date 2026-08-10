"""Behavior contracts for local, tamper-evident autoresearch artifacts."""

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import sys
from threading import Barrier

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


def _bounds_envelope() -> RoleEnvelope:
    return RoleEnvelope.create(
        Role.IN_BOUNDS_CHECKER,
        {"constraints": ("read_only",), "experiment": "experiment-0"},
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


def test_lock_acquire_and_release_oserrors_halt_for_review(tmp_path, monkeypatch):
    """Would fail if lock I/O leaked raw OSErrors at either acquisition or release."""
    class FailingLock:
        LK_LOCK = 1
        LK_UNLCK = 2

        @staticmethod
        def locking(_fd, operation, _length):
            if operation == FailingLock.LK_LOCK:
                raise OSError("acquire")

    monkeypatch.setitem(sys.modules, "msvcrt", FailingLock)
    with pytest.raises(ArtifactHaltForReview, match="lock_io_failed"):
        ArtifactStore(tmp_path / "acquire").create_run(_request())

    class ReleaseFailingLock(FailingLock):
        @staticmethod
        def locking(_fd, operation, _length):
            if operation == ReleaseFailingLock.LK_UNLCK:
                raise OSError("release")

    monkeypatch.setitem(sys.modules, "msvcrt", ReleaseFailingLock)
    with pytest.raises(ArtifactHaltForReview, match="lock_io_failed"):
        ArtifactStore(tmp_path / "release").create_run(_request())
