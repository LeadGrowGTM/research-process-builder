from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
import html
from io import BytesIO
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unicodedata
from types import SimpleNamespace
from typing import Callable, Protocol
from urllib.parse import quote_plus, urlparse
from urllib.error import HTTPError
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
from .benchmark_schedule import ROLLOUT_STAGES
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

def _load_source_plan(path: Path = Path('benchmarks/company-source-plan.yaml')):
    value = yaml.safe_load(Path(path).read_text(encoding='utf-8'))
    if (
        not isinstance(value, dict)
        or value.get('version') != '1.0'
        or not isinstance(value.get('companies'), dict)
    ):
        raise ValueError('invalid company source plan')
    allowed_company = {
        'sources', 'canonical_domain',
        'primary_funding_url', 'primary_funding_date',
        'entity_aliases', 'local_listing_url', 'local_name',
        'local_location_url',
        'local_street_postal', 'local_phone', 'local_locality',
        'listing_match_basis',
        'b2b_buyer_phrase', 'business_offer_phrase',
        'enrichment_assertions',
        'require_reviewed_qualification',
    }
    allowed_source = {'url', 'source_type', 'provider'}
    reviewed = value.get('qualification_phrases', {})
    if reviewed and not isinstance(reviewed, dict):
        raise ValueError('qualification phrases must be a mapping')
    plan = {}
    for company_id, record in value['companies'].items():
        if not isinstance(record, dict) or set(record) - allowed_company:
            raise ValueError('source plan contains unsupported company fields')
        sources = record.get('sources', [])
        if not isinstance(sources, list):
            raise ValueError('source plan sources must be a list')
        normalized = []
        for source in sources:
            if not isinstance(source, dict) or set(source) != allowed_source:
                raise ValueError('source plan entries contain unsupported fields')
            normalized.append(dict(source))
        normalized_record = {
            **({'canonical_domain': record['canonical_domain']}
               if 'canonical_domain' in record else {}),
            **({'sources': tuple(normalized)} if normalized else {}),
        }
        if 'entity_aliases' in record:
            aliases = record['entity_aliases']
            if (
                not isinstance(aliases, list) or not aliases
                or any(not isinstance(item, str) or not item.strip() for item in aliases)
            ):
                raise ValueError('entity aliases must be non-empty text')
            normalized_record['entity_aliases'] = tuple(aliases)
        if 'primary_funding_url' in record or 'primary_funding_date' in record:
            if not {
                'primary_funding_url', 'primary_funding_date',
            }.issubset(record):
                raise ValueError('funding plan requires URL and date')
            funding_url = record['primary_funding_url']
            if not isinstance(funding_url, str) or not funding_url.startswith(
                ('http://', 'https://')
            ):
                raise ValueError('invalid primary funding URL')
            try:
                funding_date = date.fromisoformat(record['primary_funding_date'])
            except (TypeError, ValueError) as error:
                raise ValueError('invalid primary funding date') from error
            if not date(2025, 8, 12) <= funding_date <= AS_OF:
                raise ValueError('primary funding date outside corpus window')
            normalized_record.update({
                'primary_funding_url': funding_url,
                'primary_funding_date': funding_date.isoformat(),
            })
        local_fields = {
            'local_listing_url', 'local_location_url', 'local_name',
            'listing_match_basis',
        }
        if any(key.startswith('local_') or key == 'listing_match_basis'
               for key in record):
            if not local_fields.issubset(record):
                raise ValueError('local plan requires listing, name, and match basis')
            basis = record['listing_match_basis']
            required = {
                'name_address': {'local_street_postal'},
                'name_phone': {'local_phone'},
                'name_address_phone': {'local_street_postal', 'local_phone'},
                'name_locality': {'local_locality'},
            }
            if basis not in required or not required[basis].issubset(record):
                raise ValueError('invalid local listing match basis')
            local_url = record['local_listing_url']
            if not isinstance(local_url, str) or not local_url.startswith(
                ('http://', 'https://')
            ):
                raise ValueError('invalid local listing URL')
            for key in local_fields | required[basis]:
                if key == 'listing_match_basis':
                    continue
                if not isinstance(record[key], str) or not record[key].strip():
                    raise ValueError('local plan fields must be non-empty text')
            normalized_record.update({
                key: record[key] for key in (
                    'local_listing_url', 'local_location_url', 'local_name',
                    'local_street_postal', 'local_phone', 'local_locality',
                    'listing_match_basis',
                ) if key in record
            })
        for key in ('b2b_buyer_phrase', 'business_offer_phrase'):
            if key in record:
                if not isinstance(record[key], str) or not record[key].strip():
                    raise ValueError('qualification phrases must be non-empty text')
                normalized_record[key] = record[key].strip()
        if company_id in reviewed:
            phrases = reviewed[company_id]
            if (
                not isinstance(phrases, dict)
                or set(phrases) != {'buyer', 'offer'}
                or any(not isinstance(item, str) or not item.strip()
                       for item in phrases.values())
            ):
                raise ValueError('reviewed qualification phrases are invalid')
            normalized_record.update({
                'b2b_buyer_phrase': phrases['buyer'].strip(),
                'business_offer_phrase': phrases['offer'].strip(),
                'require_reviewed_qualification': True,
            })
        if 'enrichment_assertions' in record:
            configured = record['enrichment_assertions']
            if not isinstance(configured, dict) or not configured:
                raise ValueError('enrichment assertions must be a mapping')
            source_urls = {item['url'] for item in normalized}
            normalized_assertions = {}
            for enrichment_id, assertions in configured.items():
                if enrichment_id not in P0_ENRICHMENTS or not isinstance(assertions, list):
                    raise ValueError('invalid enrichment assertion definition')
                seen_fields = set()
                items = []
                for assertion in assertions:
                    if (
                        not isinstance(assertion, dict)
                        or set(assertion) != {
                            'field', 'value', 'evidence_phrase', 'source_url',
                            'material_queries',
                        }
                    ):
                        raise ValueError('invalid enrichment assertion fields')
                    field = assertion['field']
                    source_url = assertion['source_url']
                    queries = assertion['material_queries']
                    if (
                        field not in P0_ENRICHMENTS[enrichment_id]
                        or field in seen_fields
                        or not isinstance(assertion['value'], str)
                        or not assertion['value'].strip()
                        or not isinstance(assertion['evidence_phrase'], str)
                        or not assertion['evidence_phrase'].strip()
                        or source_url not in source_urls
                        or not isinstance(queries, list) or not queries
                        or any(not isinstance(query, str) or not query.strip()
                               for query in queries)
                    ):
                        raise ValueError('invalid enrichment assertion definition')
                    seen_fields.add(field)
                    items.append({
                        'field': field, 'value': assertion['value'].strip(),
                        'evidence_phrase': assertion['evidence_phrase'].strip(),
                        'source_url': source_url,
                        'material_queries': tuple(queries),
                    })
                normalized_assertions[enrichment_id] = tuple(items)
            normalized_record['enrichment_assertions'] = normalized_assertions
        plan[str(company_id)] = normalized_record
    return plan

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


