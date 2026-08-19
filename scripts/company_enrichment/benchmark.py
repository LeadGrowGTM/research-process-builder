from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import Enum
import os
from pathlib import Path
from typing import Any, Mapping

from .contracts import (
    CompanyDossier,
    EnrichmentResult,
    EvidenceRef,
    FieldAssertion,
    canonical_json,
)
from .executors import P0_ENRICHMENTS


DEFAULT_ARTIFACT_ROOT = Path('runs/company-enrichment/experiments')


class ExecutionTrack(str, Enum):
    SYNCHRONOUS = 'synchronous'
    BATCH = 'batch'


def _require_text(name: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{name} must be non-empty text')
    return value


def _safe_path_component(name: str, value: object) -> str:
    text = _require_text(name, value)
    if text in {'.', '..'} or any(separator in text for separator in ('/', '\\')):
        raise ValueError(f'{name} must be a safe path component')
    return text


def _non_negative_decimal(name: str, value: object) -> Decimal:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f'{name} must be a decimal amount') from error
    if not amount.is_finite() or amount < 0:
        raise ValueError(f'{name} must be a non-negative finite amount')
    return amount


def _non_negative_int(name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f'{name} must be a non-negative integer')
    return value


@dataclass(frozen=True, slots=True)
class BenchmarkCase:
    result: EnrichmentResult
    dossier: CompanyDossier
    as_of: datetime
    latency_ms: int
    model_cost_usd: Decimal | str
    source_cost_usd: Decimal | str
    source_lookups: int
    source_cache_hits: int
    source_purchases: int

    def __post_init__(self) -> None:
        if not isinstance(self.result, EnrichmentResult):
            raise ValueError('result must be an EnrichmentResult')
        if not isinstance(self.dossier, CompanyDossier):
            raise ValueError('dossier must be a CompanyDossier')
        if self.result.company_id != self.dossier.company_id:
            raise ValueError('result and dossier company IDs must match')
        if not isinstance(self.as_of, datetime) or self.as_of.tzinfo is None:
            raise ValueError('as_of must be a timezone-aware datetime')
        object.__setattr__(
            self, 'latency_ms', _non_negative_int('latency_ms', self.latency_ms),
        )
        object.__setattr__(
            self, 'model_cost_usd',
            _non_negative_decimal('model_cost_usd', self.model_cost_usd),
        )
        object.__setattr__(
            self, 'source_cost_usd',
            _non_negative_decimal('source_cost_usd', self.source_cost_usd),
        )
        for name in ('source_lookups', 'source_cache_hits', 'source_purchases'):
            object.__setattr__(self, name, _non_negative_int(name, getattr(self, name)))
        if self.source_cache_hits > self.source_lookups:
            raise ValueError('source_cache_hits cannot exceed source_lookups')

    @property
    def fully_cached(self) -> bool:
        return self.source_lookups > 0 and self.source_cache_hits == self.source_lookups


@dataclass(frozen=True, slots=True)
class ExperimentPlan:
    experiment_id: str
    enrichment_id: str
    execution_track: ExecutionTrack
    requested_model_id: str
    cases: tuple[BenchmarkCase, ...]
    freshness_days: int

    def __post_init__(self) -> None:
        _safe_path_component('experiment_id', self.experiment_id)
        _safe_path_component('enrichment_id', self.enrichment_id)
        _require_text('requested_model_id', self.requested_model_id)
        if not isinstance(self.execution_track, ExecutionTrack):
            raise ValueError('execution_track must be an ExecutionTrack')
        cases = tuple(self.cases)
        if not cases or not all(isinstance(case, BenchmarkCase) for case in cases):
            raise ValueError('cases must contain BenchmarkCase values')
        object.__setattr__(self, 'cases', cases)
        object.__setattr__(
            self, 'freshness_days',
            _non_negative_int('freshness_days', self.freshness_days),
        )
        company_ids = [case.result.company_id for case in cases]
        if len(company_ids) != len(set(company_ids)):
            raise ValueError('experiment cases require unique company IDs')
        if any(case.result.enrichment_id != self.enrichment_id for case in cases):
            raise ValueError('case enrichment ID does not match experiment plan')


@dataclass(frozen=True, slots=True)
class ScoreCard:
    company_id: str
    result_status: str
    failure: str | None
    requested_model_id: str | None
    resolved_model_id: str | None
    correctness: float
    citation_validity: float
    citation_completeness: float
    citation_freshness: float
    latency_ms: int
    model_cost_usd: Decimal
    source_cost_usd: Decimal
    cache_reused: bool

    @property
    def quality_score(self) -> float:
        return (
            self.correctness
            + self.citation_validity
            + self.citation_completeness
            + self.citation_freshness
        ) / 4

    @property
    def total_cost_usd(self) -> Decimal:
        return self.model_cost_usd + self.source_cost_usd

    def to_payload(self) -> dict[str, Any]:
        return {
            'cache_reused': self.cache_reused,
            'citation_completeness': self.citation_completeness,
            'citation_freshness': self.citation_freshness,
            'citation_validity': self.citation_validity,
            'company_id': self.company_id,
            'correctness': self.correctness,
            'failure': self.failure,
            'latency_ms': self.latency_ms,
            'model_cost_usd': str(self.model_cost_usd),
            'quality_score': self.quality_score,
            'requested_model_id': self.requested_model_id,
            'resolved_model_id': self.resolved_model_id,
            'result_status': self.result_status,
            'source_cost_usd': str(self.source_cost_usd),
            'total_cost_usd': str(self.total_cost_usd),
        }


