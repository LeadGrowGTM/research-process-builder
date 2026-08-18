import json
from pathlib import Path

from scripts.company_enrichment.experiment_runner import ExperimentRunner
from tests.company_enrichment.test_experiment_crash_recovery import (
    CountingClient,
    _runner,
)


def test_candidate_blind_outputs_are_actual_results_with_opaque_ids(
    tmp_path: Path,
) -> None:
    summary = _runner(tmp_path, CountingClient()).run(
        "company-description", allow_paid=True,
    )

    assert len(summary.blind_outputs) == 18
    serialized = json.dumps(summary.blind_outputs)
    assert "requested_model" not in serialized
    assert "resolved_model" not in serialized
    assert "provider" not in serialized
    assert all(item["output_id"].startswith("output-") for item in summary.blind_outputs)
    assert all(item["content"]["assertions"] for item in summary.blind_outputs)


def test_blind_outputs_rehydrate_from_append_only_journal(tmp_path: Path) -> None:
    runner = _runner(tmp_path, CountingClient())
    first = runner.run("icp-persona-analysis", allow_paid=True)
    journal = tmp_path / "icp-persona-analysis" / "outcomes.jsonl"

    assert first.blind_outputs == ExperimentRunner._blind_outputs(
        journal, tmp_path / "icp-persona-analysis" / "blind-output-map.json",
    )