def _dry_queries(
    fixture: CompanyFixture, enrichment_id: str,
) -> tuple[str, ...]:
    angles = {
        'analogy-value-translator': (
            'public pricing exact dollar amount',
            'documented technology stack integration architecture',
        ),
        'company-description': (
            'official business offer customer description',
            'independent company product service profile',
        ),
        'competitor-intelligence': (
            'named competitors alternatives comparison',
            'competitive landscape market alternatives',
        ),
        'growth-signals': (
            'funding acquisition revenue growth dated',
            'audited annual growth expansion filing',
        ),
        'icp-persona-analysis': (
            'customer case study buyer role industry',
            'ideal customer persona decision maker',
        ),
        'job-opportunity-mining': (
            'verified current careers job openings',
            'current hiring roles location department',
        ),
        'news-product-launches': (
            'official latest news announcement dated',
            'new product launch release dated',
        ),
        'running-ads-offer-intelligence': (
            'verified active ad transparency creative started date',
            'current advertising library offer creative',
        ),
    }
    try:
        suffixes = angles[enrichment_id]
    except KeyError as error:
        raise ValueError('unknown enrichment dry-query scope') from error
    return tuple(f'{fixture.company_name} {suffix}' for suffix in suffixes)

@dataclass(frozen=True, slots=True)
class ResearchSource:
    url: str
    source_type: str
    provider: str
    content: str


@dataclass(frozen=True, slots=True)
class SearchHit:
    url: str
    title: str
    snippet: str


@dataclass(frozen=True, slots=True)
class SearchOutcome:
    material_facts_added: bool
    result_count: int = 0
    urls: tuple[str, ...] = ()
    hits: tuple[SearchHit, ...] = ()

    def __post_init__(self):
        object.__setattr__(self, 'urls', tuple(self.urls))
        object.__setattr__(self, 'hits', tuple(self.hits))


class SourceClient(Protocol):
    def fetch(self, fixture: CompanyFixture, url: str) -> str: ...


class SearchClient(Protocol):
    def search(self, fixture: CompanyFixture, query: str) -> SearchOutcome: ...


class CacheOnlyClient:
    def fetch(self, fixture: CompanyFixture, url: str) -> str:
        raise RuntimeError('cache_only_source_miss')

    def search(self, fixture: CompanyFixture, query: str) -> SearchOutcome:
        raise RuntimeError('cache_only_search_miss')


class HttpSourceClient:
    def __init__(self, *, contact_email_resolver=None):
        self._contact_email_resolver = (
            contact_email_resolver or self._git_contact_email
        )

    @staticmethod
    def _git_contact_email():
        completed = subprocess.run(
            ['git', 'config', '--get', 'user.email'],
            check=False, capture_output=True, text=True,
        )
        return completed.stdout.strip()

    def fetch(self, fixture: CompanyFixture, url: str) -> str:
        def retrieve(target, *, browser_fallback=True):
            headers = {
                'User-Agent': 'Mozilla/5.0',
                'Accept': 'text/html,application/xhtml+xml',
            }
            if _domain_key(target) == 'sec.gov':
                contact = self._contact_email_resolver()
                if not isinstance(contact, str) or not re.fullmatch(
                    r'[^\s@]+@[^\s@]+\.[^\s@]+', contact,
                ):
                    raise PermissionError('SEC contact email is not configured')
                headers['User-Agent'] = (
                    'research-process-builder/1.0 ' + contact
                )
            def read(request_headers):
                request = Request(target, headers=request_headers)
                with urlopen(request, timeout=90) as response:
                    resolved = (
                        response.geturl()
                        if callable(getattr(response, 'geturl', None))
                        else target
                    )
                    if _domain_key(resolved) != _domain_key(target):
                        raise ValueError(
                            'source redirected outside canonical domain'
                        )
                    return response.read(
                        6_000_000 if urlparse(target).path.casefold().endswith('.pdf')
                        else 1_000_000
                    )
            try:
                return read(headers)
            except HTTPError as error:
                if (
                    error.code not in {403, 406} or not browser_fallback
                    or _domain_key(target) == 'sec.gov'
                ):
                    raise
                headers.update({
                    'User-Agent': (
                        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                        'AppleWebKit/537.36 (KHTML, like Gecko) '
                        'Chrome/127.0.0.0 Safari/537.36'
                    ),
                    'Accept-Language': 'en-US,en;q=0.9',
                })
                try:
                    return read(headers)
                except HTTPError as fallback_error:
                    if fallback_error.code not in {403, 406}:
                        raise
                    curl_args = [
                        'curl.exe', '--location', '--fail', '--silent',
                        '--show-error', '--max-time', '90',
                        '--max-filesize', '6000000',
                        '--user-agent', headers['User-Agent'],
                        '--header', 'Accept: ' + headers['Accept'],
                        '--header', 'Accept-Language: en-US,en;q=0.9',
                        '--write-out', '\n%{url_effective}',
                        target,
                    ]
                    # A WAF can transiently reject one otherwise identical
                    # request. Retry exactly once; both attempts remain bounded
                    # by curl's 90-second deadline and six-megabyte ceiling.
                    for _attempt in range(2):
                        completed = subprocess.run(
                            curl_args, check=False, capture_output=True,
                        )
                        if completed.returncode == 0 and completed.stdout:
                            try:
                                body, effective = completed.stdout.rsplit(b'\n', 1)
                                effective_url = effective.decode('utf-8')
                            except (ValueError, UnicodeDecodeError) as error:
                                raise ValueError(
                                    'curl response lacks effective URL provenance'
                                ) from error
                            if _domain_key(effective_url) != _domain_key(target):
                                raise ValueError(
                                    'source redirected outside canonical domain'
                                )
                            if len(body) > 6_000_000:
                                raise ValueError(
                                    'curl response exceeds source limit'
                                )
                            return body
                    raise fallback_error
        try:
            raw = retrieve(url)
        except Exception:
            parsed = urlparse(url)
            if parsed.hostname != fixture.domain or parsed.hostname.startswith('www.'):
                raise
            raw = retrieve('https://www.' + fixture.domain + (parsed.path or ''))
        if urlparse(url).path.casefold().endswith('.pdf'):
            from pypdf import PdfReader
            text = ' '.join(
                page.extract_text() or ''
                for page in PdfReader(BytesIO(raw)).pages
            )
            return re.sub(r'\s+', ' ', text).strip()[:20_000]
        response_charset = 'utf-8'
        try:
            body = raw.decode('utf-8')
        except UnicodeDecodeError:
            body = raw.decode(response_charset, errors='replace')
        metadata_dates = re.findall(
            r'<meta[^>]+(?:published_time|datePublished)[^>]+content=[^0-9]*'
            r'(20\d{2}-\d{2}-\d{2})',
            body, flags=re.I,
        )
        visible_dates = re.findall(
            r'(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|'
            r'Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|'
            r'Nov(?:ember)?|Dec(?:ember)?)\.?\s+\d{1,2},?\s+20\d{2}'
            r'|\d{1,2}\s+(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|'
            r'May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|'
            r'Nov(?:ember)?|Dec(?:ember)?)\.?\s+20\d{2}',
            body, flags=re.I,
        )
        text = re.sub(r'<script[^>]*>.*?</script>', ' ', body, flags=re.I | re.S)
        text = re.sub(r'<style[^>]*>.*?</style>', ' ', text, flags=re.I | re.S)
        text = re.sub(r'<[^>]+>', ' ', text)
        normalized = html.unescape(re.sub(r'\s+', ' ', text).strip())
        dates = tuple(dict.fromkeys((*metadata_dates, *visible_dates)))
        return (' '.join(dates) + ' ' + normalized).strip()[:20_000]


