import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest
import yaml

from scripts.company_enrichment.cli import (
    CORPUS_PAID_CAP_USD, CallLedger, HttpSourceClient, ResearchSource, SearchOutcome,
    SearchHit, _LiveAdapter, _dossier_unknown_reasons, _discover_source_specs,
    _dry_queries,
    _load_source_plan, _rehydrate_dossier, _stage_fixtures,
    _validate_search_provenance,
    _validate_source_pack, main,
)
from scripts.company_enrichment.evidence import EvidenceStore, SourceRecord


QUALIFICATION_TEXT = {
    'saas-01': 'AgencyAnalytics provides reporting software and custom dashboards for marketing agencies.',
    'saas-04': 'aPriori provides product cost, manufacturability, and carbon footprint software for manufacturers and product designers.',
    'saas-07': 'Betterworks provides performance management and talent intelligence for HR and business leaders.',
}


class FakeSourceClient:
    def __init__(self, calls, *, fail_id=None):
        self.calls = calls
        self.fail_id = fail_id

    def fetch(self, fixture, url):
        self.calls.append((fixture.id, url))
        if fixture.id == self.fail_id:
            raise PermissionError('secret token must never be journaled')
        base = QUALIFICATION_TEXT.get(
            fixture.id,
            (
                f"{fixture.company_name} provides "
                f"{_load_source_plan()[fixture.id]['business_offer_phrase']} for "
                f"{_load_source_plan()[fixture.id]['b2b_buyer_phrase']}."
            ),
        )
        return (
            base + f' Independent profile for {fixture.company_name} at {url}. '
        ) * 8


class FakeSearchClient:
    def __init__(self, calls, outcomes=None):
        self.calls = calls
        self.outcomes = list(outcomes or ())

    def search(self, fixture, query):
        self.calls.append((fixture.id, query))
        if query.startswith('source discovery:'):
            slug = fixture.domain.split('.')[0]
            return SearchOutcome(
                True, 2,
                (
                    'https://sourceofficefurniture.ca/unrelated',
                    f'https://reviews.example/{slug}',
                ),
            )
        return self.outcomes.pop(0) if self.outcomes else SearchOutcome(False)


def test_http_source_client_uses_neutral_browser_user_agent(monkeypatch) -> None:
    requests = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _limit):
            return (
                b'<html><meta property=article:published_time '
                b'content=2026-06-04T12:00:00Z><body>'
                b'Aligned digital sales room</body></html>'
            )

    def open_request(request, timeout):
        requests.append((request, timeout))
        return Response()

    monkeypatch.setattr(
        'scripts.company_enrichment.cli.urlopen', open_request,
    )
    fixture = next(
        item for item in _stage_fixtures('remaining_saas')
        if item.id == 'saas-03'
    )
    content = HttpSourceClient().fetch(
        fixture, 'https://www.alignedup.com/digital-sales-room/',
    )
    assert content == '2026-06-04 Aligned digital sales room'
    assert requests[0][0].get_header('User-agent') == 'Mozilla/5.0'
    assert requests[0][1] == 90


def test_http_source_retries_403_with_standard_browser_headers(
    monkeypatch,
) -> None:
    requests = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _limit):
            return b'<html><body>Walker Sands agency for B2B companies</body></html>'

    def open_request(request, timeout):
        requests.append((request, timeout))
        if len(requests) == 1:
            raise HTTPError(request.full_url, 403, 'Forbidden', {}, None)
        return Response()

    monkeypatch.setattr(
        'scripts.company_enrichment.cli.urlopen', open_request,
    )
    fixture = next(
        item for item in _stage_fixtures('b2b_agencies')
        if item.id == 'agency-04'
    )
    content = HttpSourceClient().fetch(
        fixture, 'https://www.walkersands.com/',
    )
    assert content == 'Walker Sands agency for B2B companies'
    assert requests[0][0].get_header('User-agent') == 'Mozilla/5.0'
    assert 'Chrome/127.0.0.0' in requests[1][0].get_header('User-agent')
    assert requests[1][0].get_header('Accept-language') == 'en-US,en;q=0.9'


def test_http_source_rejects_cross_domain_redirect(monkeypatch) -> None:
    class Response:
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def geturl(self): return 'https://unrelated.example/company'
        def read(self, _limit): return b'<html>AgencyAnalytics profile</html>'
    monkeypatch.setattr(
        'scripts.company_enrichment.cli.urlopen',
        lambda request, timeout: Response(),
    )
    fixture = _stage_fixtures('saas_shared_core')[0]
    with pytest.raises(ValueError, match='redirected outside canonical domain'):
        HttpSourceClient().fetch(fixture, 'https://agencyanalytics.com/about')


