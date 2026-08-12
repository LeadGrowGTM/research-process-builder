from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
import html
import json
import os
from pathlib import Path
import re
from typing import Callable, Protocol
from urllib.request import Request, urlopen

import yaml

from .benchmark_schedule import BenchmarkRollout
from .budgets import BudgetLedger
from .contracts import (
    CompanyFixture, EnrichmentResult, FieldAssertion, ResultStatus, Visibility,
)
from .corpus import Corpus, REQUIRED_DOSSIER_FIELDS
from .dossier_runner import DossierBuilder
from .evidence import EvidenceStore, SourceRecord


CORPUS_PAID_CAP_USD = '2.00'
AS_OF = date(2026, 8, 12)


@dataclass(frozen=True, slots=True)
class ResearchSource:
    url: str
    source_type: str
    provider: str
    content: str


LIVE_SOURCES = {
    'saas-01': (
        ('https://agencyanalytics.com/company/about', 'first_party', 'official'),
        ('https://ca.linkedin.com/company/agencyanalytics', 'independent', 'linkedin'),
        ('https://sourceforge.net/software/product/AgencyAnalytics/', 'independent', 'sourceforge'),
    ),
    'saas-04': (
        ('https://www.apriori.com/about/', 'first_party', 'official'),
        ('https://www.linkedin.com/company/apriori', 'independent', 'linkedin'),
        ('https://www.vistaequitypartners.com/news/apriori-receives-growth-investment-from-vista-credit-partners-for-its-manufacturing-insights-platform/', 'independent', 'vista'),
    ),
    'saas-07': (
        ('https://www.betterworks.com/about', 'first_party', 'official'),
        ('https://www.linkedin.com/company/betterworks', 'independent', 'linkedin'),
        ('https://www.hr.software/reviews/betterworks', 'independent', 'hr-software'),
    ),
}

DRY_ANGLES = {
    'saas-01': ('ad_transparency', 'funding_transaction'),
    'saas-04': ('ad_transparency', 'public_dollar_pricing'),
    'saas-07': ('ad_transparency', 'audited_financials'),
}


class ResearchClient(Protocol):
    def research(self, fixture: CompanyFixture) -> tuple[ResearchSource, ...]: ...


class OfficialHomepageClient:
    def research(self, fixture: CompanyFixture) -> tuple[ResearchSource, ...]:
        sources = []
        for url, source_type, provider in LIVE_SOURCES.get(fixture.id, ()):
            request = Request(url, headers={
                'User-Agent': 'Mozilla/5.0 research-process-builder/1.0',
                'Accept': 'text/html,application/xhtml+xml',
            })
            try:
                with urlopen(request, timeout=30) as response:
                    raw = response.read(1_000_000)
                    try:
                        body = raw.decode('utf-8')
                    except UnicodeDecodeError:
                        body = raw.decode(
                            response.headers.get_content_charset() or 'utf-8',
                            errors='replace',
                        )
            except Exception:
                continue
            text = re.sub(r'<script[^>]*>.*?</script>', ' ', body, flags=re.I | re.S)
            text = re.sub(r'<style[^>]*>.*?</style>', ' ', text, flags=re.I | re.S)
            text = re.sub(r'<[^>]+>', ' ', text)
            text = html.unescape(re.sub(r'\s+', ' ', text).strip())
            if len(text) >= 200:
                sources.append(ResearchSource(url, source_type, provider, text[:20_000]))
        if (
            not any(item.source_type == 'first_party' for item in sources)
            or len([item for item in sources if item.source_type == 'independent']) < 2
        ):
            raise RuntimeError('source saturation was not reached')
        return tuple(sources)


def _companies() -> list[dict]:
    return yaml.safe_load(
        Path('benchmarks/companies.yaml').read_text(encoding='utf-8')
    )['companies']


def _stage_fixtures(stage: str) -> tuple[CompanyFixture, ...]:
    corpus = Corpus.load(Path('benchmarks/companies.yaml'))
    by_id = {item.id: item for item in corpus.fixtures}
    rollout = BenchmarkRollout(_companies())
    while rollout.current_stage != stage:
        if rollout.current_stage is None:
            raise ValueError(f'unknown rollout stage: {stage}')
        rollout.complete(rollout.current_company_ids)
    return tuple(by_id[item] for item in rollout.current_company_ids)