class BingSearchClient:
    def search(self, fixture: CompanyFixture, query: str) -> SearchOutcome:
        url = 'https://www.bing.com/search?format=rss&q=' + quote_plus(query)
        request = Request(url, headers={'User-Agent': 'research-process-builder/1.0'})
        with urlopen(request, timeout=30) as response:
            root = ET.fromstring(response.read(500_000))
        items = root.findall('.//item')
        hits = tuple(
            SearchHit(
                item.findtext('link', ''),
                item.findtext('title', ''),
                item.findtext('description', ''),
            )
            for item in items
            if item.findtext('link', '').startswith(('http://', 'https://'))
        )
        urls = tuple(item.url for item in hits)
        company_key = _entity_key(fixture.company_name)
        material = False
        for item in items:
            text = ' '.join(item.findtext(name, '') for name in ('title', 'description'))
            normalized = _entity_key(text)
            if company_key not in normalized:
                continue
            lowered = text.casefold()
            query_lower = query.casefold()
            if (
                ('ad transparency' in query_lower and 'active ad' in lowered)
                or ('pricing' in query_lower and re.search(r'\$\s?\d', text))
                or ('audited' in query_lower and 'audited' in lowered and 'revenue' in lowered)
                or ('funding' in query_lower and re.search(r'funding|investment', lowered) and re.search(r'\d', text))
                or ('hiring' in query_lower and re.search(r'job opening|hiring now', lowered))
                or ('procurement' in query_lower and 'contract award' in lowered)
                or (
                    'technology stack' in query_lower
                    and re.search(r'technology stack|builtwith|tech stack', lowered)
                )
                or (
                    ('competitor' in query_lower or 'alternatives' in query_lower)
                    and re.search(r'competitors?|alternatives?', lowered)
                )
                or (
                    ('latest news' in query_lower or 'announcement' in query_lower)
                    and re.search(r'announces?|announcement|press release', lowered)
                )
                or (
                    'product launch' in query_lower
                    and re.search(r'launch(?:es|ed)?|new product', lowered)
                )
            ):
                material = True
                break
        return SearchOutcome(material, len(items), urls, hits)


class CallLedger:
    def __init__(self, path: Path):
        self.path = Path(path)

    def append(self, **row) -> None:
        allowed = {
            'company_id', 'enrichment_id', 'failure', 'kind', 'status',
            'url', 'query',
        }
        if set(row) - allowed:
            raise ValueError('call ledger contains unsupported fields')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open('a', encoding='utf-8', newline='\n') as stream:
            stream.write(json.dumps(row, sort_keys=True, separators=(',', ':')) + '\n')
            stream.flush()
            os.fsync(stream.fileno())


def _entity_key(value: str) -> str:
    decomposed = unicodedata.normalize('NFKD', value.casefold())
    return ''.join(
        character for character in decomposed
        if character.isalnum() and not unicodedata.combining(character)
    )


def _domain_key(url: str) -> str:
    host = (urlparse(url).hostname or '').casefold().removeprefix('www.')
    labels = host.split('.')
    return '.'.join(labels[-2:]) if len(labels) >= 2 else host


def _validate_source_pack(
    fixture: CompanyFixture, sources: tuple[ResearchSource, ...],
) -> None:
    first_party = [item for item in sources if item.source_type == 'first_party']
    independent = [item for item in sources if item.source_type == 'independent']
    if not first_party or len(independent) < 2:
        raise ValueError(
            'source pack requires a first-party and two independent sources'
        )
    domains = {_domain_key(item.url) for item in independent}
    if len(domains) < 2:
        raise ValueError('source pack requires distinct independent domains')
    aliases = _load_source_plan().get(fixture.id, {}).get(
        'entity_aliases', (fixture.company_name,),
    )
    company_keys = tuple(_entity_key(item) for item in aliases)
    for item in sources:
        if len(item.content) < 200:
            raise ValueError('source response is too thin')
        if not any(key in _entity_key(item.content) for key in company_keys):
            raise ValueError('source failed entity relevance')
    canonical_domain = _domain_key('https://' + fixture.domain)
    if any(_domain_key(item.url) != canonical_domain for item in first_party):
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
    source_plan = _load_source_plan()
    return tuple(
        replace(
            by_id[item],
            domain=source_plan.get(item, {}).get(
                'canonical_domain', by_id[item].domain,
            ),
        )
        for item in rollout.current_company_ids
    )


def _discover_source_specs(
    fixture: CompanyFixture, search_client: SearchClient, calls: CallLedger,
):
    source_plan = _load_source_plan()
    planned = source_plan.get(fixture.id, {}).get('sources')
    if planned:
        return tuple(
            (item['url'], item['source_type'], item['provider'])
            for item in planned
        )
    qualifiers = {
        'recently_funded_b2b': 'recent funding dated primary announcement',
        'local_b2b_services': 'local business listing Yelp BBB',
    }
    suffix = qualifiers.get(fixture.primary_cohort, 'company profile reviews')
    quoted = chr(34) + fixture.company_name + chr(34)
    queries = tuple(
        f'source discovery: {quoted} site:{domain}'
        for domain in (
            'g2.com', 'capterra.com', 'softwareadvice.com', 'getapp.com',
            'sourceforge.net',
        )
    ) + (
        f'source discovery: {fixture.company_name} {suffix}',
        f'source discovery: {fixture.company_name} company news profile',
    )
    candidates = []
    official_key = _domain_key('https://' + fixture.domain)
    if fixture.linkedin_company_url:
        candidates.append(fixture.linkedin_company_url)
    generic_tokens = {
        'company', 'source', 'reviews', 'profile', 'business', 'group',
        'services', 'solutions', 'technologies', 'international',
    }
    distinctive = {
        _entity_key(token)
        for token in re.findall(r'[A-Za-z0-9]+', fixture.company_name)
        if len(_entity_key(token)) >= 4 and _entity_key(token) not in generic_tokens
    }
    distinctive.add(_entity_key(fixture.domain.split('.')[0]))
    for query in queries:
        try:
            outcome = search_client.search(fixture, query)
            calls.append(
                company_id=fixture.id, kind='search', query=query,
                status='material_fact' if outcome.urls else 'no_material_fact',
            )
        except Exception:
            calls.append(
                company_id=fixture.id, kind='search', query=query,
                status='failed', failure='terminal',
            )
            continue
        hits = outcome.hits or tuple(
            SearchHit(url, '', '') for url in outcome.urls
        )
        for hit in hits:
            url = hit.url
            key = _domain_key(url)
            relevance_text = _entity_key(
                url + ' ' + hit.title + ' ' + hit.snippet
            )
            if (
                url.startswith(('http://', 'https://'))
                and key
                and key != official_key
                and key not in {_domain_key(item) for item in candidates}
                and key not in {'bing.com', 'microsoft.com'}
                and any(token and token in relevance_text for token in distinctive)
            ):
                candidates.append(url)
        if len(candidates) >= 2:
            break
    return (
        ('https://' + fixture.domain, 'first_party', PROVIDER_IDS[0]),
        *tuple(
            (url, 'independent', PROVIDER_IDS[index + 1])
            for index, url in enumerate(candidates[:2])
        ),
    )


class _GtmProbe:
    def probe(self):
        return ProbeResult(
            'gtm', ProbeStatus.AVAILABLE,
            {'path': 'company-enrichment-cli', 'version': '1.0'},
        )


