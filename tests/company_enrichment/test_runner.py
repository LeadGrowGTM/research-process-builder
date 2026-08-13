from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.company_enrichment.budgets import BudgetLedger, Reservation
from scripts.company_enrichment.contracts import (
    EnrichmentRequest, FailureKind, FieldAssertion, ResultStatus, SellerContext,
    Visibility,
)
from scripts.company_enrichment.discovery import DiscoveryRecord, ProbeResult, ProbeStatus
from scripts.company_enrichment.evidence import EvidenceStore, SourceRecord
from scripts.company_enrichment.executors import ExecutionOutput
from scripts.company_enrichment.providers import RetryableFailure
from scripts.company_enrichment.providers import ProviderRouter
from scripts.company_enrichment.runner import AdapterResponse, EnrichmentRunner


NOW = datetime(2026, 8, 12, tzinfo=timezone.utc)


def _context() -> SellerContext:
    return SellerContext(
        'B2B SaaS', ('VP Sales',), ('research',), 'Pipeline Sprint', '30 days',
        'more pipeline', ('case study',), 'pilot', ('consumer',), 'invest in growth',
    )


def _definition(**overrides):
    values = dict(
        id='company-description', output_schema_version='1.0',
        required_inputs=('company_name', 'domain'),
        fallback_order=('homepage-scrape',), freshness_days=30,
        caps={'retries': 2}, output_visibility='message_safe',
    )
    values.update(overrides)
    return SimpleNamespace(**values)


class FakeDiscovery:
    def __init__(self, events, *, fail=False):
        self.events = events
        self.fail = fail

    def discover(self, enrichment_id, fallback_order, *, operation=None):
        self.events.append('discover')
        if self.fail:
            raise RuntimeError('verified capability gap')
        probe = ProbeResult('probe', ProbeStatus.AVAILABLE)
        return DiscoveryRecord(
            enrichment_id, ('gtm', 'nexus', 'select'), probe, probe,
            'homepage-scrape', ('homepage-scrape',), 'selected',
            'C:/gtm', '2.1.0', enrichment_id, 'available',
        )


class FakeRouter:
    def __init__(self, events):
        self.events = events

    def route(self, definition, discovery, *, known_urls=()):
        self.events.append('route')
        return SimpleNamespace(provider_ids=('homepage-scrape',))


class FakeAdapter:
    estimated_cost_usd = '0'

    def __init__(self, events, *, failures=0, resolved_model='openai/gpt-5-mini'):
        self.events = events
        self.failures = failures
        self.calls = 0
        self.resolved_model = resolved_model

    def collect(self, request, url):
        self.calls += 1
        self.events.append('collect')
        if self.calls <= self.failures:
            raise RetryableFailure('try again')
        return AdapterResponse(
            SourceRecord(url, NOW, 'first_party', 'homepage-scrape',
                         'Acme builds workflow software.', 'Workflow software', 30, '0'),
            actual_cost_usd='0', dry_angles=('angle-a', 'angle-b'),
            unavailable_source_types=('independent',),
            resolved_model=self.resolved_model,
        )


def _runner(tmp_path: Path, events: list[str], adapter=None, discovery=None):
    return EnrichmentRunner(
        definitions={'company-description': _definition()},
        discovery=discovery or FakeDiscovery(events), router=FakeRouter(events),
        evidence_store=EvidenceStore(tmp_path / 'evidence'),
        budget_ledger=BudgetLedger(tmp_path / 'budget.jsonl', {'corpus-build': '2'}),
        adapters={'homepage-scrape': adapter or FakeAdapter(events)},
        outcome_journal=tmp_path / 'outcomes.jsonl', as_of=NOW,
        record_step=events.append,
    )


def _request(**inputs):
    values = {'company_name': 'Acme', 'domain': 'acme.example',
              'seller_context': _context(), 'requested_model': 'openai/gpt-5-mini'}
    values.update(inputs)
    return EnrichmentRequest('company-description', 'saas-01', '1.0', values)


def test_runner_uses_exact_order_and_records_append_only_outcome(tmp_path: Path) -> None:
    events = []
    result = _runner(tmp_path, events).run(_request())
    assert events == ['validate', 'discover', 'route', 'resolve', 'collect',
                      'execute', 'validate_output', 'record']
    assert result.status is ResultStatus.COMPLETE
    events_json = [json.loads(line) for line in
                   (tmp_path / 'outcomes.jsonl').read_text().splitlines()]
    assert len(events_json) == 1
    assert events_json[0]['output']['discovery']['selected_capability'] == 'homepage-scrape'
    assert events_json[0]['output']['route']['provider_ids'] == ['homepage-scrape']
    assert events_json[0]['output']['requested_model'] == 'openai/gpt-5-mini'
    assert events_json[0]['output']['resolved_model'] == 'openai/gpt-5-mini'


