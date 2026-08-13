from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path

import pytest

from scripts.company_enrichment.benchmark import (
    BenchmarkCase,
    BenchmarkRunner,
    ExecutionTrack,
    ExperimentPlan,
    score_result,
)
from scripts.company_enrichment.contracts import (
    CompanyDossier,
    EnrichmentResult,
    EvidenceRef,
    FieldAssertion,
    ResultStatus,
    Visibility,
)


AS_OF = datetime(2026, 8, 12, 12, tzinfo=timezone.utc)
FRESH = datetime(2026, 8, 10, 12, tzinfo=timezone.utc)
STALE = datetime(2026, 6, 1, 12, tzinfo=timezone.utc)


def _evidence(evidence_id: str, retrieved_at: datetime, suffix: str) -> EvidenceRef:
    return EvidenceRef(
        evidence_id,
        f"https://acme.example/{suffix}",
        retrieved_at,
        ('a' if suffix == 'stale' else 'b') * 64,
        f"Evidence for {suffix}",
    )


def _assertion(field: str, value: object, *evidence_ids: str) -> FieldAssertion:
    return FieldAssertion(
        field, value, tuple(evidence_ids), 0.9, Visibility.MESSAGE_SAFE,
    )


def _dossier(*, evidence: tuple[EvidenceRef, ...] | None = None) -> CompanyDossier:
    fresh = _evidence("ev-fresh", FRESH, "fresh")
    stale = _evidence("ev-stale", STALE, "stale")
    evidence = evidence or (fresh, stale)
    return CompanyDossier(
        "saas-01",
        "1.0",
        (
            _assertion("identity", "Acme", "ev-fresh"),
            _assertion("description", "Useful workflow software", "ev-stale"),
            _assertion("offers", ("Automated reporting",), "ev-fresh"),
        ),
        evidence,
    )


def _result(
    assertions: tuple[FieldAssertion, ...],
    evidence: tuple[EvidenceRef, ...],
    *,
    requested_model: str = 'gpt-5-nano',
    resolved_model: str = 'gpt-5-nano',
) -> EnrichmentResult:
    return EnrichmentResult(
        "company-description",
        "saas-01",
        "1.0",
        ResultStatus.COMPLETE,
        {
            "assertions": assertions,
            "evidence": evidence,
            "unknowns": (),
            "requested_model": requested_model,
            "resolved_model": resolved_model,
        },
    )


def _perfect_result(**model_ids: str) -> EnrichmentResult:
    fresh = _evidence("ev-fresh", FRESH, "fresh")
    stale = _evidence("ev-stale", STALE, "stale")
    return _result(
        (
            _assertion("identity", "Acme", "ev-fresh"),
            _assertion("description", "Useful workflow software", "ev-stale"),
            _assertion("offers", ("Automated reporting",), "ev-fresh"),
        ),
        (fresh, stale),
        **model_ids,
    )


def _case(**overrides: object) -> BenchmarkCase:
    values = {
        "result": _perfect_result(),
        "dossier": _dossier(),
        "as_of": AS_OF,
        "latency_ms": 1250,
        "model_cost_usd": "0.0123",
        "source_cost_usd": "0",
        "source_lookups": 2,
        "source_cache_hits": 2,
        "source_purchases": 0,
    }
    values.update(overrides)
    return BenchmarkCase(**values)


def test_score_result_measures_field_correctness_against_fixed_dossier() -> None:
    fresh = _evidence("ev-fresh", FRESH, "fresh")
    stale = _evidence("ev-stale", STALE, "stale")
    result = _result(
        (
            _assertion("identity", "Acme", "ev-fresh"),
            _assertion("description", "Wrong description", "ev-stale"),
            _assertion("offers", ("Automated reporting",), "ev-fresh"),
        ),
        (fresh, stale),
    )

    score = score_result(
        result, _dossier(), as_of=AS_OF, freshness_days=30,
    )

    assert score.correctness == pytest.approx(2 / 3)


def test_score_result_measures_citation_validity_completeness_and_freshness() -> None:
    fresh = _evidence("ev-fresh", FRESH, "fresh")
    stale = _evidence("ev-stale", STALE, "stale")
    result = _result(
        (
            _assertion("identity", "Acme", "ev-fresh"),
            _assertion("description", "Useful workflow software", "ev-stale"),
            _assertion("offers", ("Automated reporting",), "ev-missing"),
        ),
        (fresh, stale),
    )

    score = score_result(
        result, _dossier(), as_of=AS_OF, freshness_days=30,
    )

    assert score.citation_validity == pytest.approx(2 / 3)
    assert score.citation_completeness == pytest.approx(2 / 3)
    assert score.citation_freshness == pytest.approx(1 / 2)


def test_score_result_preserves_latency_and_exact_decimal_costs() -> None:
    score = score_result(
        _perfect_result(),
        _dossier(),
        as_of=AS_OF,
        freshness_days=30,
        latency_ms=987,
        model_cost_usd="0.0101",
        source_cost_usd="0.0022",
        cache_reused=False,
    )

    assert score.latency_ms == 987
    assert score.model_cost_usd == Decimal("0.0101")
    assert score.source_cost_usd == Decimal("0.0022")
    assert score.total_cost_usd == Decimal("0.0123")


