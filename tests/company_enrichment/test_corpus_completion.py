from datetime import datetime, timezone

import pytest

from scripts.company_enrichment.cli import (
    ResearchSource, _extract_qualification, _load_source_plan,
    _all_fixtures, _atomic_publish_files, _dry_queries, _nap_tokens,
    _publish_benchmarks, _recover_publish_transaction, _stage_fixtures,
)
from scripts.company_enrichment.contracts import EvidenceRef


def _evidence(sources):
    return tuple(
        EvidenceRef(
            f'ev-{index}', item.url,
            datetime(2026, 8, 12, tzinfo=timezone.utc),
            str(index) * 64, item.content[:2000],
        )
        for index, item in enumerate(sources, 1)
    )


def test_funded_qualification_requires_recent_dated_primary_source() -> None:
    fixture = _stage_fixtures('recently_funded_b2b')[0]
    text = (
        f'{fixture.company_name} provides fraud intelligence software for financial institutions. '
        'The company announced a growth funding investment on September 10, 2025. '
    ) * 8
    sources = (
        ResearchSource('https://' + fixture.domain, 'first_party', 'official-source', text),
        ResearchSource('https://linkedin.com/company/example', 'independent', 'linkedin-source', text),
        ResearchSource('https://news.example/funding', 'independent', 'funding-primary', text),
    )
    qualified, record = _extract_qualification(
        fixture, sources, _evidence(sources), qualification_plan={
            'primary_funding_url': 'https://news.example/funding',
            'primary_funding_date': '2025-09-10',
        },
    )
    assert qualified.primary_funding_url == 'https://news.example/funding'
    assert qualified.primary_funding_date.isoformat() == '2025-09-10'
    assert record['primary_funding_date'] == '2025-09-10'


def test_funded_qualification_rejects_linkedin_as_primary_funding_source() -> None:
    fixture = _stage_fixtures('recently_funded_b2b')[0]
    ordinary = (
        f'{fixture.company_name} provides fraud intelligence software for financial institutions. '
    ) * 10
    linkedin = ordinary + (
        'The company announced a growth funding investment on September 10, 2025. '
    ) * 8
    sources = (
        ResearchSource('https://' + fixture.domain, 'first_party', 'official-source', ordinary),
        ResearchSource('https://linkedin.com/company/example', 'independent', 'linkedin-source', linkedin),
        ResearchSource('https://reviews.example/company', 'independent', 'independent-source', ordinary),
    )
    with pytest.raises(ValueError, match='dated primary source'):
        _extract_qualification(
            fixture, sources, _evidence(sources), qualification_plan={
                'primary_funding_url': 'https://news.example/funding',
                'primary_funding_date': '2025-09-10',
            },
        )


def test_funded_qualification_requires_exact_planned_primary_url_and_date() -> None:
    fixture = _stage_fixtures('recently_funded_b2b')[0]
    text = (
        f'{fixture.company_name} provides fraud intelligence software for financial institutions. '
        'The company announced growth funding on September 10, 2025. '
    ) * 8
    sources = (
        ResearchSource('https://' + fixture.domain, 'first_party', 'official-source', text),
        ResearchSource('https://linkedin.com/company/example', 'independent', 'linkedin-source', text),
        ResearchSource('https://news.example/funding', 'independent', 'funding-primary', text),
    )
    with pytest.raises(ValueError, match='dated primary source'):
        _extract_qualification(
            fixture, sources, _evidence(sources), qualification_plan={
                'primary_funding_url': 'https://issuer.example/announcement',
                'primary_funding_date': '2025-09-10',
            },
        )


def test_funded_qualification_accepts_iso_date_from_primary_metadata() -> None:
    fixture = _stage_fixtures('recently_funded_b2b')[0]
    ordinary = (
        f'{fixture.company_name} provides fraud intelligence software for financial institutions. '
    ) * 10
    primary = ordinary + ' funding announced 2025-10-15 ' * 10
    sources = (
        ResearchSource('https://' + fixture.domain, 'first_party', 'official-source', ordinary),
        ResearchSource('https://linkedin.com/company/example', 'independent', 'linkedin-source', ordinary),
        ResearchSource('https://reviews.example/company', 'independent', 'independent-source', ordinary),
        ResearchSource('https://quantifind.com/funding', 'first_party', 'funding-primary-v2', primary),
    )
    qualified, _record = _extract_qualification(
        fixture, sources, _evidence(sources), qualification_plan={
            'primary_funding_url': 'https://quantifind.com/funding',
            'primary_funding_date': '2025-10-15',
        },
    )
    assert qualified.primary_funding_date.isoformat() == '2025-10-15'
    with pytest.raises(ValueError, match='dated primary source'):
        _extract_qualification(
            fixture, sources, _evidence(sources), qualification_plan={
                'primary_funding_url': 'https://news.example/funding',
                'primary_funding_date': '2025-09-11',
            },
        )