def test_sec_403_never_falls_back_to_anonymous_browser_identity(
    monkeypatch,
) -> None:
    requests = []
    def fail(request, timeout):
        requests.append(request)
        raise HTTPError(request.full_url, 403, 'Forbidden', {}, None)
    monkeypatch.setattr('scripts.company_enrichment.cli.urlopen', fail)
    fixture = _stage_fixtures('recently_funded_b2b')[-1]
    with pytest.raises(HTTPError):
        HttpSourceClient(
            contact_email_resolver=lambda: 'operator@example.com',
        ).fetch(fixture, 'https://www.sec.gov/Archives/example.xml')
    assert len(requests) == 1
    assert 'operator@example.com' in requests[0].get_header('User-agent')


def test_http_source_uses_bounded_curl_after_two_403s(monkeypatch) -> None:
    attempts = []
    monkeypatch.setattr(
        'scripts.company_enrichment.cli.urlopen',
        lambda request, timeout: (_ for _ in ()).throw(
            HTTPError(request.full_url, 403, 'Forbidden', {}, None)
        ),
    )
    def run(args, **kwargs):
        attempts.append((args, kwargs))
        return subprocess.CompletedProcess(
            args, 0,
            (b'<html><body>Alps Controls provides HVAC controls ordering for '
             b'contractors. Alps Controls works with 200 manufacturers and '
             b'company accounts.</body></html>\nhttps://alpscontrols.com/Help'),
            b'',
        )
    monkeypatch.setattr('scripts.company_enrichment.cli.subprocess.run', run)
    fixture = next(
        item for item in _stage_fixtures('b2b_commerce_suppliers')
        if item.id == 'supplier-08'
    )
    content = HttpSourceClient().fetch(fixture, 'https://alpscontrols.com/Help')
    assert 'Alps Controls' in content
    args, kwargs = attempts[0]
    assert args[:3] == ['curl.exe', '--location', '--fail']
    assert args[args.index('--max-time') + 1] == '90'
    assert args[args.index('--max-filesize') + 1] == '6000000'
    assert kwargs == {'check': False, 'capture_output': True}


def test_http_source_retries_transient_curl_failure_once_without_sleep(
    monkeypatch,
) -> None:
    curl_attempts = []
    monkeypatch.setattr(
        'scripts.company_enrichment.cli.urlopen',
        lambda request, timeout: (_ for _ in ()).throw(
            HTTPError(request.full_url, 403, 'Forbidden', {}, None)
        ),
    )

    def run(args, **kwargs):
        curl_attempts.append((args, kwargs))
        if len(curl_attempts) == 1:
            return subprocess.CompletedProcess(args, 22, b'', b'403')
        return subprocess.CompletedProcess(
            args, 0,
            (b'<html><body>Alps Controls provides HVAC controls ordering for '
             b'contractors. Alps Controls works with 200 manufacturers and '
             b'company accounts.</body></html>\nhttps://alpscontrols.com/Help'),
            b'',
        )

    monkeypatch.setattr('scripts.company_enrichment.cli.subprocess.run', run)
    fixture = next(
        item for item in _stage_fixtures('b2b_commerce_suppliers')
        if item.id == 'supplier-08'
    )

    content = HttpSourceClient().fetch(fixture, 'https://alpscontrols.com/Help')

    assert 'Alps Controls' in content
    assert len(curl_attempts) == 2
    assert [attempt[0][-1] for attempt in curl_attempts] == [
        'https://alpscontrols.com/Help',
        'https://alpscontrols.com/Help',
    ]
    assert all(
        attempt[0][attempt[0].index('--max-time') + 1] == '90'
        for attempt in curl_attempts
    )


def test_curl_fallback_rejects_cross_domain_effective_url(monkeypatch) -> None:
    monkeypatch.setattr(
        'scripts.company_enrichment.cli.urlopen',
        lambda request, timeout: (_ for _ in ()).throw(
            HTTPError(request.full_url, 403, 'Forbidden', {}, None)
        ),
    )
    monkeypatch.setattr(
        'scripts.company_enrichment.cli.subprocess.run',
        lambda args, **kwargs: subprocess.CompletedProcess(
            args, 0,
            b'<html>Alps Controls</html>\nhttps://unrelated.example/login', b'',
        ),
    )
    fixture = next(
        item for item in _stage_fixtures('b2b_commerce_suppliers')
        if item.id == 'supplier-08'
    )
    with pytest.raises(ValueError, match='redirected outside canonical domain'):
        HttpSourceClient().fetch(fixture, 'https://alpscontrols.com/Help')