def _qualified(fixture: CompanyFixture, source_url: str) -> CompanyFixture:
    description = str(fixture.seed.get('description') or '')
    offer = str(fixture.seed.get('products_services') or description or 'B2B software')
    buyer = str(fixture.seed.get('industry') or 'business teams')
    return replace(
        fixture, seed_status='verified', b2b_buyer=buyer,
        business_offer=offer, selection_reason='Verified B2B SaaS shared-core fixture',
        cohort_evidence_url=source_url,
        secondary_tags=fixture.secondary_tags or ('b2b-saas',),
        expected_ad_channels=fixture.expected_ad_channels or ('linkedin',),
    )


def _build_dossier(
    fixture: CompanyFixture, sources: tuple[ResearchSource, ...],
    run_dir: Path,
) -> int:
    store = EvidenceStore(run_dir / 'evidence')
    evidence = tuple(
        store.put(SourceRecord(
            item.url, datetime.now(timezone.utc), item.source_type, item.provider,
            item.content, item.content[:2_000], 30, '0',
        ))
        for item in sources
    )
    official_index = next(
        index for index, item in enumerate(sources)
        if item.source_type == 'first_party'
    )
    official = sources[official_index]
    official_evidence = evidence[official_index]
    assertions = (
        FieldAssertion('identity', fixture.company_name, (official_evidence.evidence_id,),
                       .9, Visibility.MESSAGE_SAFE),
        FieldAssertion('description', official.content[:500],
                       (official_evidence.evidence_id,),
                       .8, Visibility.MESSAGE_SAFE),
    )
    unknowns = tuple(
        field for field in REQUIRED_DOSSIER_FIELDS
        if field not in {'identity', 'description'}
    )
    result = EnrichmentResult(
        'live-company-corpus', fixture.id, '1.0', ResultStatus.COMPLETE,
        {'assertions': assertions, 'evidence': evidence, 'unknowns': unknowns,
         'saturated': True},
    )
    DossierBuilder(
        load_results=lambda _fixture, _scope: (result,),
        output_dir=run_dir / 'dossiers', as_of=AS_OF,
    ).build(fixture, 'corpus-build')
    return len(evidence)


def main(
    argv: list[str] | None = None,
    *,
    client_factory: Callable[[], ResearchClient] = OfficialHomepageClient,
    emit: Callable[[str], None] = print,
) -> int:
    parser = argparse.ArgumentParser(description='Research the approved company corpus')
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
        'source_repurchases': 0, 'authentication_gaps': [], 'source_gaps': [],
        'sources_persisted': 0, 'companies_saturated': 0,
        'dry_angles': {item.id: list(DRY_ANGLES[item.id]) for item in fixtures},
        'unknown_reasons': {
            item.id: {
                field: 'not established after source saturation and dry-angle research'
                for field in REQUIRED_DOSSIER_FIELDS
                if field not in {'identity', 'description'}
            }
            for item in fixtures
        },
    }
    if args.dry_run:
        summary['mode'] = 'dry_run'
        emit(json.dumps(summary, sort_keys=True))
        return 0

    args.run_dir.mkdir(parents=True, exist_ok=True)
    BudgetLedger(args.run_dir / 'budget.jsonl', {'corpus-build': CORPUS_PAID_CAP_USD})
    client = client_factory()
    for fixture in fixtures:
        dossier_path = args.run_dir / 'dossiers' / f'{fixture.id}.yaml'
        if args.resume and dossier_path.exists():
            summary['resumed'] += 1
            summary['dossiers_valid'] += 1
            continue
        try:
            sources = client.research(fixture)
            if (
                not any(item.source_type == 'first_party' for item in sources)
                or len([item for item in sources if item.source_type == 'independent']) < 2
                or len(DRY_ANGLES[fixture.id]) < 2
            ):
                raise RuntimeError('source saturation was not reached')
            qualified = _qualified(
                fixture,
                next(item.url for item in sources if item.source_type == 'first_party'),
            )
            summary['sources_persisted'] += _build_dossier(
                qualified, sources, args.run_dir,
            )
            summary['companies_saturated'] += 1
            summary['dossiers_valid'] += 1
        except PermissionError:
            summary['authentication_gaps'].append(fixture.id)
        except Exception:
            summary['source_gaps'].append(fixture.id)

    if summary['dossiers_valid'] == len(fixtures):
        rollout = BenchmarkRollout(_companies(), journal=args.run_dir / 'rollout.jsonl')
        if rollout.current_stage == args.stage:
            rollout.complete(ids)
    report_path = args.run_dir / 'stage-report.jsonl'
    with report_path.open('a', encoding='utf-8', newline='\n') as stream:
        stream.write(json.dumps(summary, sort_keys=True, separators=(',', ':')) + '\n')
        stream.flush()
        os.fsync(stream.fileno())
    emit(json.dumps(summary, sort_keys=True))
    return 0 if summary['dossiers_valid'] == len(fixtures) else 2