def test_material_resolution_requires_matching_retained_cited_assertion(
    tmp_path: Path,
) -> None:
    class MaterialAdapter(FakeAdapter):
        def collect(self, request, url):
            return AdapterResponse(
                SourceRecord(
                    url, NOW, 'first_party', 'homepage-scrape',
                    'Acme publishes pricing.', 'Acme pricing', 30, '0',
                ),
                dry_angles=('technology query',),
                resolved_material_angles=(('pricing query', 'pricing', url),),
                unavailable_source_types=('independent',),
            )

    def execute(field):
        def executor(_enrichment_id, evidence, **_kwargs):
            other = 'technology' if field == 'pricing' else 'pricing'
            return ExecutionOutput((FieldAssertion(
                field, 'supported value', (evidence[0].evidence_id,),
                0.9, Visibility.MESSAGE_SAFE,
            ),), (other,))
        return executor

    def build(path, field):
        events = []
        return EnrichmentRunner(
            definitions={'analogy-value-translator': _definition(
                id='analogy-value-translator',
            )},
            discovery=FakeDiscovery(events), router=FakeRouter(events),
            evidence_store=EvidenceStore(path / 'evidence'),
            budget_ledger=BudgetLedger(
                path / 'budget.jsonl', {'corpus-build': '2'},
            ),
            adapters={'homepage-scrape': MaterialAdapter(events)},
            outcome_journal=path / 'outcomes.jsonl', as_of=NOW,
            executor=execute(field),
        )

    request = EnrichmentRequest(
        'analogy-value-translator', 'acme', '1.0',
        {'company_name': 'Acme', 'domain': 'acme.example',
         'seller_context': _context()},
    )
    complete = build(tmp_path / 'matched', 'pricing').run(request)
    assert complete.status is ResultStatus.COMPLETE
    assert complete.output['resolved_material_angles'] == ('pricing query',)

    mismatched = build(tmp_path / 'mismatched', 'technology').run(request)
    assert mismatched.status is ResultStatus.FAILED
    assert 'matching retained cited assertion' in mismatched.output['error']


def test_collected_metadata_is_scoped_to_company_and_enrichment_on_cache_hit(
    tmp_path: Path,
) -> None:
    events = []
    class ScopedAdapter(FakeAdapter):
        def collect(self, request, url):
            self.calls += 1
            return AdapterResponse(
                SourceRecord(
                    url, NOW, 'first_party', 'homepage-scrape',
                    'Acme publishes workflow software for businesses.',
                    'Acme workflow software', 30, '0',
                ),
                dry_angles=('description-a', 'description-b'),
                unavailable_source_types=('independent',),
            )
        def research_metadata(self, request):
            return AdapterResponse(
                SourceRecord(
                    'https://acme.example', NOW, 'first_party',
                    'homepage-scrape', 'metadata', 'metadata', 30, '0',
                ),
                dry_angles=('pricing-a', 'pricing-b'),
                unavailable_source_types=('independent',),
            )
    adapter = ScopedAdapter(events)
    definitions = {
        enrichment_id: _definition(id=enrichment_id)
        for enrichment_id in ('company-description', 'analogy-value-translator')
    }
    runner = EnrichmentRunner(
        definitions=definitions, discovery=FakeDiscovery(events),
        router=FakeRouter(events),
        evidence_store=EvidenceStore(tmp_path / 'evidence'),
        budget_ledger=BudgetLedger(tmp_path / 'budget.jsonl', {'corpus-build': '2'}),
        adapters={'homepage-scrape': adapter},
        outcome_journal=tmp_path / 'outcomes.jsonl', as_of=NOW,
    )
    assert runner.run(_request()).status is ResultStatus.COMPLETE
    second = runner.run(EnrichmentRequest(
        'analogy-value-translator', 'saas-01', '1.0',
        {'company_name': 'Acme', 'domain': 'acme.example',
         'seller_context': _context()},
    ))
    assert second.status is ResultStatus.COMPLETE
    assert second.output['dry_angles'] == ('pricing-a', 'pricing-b')
    assert adapter.calls == 1


def test_runner_uses_adapter_owned_source_url_for_cache_identity(tmp_path: Path) -> None:
    events = []
    adapter = FakeAdapter(events)
    adapter.source_url = lambda request, default: 'https://independent.example/profile'
    runner = _runner(tmp_path, events, adapter)
    result = runner.run(_request())
    assert result.output['evidence'][0].url == 'https://independent.example/profile'