def test_sec_source_uses_injected_contact_without_persisting_it(
    monkeypatch,
) -> None:
    requests = []
    contact = 'operator@example.com'

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _limit):
            return b'<xml>ThoroughCare funding October 15, 2025</xml>'

    monkeypatch.setattr(
        'scripts.company_enrichment.cli.urlopen',
        lambda request, timeout: requests.append((request, timeout)) or Response(),
    )
    fixture = next(
        item for item in _stage_fixtures('recently_funded_b2b')
        if item.id == 'funded-10'
    )
    HttpSourceClient(
        contact_email_resolver=lambda: contact,
    ).fetch(
        fixture,
        'https://www.sec.gov/Archives/edgar/data/1745729/000174572925000003/primary_doc.xml',
    )
    assert requests[0][0].get_header('User-agent') == (
        'research-process-builder/1.0 ' + contact
    )
    assert requests[0][1] == 90


def test_pdf_official_source_uses_bounded_pdf_extraction(monkeypatch) -> None:
    read_limits = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, limit):
            read_limits.append(limit)
            return b'%PDF-stub'

    class Page:
        def extract_text(self):
            return 'Plurilock provides cybersecurity services for businesses.' * 5

    class Reader:
        def __init__(self, _stream):
            self.pages = [Page()]

    monkeypatch.setattr(
        'scripts.company_enrichment.cli.urlopen',
        lambda _request, timeout: Response(),
    )
    monkeypatch.setattr('pypdf.PdfReader', Reader)
    fixture = next(
        item for item in _stage_fixtures('recently_funded_b2b')
        if item.id == 'funded-08'
    )
    content = HttpSourceClient().fetch(
        fixture,
        'https://plurilock.com/wp-content/uploads/2026/04/2026-04-PLUR-IR.pdf',
    )
    assert content.startswith('Plurilock provides cybersecurity')
    assert read_limits == [6_000_000]


def test_uppercase_pdf_extension_uses_pdf_extraction(monkeypatch) -> None:
    read_limits = []

    class Response:
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def read(self, limit):
            read_limits.append(limit)
            return b'%PDF-stub'

    class Reader:
        def __init__(self, _stream): self.pages = [self]
        def extract_text(self): return 'Optum Bank FDIC evaluation ' * 20

    monkeypatch.setattr(
        'scripts.company_enrichment.cli.urlopen',
        lambda _request, timeout: Response(),
    )
    monkeypatch.setattr('pypdf.PdfReader', Reader)
    fixture = next(
        item for item in _stage_fixtures('well_known_b2b')
        if item.id == 'known-09'
    )
    content = HttpSourceClient().fetch(
        fixture, 'https://crapes.fdic.gov/report.PDF',
    )
    assert 'Optum Bank' in content
    assert read_limits == [6_000_000]


def _run(tmp_path: Path, args, *, fail_id=None, search_outcomes=None):
    source_calls = []
    search_calls = []
    created = []
    output = []
    def source_factory():
        created.append('source')
        return FakeSourceClient(source_calls, fail_id=fail_id)
    def search_factory():
        created.append('search')
        return FakeSearchClient(search_calls, search_outcomes)
    code = main(
        [*args, '--run-dir', str(tmp_path)],
        source_client_factory=source_factory,
        search_client_factory=search_factory,
        emit=output.append,
    )
    return code, source_calls, search_calls, created, json.loads(output[-1])


def test_dry_run_constructs_no_clients_and_reports_exact_stage(tmp_path: Path) -> None:
    code, source_calls, search_calls, created, summary = _run(
        tmp_path, ['--stage', 'saas_shared_core', '--dry-run'],
    )
    assert code == 0
    assert created == source_calls == search_calls == []
    assert summary['company_ids'] == ['saas-01', 'saas-04', 'saas-07']
    assert summary['paid_cap_usd'] == '0'


def test_script_bridge_is_directly_executable(tmp_path: Path) -> None:
    completed = subprocess.run(
        [sys.executable, 'scripts/company_enrichment_cli.py',
         '--stage', 'saas_shared_core', '--run-dir', str(tmp_path), '--dry-run'],
        check=True, capture_output=True, text=True,
    )
    assert json.loads(completed.stdout)['company_ids'] == ['saas-01', 'saas-04', 'saas-07']


