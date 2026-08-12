from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
import html
import json
import os
from pathlib import Path
import re
from types import SimpleNamespace
from typing import Callable, Protocol
from urllib.parse import quote_plus, urlparse
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

import yaml

from scripts.research_orchestration.artifacts import ArtifactStore
from scripts.research_orchestration.budgets import BudgetLimits
from scripts.research_orchestration.contracts import (
    BudgetCharge, CheckerResult, EvaluationResult, Evidence as OuterEvidence,
    Experiment, Role, RunRequest,
)
from scripts.research_orchestration.orchestrator import (
    AutoresearchOrchestrator, RoleRunners,
)

from .benchmark_schedule import BenchmarkRollout
from .budgets import BudgetLedger
from .contracts import (
    CompanyDossier, CompanyFixture, EnrichmentRequest, EvidenceRef,
    FieldAssertion, HumanCorrection, ResultStatus, SellerContext, Visibility,
)
from .corpus import Corpus, REQUIRED_DOSSIER_FIELDS, validate_research_complete
from .definitions import EXPECTED_P0_IDS
from .discovery import (
    Capability, CapabilityDiscovery, CapabilityRegistry, ProbeResult, ProbeStatus,
)
from .dossier_runner import DossierBuilder
from .evidence import EvidenceStore, SourceRecord
from .executors import ExecutionOutput, Finding, P0_ENRICHMENTS, execute_p0
from .providers import AuthenticationFailure, ProviderRouter
from .runner import AdapterResponse, EnrichmentRunner


CORPUS_PAID_CAP_USD = '2.00'
AS_OF = date(2026, 8, 12)
NOW = datetime(2026, 8, 12, tzinfo=timezone.utc)
PROVIDER_IDS = ('official-source', 'linkedin-source', 'independent-source')

LIVE_SOURCES = {
    'saas-01': (
        ('https://agencyanalytics.com/company/about', 'first_party', PROVIDER_IDS[0]),
        ('https://ca.linkedin.com/company/agencyanalytics', 'independent', PROVIDER_IDS[1]),
        ('https://sourceforge.net/software/product/AgencyAnalytics/', 'independent', PROVIDER_IDS[2]),
    ),
    'saas-04': (
        ('https://www.apriori.com/about/', 'first_party', PROVIDER_IDS[0]),
        ('https://www.linkedin.com/company/apriori', 'independent', PROVIDER_IDS[1]),
        ('https://www.vistaequitypartners.com/news/apriori-receives-growth-investment-from-vista-credit-partners-for-its-manufacturing-insights-platform/', 'independent', PROVIDER_IDS[2]),
    ),
    'saas-07': (
        ('https://www.betterworks.com/about', 'first_party', PROVIDER_IDS[0]),
        ('https://www.linkedin.com/company/betterworks', 'independent', PROVIDER_IDS[1]),
        ('https://www.hr.software/reviews/betterworks', 'independent', PROVIDER_IDS[2]),
    ),
}

DRY_QUERIES = {
    'saas-01': (
        'AgencyAnalytics verified active ad transparency creative started date',
        'AgencyAnalytics disclosed funding investor transaction amount date',
        'AgencyAnalytics audited revenue filing',
        'AgencyAnalytics SEC annual report audited financial statements',
        'AgencyAnalytics investor database disclosed round amount',
    ),
    'saas-04': (
        'aPriori verified active ad transparency creative started date',
        'aPriori public pricing exact dollar amount',
        'aPriori audited revenue filing',
        'aPriori SEC annual report audited financial statements',
        'aPriori ad library current creative landing page',
    ),
    'saas-07': (
        'Betterworks verified active ad transparency creative started date',
        'Betterworks audited annual revenue profit filing',
        'Betterworks public pricing exact dollar amount',
        'Betterworks SEC annual report audited financial statements',
        'Betterworks ad library current creative landing page',
    ),
}

QUALIFICATION_FACTS = {
    'saas-01': ('marketing agencies', 'automated reports and custom dashboards'),
    'saas-04': (
        'manufacturers and product designers',
        'product cost, manufacturability, and carbon footprint',
    ),
    'saas-07': (
        'HR and business leaders',
        'performance management and talent intelligence',
    ),
}