def test_sec_form_d_marker_and_planned_iso_date_are_primary_evidence() -> None:
    fixture = _stage_fixtures('recently_funded_b2b')[0]
    ordinary = (
        f'{fixture.company_name} provides risk intelligence software for financial institutions. '
    ) * 10
    form_d = ordinary + ' X0708 D LIVE 2025-10-15 totalOfferingAmount ' * 10
    url = 'https://www.sec.gov/Archives/edgar/data/1/primary_doc.xml'
    sources = (
        ResearchSource('https://' + fixture.domain, 'first_party', 'official-source', ordinary),
        ResearchSource('https://linkedin.com/company/example', 'independent', 'linkedin-source', ordinary),
        ResearchSource('https://reviews.example/company', 'independent', 'independent-source', ordinary),
        ResearchSource(url, 'independent', 'funding-primary-v3', form_d),
    )
    qualified, _record = _extract_qualification(
        fixture, sources, _evidence(sources), qualification_plan={
            'primary_funding_url': url,
            'primary_funding_date': '2025-10-15',
        },
    )
    assert qualified.primary_funding_url == url


def test_local_qualification_requires_discovered_listing_reference() -> None:
    fixture = _stage_fixtures('local_b2b_services')[0]
    official = (
        f'{fixture.company_name} provides accounting and advisory services for businesses. '
        'A+P CPAs LLC, 1689 East 1400 South Suite 100, Clearfield UT 84015, '
        '801-776-5241. '
    ) * 8
    listing = (
        'A+P CPAs LLC, Clearfield UT 84015, Ste 100, 1689 E 1400 S, '
        '801 776 5241 provides accounting services for businesses. '
    ) * 8
    linked = (
        f'{fixture.company_name} provides accounting and advisory services for businesses. '
    ) * 10
    listing_url = 'https://members.ogdenweberchamber.com/list/member/a-p-cpas-llc-94123'
    sources = (
        ResearchSource('https://' + fixture.domain, 'first_party', 'official-source', official),
        ResearchSource('https://linkedin.com/company/example', 'independent', 'linkedin-source', linked),
        ResearchSource(listing_url, 'independent', 'independent-source', listing),
    )
    plan = {
        'local_listing_url': listing_url,
        'local_location_url': 'https://' + fixture.domain,
        'local_name': 'A+P CPAs LLC',
        'local_street_postal': '1689 East 1400 South Suite 100 Clearfield UT 84015',
        'local_phone': '801-776-5241',
    }
    qualified, record = _extract_qualification(
        fixture, sources, _evidence(sources), qualification_plan=plan,
    )
    assert qualified.local_listing_url == listing_url
    assert record['local_listing_url'] == listing_url


def test_local_qualification_rejects_unplanned_or_nap_mismatched_listing() -> None:
    fixture = _stage_fixtures('local_b2b_services')[0]
    text = (
        f'{fixture.company_name} provides accounting services for businesses. '
        'A+P CPAs LLC 1689 East 1400 South Clearfield UT 84015 801-776-5241. '
    ) * 8
    sources = (
        ResearchSource('https://' + fixture.domain, 'first_party', 'official-source', text),
        ResearchSource('https://linkedin.com/company/example', 'independent', 'linkedin-source', text),
        ResearchSource('https://directory.example/wrong', 'independent', 'independent-source', text),
    )
    plan = {
        'local_listing_url': 'https://regulator.example/correct',
        'local_location_url': 'https://' + fixture.domain,
        'local_name': 'A+P CPAs LLC',
        'local_street_postal': '999 Wrong Street Toronto ON A1A1A1',
        'local_phone': '555-555-5555',
    }
    with pytest.raises(ValueError, match='planned local listing'):
        _extract_qualification(
            fixture, sources, _evidence(sources), qualification_plan=plan,
        )