def test_discovery_occurs_on_cache_hits_and_resume_skips_collection(tmp_path: Path) -> None:
    events = []
    adapter = FakeAdapter(events)
    runner = _runner(tmp_path, events, adapter)
    first = runner.run(_request())
    events.clear()
    second = runner.run(_request())
    assert adapter.calls == 1
    assert events == ['validate', 'discover', 'route', 'resolve',
                      'execute', 'validate_output', 'record']
    assert second.output['evidence'] == first.output['evidence']
    assert second.output['cache_hits'] == 1
    assert len((tmp_path / 'outcomes.jsonl').read_text().splitlines()) == 2


def test_fresh_runner_resumes_saturated_cached_result_without_collection(
    tmp_path: Path,
) -> None:
    events = []
    adapter = FakeAdapter(events)
    first = _runner(tmp_path, events, adapter).run(_request())
    events.clear()
    resumed = _runner(tmp_path, events, adapter).run(_request())
    assert adapter.calls == 1
    assert first.status is ResultStatus.COMPLETE
    assert resumed.status is ResultStatus.COMPLETE
    assert resumed.output['cache_hits'] == 1


def test_discovery_failure_is_recorded_as_failed_outcome(tmp_path: Path) -> None:
    events = []
    result = _runner(tmp_path, events, discovery=FakeDiscovery(events, fail=True)).run(
        _request()
    )
    assert result.status is ResultStatus.FAILED
    assert result.failure is FailureKind.TERMINAL
    assert events == ['validate', 'discover', 'record']
    payload = json.loads((tmp_path / 'outcomes.jsonl').read_text())
    assert payload['output']['discovery']['selection_outcome'] == 'discovery_failed'
    assert payload['output']['requested_model'] == 'openai/gpt-5-mini'
    assert payload['output']['resolved_model'] is None


def test_partial_and_cache_hit_journals_preserve_discovery_and_route(tmp_path: Path) -> None:
    events = []
    adapter = FakeAdapter(events)
    adapter.collect = lambda request, url: AdapterResponse(
        SourceRecord(url, NOW, 'first_party', 'homepage-scrape', 'content', 'excerpt', 30, '0')
    )
    runner = _runner(tmp_path, events, adapter)
    assert runner.run(_request()).status is ResultStatus.PARTIAL
    assert runner.run(_request()).status is ResultStatus.PARTIAL
    payloads = [json.loads(line) for line in
                (tmp_path / 'outcomes.jsonl').read_text().splitlines()]
    assert payloads[0]['output']['discovery']['selection_outcome'] == 'selected'
    assert payloads[1]['output']['cache_hits'] == 1
    assert payloads[1]['output']['route']['provider_ids'] == ['homepage-scrape']
    assert payloads[1]['output']['resolved_model'] is None


def test_runner_retries_only_to_definition_cap(tmp_path: Path) -> None:
    events = []
    adapter = FakeAdapter(events, failures=3)
    result = _runner(tmp_path, events, adapter).run(_request())
    assert adapter.calls == 3
    assert result.status is ResultStatus.FAILED
    assert result.failure is FailureKind.RETRYABLE


def test_runner_reports_partial_when_source_saturation_is_not_met(tmp_path: Path) -> None:
    events = []
    adapter = FakeAdapter(events)
    adapter.collect = lambda request, url: AdapterResponse(
        SourceRecord(url, NOW, 'first_party', 'homepage-scrape', 'content', 'excerpt', 30, '0')
    )
    result = _runner(tmp_path, events, adapter).run(_request())
    assert result.status is ResultStatus.PARTIAL
    assert result.failure is FailureKind.INSUFFICIENT_EVIDENCE


def test_runner_preserves_exact_requested_and_resolved_model_ids(tmp_path: Path) -> None:
    result = _runner(tmp_path, [], FakeAdapter([], resolved_model='openai/gpt-5.1-mini')).run(
        _request(requested_model='openai/gpt-5-mini')
    )
    assert result.output['requested_model'] == 'openai/gpt-5-mini'
    assert result.output['resolved_model'] == 'openai/gpt-5.1-mini'


def test_paid_collection_executes_only_for_owned_reservation(tmp_path: Path) -> None:
    events = []
    adapter = FakeAdapter(events)
    adapter.estimated_cost_usd = '0.25'
    ledger = BudgetLedger(tmp_path / 'budget.jsonl', {'corpus-build': '2'})
    runner = EnrichmentRunner(
        definitions={'company-description': _definition()}, discovery=FakeDiscovery(events),
        router=FakeRouter(events), evidence_store=EvidenceStore(tmp_path / 'evidence'),
        budget_ledger=ledger, adapters={'homepage-scrape': adapter},
        outcome_journal=tmp_path / 'outcomes.jsonl', as_of=NOW,
    )
    result = runner.run(_request())
    assert result.status is ResultStatus.COMPLETE
    assert ledger.spent('corpus-build') == Decimal('0')
    assert adapter.calls == 1