@dataclass(frozen=True, slots=True)
class BenchmarkReport:
    experiment_id: str
    enrichment_id: str
    execution_track: ExecutionTrack
    requested_model_id: str
    resolved_model_ids: tuple[str | None, ...]
    scorecards: tuple[ScoreCard, ...]
    total_source_lookups: int
    total_source_cache_hits: int
    total_source_purchases: int

    @property
    def cache_reuse_rate(self) -> float:
        if not self.total_source_lookups:
            return 0.0
        return self.total_source_cache_hits / self.total_source_lookups

    @property
    def total_latency_ms(self) -> int:
        return sum(score.latency_ms for score in self.scorecards)

    @property
    def average_latency_ms(self) -> float:
        return self.total_latency_ms / len(self.scorecards)

    @property
    def total_model_cost_usd(self) -> Decimal:
        return sum(
            (score.model_cost_usd for score in self.scorecards), Decimal('0'),
        )

    @property
    def total_source_cost_usd(self) -> Decimal:
        return sum(
            (score.source_cost_usd for score in self.scorecards), Decimal('0'),
        )

    @property
    def total_cost_usd(self) -> Decimal:
        return self.total_model_cost_usd + self.total_source_cost_usd

    def _mean(self, attribute: str) -> float:
        return sum(getattr(score, attribute) for score in self.scorecards) / len(
            self.scorecards
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            'average_latency_ms': self.average_latency_ms,
            'cache_reuse_rate': self.cache_reuse_rate,
            'enrichment_id': self.enrichment_id,
            'execution_track': self.execution_track.value,
            'experiment_id': self.experiment_id,
            'mean_scores': {
                'citation_completeness': self._mean('citation_completeness'),
                'citation_freshness': self._mean('citation_freshness'),
                'citation_validity': self._mean('citation_validity'),
                'correctness': self._mean('correctness'),
                'quality': self._mean('quality_score'),
            },
            'requested_model_id': self.requested_model_id,
            'resolved_model_ids': list(self.resolved_model_ids),
            'schema_version': '1.0',
            'scorecards': [score.to_payload() for score in self.scorecards],
            'total_cost_usd': str(self.total_cost_usd),
            'total_latency_ms': self.total_latency_ms,
            'total_model_cost_usd': str(self.total_model_cost_usd),
            'total_source_cache_hits': self.total_source_cache_hits,
            'total_source_cost_usd': str(self.total_source_cost_usd),
            'total_source_lookups': self.total_source_lookups,
            'total_source_purchases': self.total_source_purchases,
        }


def _assertions(output: Mapping[str, Any]) -> tuple[FieldAssertion, ...]:
    assertions = tuple(output.get('assertions', ()))
    if not all(isinstance(assertion, FieldAssertion) for assertion in assertions):
        raise ValueError('benchmark result assertions must be FieldAssertion values')
    return assertions


def _evidence(output: Mapping[str, Any]) -> tuple[EvidenceRef, ...]:
    evidence = tuple(output.get('evidence', ()))
    if not all(isinstance(item, EvidenceRef) for item in evidence):
        raise ValueError('benchmark result evidence must be EvidenceRef values')
    return evidence


def _ground_truth(dossier: CompanyDossier) -> dict[str, set[str]]:
    expected: dict[str, set[str]] = {}
    for assertion in dossier.assertions:
        expected.setdefault(assertion.field, set()).add(canonical_json(assertion.value))
    for correction in dossier.corrections:
        expected[correction.field] = {canonical_json(correction.value)}
    return expected


def _correctness(
    result: EnrichmentResult,
    assertions: tuple[FieldAssertion, ...],
    dossier: CompanyDossier,
) -> float:
    fields = P0_ENRICHMENTS.get(result.enrichment_id)
    if fields is None:
        raise ValueError(f'unknown benchmark enrichment: {result.enrichment_id}')
    ground_truth = _ground_truth(dossier)
    scorable = tuple(field for field in fields if field in ground_truth)
    if not scorable:
        return 0.0
    actual: dict[str, set[str]] = {}
    for assertion in assertions:
        actual.setdefault(assertion.field, set()).add(canonical_json(assertion.value))
    correct = sum(
        bool(actual.get(field)) and actual[field].issubset(ground_truth[field])
        for field in scorable
    )
    return correct / len(scorable)