def test_reviewed_b2b_phrases_must_be_supported_by_retained_sources() -> None:
    fixture = _stage_fixtures('b2b_commerce_suppliers')[7]
    base = (f'{fixture.company_name} recruiting company. ') * 20
    official = base + (
        'Staffing solutions that drive business outcomes for organizations. '
    ) * 8
    sources = (
        ResearchSource('https://' + fixture.domain, 'first_party', 'official-source', official),
        ResearchSource('https://linkedin.com/company/example', 'independent', 'linkedin-source', base),
        ResearchSource('https://directory.example/company', 'independent', 'independent-source', base),
    )
    qualified, _record = _extract_qualification(
        fixture, sources, _evidence(sources), qualification_plan={
            'b2b_buyer_phrase': 'organizations',
            'business_offer_phrase': 'staffing solutions',
        },
    )
    assert qualified.b2b_buyer == 'organizations'
    assert qualified.business_offer == 'staffing solutions'
    with pytest.raises(ValueError, match='B2B buyer and offer'):
        _extract_qualification(
            fixture, sources, _evidence(sources), qualification_plan={
                'b2b_buyer_phrase': 'unpublished buyers',
                'business_offer_phrase': 'staffing solutions',
            },
        )


def test_loose_navigation_copy_cannot_qualify_b2b_buyer_and_offer() -> None:
    fixture = _stage_fixtures('remaining_saas')[0]
    junk = (
        f'{fixture.company_name} Legal Terms offers Privacy Policy for Desk Community Forum. '
    ) * 20
    sources = (
        ResearchSource('https://' + fixture.domain, 'first_party', 'official-source', junk),
        ResearchSource('https://linkedin.com/company/example', 'independent', 'linkedin-source', junk),
        ResearchSource('https://reviews.example/company', 'independent', 'independent-source', junk),
    )
    with pytest.raises(ValueError, match='B2B buyer and offer'):
        _extract_qualification(fixture, sources, _evidence(sources))


def test_each_enrichment_has_two_distinct_category_specific_dry_queries() -> None:
    fixture = _stage_fixtures('remaining_saas')[0]
    seen = set()
    for enrichment_id in (
        'analogy-value-translator', 'company-description',
        'competitor-intelligence', 'growth-signals', 'icp-persona-analysis',
        'job-opportunity-mining', 'news-product-launches',
        'running-ads-offer-intelligence',
    ):
        queries = _dry_queries(fixture, enrichment_id)
        assert len(queries) == 2
        assert len(set(queries)) == 2
        assert not (set(queries) & seen)
        seen.update(queries)


def test_local_plan_has_exact_retained_listing_and_reviewed_nap_basis() -> None:
    plan = _load_source_plan()
    fixtures = _stage_fixtures('local_b2b_services')
    assert len(fixtures) == 10
    for fixture in fixtures:
        record = plan[fixture.id]
        assert record['listing_match_basis'] in {
            'name_address', 'name_phone', 'name_address_phone', 'name_locality',
        }
        assert record['local_listing_url'] in {
            source['url'] for source in record['sources']
        }
    assert plan['local-07']['listing_match_basis'] == 'name_locality'
    assert plan['local-10']['sources'][0]['url'].startswith(
        'https://atlas-mechanical.com/'
    )


def test_local_nap_normalizes_alberta_province_name() -> None:
    assert _nap_tokens('Calgary Alberta T2P 2W2') == _nap_tokens(
        'Calgary AB T2P2W2'
    )


def test_local_nap_combines_punctuated_cardinal_direction() -> None:
    assert _nap_tokens('2 Street S.W.') == _nap_tokens('2 Street SW')


def test_audited_canonical_domain_corrections_replace_stale_seed_domains() -> None:
    fixtures = {
        item.id: item for item in _stage_fixtures('recently_funded_b2b')
    }
    assert fixtures['funded-03'].domain == 'floatfinancial.com'
    assert fixtures['funded-04'].domain == 'redo.com'


def test_ineligible_virtual_peaker_is_replaced_by_dualentry() -> None:
    fixture = next(
        item for item in _stage_fixtures('recently_funded_b2b')
        if item.id == 'funded-09'
    )
    assert fixture.company_name == 'DualEntry'
    assert fixture.domain == 'dualentry.com'
    assert fixture.linkedin_company_url == (
        'https://www.linkedin.com/company/dualentry'
    )


def test_software_only_audiense_is_replaced_by_walker_sands() -> None:
    fixtures = {
        item.id: item for item in _stage_fixtures('b2b_agencies')
    }
    assert fixtures['agency-04'].company_name == 'Walker Sands'
    assert fixtures['agency-04'].domain == 'walkersands.com'
    assert fixtures['agency-02'].company_name == 'AbelsonTaylor'
    plan = _load_source_plan()
    assert len(plan['agency-04']['sources']) == 3