class _NexusProbe:
    def probe(self, enrichment_id):
        return ProbeResult('nexus', ProbeStatus.UNAVAILABLE)


def _runtime_definitions(provider_ids=PROVIDER_IDS):
    return {
        enrichment_id: SimpleNamespace(
            id=enrichment_id, output_schema_version='1.0',
            required_inputs=('company_name', 'domain', 'seller_context'),
            fallback_order=tuple(provider_ids), freshness_days=30,
            caps={'retries': 2}, output_visibility='message_safe',
        )
        for enrichment_id in EXPECTED_P0_IDS
    }


def _discovery(run_dir: Path, provider_ids=PROVIDER_IDS):
    registry = CapabilityRegistry(tuple(
        Capability(
            provider_id, 'live-source', ('scrape',), index,
            provenance='company-enrichment-cli', cost_class='free',
            validation_state='available',
        )
        for index, provider_id in enumerate(provider_ids, 1)
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
        run_searches=False, enrichment_assertions=None,
    ):
        self.fixture = fixture
        self.url, self.source_type, self.provider = spec
        self.source_client = source_client
        self.search_client = search_client
        self.calls = calls
        self.run_searches = run_searches
        self.enrichment_assertions = enrichment_assertions or {}
        self.enrichment_id = None

    def _material_resolution(self, enrichment_id, query):
        for assertion in self.enrichment_assertions.get(enrichment_id, ()):
            if query in assertion['material_queries']:
                return (query, assertion['field'], assertion['source_url'])
        return None

    def cached_metadata(self):
        if (
            not self.run_searches or not self.calls.path.exists()
            or self.enrichment_id is None
        ):
            return None
        expected = set(_dry_queries(self.fixture, self.enrichment_id))
        dry = []
        resolved = []
        observed = set()
        for line in self.calls.path.read_text(encoding='utf-8').splitlines():
            row = json.loads(line)
            if (
                row.get('company_id') == self.fixture.id
                and row.get('enrichment_id') == self.enrichment_id
                and row.get('kind') == 'search'
                and row.get('query') in expected
                and row.get('status') in {'no_material_fact', 'material_fact'}
                and row.get('query') not in observed
            ):
                observed.add(row['query'])
                if row['status'] == 'no_material_fact':
                    dry.append(row['query'])
                else:
                    resolution = self._material_resolution(
                        self.enrichment_id, row['query'],
                    )
                    if resolution is not None:
                        resolved.append(resolution)
        if observed != expected or len(dry) + len(resolved) < 2:
            return None
        return AdapterResponse(
            SourceRecord(
                self.url, NOW, self.source_type, self.provider,
                'retained cache metadata', 'retained cache metadata', 30, '0',
            ),
            dry_angles=tuple(dry[:2]), resolved_model=self.resolved_model,
            resolved_material_angles=tuple(resolved),
        )

    def source_url(self, request, default):
        self.enrichment_id = request.enrichment_id
        return self.url

    def research_metadata(self, request):
        if not self.run_searches:
            return None
        return self._research_metadata(request)

    def _research_metadata(self, request):
        dry = []
        resolved = []
        for query in _dry_queries(self.fixture, request.enrichment_id):
            try:
                outcome = self.search_client.search(self.fixture, query)
            except Exception:
                self.calls.append(
                    company_id=self.fixture.id,
                    enrichment_id=request.enrichment_id,
                    kind='search', query=query,
                    status='failed', failure='terminal',
                )
                continue
            status = (
                'material_fact' if outcome.material_facts_added
                else 'no_material_fact'
            )
            self.calls.append(
                company_id=self.fixture.id,
                enrichment_id=request.enrichment_id,
                kind='search', query=query, status=status,
            )
            if not outcome.material_facts_added:
                dry.append(query)
            else:
                resolution = self._material_resolution(request.enrichment_id, query)
                if resolution is not None:
                    resolved.append(resolution)
        return AdapterResponse(
            SourceRecord(
                self.url, NOW, self.source_type, self.provider,
                'research metadata', 'research metadata', 30, '0',
            ),
            dry_angles=tuple(dry), resolved_model=self.resolved_model,
            resolved_material_angles=tuple(resolved),
        )

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
        metadata = self.cached_metadata() if self.run_searches else None
        if metadata is None and self.run_searches:
            metadata = self._research_metadata(request)
        return AdapterResponse(
            SourceRecord(
                url, datetime.now(timezone.utc), self.source_type, self.provider,
                content, content[:2_000], 30, '0',
            ),
            actual_cost_usd='0',
            dry_angles=metadata.dry_angles if metadata else (),
            resolved_material_angles=(
                metadata.resolved_material_angles if metadata else ()
            ),
            resolved_model=self.resolved_model,
        )


def _supports(content: str, phrase: str) -> bool:
    normalized = _entity_key(content)
    return all(
        _entity_key(token) in normalized
        for token in phrase.split()
        if len(_entity_key(token)) > 2
    )


def _nap_tokens(value: str) -> frozenset[str]:
    tokens = re.findall(r'[a-z0-9]+', unicodedata.normalize(
        'NFKD', value.casefold(),
    ).encode('ascii', 'ignore').decode('ascii'))
    aliases = {
        'east': 'e', 'west': 'w', 'north': 'n', 'south': 's',
        'street': 'st', 'avenue': 'ave', 'road': 'rd', 'drive': 'dr',
        'boulevard': 'blvd', 'parkway': 'pkwy', 'suite': 'ste',
        'alberta': 'ab',
    }
    normalized = []
    index = 0
    while index < len(tokens):
        if (
            index + 1 < len(tokens)
            and tokens[index] in {'n', 's'}
            and tokens[index + 1] in {'e', 'w'}
        ):
            normalized.append(tokens[index] + tokens[index + 1])
            index += 2
            continue
        normalized.append(aliases.get(tokens[index], tokens[index]))
        index += 1
    compact_postal = re.search(r'\b([a-z]\d[a-z])\s*(\d[a-z]\d)\b', value, re.I)
    if compact_postal:
        normalized = [
            token for token in normalized
            if token not in {
                compact_postal.group(1).casefold(),
                compact_postal.group(2).casefold(),
            }
        ]
        normalized.append(
            (compact_postal.group(1) + compact_postal.group(2)).casefold()
        )
    return frozenset(normalized)


def _extract_qualification(
    fixture: CompanyFixture, sources: tuple[ResearchSource, ...],
    evidence: tuple[EvidenceRef, ...],
    *, qualification_plan=None,
):
    _validate_source_pack(fixture, sources)
    plan = (
        qualification_plan if qualification_plan is not None
        else _load_source_plan().get(fixture.id, {})
    )
    official_index = next(
        index for index, item in enumerate(sources)
        if item.source_type == 'first_party'
    )
    official = sources[official_index]
    official_evidence = evidence[official_index]
    buyer = None
    offer = None
    qualification_index = None
    planned_buyer = plan.get('b2b_buyer_phrase')
    planned_offer = plan.get('business_offer_phrase')
    if plan.get('require_reviewed_qualification') and not (
        isinstance(planned_buyer, str) and isinstance(planned_offer, str)
    ):
        raise ValueError('benchmark fixture requires reviewed qualification phrases')
    if plan.get('require_reviewed_qualification'):
        if planned_buyer.casefold() in {
            'business', 'businesses', 'customers', 'companies',
            'organizations', 'clients',
        }:
            raise ValueError('reviewed buyer phrase is not cohort-specific')
        offer_terms = {
            'b2b_saas': (
                'software', 'platform', 'automation', 'digital sales',
                'performance management', 'intelligence', 'link management',
            ),
            'recently_funded_b2b': (
                'software', 'platform', 'intelligence', 'scheduling', 'cards',
                'returns', 'finance', 'database', 'security', 'care coordination',
            ),
            'b2b_agencies': (
                'relations', 'advertising', 'marketing', 'creative', 'arts',
            ),
            'well_known_b2b': (
                'automation', 'graphics', 'heating', 'packaging', 'software',
                'benefits', 'motion control', 'dispensing', 'savings accounts',
                'distribution',
            ),
            'b2b_commerce_suppliers': (
                'supplies', 'cartons', 'manufacturing', 'flavors', 'automation',
                'equipment', 'panels', 'controls', 'packaging',
            ),
            'local_b2b_services': (
                'services', 'automation', 'service', 'accounting', 'testing',
                'flooring', 'staffing', 'consulting',
            ),
        }[fixture.primary_cohort]
        if not any(term in planned_offer.casefold() for term in offer_terms):
            raise ValueError('reviewed offer phrase does not establish cohort fit')
    buyer_terms = (
        'financial institutions', 'healthcare providers', 'marketing agencies',
        'finance teams', 'itops and itsm teams', 'accounting firms',
        'car dealers', 'small businesses', 'technology companies',
        'business leaders', 'building owners', 'business', 'manufacturers',
        'operators and distributors', 'professional services',
        'government agencies', 'enterprises', 'organizations', 'businesses',
        'companies', 'clients', 'customers', 'contractors', 'professionals',
        'providers', 'dealers', 'teams', 'brands',
    )
    offer_terms = (
        'digital sales room', 'performance management',
        'predictive procurement platform', 'building automation',
        'marketing and pr', 'marketing services', 'consulting services',
        'staffing solutions', 'accounting services', 'engineering services',
        'testing services', 'group benefit solutions',
        'motion control solutions', 'packaging solutions',
        'automation solutions', 'automated reports', 'dashboards', 'reports',
        'software', 'platform', 'services',
        'solutions', 'equipment', 'products', 'controls', 'analytics',
        'marketing', 'packaging', 'testing', 'staffing', 'accounting',
        'engineering',
    )
    enrichment_source_urls = {
        assertion['source_url']
        for assertions in plan.get('enrichment_assertions', {}).values()
        for assertion in assertions
    }
    for index, source in enumerate(sources):
        if source.url in enrichment_source_urls:
            continue
        lowered = source.content.casefold()
        if isinstance(planned_buyer, str) and isinstance(planned_offer, str):
            candidate_buyer = planned_buyer if _supports(
                source.content, planned_buyer,
            ) else None
            candidate_offer = planned_offer if _supports(
                source.content, planned_offer,
            ) else None
        else:
            candidate_buyer = None
            candidate_offer = None
            clean = re.sub(r'\s+', ' ', source.content)
            for match in re.finditer(
                r'(?:provides?|offers?)\s+(.{3,160}?)\s+for\s+'
                r'(.{3,100}?)(?:[.;]|$)', clean, flags=re.I,
            ):
                offered = match.group(1).strip(' ,:-')
                bought_by = match.group(2).strip(' ,:-')
                if (
                    any(term in bought_by.casefold() for term in buyer_terms)
                    and any(term in offered.casefold() for term in offer_terms)
                    and len(offered) <= 80
                ):
                    candidate_buyer, candidate_offer = bought_by, offered
                    break
            if not candidate_buyer:
                candidate_buyer = next(
                    (term for term in buyer_terms if term in lowered), None,
                )
                candidate_offer = next(
                    (term for term in offer_terms if term in lowered), None,
                )
        if candidate_buyer and candidate_offer:
            buyer, offer = candidate_buyer, candidate_offer
            qualification_index = index
            break
    if not buyer or not offer:
        raise ValueError('retained sources do not establish B2B buyer and offer')
    buyer = buyer.strip(' ,:-')
    offer = offer.strip(' ,:-')
    qualification_source = sources[qualification_index]
    qualification_evidence = evidence[qualification_index]
    qualified = replace(
        fixture, seed_status='verified', b2b_buyer=buyer,
        business_offer=offer,
        selection_reason=(
            'Retained sources establish a B2B buyer, offer, and '
            + fixture.primary_cohort + ' fit'
        ),
        cohort_evidence_url=qualification_source.url,
        secondary_tags=('b2b', fixture.primary_cohort),
        expected_ad_channels=('linkedin',),
    )
    record = {
        'b2b_buyer': buyer,
        'business_offer': offer,
        'cohort_evidence_url': qualification_source.url,
        'evidence_id': qualification_evidence.evidence_id,
        'seed_status': 'verified',
        'company_name': fixture.company_name,
        'domain': fixture.domain,
        'primary_cohort': fixture.primary_cohort,
    }
    if fixture.primary_cohort == 'recently_funded_b2b':
        planned_url = plan.get('primary_funding_url')
        try:
            planned_date = date.fromisoformat(plan['primary_funding_date'])
        except (KeyError, TypeError, ValueError):
            planned_date = None
        dated = []
        for source in reversed(sources):
            if source.provider not in {
                PROVIDER_IDS[0], PROVIDER_IDS[2],
                'funding-primary', 'funding-primary-v2', 'funding-primary-v3',
            }:
                continue
            if source.url != planned_url:
                continue
            funding_semantic = re.search(
                r'funding|investment|financing|series\s+[a-z]|offering|securities',
                source.content, re.I,
            )
            sec_form_d = (
                _domain_key(source.url) == 'sec.gov'
                and planned_date is not None
                and planned_date.isoformat() in source.content
                and re.search(r'X0708|FORM\s*D|totalOfferingAmount', source.content, re.I)
            )
            if not funding_semantic and not sec_form_d:
                continue
            for month, day, year in re.findall(
                r'(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),?\s+(20\d{2})',
                source.content,
                flags=re.I,
            ):
                parsed = datetime.strptime(
                    f'{month} {day} {year}', '%B %d %Y',
                ).date()
                if (
                    date(2025, 8, 12) <= parsed <= AS_OF
                    and parsed == planned_date
                ):
                    dated.append((
                        parsed,
                        source.provider == PROVIDER_IDS[2],
                        source.source_type == 'independent',
                        source.url,
                    ))
            for value in re.findall(r'\b20\d{2}-\d{2}-\d{2}\b', source.content):
                parsed = date.fromisoformat(value)
                if (
                    date(2025, 8, 12) <= parsed <= AS_OF
                    and parsed == planned_date
                ):
                    dated.append((
                        parsed, True,
                        source.source_type == 'independent', source.url,
                    ))
            month_pattern = (
                r'(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|'
                r'Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|'
                r'Nov(?:ember)?|Dec(?:ember)?)'
            )
            for day, month, year in re.findall(
                rf'(\d{{1,2}})\s+{month_pattern}\.?\s+(20\d{{2}})',
                source.content, flags=re.I,
            ):
                parsed = datetime.strptime(
                    f'{day} {month[:3]} {year}', '%d %b %Y',
                ).date()
                if parsed == planned_date:
                    dated.append((
                        parsed, True,
                        source.source_type == 'independent', source.url,
                    ))
            for month, day, year in re.findall(
                rf'{month_pattern}\.?\s+(\d{{1,2}}),?\s+(20\d{{2}})',
                source.content, flags=re.I,
            ):
                parsed = datetime.strptime(
                    f'{month[:3]} {day} {year}', '%b %d %Y',
                ).date()
                if parsed == planned_date:
                    dated.append((
                        parsed, True,
                        source.source_type == 'independent', source.url,
                    ))
        if not dated:
            raise ValueError('recently funded fixture lacks a dated primary source')
        funding_date, _primary_rank, _independent_rank, funding_url = max(dated)
        qualified = replace(
            qualified, primary_funding_url=funding_url,
            primary_funding_date=funding_date,
        )
        record.update({
            'primary_funding_url': funding_url,
            'primary_funding_date': funding_date.isoformat(),
        })
    if fixture.primary_cohort == 'local_b2b_services':
        listing_url = plan.get('local_listing_url')
        location_url = plan.get('local_location_url')
        listing_index = next(
            (index for index, source in enumerate(sources)
             if source.url == listing_url), None,
        )
        if listing_index is None:
            raise ValueError('planned local listing is not retained Evidence')
        listing_source = sources[listing_index]
        location_source = next(
            (source for source in sources if source.url == location_url), None,
        )
        if location_source is None:
            raise ValueError('planned local location is not retained Evidence')
        name_key = _entity_key(plan.get('local_name', ''))
        if not name_key or any(
            name_key not in _entity_key(source.content)
            for source in (location_source, listing_source)
        ):
            raise ValueError('planned local listing name does not match official')
        basis = plan.get('listing_match_basis', 'name_address_phone')
        if basis in {'name_address', 'name_address_phone'}:
            address_tokens = _nap_tokens(plan.get('local_street_postal', ''))
            if not address_tokens or any(
                not address_tokens.issubset(_nap_tokens(source.content))
                for source in (location_source, listing_source)
            ):
                raise ValueError('planned local listing address does not match official')
        if basis in {'name_phone', 'name_address_phone'}:
            phone = re.sub(r'\D', '', plan.get('local_phone', ''))
            if not phone or any(
                phone not in re.sub(r'\D', '', source.content)
                for source in (location_source, listing_source)
            ):
                raise ValueError('planned local listing phone does not match official')
        if basis == 'name_locality':
            locality = _nap_tokens(plan.get('local_locality', ''))
            if not locality or any(
                not locality.issubset(_nap_tokens(source.content))
                for source in (location_source, listing_source)
            ):
                raise ValueError('planned local listing locality does not match official')
        qualified = replace(qualified, local_listing_url=listing_url)
        record.update({
            'local_listing_url': listing_url,
            'local_listing_evidence_id': evidence[listing_index].evidence_id,
            'listing_match_basis': basis,
        })
    return qualified, record


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


def _dossier_unknown_reasons(dossier: CompanyDossier) -> dict[str, str]:
    return {
        field: 'not established after executed source and dry-angle research'
        for field in dossier.unknowns
    }


def _run_company(
    fixture: CompanyFixture, run_dir: Path, source_client: SourceClient,
    search_client: SearchClient, calls: CallLedger, budget: BudgetLedger,
):
    evidence_store = EvidenceStore(run_dir / 'evidence')
    company_plan = _load_source_plan().get(fixture.id, {})
    enrichment_assertions = company_plan.get('enrichment_assertions', {})
    specs = _discover_source_specs(fixture, search_client, calls)
    adapters = {
        provider: _LiveAdapter(
            fixture, spec, source_client, search_client, calls,
            run_searches=(index == len(specs) - 1),
            enrichment_assertions=enrichment_assertions,
        )
        for index, spec in enumerate(specs)
        for provider in (spec[2],)
    }
    provider_ids = tuple(adapters)
    qualification_box = {}

    def executor(enrichment_id, evidence, *, seller_context, output_visibility):
        if enrichment_id != 'company-description':
            qualified = qualification_box['fixture']
            planned_assertions = enrichment_assertions.get(enrichment_id, ())
            if planned_assertions:
                findings = []
                cited = []
                for assertion in planned_assertions:
                    reference = next(
                        (item for item in evidence
                         if item.url == assertion['source_url']), None,
                    )
                    if reference is None:
                        raise ValueError(
                            'planned enrichment source is not retained Evidence'
                        )
                    source = evidence_store.get(reference.content_hash)
                    if not _supports(source.content, assertion['evidence_phrase']):
                        raise ValueError(
                            'planned enrichment fact is not supported by source'
                        )
                    findings.append(Finding(
                        assertion['field'], assertion['value'],
                    ))
                    cited.append(reference)
                asserted = {item.field for item in findings}
                return execute_p0(
                    enrichment_id, tuple(cited), seller_context=seller_context,
                    findings=tuple(findings),
                    unknowns=tuple(
                        field for field in P0_ENRICHMENTS[enrichment_id]
                        if field not in asserted
                    ),
                    output_visibility=output_visibility,
                )
            if enrichment_id == 'icp-persona-analysis':
                return execute_p0(
                    enrichment_id, evidence, seller_context=seller_context,
                    findings=(
                        Finding('icp', qualified.b2b_buyer),
                        Finding('personas', qualified.b2b_buyer),
                    ),
                    output_visibility=output_visibility,
                )
            if (
                enrichment_id == 'growth-signals'
                and qualified.primary_funding_date is not None
            ):
                funding_evidence = tuple(
                    item for item in evidence
                    if item.url == qualified.primary_funding_url
                )
                return execute_p0(
                    enrichment_id, funding_evidence,
                    seller_context=seller_context,
                    findings=(Finding(
                        'growth',
                        'Primary funding event dated '
                        + qualified.primary_funding_date.isoformat(),
                    ),), output_visibility=output_visibility,
                )
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
            fixture, sources, tuple(evidence), qualification_plan=company_plan,
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
        definitions=_runtime_definitions(provider_ids),
        discovery=_discovery(run_dir, provider_ids),
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
    dry_angles = tuple(
        angle
        for result in results
        for angle in result.output.get('dry_angles', ())
    )
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
        record = store.verify_reference(reference)
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
    _validate_search_provenance(fixture, dossier, run_dir)
    return dossier


def _validate_search_provenance(
    fixture: CompanyFixture, dossier: CompanyDossier, run_dir: Path,
) -> None:
    path = Path(run_dir) / 'calls.jsonl'
    if not path.exists():
        raise ValueError('resume dossier lacks search provenance')
    rows = [
        json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()
    ]
    plan = _load_source_plan().get(fixture.id, {})
    assertions = {item.field: item for item in dossier.assertions}
    evidence_urls = {
        item.evidence_id: item.url for item in dossier.evidence
    }
    for enrichment_id in EXPECTED_P0_IDS:
        expected = set(_dry_queries(fixture, enrichment_id))
        observed = {
            row.get('query'): row.get('status')
            for row in rows
            if row.get('company_id') == fixture.id
            and row.get('enrichment_id') == enrichment_id
            and row.get('kind') == 'search'
            and row.get('query') in expected
        }
        if set(observed) != expected:
            raise ValueError(
                f'resume dossier lacks category search provenance: {enrichment_id}'
            )
        for query, status in observed.items():
            if status == 'no_material_fact':
                continue
            if status != 'material_fact':
                raise ValueError('resume dossier has failed category search')
            resolution = next((
                item for item in plan.get('enrichment_assertions', {}).get(
                    enrichment_id, ()
                )
                if query in item['material_queries']
            ), None)
            assertion = assertions.get(resolution['field']) if resolution else None
            if (
                assertion is None
                or resolution['source_url'] not in {
                    evidence_urls.get(evidence_id)
                    for evidence_id in assertion.evidence_ids
                }
            ):
                raise ValueError(
                    'material search lacks exact cited resolved assertion'
                )


def _resume_state(fixtures, run_dir: Path):
    store = EvidenceStore(run_dir / 'evidence')
    valid = []
    invalid = []
    for fixture in fixtures:
        try:
            dossier = _validate_resume_company(fixture, run_dir, store)
            valid.append(fixture.id)
        except Exception:
            invalid.append(fixture.id)
    if not invalid:
        referenced = set()
        try:
            for path in (run_dir / 'dossiers').glob('*.yaml'):
                dossier = _rehydrate_dossier(path)
                referenced.update(
                    item.content_hash for item in dossier.evidence
                )
        except Exception:
            return (), ('orphaned_evidence_object',)
        objects = {
            path.stem for path in (run_dir / 'evidence' / 'objects').glob('*.json')
        }
        journal_hashes = {
            event['content_hash'] for event in store._read_events()
        }
        events = store._read_events()
        referenced_urls = {
            event['url'] for event in events
            if event['content_hash'] in referenced
        }
        def provider_lineage(provider):
            return re.sub(r'-v\d+$', '', provider)
        referenced_identities = {
            (event['url'], provider_lineage(event['provider']))
            for event in events if event['content_hash'] in referenced
        }
        superseded = {
            event['content_hash'] for event in events
            if (
                event['url'] in referenced_urls
                and (event['url'], provider_lineage(event['provider']))
                in referenced_identities
            )
        }
        try:
            for content_hash in objects:
                store.get(content_hash)
        except Exception:
            return (), ('orphaned_evidence_object',)
        dossier_count = len(list((run_dir / 'dossiers').glob('*.yaml')))
        scoped_objects = (
            objects if dossier_count == 60 else {
                event['content_hash'] for event in events
                if event['url'] in referenced_urls
            }
        )
        if (
            journal_hashes != objects
            or not referenced.issubset(objects)
            or not (scoped_objects - referenced).issubset(superseded)
        ):
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


def _all_fixtures() -> tuple[CompanyFixture, ...]:
    by_id = {}
    for stage in ROLLOUT_STAGES:
        for fixture in _stage_fixtures(stage):
            by_id[fixture.id] = fixture
    ordered_ids = BenchmarkRollout(_companies())._batches
    return tuple(
        by_id[company_id]
        for stage in ROLLOUT_STAGES
        for company_id in ordered_ids[stage]
    )


def _atomic_publish_files(
    dossiers: list[Path], staged_corpus: Path, benchmark_dir: Path,
) -> list[Path]:
    benchmark_dir = Path(benchmark_dir)
    destination = benchmark_dir / 'dossiers'
    backup = benchmark_dir / '.dossiers.publish.backup'
    corpus_path = benchmark_dir / 'companies.yaml'
    corpus_backup = benchmark_dir / '.companies.publish.backup.yaml'
    marker = benchmark_dir / '.publish-transaction.json'
    _recover_publish_transaction(benchmark_dir)
    if backup.exists() or corpus_backup.exists():
        raise ValueError('stale publication backup requires review')
    with tempfile.TemporaryDirectory(
        prefix='.dossiers.publish.', dir=benchmark_dir,
    ) as temporary_directory:
        staged = Path(temporary_directory) / 'dossiers'
        staged.mkdir()
        for source in dossiers:
            shutil.copyfile(source, staged / source.name)
        if len(list(staged.glob('*.yaml'))) != 60:
            raise ValueError('published dossier count is not exactly 60')
        moved_existing = False
        try:
            shutil.copyfile(corpus_path, corpus_backup)
            _write_json(marker, {
                'state': 'prepared', 'had_destination': destination.exists(),
            })
            if destination.exists():
                os.replace(destination, backup)
                moved_existing = True
            os.replace(staged, destination)
            _write_json(marker, {
                'state': 'dossiers_swapped',
                'had_destination': moved_existing,
            })
            os.replace(staged_corpus, corpus_path)
            _write_json(marker, {
                'state': 'committed',
                'had_destination': moved_existing,
            })
        except Exception:
            if destination.exists():
                shutil.rmtree(destination)
            if moved_existing:
                os.replace(backup, destination)
            if corpus_backup.exists():
                os.replace(corpus_backup, corpus_path)
            marker.unlink(missing_ok=True)
            raise
        else:
            if backup.exists():
                shutil.rmtree(backup)
            corpus_backup.unlink(missing_ok=True)
            marker.unlink(missing_ok=True)
    published = sorted(destination.glob('*.yaml'))
    if len(published) != 60:
        raise ValueError('published dossier count is not exactly 60')
    return published


def _recover_publish_transaction(benchmark_dir: Path) -> None:
    benchmark_dir = Path(benchmark_dir)
    destination = benchmark_dir / 'dossiers'
    backup = benchmark_dir / '.dossiers.publish.backup'
    corpus_path = benchmark_dir / 'companies.yaml'
    corpus_backup = benchmark_dir / '.companies.publish.backup.yaml'
    marker = benchmark_dir / '.publish-transaction.json'
    if not marker.exists():
        return
    transaction = json.loads(marker.read_text(encoding='utf-8'))
    state = transaction.get('state')
    if state == 'committed':
        if backup.exists():
            shutil.rmtree(backup)
        corpus_backup.unlink(missing_ok=True)
        marker.unlink()
        return
    if state not in {'prepared', 'dossiers_swapped'}:
        raise ValueError('invalid publication transaction state')
    had_destination = transaction.get('had_destination')
    if not isinstance(had_destination, bool):
        raise ValueError('publication transaction lacks destination state')
    if backup.exists():
        if destination.exists():
            shutil.rmtree(destination)
        os.replace(backup, destination)
    elif not had_destination and destination.exists():
        shutil.rmtree(destination)
    elif had_destination and not destination.exists():
        raise ValueError('publication recovery cannot restore dossier backup')
    if corpus_backup.exists():
        os.replace(corpus_backup, corpus_path)
    else:
        raise ValueError('publication recovery cannot restore corpus backup')
    marker.unlink()


def _publish_benchmarks(run_dir: Path):
    run_dir = Path(run_dir)
    dossiers = sorted((run_dir / 'dossiers').glob('*.yaml'))
    qualifications = sorted((run_dir / 'qualifications').glob('*.json'))
    if len(dossiers) != 60 or len(qualifications) != 60:
        raise ValueError('publish requires exactly 60 validated dossiers')
    fixtures = _all_fixtures()
    valid, invalid = _resume_state(fixtures, run_dir)
    if len(valid) != 60 or invalid:
        raise ValueError('publish requires exactly 60 validated dossiers')
    rollout = BenchmarkRollout(_companies(), journal=run_dir / 'rollout.jsonl')
    if rollout.current_stage is not None:
        raise ValueError('publish requires a completed rollout journal')

    corpus_path = Path('benchmarks/companies.yaml')
    payload = yaml.safe_load(corpus_path.read_text(encoding='utf-8'))
    records = {
        path.stem: json.loads(path.read_text(encoding='utf-8'))
        for path in qualifications
    }
    if set(records) != {item['id'] for item in payload['companies']}:
        raise ValueError('qualification IDs do not match benchmark corpus')
    conditional = {
        'primary_funding_url', 'primary_funding_date', 'local_listing_url',
    }
    for company in payload['companies']:
        record = records[company['id']]
        for key in (
            'company_name', 'domain', 'seed_status', 'b2b_buyer',
            'business_offer', 'cohort_evidence_url',
        ):
            company[key] = record[key]
        company['selection_reason'] = (
            'Retained sources establish a B2B buyer, offer, and cohort fit'
        )
        company['secondary_tags'] = ['b2b', 'live-researched']
        company['expected_ad_channels'] = ['linkedin']
        company['gaps'] = []
        for key in conditional:
            if key in record:
                company[key] = record[key]
            else:
                company.pop(key, None)
    payload['status'] = 'research_complete'
    temporary = corpus_path.with_suffix('.yaml.tmp')
    temporary.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
        encoding='utf-8',
    )
    staged_corpus = Corpus.load(temporary)
    staged_corpus.validate(AS_OF)
    published = _atomic_publish_files(dossiers, temporary, Path('benchmarks'))
    corpus = Corpus.load(corpus_path)
    corpus.validate(AS_OF)
    counts = {}
    for fixture in corpus.fixtures:
        counts[fixture.primary_cohort] = counts.get(fixture.primary_cohort, 0) + 1
    return {
        'companies': len(corpus.fixtures),
        'cohorts': len(counts),
        'each': sorted(counts.values()),
        'core': sum(item.shared_core for item in corpus.fixtures),
        'dossiers': len(published),
    }


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
    parser.add_argument('--publish-benchmarks', action='store_true')
    parser.add_argument('--cache-only', action='store_true')
    args = parser.parse_args(argv)
    if args.paid_cap_usd != '0' and not args.allow_paid:
        parser.error('--paid-cap-usd requires --allow-paid')
    if args.allow_paid and args.paid_cap_usd != CORPUS_PAID_CAP_USD:
        parser.error(f'paid corpus cap must be exactly {CORPUS_PAID_CAP_USD}')
    if args.publish_benchmarks and not args.resume:
        parser.error('--publish-benchmarks requires --resume')

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
        'unknown_reasons': {},
    }
    if args.dry_run:
        summary['mode'] = 'dry_run'
        emit(json.dumps(summary, sort_keys=True))
        return 0

    args.run_dir.mkdir(parents=True, exist_ok=True)
    execution_fixtures = fixtures
    outer_dir = args.run_dir / 'outer' / args.stage
    if args.resume:
        valid, invalid = _resume_state(fixtures, args.run_dir)
        summary['resumed'] = len(valid)
        summary['dossiers_valid'] = len(valid)
        summary['invalid_artifacts'] = list(invalid)
        summary['dry_angles'] = _retained_dry_angles(args.run_dir, ids)
        summary['unknown_reasons'] = {
            company_id: _dossier_unknown_reasons(_rehydrate_dossier(
                args.run_dir / 'dossiers' / f'{company_id}.yaml',
            ))
            for company_id in valid
        }
        if invalid == ('orphaned_evidence_object',):
            _append_stage_report(args.run_dir, summary)
            emit(json.dumps(summary, sort_keys=True))
            return 2
        retry_dirs = sorted(
            (args.run_dir / 'outer').glob(args.stage + '-retry-*')
        )
        if not invalid:
            retained_outer = retry_dirs[-1] if retry_dirs else outer_dir
            try:
                AutoresearchOrchestrator(
                    ArtifactStore(retained_outer),
                    _outer_roles(
                        lambda envelope: (_ for _ in ()).throw(
                            RuntimeError(
                                'completed outer run unexpectedly executed'
                            ),
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
            if args.publish_benchmarks:
                summary['published'] = _publish_benchmarks(args.run_dir)
            _append_stage_report(args.run_dir, summary)
            emit(json.dumps(summary, sort_keys=True))
            return 0
        execution_fixtures = tuple(
            fixture for fixture in fixtures if fixture.id in invalid
        )
        summary['invalid_artifacts'] = []
        outer_dir = (
            args.run_dir / 'outer'
            / f'{args.stage}-retry-{len(retry_dirs) + 1}'
        )

    if args.cache_only:
        source_client = search_client = CacheOnlyClient()
    else:
        source_client = source_client_factory()
        search_client = search_client_factory()
    calls = CallLedger(args.run_dir / 'calls.jsonl')
    prior_call_rows = (
        tuple(
            json.loads(line) for line in calls.path.read_text(
                encoding='utf-8',
            ).splitlines()
        ) if calls.path.exists() else ()
    )
    prior_source_keys = {
        (row.get('company_id'), row.get('url')) for row in prior_call_rows
        if row.get('kind') == 'source' and row.get('status') == 'succeeded'
    }
    prior_call_count = len(prior_call_rows)
    budget = BudgetLedger(
        args.run_dir / 'budget.jsonl',
        {'corpus-build': CORPUS_PAID_CAP_USD if args.allow_paid else '0'},
    )
    before_objects = len(list(
        (args.run_dir / 'evidence' / 'objects').glob('*.json'),
    ))
    completed = list(valid) if args.resume else []
    rollout = BenchmarkRollout(
        _companies(), journal=args.run_dir / 'rollout.jsonl',
    )
    retrying_completed_stage = (
        args.resume and rollout.current_stage != args.stage
    )
    if rollout.current_stage != args.stage and not retrying_completed_stage:
        raise ValueError(
            f'rollout requires stage {rollout.current_stage}, not {args.stage}'
        )

    def execute_stage(envelope):
        if envelope.payload['execution_inputs']['stage'] != args.stage:
            raise ValueError('outer executor received the wrong stage')
        outer_evidence = []
        for fixture in execution_fixtures:
            try:
                dossier, _qualified, dry = _run_company(
                    fixture, args.run_dir, source_client, search_client,
                    calls, budget,
                )
                completed.append(fixture.id)
                summary['dossiers_valid'] += 1
                summary['companies_saturated'] += 1
                summary['dry_angles'][fixture.id] = list(dry)
                summary['unknown_reasons'][fixture.id] = (
                    _dossier_unknown_reasons(dossier)
                )
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
        ArtifactStore(outer_dir),
        _outer_roles(execute_stage, lambda: len(completed) == len(fixtures)),
    ).run(_outer_request(args.stage, ids))
    after_objects = len(list(
        (args.run_dir / 'evidence' / 'objects').glob('*.json'),
    ))
    summary['sources_persisted'] = after_objects - before_objects
    current_rows = tuple(
        json.loads(line) for line in calls.path.read_text(
            encoding='utf-8',
        ).splitlines()
    )
    summary['source_repurchases'] = sum(
        1 for row in current_rows[prior_call_count:]
        if row.get('kind') == 'source' and row.get('status') == 'succeeded'
        and (row.get('company_id'), row.get('url')) in prior_source_keys
    )
    summary['paid_cost_usd'] = str(budget.spent('corpus-build'))
    if summary['dossiers_valid'] == len(fixtures) and not retrying_completed_stage:
        if rollout.current_stage == args.stage:
            rollout.complete(ids)
    _append_stage_report(args.run_dir, summary)
    emit(json.dumps(summary, sort_keys=True))
    return 0 if summary['dossiers_valid'] == len(fixtures) else 2