def test_runner_rejects_adapter_result_with_wrong_provider(tmp_path: Path) -> None:
    events = []
    adapter = FakeAdapter(events)
    adapter.collect = lambda request, url: AdapterResponse(
        SourceRecord(url, NOW, 'first_party', 'other', 'content', 'excerpt', 30, '0')
    )
    result = _runner(tmp_path, events, adapter).run(_request())
    assert result.status is ResultStatus.FAILED
    assert result.failure is FailureKind.CONTRACT_INVALID


def test_runner_rejects_empty_typed_executor_output(tmp_path: Path) -> None:
    events = []
    runner = EnrichmentRunner(
        definitions={'company-description': _definition()},
        discovery=FakeDiscovery(events), router=FakeRouter(events),
        evidence_store=EvidenceStore(tmp_path / 'evidence'),
        budget_ledger=BudgetLedger(tmp_path / 'budget.jsonl', {'corpus-build': '2'}),
        adapters={'homepage-scrape': FakeAdapter(events)},
        outcome_journal=tmp_path / 'outcomes.jsonl', as_of=NOW,
        executor=lambda *args, **kwargs: ExecutionOutput(()),
    )
    result = runner.run(_request())
    assert result.status is ResultStatus.FAILED
    assert result.failure is FailureKind.CONTRACT_INVALID


@pytest.mark.parametrize(
    'execution',
    (
        ExecutionOutput((FieldAssertion(
            'competitors', 'Wrong field', ('ev-placeholder',), .8,
            Visibility.MESSAGE_SAFE,
        ),)),
        ExecutionOutput((FieldAssertion(
            'identity', 'Filter value', ('ev-placeholder',), .8,
            Visibility.FILTER_ONLY,
        ),)),
        ExecutionOutput((FieldAssertion(
            'identity', 'Only identity', ('ev-placeholder',), .8,
            Visibility.MESSAGE_SAFE,
        ),)),
    ),
)
def test_runner_rejects_wrong_visibility_or_incomplete_field_coverage(
    tmp_path: Path, execution: ExecutionOutput,
) -> None:
    def executor(_enrichment_id, evidence, **_kwargs):
        return ExecutionOutput(tuple(
            FieldAssertion(item.field, item.value, (evidence[0].evidence_id,),
                           item.confidence, item.visibility)
            for item in execution.assertions
        ))
    runner = EnrichmentRunner(
        definitions={'company-description': _definition()},
        discovery=FakeDiscovery([]), router=FakeRouter([]),
        evidence_store=EvidenceStore(tmp_path / 'evidence'),
        budget_ledger=BudgetLedger(tmp_path / 'budget.jsonl', {'corpus-build': '2'}),
        adapters={'homepage-scrape': FakeAdapter([])},
        outcome_journal=tmp_path / 'outcomes.jsonl', as_of=NOW,
        executor=executor,
    )
    result = runner.run(_request())
    assert result.status is ResultStatus.FAILED
    assert result.failure is FailureKind.CONTRACT_INVALID


def test_explicit_unknowns_cover_allowed_fields_without_fabricated_citations(
    tmp_path: Path,
) -> None:
    def executor(_enrichment_id, evidence, **_kwargs):
        return ExecutionOutput((FieldAssertion(
            'identity', 'Acme', (evidence[0].evidence_id,), .8,
            Visibility.MESSAGE_SAFE,
        ),), ('description', 'offers'))
    result = EnrichmentRunner(
        definitions={'company-description': _definition()},
        discovery=FakeDiscovery([]), router=FakeRouter([]),
        evidence_store=EvidenceStore(tmp_path / 'evidence'),
        budget_ledger=BudgetLedger(tmp_path / 'budget.jsonl', {'corpus-build': '2'}),
        adapters={'homepage-scrape': FakeAdapter([])},
        outcome_journal=tmp_path / 'outcomes.jsonl', as_of=NOW,
        executor=executor,
    ).run(_request())
    assert result.status is ResultStatus.COMPLETE
    assert [item.field for item in result.output['assertions']] == ['identity']


