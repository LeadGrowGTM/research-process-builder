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
class GateReport:
    mean_quality_score: float


class GateBenchmark:
    def __init__(self, score: float) -> None:
        self.score = score
        self.plans = []

    def run(self, plan):
        self.plans.append(plan)
        return GateReport(self.score)


def _gate_runner(tmp_path: Path, score: float) -> ExperimentRunner:
    return ExperimentRunner(
        artifact_root=tmp_path,
        dossiers={
            item: _rehydrate_dossier(Path("benchmarks/dossiers") / f"{item}.yaml")
            for item in FIXED_SAAS_CORE
        },
        model_client=CountingClient(),
        benchmark_runner=GateBenchmark(score),
        as_of=AS_OF,
    )


def test_all_completed_below_programmed_gate_stays_experiment(tmp_path: Path) -> None:
    summary = _gate_runner(tmp_path, 0.899).run(
        "company-description", allow_paid=True,
    )

    assert summary.completed_cases == 24
    assert summary.programmed_gate_score == 0.899
    assert summary.status == "experiment"
    assert summary.approved is False


def test_all_completed_at_programmed_gate_can_be_candidate(tmp_path: Path) -> None:
    summary = _gate_runner(tmp_path, 0.90).run(
        "icp-persona-analysis", allow_paid=True,
    )

    assert summary.completed_cases == 24
    assert summary.programmed_gate_score == 0.90
    assert summary.status == "candidate"
    assert summary.approved is False


def test_growth_with_zero_programmed_score_cannot_be_candidate(tmp_path: Path) -> None:
    summary = _gate_runner(tmp_path, 0.0).run(
        "growth-signals", allow_paid=True,
    )

    assert summary.completed_cases == 24
    assert summary.programmed_gate_score == 0.0
    assert summary.status == "experiment"