def test_runner_preserves_exact_model_ids_and_aggregates_cache_reuse(
    tmp_path: Path,
) -> None:
    requested = 'gpt-5-nano'
    resolved = 'gpt-5-nano-2026-08-07'
    case = _case(result=_perfect_result(
        requested_model=requested, resolved_model=resolved,
    ))
    plan = ExperimentPlan(
        "exp-exact-model", "company-description", ExecutionTrack.SYNCHRONOUS,
        requested, (case,), freshness_days=30,
    )

    report = BenchmarkRunner(tmp_path).run(plan)

    assert report.requested_model_id == requested
    assert report.resolved_model_ids == (resolved,)
    assert report.execution_track is ExecutionTrack.SYNCHRONOUS
    assert report.cache_reuse_rate == 1.0
    assert report.total_source_purchases == 0
    assert report.total_source_cost_usd == Decimal("0")
    assert report.total_model_cost_usd == Decimal("0.0123")


def test_runner_keeps_synchronous_and_batch_reports_in_separate_artifacts(
    tmp_path: Path,
) -> None:
    runner = BenchmarkRunner(tmp_path)
    requested = 'gpt-5-nano'
    common = ("exp-tracks", "company-description")
    sync_plan = ExperimentPlan(
        *common, ExecutionTrack.SYNCHRONOUS, requested, (_case(),),
        freshness_days=30,
    )
    batch_plan = ExperimentPlan(
        *common, ExecutionTrack.BATCH, requested, (_case(),), freshness_days=30,
    )

    sync_report = runner.run(sync_plan)
    batch_report = runner.run(batch_plan)
    sync_path = runner.report_path(sync_plan)
    batch_path = runner.report_path(batch_plan)

    assert sync_path != batch_path
    assert sync_path.is_file()
    assert batch_path.is_file()
    assert sync_report.execution_track is ExecutionTrack.SYNCHRONOUS
    assert batch_report.execution_track is ExecutionTrack.BATCH
    assert json.loads(sync_path.read_text(encoding="utf-8"))["execution_track"] == (
        "synchronous"
    )
    assert json.loads(batch_path.read_text(encoding="utf-8"))["execution_track"] == (
        "batch"
    )


def test_runner_rejects_source_repurchase_for_fully_cached_evidence(
    tmp_path: Path,
) -> None:
    case = _case(
        source_cost_usd="0.01", source_lookups=2, source_cache_hits=2,
        source_purchases=1,
    )
    plan = ExperimentPlan(
        "exp-cache-violation",
        "company-description",
        ExecutionTrack.SYNCHRONOUS,
        'gpt-5-nano',
        (case,),
        freshness_days=30,
    )

    with pytest.raises(ValueError, match="cached evidence cannot repurchase sources"):
        BenchmarkRunner(tmp_path).run(plan)

    assert not BenchmarkRunner(tmp_path).report_path(plan).exists()


def test_runner_rejects_purchases_that_exceed_partial_cache_misses(
    tmp_path: Path,
) -> None:
    case = _case(
        source_cost_usd='0.02', source_lookups=3, source_cache_hits=2,
        source_purchases=2,
    )
    plan = ExperimentPlan(
        'exp-partial-cache-violation',
        'company-description',
        ExecutionTrack.SYNCHRONOUS,
        'gpt-5-nano',
        (case,),
        freshness_days=30,
    )

    with pytest.raises(ValueError, match='source purchases cannot exceed cache misses'):
        BenchmarkRunner(tmp_path).run(plan)


def test_runner_rejects_a_requested_model_id_mismatch(tmp_path: Path) -> None:
    case = _case(result=_perfect_result(
        requested_model='gpt-4.1-mini',
    ))
    plan = ExperimentPlan(
        "exp-model-mismatch",
        "company-description",
        ExecutionTrack.SYNCHRONOUS,
        'gpt-5-nano',
        (case,),
        freshness_days=30,
    )

    with pytest.raises(ValueError, match="requested model ID does not match"):
        BenchmarkRunner(tmp_path).run(plan)


def test_reports_are_deterministic_and_append_only(tmp_path: Path) -> None:
    plan = ExperimentPlan(
        "exp-deterministic",
        "company-description",
        ExecutionTrack.SYNCHRONOUS,
        'gpt-5-nano',
        (_case(),),
        freshness_days=30,
    )
    first_runner = BenchmarkRunner(tmp_path / "first")
    second_runner = BenchmarkRunner(tmp_path / "second")

    first_runner.run(plan)
    second_runner.run(plan)

    assert first_runner.report_path(plan).read_text(encoding="utf-8") == (
        second_runner.report_path(plan).read_text(encoding="utf-8")
    )
    with pytest.raises(FileExistsError):
        first_runner.run(plan)