def test_well_known_plan_has_three_exact_sources_per_fixture() -> None:
    plan = _load_source_plan()
    fixtures = _stage_fixtures('well_known_b2b')
    assert len(fixtures) == 10
    assert all(len(plan[item.id]['sources']) == 3 for item in fixtures)
    assert plan['known-07']['sources'][2]['url'].endswith(
        'a2025moogannualreport.pdf'
    )
    assert 'fdic.gov' in plan['known-09']['sources'][2]['url']
    assert 'Nordson' in plan['known-08']['entity_aliases']
    assert 'Optum Bank Inc' in plan['known-09']['entity_aliases']
    assert plan['known-09']['sources'][2]['provider'] == 'independent-source-v3'


def test_publish_rejects_incomplete_run_before_writing_benchmarks(
    tmp_path,
) -> None:
    try:
        _publish_benchmarks(tmp_path)
    except ValueError as error:
        assert str(error) == 'publish requires exactly 60 validated dossiers'
    else:
        raise AssertionError('incomplete run was published')
    assert not (tmp_path / 'published').exists()


def test_atomic_publisher_stages_and_swaps_real_files(tmp_path) -> None:
    benchmark_dir = tmp_path / 'benchmarks'
    benchmark_dir.mkdir()
    (benchmark_dir / 'companies.yaml').write_text(
        'status: seed\n', encoding='utf-8',
    )
    old = benchmark_dir / 'dossiers'
    old.mkdir()
    (old / 'old.yaml').write_text('old: true\n', encoding='utf-8')
    sources = tmp_path / 'source-dossiers'
    sources.mkdir()
    dossiers = []
    for index in range(60):
        path = sources / f'company-{index:02}.yaml'
        path.write_text(f'id: company-{index:02}\n', encoding='utf-8')
        dossiers.append(path)
    staged_corpus = benchmark_dir / 'companies.yaml.tmp'
    staged_corpus.write_text('status: research_complete\n', encoding='utf-8')

    published = _atomic_publish_files(
        dossiers, staged_corpus, benchmark_dir,
    )

    assert len(published) == 60
    assert not (benchmark_dir / 'dossiers' / 'old.yaml').exists()
    assert (benchmark_dir / 'companies.yaml').read_text(encoding='utf-8') == (
        'status: research_complete\n'
    )
    assert not (benchmark_dir / '.dossiers.publish.backup').exists()


def test_atomic_publisher_recovers_durable_interrupted_swap(tmp_path) -> None:
    benchmark_dir = tmp_path / 'benchmarks'
    benchmark_dir.mkdir()
    old_backup = benchmark_dir / '.dossiers.publish.backup'
    old_backup.mkdir()
    (old_backup / 'old.yaml').write_text('old: true\n', encoding='utf-8')
    mixed = benchmark_dir / 'dossiers'
    mixed.mkdir()
    (mixed / 'new.yaml').write_text('new: true\n', encoding='utf-8')
    (benchmark_dir / 'companies.yaml').write_text(
        'status: research_complete\n', encoding='utf-8',
    )
    (benchmark_dir / '.companies.publish.backup.yaml').write_text(
        'status: proposed_seed\n', encoding='utf-8',
    )
    (benchmark_dir / '.publish-transaction.json').write_text(
        '{"had_destination":true,"state":"dossiers_swapped"}\n',
        encoding='utf-8',
    )
    sources = tmp_path / 'sources'
    sources.mkdir()
    dossiers = []
    for index in range(60):
        path = sources / f'company-{index:02}.yaml'
        path.write_text(f'id: company-{index:02}\n', encoding='utf-8')
        dossiers.append(path)
    staged = benchmark_dir / 'companies.yaml.tmp'
    staged.write_text('status: final\n', encoding='utf-8')
    published = _atomic_publish_files(dossiers, staged, benchmark_dir)
    assert len(published) == 60
    assert (benchmark_dir / 'companies.yaml').read_text() == 'status: final\n'
    assert not list(benchmark_dir.glob('.publish-*'))
    assert not list(benchmark_dir.glob('.*.publish.backup*'))


