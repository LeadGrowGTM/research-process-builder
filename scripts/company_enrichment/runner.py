from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
import os
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Mapping, Protocol

from .budgets import BudgetExhausted, BudgetLedger, ReservationSettled
from ._locking import file_lock
from .contracts import (
    EnrichmentRequest, EnrichmentResult, FailureKind, ResultStatus,
    SellerContext, canonical_json,
)
from .evidence import EvidenceStore, SaturationTracker, SearchAngleResult, SourceRecord
from .executors import ExecutionOutput, P0_ENRICHMENTS, execute_p0
from .providers import ProviderRouter, RetryableFailure, normalize_failure


@dataclass(frozen=True, slots=True)
class AdapterResponse:
    source: SourceRecord
    actual_cost_usd: str = '0'
    dry_angles: tuple[str, ...] = ()
    unavailable_source_types: tuple[str, ...] = ()
    resolved_model: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.source, SourceRecord):
            raise ValueError('adapter response requires a SourceRecord')
        Decimal(self.actual_cost_usd)
        object.__setattr__(self, 'dry_angles', tuple(self.dry_angles))
        object.__setattr__(
            self, 'unavailable_source_types', tuple(self.unavailable_source_types),
        )


class SourceAdapter(Protocol):
    estimated_cost_usd: str
    resolved_model: str | None

    def collect(self, request: EnrichmentRequest, url: str) -> AdapterResponse: ...


class OutcomeJournal:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def append(self, result: EnrichmentResult) -> None:
        lock_path = self.path.with_suffix(self.path.suffix + '.lock')
        with file_lock(lock_path):
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open('a', encoding='utf-8', newline='\n') as stream:
                stream.write(canonical_json(result) + '\n')
                stream.flush()
                os.fsync(stream.fileno())

    def latest_success(
        self, enrichment_id: str, company_id: str,
    ) -> Mapping[str, Any] | None:
        if not self.path.exists():
            return None
        latest = None
        for line in self.path.read_text(encoding='utf-8').splitlines():
            event = __import__('json').loads(line)
            if (
                event.get('enrichment_id') == enrichment_id
                and event.get('company_id') == company_id
                and event.get('status') == ResultStatus.COMPLETE.value
            ):
                latest = event
        return MappingProxyType(latest) if latest is not None else None


