from dataclasses import FrozenInstanceError
from datetime import date
from pathlib import Path

import pytest

from scripts.company_enrichment.contracts import CompanyFixture
from scripts.company_enrichment.corpus import Corpus


AS_OF = date(2026, 8, 12)


def _fixture(**overrides: object) -> CompanyFixture:
    values: dict[str, object] = {
        'id': 'saas-01', 'company_name': 'Acme', 'domain': 'acme.example',
        'linkedin_company_url': 'https://www.linkedin.com/company/acme',
        'primary_cohort': 'b2b_saas', 'shared_core': False,
        'difficulty': 'easy', 'seed_status': 'verified', 'seed': {}, 'gaps': (),
    }
    values.update({
        'b2b_buyer': 'Operations leaders',
        'business_offer': 'Workflow software',
        'selection_reason': 'Representative fixture',
        'cohort_evidence_url': 'https://acme.example/product',
        'secondary_tags': ('workflow',),
        'expected_ad_channels': ('linkedin',),
    })
    values.update(overrides)
    return CompanyFixture(**values)  # type: ignore[arg-type]


def test_load_preserves_immutable_fixture_values() -> None:
    corpus = Corpus.load(Path('benchmarks/companies.yaml'))
    assert len(corpus.fixtures) == 60
    with pytest.raises(FrozenInstanceError):
        corpus.fixtures[0].company_name = 'Changed'  # type: ignore[misc]
    with pytest.raises(TypeError):
        corpus.fixtures[0].seed['description'] = 'Changed'  # type: ignore[index]


def test_load_rejects_unknown_fixture_fields(tmp_path: Path) -> None:
    path = tmp_path / 'companies.yaml'
    path.write_text('''version: '1.0'
as_of: '2026-08-12'
status: approved
source: {}
companies:
  - id: saas-01
    company_name: Acme
    domain: acme.example
    linkedin_company_url: null
    primary_cohort: b2b_saas
    shared_core: false
    difficulty: easy
    seed_status: verified
    seed: {}
    gaps: []
    unexpected: value
''', encoding='utf-8')
    with pytest.raises(ValueError, match='unexpected fixture fields.*unexpected'):
        Corpus.load(path)


def test_recently_funded_requires_primary_url_and_recent_date() -> None:
    missing = _fixture(id='funded-01', primary_cohort='recently_funded_b2b',
                       primary_funding_date=AS_OF)
    stale = _fixture(id='funded-01', primary_cohort='recently_funded_b2b',
                     primary_funding_url='https://acme.example/funding',
                     primary_funding_date=date(2025, 8, 11))
    with pytest.raises(ValueError, match='primary funding URL'):
        Corpus((missing,)).validate(AS_OF)
    with pytest.raises(ValueError, match='within 12 months'):
        Corpus((stale,)).validate(AS_OF)


def test_local_fixture_requires_an_absolute_listing_url() -> None:
    fixture = _fixture(id='local-01', primary_cohort='local_b2b_services',
                       local_listing_url=None)
    with pytest.raises(ValueError, match='local listing URL'):
        Corpus((fixture,)).validate(AS_OF)


def test_load_rejects_duplicate_yaml_keys(tmp_path: Path) -> None:
    path = tmp_path / 'companies.yaml'
    path.write_text('''version: '1.0'
version: '1.0'
as_of: '2026-08-12'
status: approved
source: {}
companies: []
''', encoding='utf-8')
    with pytest.raises(ValueError, match='duplicate YAML key.*version'):
        Corpus.load(path)