def test_atomic_publisher_recovery_preserves_old_dossiers_before_first_swap(
    tmp_path,
) -> None:
    benchmark_dir = tmp_path / 'benchmarks'
    benchmark_dir.mkdir()
    old = benchmark_dir / 'dossiers'
    old.mkdir()
    (old / 'old.yaml').write_text('old: true\n', encoding='utf-8')
    (benchmark_dir / 'companies.yaml').write_text('status: old\n', encoding='utf-8')
    (benchmark_dir / '.companies.publish.backup.yaml').write_text(
        'status: old\n', encoding='utf-8',
    )
    (benchmark_dir / '.publish-transaction.json').write_text(
        '{"had_destination":true,"state":"prepared"}\n', encoding='utf-8',
    )
    sources = tmp_path / 'sources'
    sources.mkdir()
    dossiers = []
    for index in range(60):
        path = sources / f'company-{index:02}.yaml'
        path.write_text(f'id: company-{index:02}\n', encoding='utf-8')
        dossiers.append(path)
    staged = benchmark_dir / 'companies.yaml.tmp'
    staged.write_text('status: final\n', encoding='utf-8')
    assert len(_atomic_publish_files(dossiers, staged, benchmark_dir)) == 60
    assert (benchmark_dir / 'companies.yaml').read_text() == 'status: final\n'


@pytest.mark.parametrize('new_installed', [False, True])
def test_publish_recovery_restores_old_generation_from_prepared_crash(
    tmp_path, new_installed,
) -> None:
    benchmark_dir = tmp_path / 'benchmarks'
    benchmark_dir.mkdir()
    backup = benchmark_dir / '.dossiers.publish.backup'
    backup.mkdir()
    (backup / 'old.yaml').write_text('old: true\n', encoding='utf-8')
    if new_installed:
        destination = benchmark_dir / 'dossiers'
        destination.mkdir()
        (destination / 'new.yaml').write_text('new: true\n', encoding='utf-8')
    (benchmark_dir / 'companies.yaml').write_text('status: mixed\n', encoding='utf-8')
    (benchmark_dir / '.companies.publish.backup.yaml').write_text(
        'status: old\n', encoding='utf-8',
    )
    (benchmark_dir / '.publish-transaction.json').write_text(
        '{"had_destination":true,"state":"prepared"}\n', encoding='utf-8',
    )
    _recover_publish_transaction(benchmark_dir)
    assert (benchmark_dir / 'dossiers' / 'old.yaml').exists()
    assert not (benchmark_dir / 'dossiers' / 'new.yaml').exists()
    assert (benchmark_dir / 'companies.yaml').read_text() == 'status: old\n'
    assert not (benchmark_dir / '.publish-transaction.json').exists()


def test_publish_recovery_keeps_new_generation_after_committed_cleanup_crash(
    tmp_path,
) -> None:
    benchmark_dir = tmp_path / 'benchmarks'
    benchmark_dir.mkdir()
    destination = benchmark_dir / 'dossiers'
    destination.mkdir()
    (destination / 'new.yaml').write_text('new: true\n', encoding='utf-8')
    (benchmark_dir / 'companies.yaml').write_text('status: new\n', encoding='utf-8')
    (benchmark_dir / '.publish-transaction.json').write_text(
        '{"had_destination":true,"state":"committed"}\n', encoding='utf-8',
    )
    _recover_publish_transaction(benchmark_dir)
    assert (destination / 'new.yaml').exists()
    assert (benchmark_dir / 'companies.yaml').read_text() == 'status: new\n'
    assert not (benchmark_dir / '.publish-transaction.json').exists()


def test_checked_in_source_plan_contains_locators_not_verified_facts() -> None:
    plan = _load_source_plan()
    assert set(plan['saas-01']['sources'][0]) == {
        'url', 'source_type', 'provider',
    }
    assert 'business_offer' not in plan['saas-01']
    assert plan['funded-03']['canonical_domain'] == 'floatfinancial.com'
    assert plan['funded-04']['canonical_domain'] == 'redo.com'
    assert {
        source['url'] for source in plan['saas-10']['sources'][1:]
    } == {
        'https://builtin.com/company/built-technologies',
        'https://www.canapi.com/investment/built',
    }


def test_all_60_benchmark_fixtures_require_specific_reviewed_qualification() -> None:
    plan = _load_source_plan()
    fixtures = _all_fixtures()
    assert len(fixtures) == 60
    generic = {'business', 'businesses', 'customers', 'companies',
               'organizations', 'clients'}
    for fixture in fixtures:
        record = plan[fixture.id]
        assert record['require_reviewed_qualification'] is True
        assert record['b2b_buyer_phrase'].casefold() not in generic
        assert len(record['business_offer_phrase'].split()) >= 2
    funded = plan['funded-01']
    assert funded['primary_funding_url'] in {
        source['url'] for source in funded['sources']
        if source['provider'] == 'funding-primary'
    }
    assert funded['primary_funding_date'] == '2026-06-26'