class EnrichmentRunner:
    def __init__(
        self,
        *,
        definitions: Mapping[str, Any],
        discovery: Any,
        router: ProviderRouter,
        evidence_store: EvidenceStore,
        budget_ledger: BudgetLedger,
        adapters: Mapping[str, SourceAdapter],
        outcome_journal: Path,
        as_of: datetime,
        scope_id: str = 'corpus-build',
        executor: Callable[..., ExecutionOutput] = execute_p0,
        record_step: Callable[[str], None] | None = None,
    ) -> None:
        if as_of.tzinfo is None:
            raise ValueError('as_of must include a timezone')
        self._definitions = definitions
        self._discovery = discovery
        self._router = router
        self._evidence_store = evidence_store
        self._budget = budget_ledger
        self._adapters = adapters
        self._journal = OutcomeJournal(outcome_journal)
        self._as_of = as_of
        self._scope_id = scope_id
        self._executor = executor
        self._record_step = record_step or (lambda _step: None)
        self._response_metadata: dict[tuple[str, str], AdapterResponse] = {}

    def run(self, request: EnrichmentRequest) -> EnrichmentResult:
        try:
            self._record_step('validate')
            definition = self._validate_request(request)
            discovery = self._discovery.discover(
                request.enrichment_id, tuple(definition.fallback_order),
            )
            known_url = f"https://{request.inputs['domain']}"
            route = self._router.route(
                definition, discovery,
                known_urls=(known_url,)
                if 'homepage-scrape' in definition.fallback_order
                else (),
            )
            evidence = []
            cache_hits = 0
            tracker = SaturationTracker(P0_ENRICHMENTS[request.enrichment_id])
            prior = self._journal.latest_success(
                request.enrichment_id, request.company_id,
            )
            resolved_model = (
                prior.get('output', {}).get('resolved_model') if prior else None
            )
            for provider_id in route.provider_ids:
                adapter = self._adapters.get(provider_id)
                if adapter is None:
                    continue
                self._record_step('resolve')
                response_box: list[AdapterResponse] = []

                def collect() -> SourceRecord:
                    response = self._collect_with_retries(
                        adapter, request, known_url, provider_id,
                        int(definition.caps['retries']),
                    )
                    response_box.append(response)
                    return response.source

                reference, cache_hit = self._evidence_store.resolve(
                    url=known_url, provider=provider_id,
                    freshness_days=int(definition.freshness_days), as_of=self._as_of,
                    collect=collect,
                )
                cache_hits += int(cache_hit)
                evidence.append(reference)
                key = (known_url, provider_id)
                if response_box:
                    self._response_metadata[key] = response_box[0]
                metadata = self._response_metadata.get(key)
                source = self._evidence_store.get(reference.content_hash)
                tracker.observe(SearchAngleResult(
                    True, source_id=reference.evidence_id,
                    source_type=source.source_type,
                    field_citations=tuple(
                        (field, reference.evidence_id)
                        for field in P0_ENRICHMENTS[request.enrichment_id]
                    ),
                    unavailable_source_types=(
                        metadata.unavailable_source_types if metadata else ()
                    ),
                ))
                if metadata is None and cache_hit and prior is not None:
                    prior_output = prior.get('output', {})
                    if prior_output.get('saturated') is True:
                        tracker.observe(SearchAngleResult(
                            False, angle_id='cached-saturation-angle-a',
                            unavailable_source_types=('independent',),
                        ))
                        tracker.observe(SearchAngleResult(
                            False, angle_id='cached-saturation-angle-b',
                        ))
                if metadata:
                    resolved_model = metadata.resolved_model or resolved_model
                    for angle in metadata.dry_angles:
                        tracker.observe(SearchAngleResult(False, angle_id=angle))
                resolved_model = resolved_model or getattr(adapter, 'resolved_model', None)

            if not evidence:
                raise ValueError('eligible route produced no evidence')
            self._record_step('execute')
            execution = self._executor(
                request.enrichment_id, tuple(evidence),
                seller_context=request.inputs.get('seller_context'),
                output_visibility=definition.output_visibility,
            )
            self._record_step('validate_output')
            self._validate_output(execution, evidence)
            saturated = tracker.is_saturated
            result = EnrichmentResult(
                request.enrichment_id, request.company_id,
                definition.output_schema_version,
                ResultStatus.COMPLETE if saturated else ResultStatus.PARTIAL,
                {
                    'assertions': execution.assertions,
                    'evidence': tuple(evidence),
                    'unknowns': execution.unknowns,
                    'cache_hits': cache_hits,
                    'saturated': saturated,
                    'requested_model': request.inputs.get('requested_model'),
                    'resolved_model': resolved_model,
                },
                None if saturated else FailureKind.INSUFFICIENT_EVIDENCE,
            )
        except Exception as error:
            result = self._failure_result(request, error)
        self._record(result)
        return result

    def _validate_request(self, request: EnrichmentRequest) -> Any:
        try:
            definition = self._definitions[request.enrichment_id]
        except KeyError as error:
            raise ValueError('unknown enrichment definition') from error
        missing = [name for name in definition.required_inputs if name not in request.inputs]
        if missing:
            raise ValueError(f'missing required inputs: {", ".join(missing)}')
        if request.enrichment_id not in P0_ENRICHMENTS:
            raise ValueError('definition has no typed P0 executor')
        return definition

    def _collect_with_retries(
        self, adapter: SourceAdapter, request: EnrichmentRequest, url: str,
        provider_id: str, retry_cap: int,
    ) -> AdapterResponse:
        last_error: Exception | None = None
        for attempt in range(retry_cap + 1):
            reservation = None
            try:
                estimate = str(getattr(adapter, 'estimated_cost_usd', '0'))
                if Decimal(estimate) > 0:
                    reservation = self._budget.reserve(
                        self._scope_id,
                        f'{request.company_id}:{request.enrichment_id}:'
                        f'{provider_id}:{url}:attempt-{attempt}',
                        estimate,
                    )
                    if not reservation.should_execute:
                        raise RuntimeError('paid reservation is already owned')
                response = adapter.collect(request, url)
                if response.source.provider != provider_id:
                    raise ValueError('adapter source provider does not match route')
                if reservation is not None:
                    self._budget.reconcile(reservation, response.actual_cost_usd)
                return response
            except RetryableFailure as error:
                last_error = error
                if reservation is not None and reservation.created:
                    self._budget.release(reservation)
            except Exception:
                if reservation is not None and reservation.created:
                    self._budget.release(reservation)
                raise
        assert last_error is not None
        raise last_error

    @staticmethod
    def _validate_output(execution: ExecutionOutput, evidence: list[Any]) -> None:
        if not isinstance(execution, ExecutionOutput):
            raise ValueError('executor returned an invalid typed output')
        if not execution.assertions:
            raise ValueError('executor returned no typed assertions')
        evidence_ids = {item.evidence_id for item in evidence}
        if any(not item.evidence_ids for item in execution.assertions):
            raise ValueError('output assertions require citations')
        if any(set(item.evidence_ids) - evidence_ids for item in execution.assertions):
            raise ValueError('output assertion cites unknown evidence')

    def _failure_result(
        self, request: EnrichmentRequest, error: Exception,
    ) -> EnrichmentResult:
        if isinstance(error, (ValueError, TypeError)):
            kind = FailureKind.CONTRACT_INVALID
        elif isinstance(error, (BudgetExhausted, ReservationSettled)):
            kind = FailureKind.BUDGET_EXHAUSTED
        else:
            kind = normalize_failure(error).kind
        return EnrichmentResult(
            request.enrichment_id, request.company_id, '1.0', ResultStatus.FAILED,
            {'error': str(error)}, kind,
        )

    def _record(self, result: EnrichmentResult) -> None:
        self._record_step('record')
        self._journal.append(result)