def test_live_composes_outer_orchestrator_and_task2_runner(tmp_path: Path) -> None:
    code, source_calls, search_calls, _created, summary = _run(
        tmp_path, ['--stage', 'saas_shared_core'],
    )
    assert code == 0
    assert len(source_calls) == 9
    assert len(search_calls) == 48
    assert summary['dossiers_valid'] == 3
    assert summary['duplicate_ids'] == 0
    assert (tmp_path / 'outer' / 'saas_shared_core' / 'run.json').exists()
    outcomes = [json.loads(line) for line in (tmp_path / 'outcomes.jsonl').read_text().splitlines()]
    assert len(outcomes) == 24
    assert len({item['enrichment_id'] for item in outcomes}) == 8
    assert all(item['status'] == 'complete' for item in outcomes)
    assert all(item['output']['discovery']['selection_outcome'] == 'selected' for item in outcomes)
    assert all(len(item['output']['route']['provider_ids']) == 3 for item in outcomes)


def test_live_resume_rehydrates_and_validates_without_repurchase(tmp_path: Path) -> None:
    first = _run(tmp_path, ['--stage', 'saas_shared_core'])
    resumed = _run(tmp_path, ['--stage', 'saas_shared_core', '--resume'])
    assert resumed[1] == []
    assert resumed[2] == []
    assert resumed[4]['resumed'] == 3
    assert resumed[4]['source_repurchases'] == 0
    assert resumed[4]['dry_angles'] == first[4]['dry_angles']


def test_resume_retries_only_invalid_dossier(tmp_path: Path) -> None:
    _run(tmp_path, ['--stage', 'saas_shared_core'])
    (tmp_path / 'dossiers' / 'saas-01.yaml').write_text('assertions: [', encoding='utf-8')
    resumed = _run(tmp_path, ['--stage', 'saas_shared_core', '--resume'])
    assert resumed[0] == 0
    assert resumed[4]['resumed'] == 2
    assert resumed[1] == []
    assert resumed[4]['invalid_artifacts'] == []
    assert resumed[4]['dossiers_valid'] == 3
    assert resumed[4]['companies_saturated'] == 1
    assert (tmp_path / 'outer' / 'saas_shared_core-retry-1' / 'run.json').exists()

    (tmp_path / 'dossiers' / 'saas-01.yaml').write_text(
        'assertions: [', encoding='utf-8',
    )
    retried = _run(
        tmp_path, ['--stage', 'saas_shared_core', '--resume'],
    )
    assert retried[0] == 0
    assert retried[4]['resumed'] == 2
    assert (
        tmp_path / 'outer' / 'saas_shared_core-retry-2' / 'run.json'
    ).exists()


def test_resume_rejects_orphaned_evidence_object(tmp_path: Path) -> None:
    _run(tmp_path, ['--stage', 'saas_shared_core'])
    orphan = tmp_path / 'evidence' / 'objects' / (('a' * 64) + '.json')
    orphan.write_text('{"content":"orphan"}', encoding='utf-8')
    resumed = _run(tmp_path, ['--stage', 'saas_shared_core', '--resume'])
    assert resumed[0] == 2
    assert resumed[4]['resumed'] == 0
    assert resumed[4]['invalid_artifacts'] == ['orphaned_evidence_object']


def test_resume_accepts_append_only_superseded_same_locator_observation(
    tmp_path: Path,
) -> None:
    _run(tmp_path, ['--stage', 'saas_shared_core'])
    store = EvidenceStore(tmp_path / 'evidence')
    store.put(SourceRecord(
        'https://agencyanalytics.com/company/about',
        datetime(2026, 8, 12, 23, tzinfo=timezone.utc),
        'first_party', 'official-source-v2',
        'AgencyAnalytics superseded page observation ' * 20,
        'AgencyAnalytics superseded page observation', 30, '0',
    ))

    resumed = _run(tmp_path, ['--stage', 'saas_shared_core', '--resume'])

    assert resumed[0] == 0
    assert resumed[4]['resumed'] == 3
    assert resumed[4]['source_repurchases'] == 0