@dataclass(frozen=True, slots=True)
class ResearchSource:
    url: str
    source_type: str
    provider: str
    content: str


@dataclass(frozen=True, slots=True)
class SearchOutcome:
    material_facts_added: bool
    result_count: int = 0


class SourceClient(Protocol):
    def fetch(self, fixture: CompanyFixture, url: str) -> str: ...


class SearchClient(Protocol):
    def search(self, fixture: CompanyFixture, query: str) -> SearchOutcome: ...


class HttpSourceClient:
    def fetch(self, fixture: CompanyFixture, url: str) -> str:
        request = Request(url, headers={
            'User-Agent': 'Mozilla/5.0 research-process-builder/1.0',
            'Accept': 'text/html,application/xhtml+xml',
        })
        with urlopen(request, timeout=30) as response:
            raw = response.read(1_000_000)
            try:
                body = raw.decode('utf-8')
            except UnicodeDecodeError:
                body = raw.decode(
                    response.headers.get_content_charset() or 'utf-8',
                    errors='replace',
                )
        text = re.sub(r'<script[^>]*>.*?</script>', ' ', body, flags=re.I | re.S)
        text = re.sub(r'<style[^>]*>.*?</style>', ' ', text, flags=re.I | re.S)
        text = re.sub(r'<[^>]+>', ' ', text)
        return html.unescape(re.sub(r'\s+', ' ', text).strip())[:20_000]


class BingSearchClient:
    def search(self, fixture: CompanyFixture, query: str) -> SearchOutcome:
        url = 'https://www.bing.com/search?format=rss&q=' + quote_plus(query)
        request = Request(url, headers={'User-Agent': 'research-process-builder/1.0'})
        with urlopen(request, timeout=30) as response:
            root = ET.fromstring(response.read(500_000))
        items = root.findall('.//item')
        company_key = _entity_key(fixture.company_name)
        material = False
        for item in items:
            text = ' '.join(item.findtext(name, '') for name in ('title', 'description'))
            normalized = _entity_key(text)
            if company_key not in normalized:
                continue
            lowered = text.casefold()
            if (
                re.search(r'\$\s?\d', text)
                or 'active ad' in lowered
                or ('audited' in lowered and 'revenue' in lowered)
                or ('investment' in lowered and re.search(r'\d', text))
            ):
                material = True
                break
        return SearchOutcome(material, len(items))


class CallLedger:
    def __init__(self, path: Path):
        self.path = Path(path)

    def append(self, **row) -> None:
        allowed = {'company_id', 'failure', 'kind', 'status', 'url', 'query'}
        if set(row) - allowed:
            raise ValueError('call ledger contains unsupported fields')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open('a', encoding='utf-8', newline='\n') as stream:
            stream.write(json.dumps(row, sort_keys=True, separators=(',', ':')) + '\n')
            stream.flush()
            os.fsync(stream.fileno())


def _entity_key(value: str) -> str:
    return ''.join(character for character in value.casefold() if character.isalnum())


def _domain_key(url: str) -> str:
    host = (urlparse(url).hostname or '').casefold().removeprefix('www.')
    labels = host.split('.')
    return '.'.join(labels[-2:]) if len(labels) >= 2 else host


def _validate_source_pack(
    fixture: CompanyFixture, sources: tuple[ResearchSource, ...],
) -> None:
    first_party = [item for item in sources if item.source_type == 'first_party']
    independent = [item for item in sources if item.source_type == 'independent']
    if len(first_party) != 1 or len(independent) < 2:
        raise ValueError('source pack requires one first-party and two independent sources')
    domains = {_domain_key(item.url) for item in independent}
    if len(domains) < 2:
        raise ValueError('source pack requires distinct independent domains')
    company_key = _entity_key(fixture.company_name)
    for item in sources:
        if len(item.content) < 200:
            raise ValueError('source response is too thin')
        if company_key not in _entity_key(item.content):
            raise ValueError('source failed entity relevance')
    official_domain = _domain_key(first_party[0].url)
    if official_domain != _domain_key('https://' + fixture.domain):
        raise ValueError('first-party source does not match canonical domain')


