from pathlib import Path

from scripts.company_enrichment.mechanical_experiment import (
    MECHANICAL_MODEL_ID,
    run_mechanical_validation,
)


def test_mechanical_validation_is_cached_evidence_only_and_not_candidate(
    tmp_path: Path,
) -> None:
    manifest = run_mechanical_validation(tmp_path)

    assert manifest["model_id"] == MECHANICAL_MODEL_ID
    assert manifest["live_model_calls"] == 0
    assert manifest["model_outputs_fabricated"] is False
    assert manifest["source_purchases"] == 0
    assert manifest["candidate"] is False
    assert manifest["approval"] is False
    assert len(manifest["reports"]) == 3
    assert all(Path(path).is_file() for path in manifest["reports"])


def test_mechanical_validation_resume_reuses_reports_without_rewrite(
    tmp_path: Path,
) -> None:
    first = run_mechanical_validation(tmp_path)
    report = Path(first["reports"][0])
    original = report.read_bytes()

    second = run_mechanical_validation(tmp_path, resume=True)

    assert second == first
    assert report.read_bytes() == original