def test_partial_rollout_resume_ignores_journaled_other_cohort_evidence(
    tmp_path: Path,
) -> None:
    _run(tmp_path, ['--stage', 'saas_shared_core'])
    EvidenceStore(tmp_path / 'evidence').put(SourceRecord(
        'https://carney.co/about-us/',
        datetime(2026, 8, 12, 23, tzinfo=timezone.utc),
        'first_party', 'official-source',
        'Carney provides marketing services for business clients. ' * 20,
        'Carney provides marketing services', 30, '0',
    ))
    resumed = _run(tmp_path, ['--stage', 'saas_shared_core', '--resume'])
    assert resumed[0] == 0
    assert resumed[4]['resumed'] == 3
    assert resumed[4]['invalid_artifacts'] == []


def test_resume_rejects_same_locator_object_from_unrelated_provider(
    tmp_path: Path,
) -> None:
    _run(tmp_path, ['--stage', 'saas_shared_core'])
    EvidenceStore(tmp_path / 'evidence').put(SourceRecord(
        'https://agencyanalytics.com/company/about',
        datetime(2026, 8, 12, 23, tzinfo=timezone.utc),
        'first_party', 'unrelated-importer',
        'AgencyAnalytics unrelated imported observation ' * 20,
        'AgencyAnalytics unrelated imported observation', 30, '0',
    ))
    resumed = _run(tmp_path, ['--stage', 'saas_shared_core', '--resume'])
    assert resumed[0] == 2
    assert resumed[4]['invalid_artifacts'] == ['orphaned_evidence_object']


def test_source_gate_requires_distinct_independent_domains_and_entity_relevance() -> None:
    fixture = _stage_fixtures('saas_shared_core')[0]
    official = ResearchSource('https://agencyanalytics.com/about', 'first_party', 'official', QUALIFICATION_TEXT['saas-01'] * 5)
    duplicate_domain = (
        official,
        ResearchSource('https://ca.linkedin.com/a', 'independent', 'linkedin', 'AgencyAnalytics profile ' * 20),
        ResearchSource('https://www.linkedin.com/b', 'independent', 'linkedin-2', 'AgencyAnalytics reviews ' * 20),
    )
    with pytest.raises(ValueError, match='distinct independent domains'):
        _validate_source_pack(fixture, duplicate_domain)
    unrelated = (
        official,
        ResearchSource('https://linkedin.com/a', 'independent', 'linkedin', 'AgencyAnalytics profile ' * 20),
        ResearchSource('https://sourceforge.net/a', 'independent', 'sourceforge', 'Unrelated accounting product ' * 20),
    )
    with pytest.raises(ValueError, match='entity relevance'):
        _validate_source_pack(fixture, unrelated)


def test_source_plan_normalizes_typed_enrichment_assertions(tmp_path: Path) -> None:
    plan_path = tmp_path / 'source-plan.yaml'
    plan_path.write_text('''
version: '1.0'
companies:
  funded-05:
    sources:
    - url: https://www.baseten.co/pricing/
      source_type: first_party
      provider: official-pricing-source-v2
    enrichment_assertions:
      analogy-value-translator:
      - field: pricing
        value: Baseten publishes model API and dedicated deployment pricing.
        evidence_phrase: model API pricing
        source_url: https://www.baseten.co/pricing/
        material_queries:
        - Baseten public pricing exact dollar amount
''', encoding='utf-8')
    plan = _load_source_plan(plan_path)
    assertion = plan['funded-05']['enrichment_assertions'][
        'analogy-value-translator'
    ][0]
    assert assertion['field'] == 'pricing'
    assert assertion['material_queries'] == (
        'Baseten public pricing exact dollar amount',
    )


def test_cached_material_result_is_reused_without_search_and_requires_plan_source(
    tmp_path: Path,
) -> None:
    fixture = _stage_fixtures('recently_funded_b2b')[4]
    calls = CallLedger(tmp_path / 'calls.jsonl')
    pricing_query, technology_query = _dry_queries(
        fixture, 'analogy-value-translator',
    )
    calls.append(
        company_id=fixture.id, enrichment_id='analogy-value-translator',
        kind='search', query=pricing_query, status='material_fact',
    )
    calls.append(
        company_id=fixture.id, enrichment_id='analogy-value-translator',
        kind='search', query=technology_query, status='no_material_fact',
    )
    search_calls = []
    search = FakeSearchClient(search_calls)
    spec = (
        'https://www.baseten.co/pricing/', 'first_party',
        'official-pricing-source-v2',
    )
    adapter = _LiveAdapter(
        fixture, spec, FakeSourceClient([]), search, calls, run_searches=True,
        enrichment_assertions={
            'analogy-value-translator': ({
                'field': 'pricing', 'value': 'Published pricing',
                'evidence_phrase': 'pricing',
                'source_url': spec[0], 'material_queries': (pricing_query,),
            },),
        },
    )
    adapter.enrichment_id = 'analogy-value-translator'
    metadata = adapter.cached_metadata()
    assert metadata.dry_angles == (technology_query,)
    assert metadata.resolved_material_angles == ((pricing_query, 'pricing', spec[0]),)
    assert search_calls == []