def score_result(
    result: EnrichmentResult,
    dossier: CompanyDossier,
    *,
    as_of: datetime,
    freshness_days: int,
    latency_ms: int = 0,
    model_cost_usd: Decimal | str = '0',
    source_cost_usd: Decimal | str = '0',
    cache_reused: bool = False,
) -> ScoreCard:
    if not isinstance(result, EnrichmentResult):
        raise ValueError('result must be an EnrichmentResult')
    if not isinstance(dossier, CompanyDossier):
        raise ValueError('dossier must be a CompanyDossier')
    if result.company_id != dossier.company_id:
        raise ValueError('result and dossier company IDs must match')
    if not isinstance(as_of, datetime) or as_of.tzinfo is None:
        raise ValueError('as_of must be a timezone-aware datetime')
    freshness_days = _non_negative_int('freshness_days', freshness_days)
    latency_ms = _non_negative_int('latency_ms', latency_ms)
    model_cost = _non_negative_decimal('model_cost_usd', model_cost_usd)
    source_cost = _non_negative_decimal('source_cost_usd', source_cost_usd)
    if not isinstance(cache_reused, bool):
        raise ValueError('cache_reused must be boolean')

    assertions = _assertions(result.output)
    result_evidence = {item.evidence_id: item for item in _evidence(result.output)}
    dossier_evidence = {item.evidence_id: item for item in dossier.evidence}
    valid_citations: list[EvidenceRef] = []
    citation_count = 0
    complete_assertions = 0
    for assertion in assertions:
        valid_for_assertion = []
        for evidence_id in assertion.evidence_ids:
            citation_count += 1
            observed = result_evidence.get(evidence_id)
            reference = dossier_evidence.get(evidence_id)
            if (
                observed is not None
                and reference is not None
                and observed.url == reference.url
                and observed.content_hash == reference.content_hash
            ):
                valid_for_assertion.append(observed)
                valid_citations.append(observed)
        complete_assertions += bool(valid_for_assertion)

    cutoff = as_of - timedelta(days=freshness_days)
    fresh_citations = sum(
        cutoff <= evidence.retrieved_at <= as_of for evidence in valid_citations
    )
    failure = result.failure.value if result.failure is not None else None
    return ScoreCard(
        company_id=result.company_id,
        result_status=result.status.value,
        failure=failure,
        requested_model_id=result.output.get('requested_model'),
        resolved_model_id=result.output.get('resolved_model'),
        correctness=_correctness(result, assertions, dossier),
        citation_validity=(len(valid_citations) / citation_count if citation_count else 0.0),
        citation_completeness=(
            complete_assertions / len(assertions) if assertions else 0.0
        ),
        citation_freshness=(
            fresh_citations / len(valid_citations) if valid_citations else 0.0
        ),
        latency_ms=latency_ms,
        model_cost_usd=model_cost,
        source_cost_usd=source_cost,
        cache_reused=cache_reused,
    )


class BenchmarkRunner:
    def __init__(self, artifact_root: Path = DEFAULT_ARTIFACT_ROOT) -> None:
        self.artifact_root = Path(artifact_root)

    def report_path(self, plan: ExperimentPlan) -> Path:
        return (
            self.artifact_root
            / plan.enrichment_id
            / plan.experiment_id
            / plan.execution_track.value
            / 'report.json'
        )

    def run(self, plan: ExperimentPlan) -> BenchmarkReport:
        if not isinstance(plan, ExperimentPlan):
            raise ValueError('plan must be an ExperimentPlan')
        scorecards = []
        resolved_model_ids: list[str | None] = []
        for case in plan.cases:
            requested_model = case.result.output.get('requested_model')
            if requested_model != plan.requested_model_id:
                raise ValueError('requested model ID does not match experiment plan')
            resolved_model = case.result.output.get('resolved_model')
            if resolved_model is not None and (
                not isinstance(resolved_model, str) or not resolved_model.strip()
            ):
                raise ValueError('resolved model ID must be non-empty text or null')
            if case.fully_cached and (
                case.source_purchases or case.source_cost_usd != Decimal('0')
            ):
                raise ValueError('cached evidence cannot repurchase sources')
            cache_misses = case.source_lookups - case.source_cache_hits
            if case.source_purchases > cache_misses:
                raise ValueError('source purchases cannot exceed cache misses')
            if resolved_model not in resolved_model_ids:
                resolved_model_ids.append(resolved_model)
            scorecards.append(score_result(
                case.result,
                case.dossier,
                as_of=case.as_of,
                freshness_days=plan.freshness_days,
                latency_ms=case.latency_ms,
                model_cost_usd=case.model_cost_usd,
                source_cost_usd=case.source_cost_usd,
                cache_reused=case.source_cache_hits > 0,
            ))
        report = BenchmarkReport(
            experiment_id=plan.experiment_id,
            enrichment_id=plan.enrichment_id,
            execution_track=plan.execution_track,
            requested_model_id=plan.requested_model_id,
            resolved_model_ids=tuple(resolved_model_ids),
            scorecards=tuple(scorecards),
            total_source_lookups=sum(case.source_lookups for case in plan.cases),
            total_source_cache_hits=sum(
                case.source_cache_hits for case in plan.cases
            ),
            total_source_purchases=sum(case.source_purchases for case in plan.cases),
        )
        path = self.report_path(plan)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('x', encoding='utf-8', newline='\n') as stream:
            stream.write(canonical_json(report.to_payload()) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        return report
