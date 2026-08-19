from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts.company_enrichment.contracts import ReviewStatus, canonical_json
from scripts.company_enrichment.experiment_program import ExperimentProgram
from scripts.company_enrichment.review import (
    BlindReviewScorecard,
    ReviewVerdict,
    record_blind_verdict,
)
from tests.company_enrichment.test_experiment_program_gate_pack import (
    CandidateRunner, _gate_manifest,
)


NOW = datetime(2026, 8, 13, 12, tzinfo=timezone.utc)


def _program(tmp_path: Path) -> ExperimentProgram:
    manifest = tmp_path / "aggregate-gate.json"
    if not manifest.exists():
        _gate_manifest(manifest, 0.95)
    runner = CandidateRunner()
    runner.gate_path = str(manifest)
    return ExperimentProgram(
        artifact_root=tmp_path,
        dossier_root=Path("benchmarks/dossiers"),
        model_client=None,
        as_of=NOW,
        runner_factory=lambda **_kwargs: runner,
    )


def test_candidate_restart_roundtrips_gate_pack_and_hash_byte_identically(
    tmp_path: Path,
) -> None:
    first = _program(tmp_path).run("company-description")
    original = first.review_path.read_bytes()
    first_history = ExperimentProgram._load_history(
        "initial-company-description", first.review_path,
    )

    resumed = _program(tmp_path).run("company-description", resume=True)
    history = ExperimentProgram._load_history(
        "initial-company-description", resumed.review_path,
    )

    assert resumed.review_status is ReviewStatus.CANDIDATE
    assert resumed.review_path.read_bytes() == original
    assert history.records[-1].record_hash == first_history.records[-1].record_hash
    assert history.records[-1].gate_evidence == first_history.records[-1].gate_evidence
    assert history.records[-1].blind_review_pack.pack_id == (
        first_history.records[-1].blind_review_pack.pack_id
    )


def test_approved_and_rejected_histories_roundtrip_typed_human_evidence(
    tmp_path: Path,
) -> None:
    candidate = _program(tmp_path).run("company-description")
    candidate_history = ExperimentProgram._load_history(
        "initial-company-description", candidate.review_path,
    )
    pack_id = candidate_history.records[-1].blind_review_pack.pack_id
    scorecard = BlindReviewScorecard(5, 4, 5, 4, 5)

    for verdict, expected in (
        (ReviewVerdict.APPROVE, ReviewStatus.APPROVED),
        (ReviewVerdict.REJECT, ReviewStatus.REJECTED),
    ):
        history = record_blind_verdict(
            candidate_history, verdict, reviewer_id="human-42",
            reviewed_at=NOW + timedelta(minutes=1),
            pack_id=pack_id, scorecard=scorecard,
        )
        path = tmp_path / f"{verdict.value}.jsonl"
        path.write_text(
            "\n".join(canonical_json(item) for item in history.records) + "\n",
            encoding="utf-8",
        )

        restored = ExperimentProgram._load_history(history.experiment_id, path)

        assert restored.status is expected
        assert restored.records[-1].scorecard == scorecard
        assert restored.records[-1].blind_review_pack_id == pack_id
        assert restored.records[-1].record_hash == history.records[-1].record_hash