def test_carney_news_material_result_maps_to_cited_launch_assertion() -> None:
    plan = _load_source_plan()['agency-09']
    assertion = plan['enrichment_assertions']['news-product-launches'][0]
    assert assertion == {
        'field': 'launches',
        'value': (
            'Carney announced a redesigned Carney.co website and a new '
            'Daily Carnage hub on December 22, 2025.'
        ),
        'evidence_phrase': 'redesigned Carney Daily Carnage',
        'source_url': 'https://carney.co/daily-carnage/choose-your-adventure/',
        'material_queries': ('Carney official latest news announcement dated',),
    }


def test_qualification_is_extracted_from_retained_sources_not_seed(tmp_path: Path) -> None:
    _run(tmp_path, ['--stage', 'saas_shared_core'])
    qualification = json.loads((tmp_path / 'qualifications' / 'saas-01.json').read_text())
    assert qualification['b2b_buyer'] == 'marketing agencies'
    assert qualification['business_offer'] == 'reporting software'
    assert qualification['evidence_id'].startswith('ev-')


def test_dry_angles_are_executed_distinct_outcomes_and_journaled(tmp_path: Path) -> None:
    outcomes = [SearchOutcome(True), SearchOutcome(False), SearchOutcome(False)]
    result = _run(tmp_path, ['--stage', 'saas_shared_core'], search_outcomes=outcomes)
    assert result[0] == 2
    ledger = [json.loads(line) for line in (tmp_path / 'calls.jsonl').read_text().splitlines()]
    dry = [item for item in ledger if item['kind'] == 'search']
    assert any(item['status'] == 'material_fact' for item in dry)
    assert all(item.get('enrichment_id') for item in dry)
    assert not (tmp_path / 'dossiers' / 'saas-01.yaml').exists()
    assert all('query' in item and 'failure' not in item for item in dry)


def test_call_ledger_redacts_failures_and_records_urls(tmp_path: Path) -> None:
    result = _run(tmp_path, ['--stage', 'saas_shared_core'], fail_id='saas-04')
    assert result[0] == 2
    text = (tmp_path / 'calls.jsonl').read_text()
    assert 'secret token' not in text
    rows = [json.loads(line) for line in text.splitlines()]
    assert all(set(item) <= {'company_id', 'enrichment_id', 'failure', 'kind', 'status', 'url', 'query'} for item in rows)
    assert any(item.get('failure') == 'authentication_required' for item in rows)


def test_paid_execution_requires_explicit_opt_in_and_fixed_corpus_cap(tmp_path: Path) -> None:
    assert CORPUS_PAID_CAP_USD == '2.00'
    with pytest.raises(SystemExit):
        _run(tmp_path, ['--stage', 'saas_shared_core', '--paid-cap-usd', '2.00'])
    with pytest.raises(SystemExit):
        _run(tmp_path, ['--stage', 'saas_shared_core', '--allow-paid', '--paid-cap-usd', '1.00'])


def test_live_run_persists_raw_evidence_and_research_complete_dossiers(tmp_path: Path) -> None:
    run = _run(tmp_path, ['--stage', 'saas_shared_core'])
    assert len(list((tmp_path / 'evidence' / 'objects').glob('*.json'))) == 9
    dossiers = list((tmp_path / 'dossiers').glob('*.yaml'))
    assert len(dossiers) == 3
    payloads = [yaml.safe_load(path.read_text()) for path in dossiers]
    assert all(item['unknowns'] for item in payloads)
    for payload in payloads:
        fields = {item['field'] for item in payload['assertions']}
        assert {'identity', 'description', 'offers', 'icp', 'personas'} <= fields
        assert 'icp' not in payload['unknowns']
        assert 'personas' not in payload['unknowns']
    assert all(
        'icp' not in reasons and 'personas' not in reasons
        for reasons in run[4]['unknown_reasons'].values()
    )
    for payload in payloads:
        assert set(run[4]['unknown_reasons'][payload['company_id']]) == set(
            payload['unknowns']
        )


