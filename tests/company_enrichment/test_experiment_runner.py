from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from scripts.company_enrichment.benchmark import ExecutionTrack
from scripts.company_enrichment.contracts import (
    CompanyDossier,
    EvidenceRef,
    FieldAssertion,
    Visibility,
)
from scripts.company_enrichment.experiment_runner import (
    EXPERIMENT_ENRICHMENTS,
    EXPERIMENT_MODELS,
    FIXED_SAAS_CORE,
    ExperimentRunner,
    ModelExecution,
)


AS_OF = datetime(2026, 8, 12, 12, tzinfo=timezone.utc)


def _dossier(company_id: str) -> CompanyDossier:
    evidence = EvidenceRef(
        f"ev-{company_id}", f"https://{company_id}.example/about", AS_OF,
        (company_id[-1] if company_id[-1].isdigit() else "a") * 64,
        f"{company_id} provides reporting software to marketing teams.",
    )
    return CompanyDossier(
        company_id, "1.0",
        (
            FieldAssertion(
                "identity", company_id, (evidence.evidence_id,), 0.9,
                Visibility.MESSAGE_SAFE,
            ),
            FieldAssertion(
                "description", "Reporting software", (evidence.evidence_id,),
                0.9, Visibility.MESSAGE_SAFE,
            ),
            FieldAssertion(
                "offers", "Automated reports", (evidence.evidence_id,), 0.9,
                Visibility.MESSAGE_SAFE,
            ),
            FieldAssertion(
                "icp", "Marketing teams", (evidence.evidence_id,), 0.9,
                Visibility.MESSAGE_SAFE,
            ),
            FieldAssertion(
                "personas", "Marketing leaders", (evidence.evidence_id,), 0.9,
                Visibility.MESSAGE_SAFE,
            ),
        ),
        (evidence,),
        ("growth",),
    )


class FakeBenchmarkRunner:
    def __init__(self) -> None:
        self.plans = []

    def run(self, plan):
        self.plans.append(plan)
        return {
            "experiment_id": plan.experiment_id,
            "mean_scores": {"quality": 1.0},
        }


@dataclass
class FakeClient:
    calls: list
    estimated_cost: str = "0.01"

    def estimate(self, requests, track):
        return str(Decimal(self.estimated_cost) * len(requests))

    def execute(self, requests, track):
        self.calls.append((track, tuple(item.company_id for item in requests)))
        outputs = []
        for request in requests:
            dossier = request.dossier
            fields = {
                "company-description": ("identity", "description", "offers"),
                "icp-persona-analysis": ("icp", "personas"),
                "growth-signals": ("growth",),
            }[request.enrichment_id]
            assertions = tuple(
                next(item for item in dossier.assertions if item.field == field)
                for field in fields if field != "growth"
            )
            unknowns = ("growth",) if request.enrichment_id == "growth-signals" else ()
            outputs.append(ModelExecution(
                request.company_id, assertions, unknowns,
                request.requested_model_id, 25, self.estimated_cost,
            ))
        return tuple(outputs)


def _runner(tmp_path: Path, client: FakeClient, benchmark=None) -> ExperimentRunner:
    dossiers = {company_id: _dossier(company_id) for company_id in FIXED_SAAS_CORE}
    return ExperimentRunner(
        artifact_root=tmp_path,
        dossiers=dossiers,
        model_client=client,
        benchmark_runner=benchmark or FakeBenchmarkRunner(),
        as_of=AS_OF,
    )


def test_matrix_uses_fixed_saas_core_and_official_exact_model_ids() -> None:
    assert FIXED_SAAS_CORE == ("saas-01", "saas-04", "saas-07")
    assert EXPERIMENT_ENRICHMENTS == (
        "company-description", "icp-persona-analysis", "growth-signals",
    )
    assert EXPERIMENT_MODELS == (
        "gpt-5-nano", "gpt-4o-mini", "gpt-4.1-mini", "gpt-5.6-luna",
    )


def test_run_uses_one_aggregate_dollar_cap_and_separate_tracks(
    tmp_path: Path,
) -> None:
    benchmark = FakeBenchmarkRunner()
    client = FakeClient([])
    summary = _runner(tmp_path, client, benchmark).run(
        "company-description", allow_paid=True,
    )

    assert len(client.calls) == 8
    assert {track for track, _ids in client.calls} == {
        ExecutionTrack.SYNCHRONOUS, ExecutionTrack.BATCH,
    }
    assert len(benchmark.plans) == 8
    assert all(len(plan.cases) == 3 for plan in benchmark.plans)
    assert {plan.requested_model_id for plan in benchmark.plans} == set(
        EXPERIMENT_MODELS
    )
    assert summary.status == "candidate"
    assert summary.approved is False
    assert summary.source_purchases == 0
    assert summary.source_cache_hits == 24
    assert summary.model_cost_usd == Decimal("0.24")
    assert summary.cap_usd == Decimal("1.00")


def test_resume_reuses_append_only_outcomes_without_model_or_source_calls(
    tmp_path: Path,
) -> None:
    client = FakeClient([])
    runner = _runner(tmp_path, client)
    first = runner.run("icp-persona-analysis", allow_paid=True)
    call_count = len(client.calls)
    journal = tmp_path / "icp-persona-analysis" / "outcomes.jsonl"
    original = journal.read_bytes()

    second = runner.run("icp-persona-analysis", allow_paid=True, resume=True)

    assert first.completed_cases == second.completed_cases == 24
    assert len(client.calls) == call_count
    assert journal.read_bytes() == original
    assert second.resumed_cases == 24


def test_paid_execution_requires_opt_in_and_never_exceeds_one_dollar(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="explicit paid opt-in"):
        _runner(tmp_path, FakeClient([])).run("growth-signals")

    with pytest.raises(ValueError, match="aggregate experiment cap"):
        _runner(tmp_path / "over", FakeClient([], estimated_cost="0.07")).run(
            "growth-signals", allow_paid=True,
        )


def test_resolved_model_identity_must_be_explicit_and_requested_is_preserved(
    tmp_path: Path,
) -> None:
    class BadClient(FakeClient):
        def execute(self, requests, track):
            values = list(super().execute(requests, track))
            values[0] = ModelExecution(
                values[0].company_id, values[0].assertions,
                values[0].unknowns, None, values[0].latency_ms,
                values[0].actual_cost_usd,
            )
            return tuple(values)

    summary = _runner(tmp_path, BadClient([])).run(
        "company-description", allow_paid=True,
    )
    events = [
        __import__("json").loads(line)
        for line in (tmp_path / "company-description" / "outcomes.jsonl")
        .read_text(encoding="utf-8").splitlines()
    ]

    assert summary.status == "experiment"
    assert any(
        event["status"] == "failed"
        and event["failure"] == "contract_invalid"
        and event["resolved_model_id"] is None
        for event in events
    )