def _companies() -> list[dict]:
    return yaml.safe_load(Path('benchmarks/companies.yaml').read_text(encoding='utf-8'))['companies']


def _stage_fixtures(stage: str) -> tuple[CompanyFixture, ...]:
    corpus = Corpus.load(Path('benchmarks/companies.yaml'))
    by_id = {item.id: item for item in corpus.fixtures}
    rollout = BenchmarkRollout(_companies())
    while rollout.current_stage != stage:
        if rollout.current_stage is None:
            raise ValueError(f'unknown rollout stage: {stage}')
        rollout.complete(rollout.current_company_ids)
    return tuple(by_id[item] for item in rollout.current_company_ids)


class _GtmProbe:
    def probe(self):
        return ProbeResult(
            'gtm', ProbeStatus.AVAILABLE,
            {'path': 'company-enrichment-cli', 'version': '1.0'},
        )


class _NexusProbe:
    def probe(self, enrichment_id):
        return ProbeResult('nexus', ProbeStatus.UNAVAILABLE)


def _runtime_definitions():
    return {
        enrichment_id: SimpleNamespace(
            id=enrichment_id, output_schema_version='1.0',
            required_inputs=('company_name', 'domain', 'seller_context'),
            fallback_order=PROVIDER_IDS, freshness_days=30,
            caps={'retries': 2}, output_visibility='message_safe',
        )
        for enrichment_id in EXPECTED_P0_IDS
    }