def test_unknown_reason_summary_uses_final_dossier_unknowns_only() -> None:
    dossier = SimpleNamespace(unknowns=('technology',))
    assert _dossier_unknown_reasons(dossier) == {
        'technology': 'not established after executed source and dry-angle research',
    }


def test_resume_rejects_missing_category_specific_search_provenance(tmp_path: Path) -> None:
    _run(tmp_path, ['--stage', 'saas_shared_core'])
    dossier = _rehydrate_dossier(tmp_path / 'dossiers' / 'saas-01.yaml')
    rows = [
        json.loads(line)
        for line in (tmp_path / 'calls.jsonl').read_text().splitlines()
    ]
    rows = [
        row for row in rows
        if not (
            row.get('company_id') == 'saas-01'
            and row.get('enrichment_id') == 'competitor-intelligence'
        )
    ]
    (tmp_path / 'calls.jsonl').write_text(
        ''.join(json.dumps(row) + '\n' for row in rows), encoding='utf-8',
    )
    fixture = _stage_fixtures('saas_shared_core')[0]
    with pytest.raises(ValueError, match='category search provenance'):
        _validate_search_provenance(fixture, dossier, tmp_path)


def test_cache_only_mode_never_constructs_or_calls_live_clients(tmp_path: Path) -> None:
    emitted = []
    def forbidden_factory():
        raise AssertionError('cache-only mode constructed a live client')

    code = main(
        ['--stage', 'saas_shared_core', '--run-dir', str(tmp_path), '--cache-only'],
        source_client_factory=forbidden_factory,
        search_client_factory=forbidden_factory,
        emit=emitted.append,
    )

    assert code == 2
    summary = json.loads(emitted[-1])
    assert summary['source_gaps'] == ['saas-01', 'saas-04', 'saas-07']


def test_source_specs_are_discovered_for_non_saas_fixture(tmp_path: Path) -> None:
    fixture = replace(
        _stage_fixtures('local_b2b_services')[0], id='local-unplanned',
    )
    calls = []
    search = FakeSearchClient(calls)
    review_url = 'https://reviews.example/' + fixture.domain.split('.')[0]
    specs = _discover_source_specs(
        fixture, search, CallLedger(tmp_path / 'calls.jsonl'),
    )
    assert specs[0] == (
        'https://' + fixture.domain, 'first_party', 'official-source',
    )
    assert {spec[0] for spec in specs[1:]} == {
        fixture.linkedin_company_url,
        review_url,
    }
    assert calls == [
        (
            fixture.id,
            'source discovery: ' + chr(34) + fixture.company_name
            + chr(34) + ' site:g2.com',
        ),
    ]


def test_source_discovery_rejects_polluted_generic_word_results(
    tmp_path: Path,
) -> None:
    fixture = _stage_fixtures('saas_shared_core')[0]
    class PollutedSearch:
        def search(self, fixture, query):
            return SearchOutcome(
                True, 3,
                (
                    'https://sourceforsports.ca/company/reviews',
                    'https://sourceofficefurniture.ca/company-profile',
                    'https://sourceforge.net/software/product/AgencyAnalytics/',
                ),
            )
    specs = _discover_source_specs(
        fixture, PollutedSearch(), CallLedger(tmp_path / 'calls.jsonl'),
    )
    assert [item[0] for item in specs] == [
        'https://agencyanalytics.com/company/about',
        'https://ca.linkedin.com/company/agencyanalytics',
        'https://sourceforge.net/software/product/AgencyAnalytics/',
    ]


def test_verified_core_source_locators_are_reused_exactly(tmp_path: Path) -> None:
    class NoSearch:
        def search(self, fixture, query):
            raise AssertionError('known v6 source locators must not be rediscovered')
    expected = {
        'saas-01': 'https://agencyanalytics.com/company/about',
        'saas-04': 'https://www.apriori.com/about/',
        'saas-07': 'https://www.betterworks.com/about',
    }
    for fixture in _stage_fixtures('saas_shared_core'):
        specs = _discover_source_specs(
            fixture, NoSearch(), CallLedger(tmp_path / 'calls.jsonl'),
        )
        assert len(specs) == 3
        assert specs[0][0] == expected[fixture.id]


def test_aligned_uses_verified_bounded_product_page(tmp_path: Path) -> None:
    fixture = next(
        item for item in _stage_fixtures('remaining_saas')
        if item.id == 'saas-03'
    )
    specs = _discover_source_specs(
        fixture, FakeSearchClient([]), CallLedger(tmp_path / 'calls.jsonl'),
    )
    assert specs[0][0] == 'https://www.alignedup.com/digital-sales-room/'


