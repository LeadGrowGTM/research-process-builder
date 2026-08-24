from datetime import datetime, timezone
from pathlib import Path

from scripts.company_enrichment.benchmark import BenchmarkRunner
from scripts.company_enrichment.cli import _rehydrate_dossier
from scripts.company_enrichment.experiment_runner import FIXED_SAAS_CORE, ExperimentRunner


def test_authentication_blocked_resume_does_not_duplicate_attempts(
    tmp_path: Path,
) -> None:
    dossiers = {
        company_id: _rehydrate_dossier(
            Path("benchmarks/dossiers") / f"{company_id}.yaml"
        )
        for company_id in FIXED_SAAS_CORE
    }
    runner = ExperimentRunner(
        artifact_root=tmp_path,
        dossiers=dossiers,
        model_client=None,
        benchmark_runner=BenchmarkRunner(tmp_path),
        as_of=datetime(2026, 8, 12, 23, 59, tzinfo=timezone.utc),
    )

    runner.run("company-description")
    journal = tmp_path / "company-description" / "outcomes.jsonl"
    first = journal.read_bytes()
    resumed = runner.run("company-description", resume=True)

    assert journal.read_bytes() == first
    assert len(first.splitlines()) == 18
    assert resumed.authentication_gap