def _discovery(run_dir: Path):
    registry = CapabilityRegistry(tuple(
        Capability(
            provider_id, 'live-source', ('scrape',), index,
            provenance='company-enrichment-cli', cost_class='free',
            validation_state='available',
        )
        for index, provider_id in enumerate(PROVIDER_IDS, 1)
    ))
    path = run_dir / 'discovery.jsonl'
    def record(item):
        row = {
            'eligible_capabilities': list(item.eligible_capabilities),
            'enrichment_id': item.enrichment_id,
            'selected_capability': item.selected_capability,
            'selection_outcome': item.selection_outcome,
        }
        with path.open('a', encoding='utf-8', newline='\n') as stream:
            stream.write(json.dumps(row, sort_keys=True, separators=(',', ':')) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
    return CapabilityDiscovery(
        gtm_probe=_GtmProbe(), nexus_probe=_NexusProbe(), registry=registry,
        record_discovery=record,
    )


class _LiveAdapter:
    estimated_cost_usd = '0'
    resolved_model = 'deterministic/source-extractor-v1'

    def __init__(
        self, fixture, spec, source_client, search_client, calls, *,
        run_searches=False,
    ):
        self.fixture = fixture
        self.url, self.source_type, self.provider = spec
        self.source_client = source_client
        self.search_client = search_client
        self.calls = calls
        self.run_searches = run_searches

    def source_url(self, request, default):
        return self.url

    def collect(self, request, url):
        try:
            content = self.source_client.fetch(self.fixture, url)
            self.calls.append(
                company_id=self.fixture.id, kind='source', url=url,
                status='succeeded',
            )
        except PermissionError as error:
            self.calls.append(
                company_id=self.fixture.id, kind='source', url=url,
                status='failed', failure='authentication_required',
            )
            raise AuthenticationFailure('source authentication required') from error
        except Exception as error:
            self.calls.append(
                company_id=self.fixture.id, kind='source', url=url,
                status='failed', failure='terminal',
            )
            raise RuntimeError('source collection failed') from error
        dry = []
        if self.run_searches:
            for query in DRY_QUERIES[self.fixture.id]:
                try:
                    outcome = self.search_client.search(self.fixture, query)
                except Exception:
                    self.calls.append(
                        company_id=self.fixture.id, kind='search', query=query,
                        status='failed', failure='terminal',
                    )
                    continue
                status = (
                    'material_fact' if outcome.material_facts_added
                    else 'no_material_fact'
                )
                self.calls.append(
                    company_id=self.fixture.id, kind='search', query=query,
                    status=status,
                )
                if not outcome.material_facts_added:
                    dry.append(query)
                if len(dry) == 2:
                    break
        return AdapterResponse(
            SourceRecord(
                url, datetime.now(timezone.utc), self.source_type, self.provider,
                content, content[:2_000], 30, '0',
            ),
            actual_cost_usd='0', dry_angles=tuple(dry),
            resolved_model=self.resolved_model,
        )


def _supports(content: str, phrase: str) -> bool:
    normalized = _entity_key(content)
    return all(
        _entity_key(token) in normalized
        for token in phrase.split()
        if len(_entity_key(token)) > 2
    )


def _extract_qualification(
    fixture: CompanyFixture, sources: tuple[ResearchSource, ...],
    evidence: tuple[EvidenceRef, ...],
):
    _validate_source_pack(fixture, sources)
    buyer, offer = QUALIFICATION_FACTS[fixture.id]
    if not any(_supports(item.content, buyer) for item in sources):
        raise ValueError('retained sources do not establish B2B buyer')
    if not any(_supports(item.content, offer) for item in sources):
        raise ValueError('retained sources do not establish business offer')
    official_index = next(
        index for index, item in enumerate(sources)
        if item.source_type == 'first_party'
    )
    official = sources[official_index]
    official_evidence = evidence[official_index]
    qualified = replace(
        fixture, seed_status='verified', b2b_buyer=buyer,
        business_offer=offer,
        selection_reason='Retained sources establish a B2B SaaS buyer and offer',
        cohort_evidence_url=official.url,
        secondary_tags=('b2b-saas',), expected_ad_channels=('linkedin',),
    )
    return qualified, {
        'b2b_buyer': buyer,
        'business_offer': offer,
        'cohort_evidence_url': official.url,
        'evidence_id': official_evidence.evidence_id,
        'seed_status': 'verified',
    }


def _seller_context() -> SellerContext:
    return SellerContext(
        'B2B teams', ('business leader',), ('company research',),
        'Research workflow', '30 days', 'validated company context',
        ('cited Evidence',), 'explicit unknowns', ('consumer targeting',),
        'invest in evidence-backed research',
    )


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(
        json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n',
        encoding='utf-8',
    )
    os.replace(temporary, path)


def _run_company(
    fixture: CompanyFixture, run_dir: Path, source_client: SourceClient,
    search_client: SearchClient, calls: CallLedger, budget: BudgetLedger,
):
    evidence_store = EvidenceStore(run_dir / 'evidence')
    specs = LIVE_SOURCES[fixture.id]
    adapters = {
        provider: _LiveAdapter(
            fixture, spec, source_client, search_client, calls,
            run_searches=(index == len(specs) - 1),
        )
        for index, spec in enumerate(specs)
        for provider in (spec[2],)
    }
    qualification_box = {}

    def executor(enrichment_id, evidence, *, seller_context, output_visibility):
        if enrichment_id != 'company-description':
            return execute_p0(
                enrichment_id, evidence, seller_context=seller_context,
                unknowns=P0_ENRICHMENTS[enrichment_id],
                output_visibility=output_visibility,
            )
        sources = tuple(
            ResearchSource(
                item.url,
                evidence_store.get(item.content_hash).source_type,
                evidence_store.get(item.content_hash).provider,
                evidence_store.get(item.content_hash).content,
            )
            for item in evidence
        )
        qualified, qualification = _extract_qualification(
            fixture, sources, tuple(evidence),
        )
        qualification_box['fixture'] = qualified
        qualification_box['record'] = qualification
        official = next(
            item for item in sources if item.source_type == 'first_party'
        )
        return execute_p0(
            enrichment_id, evidence, seller_context=seller_context,
            findings=(
                Finding('identity', fixture.company_name),
                Finding('description', official.content[:500]),
                Finding('offers', qualified.business_offer),
            ),
            output_visibility=output_visibility,
        )

    runner = EnrichmentRunner(
        definitions=_runtime_definitions(), discovery=_discovery(run_dir),
        router=ProviderRouter(), evidence_store=evidence_store,
        budget_ledger=budget, adapters=adapters,
        outcome_journal=run_dir / 'outcomes.jsonl',
        as_of=datetime.now(timezone.utc), scope_id='corpus-build',
        executor=executor,
    )
    order = ('company-description', *sorted(EXPECTED_P0_IDS - {'company-description'}))
    results = []
    for enrichment_id in order:
        result = runner.run(EnrichmentRequest(
            enrichment_id, fixture.id, '1.0',
            {
                'company_name': fixture.company_name,
                'domain': fixture.domain,
                'seller_context': _seller_context(),
                'requested_model': 'deterministic/source-extractor-v1',
            },
        ))
        if result.status is not ResultStatus.COMPLETE:
            failure = result.failure.value if result.failure is not None else 'partial'
            raise RuntimeError('enrichment_failed:' + failure)
        results.append(result)
    qualified = qualification_box['fixture']
    _write_json(
        run_dir / 'qualifications' / f'{fixture.id}.json',
        qualification_box['record'],
    )
    dossier = DossierBuilder(
        load_results=lambda _fixture, _scope: tuple(results),
        output_dir=run_dir / 'dossiers', as_of=AS_OF,
    ).build(qualified, 'corpus-build')
    dry_angles = tuple(results[0].output.get('dry_angles', ()))
    return dossier, qualified, dry_angles


def _rehydrate_dossier(path: Path) -> CompanyDossier:
    value = yaml.safe_load(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ValueError('dossier YAML must be a mapping')
    evidence = tuple(
        EvidenceRef(
            item['evidence_id'], item['url'],
            datetime.fromisoformat(item['retrieved_at']),
            item['content_hash'], item['excerpt'],
        )
        for item in value['evidence']
    )
    assertions = tuple(
        FieldAssertion(
            item['field'], item['value'], tuple(item['evidence_ids']),
            item['confidence'], Visibility(item['visibility']),
        )
        for item in value['assertions']
    )
    corrections = tuple(
        HumanCorrection(
            item['correction_id'], item['field'], item['value'],
            item['reviewer_id'], datetime.fromisoformat(item['corrected_at']),
            item.get('supersedes_correction_id'),
        )
        for item in value.get('corrections', ())
    )
    return CompanyDossier(
        value['company_id'], value['schema_version'], assertions, evidence,
        tuple(value.get('unknowns', ())), corrections,
    )


def _validate_resume_company(
    fixture: CompanyFixture, run_dir: Path, store: EvidenceStore,
):
    dossier = _rehydrate_dossier(
        run_dir / 'dossiers' / f'{fixture.id}.yaml',
    )
    if dossier.company_id != fixture.id:
        raise ValueError('resume dossier company ID mismatch')
    sources = []
    for reference in dossier.evidence:
        record = store.get(reference.content_hash)
        if (
            record.url != reference.url
            or record.excerpt != reference.excerpt
            or record.retrieved_at != reference.retrieved_at
        ):
            raise ValueError('retained Evidence reference mismatch')
        sources.append(ResearchSource(
            record.url, record.source_type, record.provider, record.content,
        ))
    qualified, expected = _extract_qualification(
        fixture, tuple(sources), dossier.evidence,
    )
    qualification = json.loads(
        (run_dir / 'qualifications' / f'{fixture.id}.json').read_text(
            encoding='utf-8',
        )
    )
    if qualification != expected:
        raise ValueError('qualification record does not match retained Evidence')
    validate_research_complete(qualified, dossier, as_of=AS_OF)
    return dossier


def _resume_state(fixtures, run_dir: Path):
    store = EvidenceStore(run_dir / 'evidence')
    valid = []
    invalid = []
    referenced = set()
    for fixture in fixtures:
        try:
            dossier = _validate_resume_company(fixture, run_dir, store)
            valid.append(fixture.id)
            referenced.update(item.content_hash for item in dossier.evidence)
        except Exception:
            invalid.append(fixture.id)
    if not invalid:
        objects = {
            path.stem for path in (run_dir / 'evidence' / 'objects').glob('*.json')
        }
        journal_hashes = {
            event['content_hash'] for event in store._read_events()
        }
        if objects != referenced or journal_hashes != objects:
            return (), ('orphaned_evidence_object',)
    return tuple(valid), tuple(invalid)


def _outer_roles(stage_executor, evaluation_passed):
    experiment = Experiment(
        '1.0', 'company-corpus',
        'Execute the approved company corpus through the typed enrichment runner.',
        'Run the exact requested rollout stage.',
    )
    return RoleRunners(
        inventor=lambda envelope: experiment,
        in_bounds_checker=lambda envelope: CheckerResult(
            '1.0', 'in_bounds', True, 'accepted',
        ),
        novelty_checker=lambda envelope: CheckerResult(
            '1.0', 'novelty', True, 'novel',
        ),
        executor=lambda envelope: stage_executor(envelope),
        evaluator=lambda envelope: EvaluationResult(
            '1.0', evaluation_passed(), 0.90 if evaluation_passed() else 0.0,
            'research_complete' if evaluation_passed() else 'incomplete',
        ),
        charges={role: BudgetCharge() for role in Role},
    )


def _outer_request(stage, ids):
    return RunRequest(
        '1.0', 'company-corpus-' + stage,
        'Build research-complete company dossiers.', ('free_first', 'no_secrets'),
        {}, BudgetLimits(max_stages=5), 0.90,
        execution_inputs={'stage': stage, 'company_ids': ids},
        rubric='research_complete_company_dossier',
    )


def _append_stage_report(run_dir: Path, summary) -> None:
    path = run_dir / 'stage-report.jsonl'
    with path.open('a', encoding='utf-8', newline='\n') as stream:
        stream.write(json.dumps(summary, sort_keys=True, separators=(',', ':')) + '\n')
        stream.flush()
        os.fsync(stream.fileno())


def _retained_dry_angles(run_dir: Path, ids) -> dict[str, list[str]]:
    retained = {company_id: [] for company_id in ids}
    path = run_dir / 'calls.jsonl'
    if not path.exists():
        return retained
    for line in path.read_text(encoding='utf-8').splitlines():
        row = json.loads(line)
        company_id = row.get('company_id')
        query = row.get('query')
        if (
            row.get('kind') == 'search'
            and row.get('status') == 'no_material_fact'
            and company_id in retained
            and isinstance(query, str)
            and query not in retained[company_id]
        ):
            retained[company_id].append(query)
    return retained


def main(
    argv: list[str] | None = None,
    *,
    source_client_factory: Callable[[], SourceClient] = HttpSourceClient,
    search_client_factory: Callable[[], SearchClient] = BingSearchClient,
    emit: Callable[[str], None] = print,
) -> int:
    parser = argparse.ArgumentParser(
        description='Research the approved company corpus',
    )
    parser.add_argument('--stage', required=True)
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--allow-paid', action='store_true')
    parser.add_argument('--paid-cap-usd', default='0')
    args = parser.parse_args(argv)
    if args.paid_cap_usd != '0' and not args.allow_paid:
        parser.error('--paid-cap-usd requires --allow-paid')
    if args.allow_paid and args.paid_cap_usd != CORPUS_PAID_CAP_USD:
        parser.error(f'paid corpus cap must be exactly {CORPUS_PAID_CAP_USD}')

    fixtures = _stage_fixtures(args.stage)
    ids = [item.id for item in fixtures]
    summary = {
        'stage': args.stage, 'company_ids': ids,
        'duplicate_ids': len(ids) - len(set(ids)),
        'paid_cap_usd': CORPUS_PAID_CAP_USD if args.allow_paid else '0',
        'paid_cost_usd': '0', 'dossiers_valid': 0, 'resumed': 0,
        'source_repurchases': 0, 'authentication_gaps': [],
        'source_gaps': [], 'invalid_artifacts': [],
        'sources_persisted': 0, 'companies_saturated': 0,
        'dry_angles': {item.id: [] for item in fixtures},
        'unknown_reasons': {
            item.id: {
                field: 'not established after executed source and dry-angle research'
                for field in REQUIRED_DOSSIER_FIELDS
                if field not in {'identity', 'description', 'offers'}
            }
            for item in fixtures
        },
    }
    if args.dry_run:
        summary['mode'] = 'dry_run'
        emit(json.dumps(summary, sort_keys=True))
        return 0

    args.run_dir.mkdir(parents=True, exist_ok=True)
    if args.resume:
        valid, invalid = _resume_state(fixtures, args.run_dir)
        summary['resumed'] = len(valid)
        summary['dossiers_valid'] = len(valid)
        summary['invalid_artifacts'] = list(invalid)
        summary['dry_angles'] = _retained_dry_angles(args.run_dir, ids)
        if invalid:
            _append_stage_report(args.run_dir, summary)
            emit(json.dumps(summary, sort_keys=True))
            return 2
        try:
            AutoresearchOrchestrator(
                ArtifactStore(args.run_dir / 'outer'),
                _outer_roles(
                    lambda envelope: (_ for _ in ()).throw(
                        RuntimeError('completed outer run unexpectedly executed'),
                    ),
                    lambda: True,
                ),
            ).run(_outer_request(args.stage, ids))
        except Exception:
            summary['invalid_artifacts'] = ['outer_run']
            summary['resumed'] = 0
            summary['dossiers_valid'] = 0
            _append_stage_report(args.run_dir, summary)
            emit(json.dumps(summary, sort_keys=True))
            return 2
        _append_stage_report(args.run_dir, summary)
        emit(json.dumps(summary, sort_keys=True))
        return 0

    source_client = source_client_factory()
    search_client = search_client_factory()
    calls = CallLedger(args.run_dir / 'calls.jsonl')
    budget = BudgetLedger(
        args.run_dir / 'budget.jsonl',
        {'corpus-build': CORPUS_PAID_CAP_USD if args.allow_paid else '0'},
    )
    before_objects = len(list(
        (args.run_dir / 'evidence' / 'objects').glob('*.json'),
    ))
    completed = []

    def execute_stage(envelope):
        if envelope.payload['execution_inputs']['stage'] != args.stage:
            raise ValueError('outer executor received the wrong stage')
        outer_evidence = []
        for fixture in fixtures:
            try:
                dossier, _qualified, dry = _run_company(
                    fixture, args.run_dir, source_client, search_client,
                    calls, budget,
                )
                completed.append(fixture.id)
                summary['dossiers_valid'] += 1
                summary['companies_saturated'] += 1
                summary['dry_angles'][fixture.id] = list(dry)
                outer_evidence.append(OuterEvidence(
                    '1.0', dossier.evidence[0].url,
                    f'{fixture.id} research-complete dossier',
                    datetime.now(timezone.utc).isoformat(),
                ))
            except Exception as error:
                reason = str(error)
                if 'authentication_required' in reason:
                    summary['authentication_gaps'].append(fixture.id)
                else:
                    summary['source_gaps'].append(fixture.id)
                outer_evidence.append(OuterEvidence(
                    '1.0', 'https://' + fixture.domain,
                    f'{fixture.id} incomplete research attempt',
                    datetime.now(timezone.utc).isoformat(),
                ))
        return tuple(outer_evidence)

    AutoresearchOrchestrator(
        ArtifactStore(args.run_dir / 'outer'),
        _outer_roles(execute_stage, lambda: len(completed) == len(fixtures)),
    ).run(_outer_request(args.stage, ids))
    after_objects = len(list(
        (args.run_dir / 'evidence' / 'objects').glob('*.json'),
    ))
    summary['sources_persisted'] = after_objects - before_objects
    summary['paid_cost_usd'] = str(budget.spent('corpus-build'))
    if summary['dossiers_valid'] == len(fixtures):
        rollout = BenchmarkRollout(
            _companies(), journal=args.run_dir / 'rollout.jsonl',
        )
        if rollout.current_stage == args.stage:
            rollout.complete(ids)
    _append_stage_report(args.run_dir, summary)
    emit(json.dumps(summary, sort_keys=True))
    return 0 if summary['dossiers_valid'] == len(fixtures) else 2