def test_aligned_www_alias_passes_normalized_canonical_domain_gate() -> None:
    fixture = next(
        item for item in _stage_fixtures('remaining_saas')
        if item.id == 'saas-03'
    )
    content = (
        'Aligned provides a digital sales room for business sales teams. '
        'Aligned workspace helps revenue teams and buyers. '
    ) * 10
    _validate_source_pack(fixture, (
        ResearchSource(
            'https://www.alignedup.com/digital-sales-room/',
            'first_party', 'official-source', content,
        ),
        ResearchSource(
            'https://www.linkedin.com/company/alignedup',
            'independent', 'linkedin-source', content,
        ),
        ResearchSource(
            'https://www.nfx.com/post/why-nfx-invested-in-aligned',
            'independent', 'independent-source', content,
        ),
    ))


def test_generic_discovery_queries_review_domains_in_order(tmp_path: Path) -> None:
    fixture = replace(
        _stage_fixtures('local_b2b_services')[0], id='local-unplanned',
    )
    queries = []
    class OrderedSearch:
        def search(self, fixture, query):
            queries.append(query)
            if 'site:capterra.com' in query:
                return SearchOutcome(
                    True, 1, (), (
                        SearchHit(
                            'https://capterra.com/p/1234',
                            fixture.company_name + ' reviews',
                            'Independent review for ' + fixture.company_name,
                        ),
                    ),
                )
            return SearchOutcome(False)
    specs = _discover_source_specs(
        fixture, OrderedSearch(), CallLedger(tmp_path / 'calls.jsonl'),
    )
    quoted = chr(34) + fixture.company_name + chr(34)
    assert queries[:2] == [
        'source discovery: ' + quoted + ' site:g2.com',
        'source discovery: ' + quoted + ' site:capterra.com',
    ]
    assert specs[-1][0] == 'https://capterra.com/p/1234'


def test_search_title_can_establish_candidate_relevance(tmp_path: Path) -> None:
    fixture = replace(
        _stage_fixtures('local_b2b_services')[0], id='local-unplanned',
    )
    class TitledSearch:
        def search(self, fixture, query):
            return SearchOutcome(
                True, 1, (),
                (
                    SearchHit(
                        'https://reviews.example/product/12345',
                            fixture.company_name + ' verified reviews',
                            'Independent ' + fixture.company_name + ' product profile',
                    ),
                ),
            )
    specs = _discover_source_specs(
        fixture, TitledSearch(), CallLedger(tmp_path / 'calls.jsonl'),
    )
    assert specs[-1][0] == 'https://reviews.example/product/12345'


def test_missing_discovered_source_still_returns_known_sources(
    tmp_path: Path,
) -> None:
    class NoCandidateSearch(FakeSearchClient):
        def search(self, fixture, query):
            self.calls.append((fixture.id, query))
            if query.startswith('source discovery:'):
                return SearchOutcome(False)
            return SearchOutcome(False)
    search_calls = []
    fixture = replace(
        _stage_fixtures('local_b2b_services')[0], id='local-unplanned',
    )
    specs = _discover_source_specs(
        fixture, NoCandidateSearch(search_calls),
        CallLedger(tmp_path / 'calls.jsonl'),
    )
    assert specs == (
        ('https://' + fixture.domain, 'first_party', 'official-source'),
        (fixture.linkedin_company_url, 'independent', 'linkedin-source'),
    )
    assert len(search_calls) == 7


def test_shared_run_directory_preserves_stage_order_without_repeats(
    tmp_path: Path,
) -> None:
    first = _run(tmp_path, ['--stage', 'saas_shared_core'])
    second = _run(tmp_path, ['--stage', 'remaining_saas'])
    assert first[0] == second[0] == 0
    assert set(first[4]['company_ids']).isdisjoint(second[4]['company_ids'])
    assert len(list((tmp_path / 'dossiers').glob('*.yaml'))) == 10
    assert (tmp_path / 'outer' / 'saas_shared_core' / 'run.json').exists()
    assert (tmp_path / 'outer' / 'remaining_saas' / 'run.json').exists()
    rollout = [
        json.loads(line)
        for line in (tmp_path / 'rollout.jsonl').read_text().splitlines()
    ]
    assert [item['stage'] for item in rollout] == [
        'saas_shared_core', 'remaining_saas',
    ]
