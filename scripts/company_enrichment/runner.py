from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
import os
import json
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
    resolved_material_angles: tuple[tuple[str, str, str], ...] = ()
    unavailable_source_types: tuple[str, ...] = ()
    resolved_model: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.source, SourceRecord):
            raise ValueError('adapter response requires a SourceRecord')
        Decimal(self.actual_cost_usd)
        object.__setattr__(self, 'dry_angles', tuple(self.dry_angles))
        resolutions = tuple(tuple(item) for item in self.resolved_material_angles)
        if any(
            len(item) != 3 or any(not isinstance(value, str) or not value.strip()
                                  for value in item)
            for item in resolutions
        ):
            raise ValueError('resolved material angles require query, field, and URL')
        object.__setattr__(self, 'resolved_material_angles', resolutions)
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
        self._response_metadata: dict[tuple[str, str, str, str], AdapterResponse] = {}

    def run(self, request: EnrichmentRequest) -> EnrichmentResult:
        requested_model = request.inputs.get('requested_model')
        resolved_model = None
        discovery_output: dict[str, Any] = {
            'selected_capability': None,
            'eligible_capabilities': (),
            'selection_outcome': 'not_run',
        }
        route_output: dict[str, Any] = {'provider_ids': ()}
        validation_complete = False
        try:
            self._record_step('validate')
            definition = self._validate_request(request)
            validation_complete = True
            discovery = self._discovery.discover(
                request.enrichment_id, tuple(definition.fallback_order),
            )
            discovery_output = self._discovery_output(discovery)
            known_url = f"https://{request.inputs['domain']}"
            route = self._router.route(
                definition, discovery,
                known_urls=(known_url,)
                if 'homepage-scrape' in definition.fallback_order
                else (),
            )
            route_output = {'provider_ids': tuple(route.provider_ids)}
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
                source_url = (
                    adapter.source_url(request, known_url)
                    if callable(getattr(adapter, 'source_url', None))
                    else known_url
                )
                self._record_step('resolve')
                response_box: list[AdapterResponse] = []

                def collect() -> SourceRecord:
                    response = self._collect_with_retries(
                        adapter, request, source_url, provider_id,
                        int(definition.caps['retries']),
                    )
                    response_box.append(response)
                    return response.source

                reference, cache_hit = self._evidence_store.resolve(
                    url=source_url, provider=provider_id,
                    freshness_days=int(definition.freshness_days), as_of=self._as_of,
                    collect=collect,
                )
                cache_hits += int(cache_hit)
                evidence.append(reference)
                key = (
                    request.company_id, request.enrichment_id,
                    source_url, provider_id,
                )
                if response_box:
                    self._response_metadata[key] = response_box[0]
                metadata = self._response_metadata.get(key)
                if metadata is None and cache_hit and callable(
                    getattr(adapter, 'cached_metadata', None)
                ):
                    metadata = adapter.cached_metadata()
                if metadata is None and cache_hit and callable(
                    getattr(adapter, 'research_metadata', None)
                ):
                    metadata = adapter.research_metadata(request)
                source = self._evidence_store.get(reference.content_hash)
                tracker.observe(SearchAngleResult(
                    True, source_id=reference.evidence_id,
                    source_type=source.source_type,
                    unavailable_source_types=(
                        metadata.unavailable_source_types if metadata else ()
                    ),
                ))
                if metadata is None and cache_hit and prior is not None:
                    prior_output = prior.get('output', {})
                    for angle in prior_output.get('dry_angles', ()):
                        tracker.observe(SearchAngleResult(
                            False, angle_id=angle,
                            unavailable_source_types=tuple(
                                prior_output.get('unavailable_source_types', ())
                            ),
                        ))
                if metadata:
                    resolved_model = metadata.resolved_model or resolved_model
                    for angle in metadata.dry_angles:
                        tracker.observe(SearchAngleResult(False, angle_id=angle))

            if not evidence:
                raise ValueError('eligible route produced no evidence')
            self._record_step('execute')
            execution = self._executor(
                request.enrichment_id, tuple(evidence),
                seller_context=request.inputs.get('seller_context'),
                output_visibility=definition.output_visibility,
            )
            self._record_step('validate_output')
            self._validate_output(
                request.enrichment_id, execution, evidence,
                definition.output_visibility,
            )
            for assertion in execution.assertions:
                tracker.observe(SearchAngleResult(
                    True,
                    field_citations=tuple(
                        (assertion.field, evidence_id)
                        for evidence_id in assertion.evidence_ids
                    ),
                ))
            for field in execution.unknowns:
                tracker.observe(SearchAngleResult(
                    True, field_citations=((field, f'unknown:{field}'),),
                ))
            resolved_material_angles = (
                metadata.resolved_material_angles
                if 'metadata' in locals() and metadata else ()
            )
            evidence_by_url = {item.url: item.evidence_id for item in evidence}
            for angle, field, source_url in resolved_material_angles:
                evidence_id = evidence_by_url.get(source_url)
                if evidence_id is None or not any(
                    assertion.field == field
                    and evidence_id in assertion.evidence_ids
                    for assertion in execution.assertions
                ):
                    raise ValueError(
                        'material search resolution requires a matching '
                        'retained cited assertion'
                    )
                tracker.observe(SearchAngleResult(False, angle_id=angle))
            dry_angles = (
                metadata.dry_angles if 'metadata' in locals() and metadata
                else tuple(prior.get('output', {}).get('dry_angles', ())) if prior
                else ()
            )
            unavailable_source_types = (
                metadata.unavailable_source_types
                if 'metadata' in locals() and metadata
                else tuple(prior.get('output', {}).get('unavailable_source_types', ()))
                if prior else ()
            )
            for index, angle in enumerate(dry_angles):
                tracker.observe(SearchAngleResult(
                    False, angle_id=angle,
                    unavailable_source_types=(
                        unavailable_source_types if index == 0 else ()
                    ),
                ))
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
                    'discovery': discovery_output,
                    'route': route_output,
                    'dry_angles': dry_angles,
                    'resolved_material_angles': tuple(
                        item[0] for item in resolved_material_angles
                    ),
                    'unavailable_source_types': unavailable_source_types,
                    'requested_model': requested_model,
                    'resolved_model': resolved_model,
                },
                None if saturated else FailureKind.INSUFFICIENT_EVIDENCE,
            )
        except Exception as error:
            if discovery_output['selection_outcome'] == 'not_run':
                discovery_output['selection_outcome'] = (
                    'discovery_failed' if validation_complete else 'validation_failed'
                )
            result = self._failure_result(
                request, error, discovery_output, route_output,
                requested_model, resolved_model,
            )
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
    def _validate_output(
        enrichment_id: str, execution: ExecutionOutput, evidence: list[Any],
        output_visibility: str,
    ) -> None:
        if not isinstance(execution, ExecutionOutput):
            raise ValueError('executor returned an invalid typed output')
        if not execution.assertions and not execution.unknowns:
            raise ValueError('executor returned no covered fields')
        evidence_ids = {item.evidence_id for item in evidence}
        if any(not item.evidence_ids for item in execution.assertions):
            raise ValueError('output assertions require citations')
        if any(set(item.evidence_ids) - evidence_ids for item in execution.assertions):
            raise ValueError('output assertion cites unknown evidence')
        allowed = frozenset(P0_ENRICHMENTS[enrichment_id])
        asserted = {item.field for item in execution.assertions}
        unknowns = set(execution.unknowns)
        outside = (asserted | unknowns) - allowed
        if outside:
            raise ValueError(f'output contains fields outside enrichment scope: {sorted(outside)}')
        if asserted & unknowns:
            raise ValueError('a field cannot be asserted and unknown')
        missing = allowed - asserted - unknowns
        if missing:
            raise ValueError(f'output omits required enrichment fields: {sorted(missing)}')
        if output_visibility == 'message_safe' and any(
            item.visibility.value != 'message_safe' for item in execution.assertions
        ):
            raise ValueError('message-safe output cannot contain filter-only assertions')

    def _failure_result(
        self, request: EnrichmentRequest, error: Exception,
        discovery_output: Mapping[str, Any], route_output: Mapping[str, Any],
        requested_model: Any, resolved_model: Any,
    ) -> EnrichmentResult:
        if isinstance(error, (ValueError, TypeError)):
            kind = FailureKind.CONTRACT_INVALID
        elif isinstance(error, (BudgetExhausted, ReservationSettled)):
            kind = FailureKind.BUDGET_EXHAUSTED
        else:
            kind = normalize_failure(error).kind
        return EnrichmentResult(
            request.enrichment_id, request.company_id, '1.0', ResultStatus.FAILED,
            {
                'error': str(error), 'discovery': discovery_output,
                'route': route_output, 'requested_model': requested_model,
                'resolved_model': resolved_model,
            }, kind,
        )

    @staticmethod
    def _discovery_output(discovery: Any) -> dict[str, Any]:
        try:
            normalized = json.loads(canonical_json(discovery))
        except (TypeError, ValueError):
            normalized = {}
        normalized.update({
            'selected_capability': discovery.selected_capability,
            'eligible_capabilities': tuple(discovery.eligible_capabilities),
            'selection_outcome': getattr(discovery, 'selection_outcome', 'selected'),
        })
        return normalized

    def _record(self, result: EnrichmentResult) -> None:
        self._record_step('record')
        self._journal.append(result)