@pytest.mark.parametrize('enrichment_id', sorted(__import__(
    'scripts.company_enrichment.executors', fromlist=['P0_ENRICHMENTS']
).P0_ENRICHMENTS))
def test_runner_accepts_all_unknown_typed_output_without_fabricated_assertions(
    tmp_path: Path, enrichment_id: str,
) -> None:
    from scripts.company_enrichment.executors import P0_ENRICHMENTS

    definition = _definition(id=enrichment_id)
    if enrichment_id == 'analogy-value-translator':
        definition = _definition(id=enrichment_id, fallback_order=('homepage-scrape',))
    runner = EnrichmentRunner(
        definitions={enrichment_id: definition},
        discovery=FakeDiscovery([]), router=FakeRouter([]),
        evidence_store=EvidenceStore(tmp_path / 'evidence'),
        budget_ledger=BudgetLedger(tmp_path / 'budget.jsonl', {'corpus-build': '2'}),
        adapters={'homepage-scrape': FakeAdapter([])},
        outcome_journal=tmp_path / 'outcomes.jsonl', as_of=NOW,
        executor=lambda *args, **kwargs: ExecutionOutput(
            (), P0_ENRICHMENTS[enrichment_id],
        ),
    )
    result = runner.run(EnrichmentRequest(
        enrichment_id, 'saas-01', '1.0',
        {'company_name': 'Acme', 'domain': 'acme.example',
         'seller_context': _context()},
    ))
    assert result.status is ResultStatus.COMPLETE
    assert result.output['assertions'] == ()
    assert result.output['unknowns'] == P0_ENRICHMENTS[enrichment_id]


def test_request_validation_failure_is_not_labeled_discovery_failure(
    tmp_path: Path,
) -> None:
    result = _runner(tmp_path, []).run(EnrichmentRequest(
        'company-description', 'saas-01', '1.0',
        {'company_name': 'Acme', 'seller_context': _context()},
    ))
    assert result.status is ResultStatus.FAILED
    payload = json.loads((tmp_path / 'outcomes.jsonl').read_text())
    assert payload['output']['discovery']['selection_outcome'] == 'validation_failed'
    assert payload['output']['route']['provider_ids'] == []


def test_runner_never_executes_paid_work_owned_by_another_run(tmp_path: Path) -> None:
    events = []
    adapter = FakeAdapter(events)
    adapter.estimated_cost_usd = '0.25'
    ledger = BudgetLedger(tmp_path / 'budget.jsonl', {'corpus-build': '2'})
    ledger.reserve(
        'corpus-build',
        'saas-01:company-description:homepage-scrape:https://acme.example:attempt-0',
        '0.25',
    )
    runner = EnrichmentRunner(
        definitions={'company-description': _definition()},
        discovery=FakeDiscovery(events), router=FakeRouter(events),
        evidence_store=EvidenceStore(tmp_path / 'evidence'), budget_ledger=ledger,
        adapters={'homepage-scrape': adapter},
        outcome_journal=tmp_path / 'outcomes.jsonl', as_of=NOW,
    )
    result = runner.run(_request())
    assert adapter.calls == 0
    assert result.status is ResultStatus.FAILED


def test_model_only_enrichment_does_not_force_a_known_url_route(tmp_path: Path) -> None:
    events = []
    definition = _definition(
        id='analogy-value-translator',
        required_inputs=('company_name', 'domain', 'seller_context'),
        fallback_order=('model-router',),
    )

    class ModelDiscovery:
        def discover(self, enrichment_id, fallback_order, *, operation=None):
            return SimpleNamespace(
                selected_capability='model-router',
                eligible_capabilities=('model-router',),
            )

    class ModelAdapter(FakeAdapter):
        def collect(self, request, url):
            self.calls += 1
            return AdapterResponse(
                SourceRecord(url, NOW, 'first_party', 'model-router',
                             'Cited dossier', 'Cited dossier', 30, '0'),
                dry_angles=('angle-a', 'angle-b'),
                unavailable_source_types=('independent',),
                resolved_model=self.resolved_model,
            )

    result = EnrichmentRunner(
        definitions={'analogy-value-translator': definition},
        discovery=ModelDiscovery(), router=ProviderRouter(),
        evidence_store=EvidenceStore(tmp_path / 'evidence'),
        budget_ledger=BudgetLedger(tmp_path / 'budget.jsonl', {'corpus-build': '2'}),
        adapters={'model-router': ModelAdapter(events)},
        outcome_journal=tmp_path / 'outcomes.jsonl', as_of=NOW,
    ).run(EnrichmentRequest(
        'analogy-value-translator', 'saas-01', '1.0',
        {'company_name': 'Acme', 'domain': 'acme.example',
         'seller_context': _context(), 'requested_model': 'openai/gpt-5-mini'},
    ))
    assert result.status is ResultStatus.COMPLETE
