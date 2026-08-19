from datetime import datetime, timezone
from pathlib import Path
import json

from scripts.company_enrichment.benchmark import BenchmarkRunner
from scripts.company_enrichment.cli import _rehydrate_dossier
from scripts.company_enrichment.experiment_runner import (
    FIXED_SAAS_CORE,
    ExperimentRunner,
)


def test_missing_model_client_records_auth_gaps_without_fake_outputs(
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
        as_of=datetime(2026, 8, 12, 12, tzinfo=timezone.utc),
    )

    summary = runner.run("growth-signals")
    events = tuple(
        json.loads(line)
        for line in (tmp_path / "growth-signals" / "outcomes.jsonl")
        .read_text(encoding="utf-8").splitlines()
    )

    assert summary.status == "experiment"
    assert summary.completed_cases == 0
    assert summary.authentication_gap
    assert len(events) == 18
    assert {event["status"] for event in events} == {"not_executed"}
    assert {event["failure"] for event in events} == {"authentication_required"}
    assert {event["resolved_model_id"] for event in events} == {None}
    assert not any("result" in event for event in events)
