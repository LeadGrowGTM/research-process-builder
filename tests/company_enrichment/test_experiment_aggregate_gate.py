from dataclasses import dataclass
from pathlib import Path

from scripts.company_enrichment.experiment_runner import ExperimentRunner
from tests.company_enrichment.test_experiment_crash_recovery import (
    AS_OF,
    CountingClient,
    FIXED_SAAS_CORE,
    _rehydrate_dossier,
)


@dataclass(frozen=True)
class Report:
    mean_quality_score: float


class SequenceBenchmark:
    def __init__(self, scores):
        self.scores = list(scores)
        self.calls = 0

    def run(self, plan):
        score = self.scores[self.calls]
        self.calls += 1
        return Report(score)


def _runner(tmp_path: Path, scores, client=None):
    return ExperimentRunner(
        artifact_root=tmp_path,
        dossiers={
            item: _rehydrate_dossier(Path("benchmarks/dossiers") / f"{item}.yaml")
            for item in FIXED_SAAS_CORE
        },
        model_client=client or CountingClient(),
        benchmark_runner=SequenceBenchmark(scores),
        as_of=AS_OF,
    )


def test_candidate_gate_aggregates_all_eight_model_track_reports(
    tmp_path: Path,
) -> None:
    summary = _runner(tmp_path, [1.0, 0, 0, 0, 0, 0, 0, 0]).run(
        "company-description", allow_paid=True,
    )

    assert summary.programmed_gate_score == 1 / 8
    assert summary.status == "experiment"
    assert Path(summary.gate_artifact_path).is_file()


def test_all_eight_passing_reports_create_one_real_aggregate_manifest(
    tmp_path: Path,
) -> None:
    summary = _runner(tmp_path, [0.9] * 8).run(
        "icp-persona-analysis", allow_paid=True,
    )

    manifest = Path(summary.gate_artifact_path)
    assert summary.programmed_gate_score == 0.9
    assert summary.status == "candidate"
    assert manifest.is_file()
    assert manifest.read_text(encoding="utf-8").count('"report_hash"') == 8


def test_completed_resume_revalidates_aggregate_without_model_calls(
    tmp_path: Path,
) -> None:
    client = CountingClient()
    first = _runner(tmp_path, [0.95] * 8, client).run(
        "company-description", allow_paid=True,
    )
    call_count = client.executions
    manifest = Path(first.gate_artifact_path)
    original = manifest.read_bytes()

    second = _runner(tmp_path, [], client).run(
        "company-description", allow_paid=True, resume=True,
    )

    assert client.executions == call_count
    assert second.status == "candidate"
    assert second.programmed_gate_score == 0.95
    assert manifest.read_bytes() == original
