from datetime import date, datetime, timezone
from pathlib import Path

import pytest
import yaml

from scripts.company_enrichment.contracts import (
    CompanyFixture, EnrichmentResult, EvidenceRef, FailureKind, FieldAssertion,
    ResultStatus, Visibility,
)
from scripts.company_enrichment.corpus import REQUIRED_DOSSIER_FIELDS
from scripts.company_enrichment.dossier_runner import DossierBuilder


NOW = datetime(2026, 8, 12, tzinfo=timezone.utc)


def _fixture():
    return CompanyFixture(
        'saas-01', 'Acme', 'acme.example', None, 'b2b_saas', False, 'easy',
        'verified', {}, (), 'Revenue leaders', 'Workflow software',
        'Representative', 'https://acme.example', ('workflow',), ('linkedin',),
    )


def _result(enrichment_id, assertions=(), unknowns=(), evidence=None):
    evidence = evidence or EvidenceRef(
        f'ev-{enrichment_id}', f'https://acme.example/{enrichment_id}', NOW,
        ('a' if enrichment_id == 'one' else 'b') * 64, enrichment_id,
    )
    return EnrichmentResult(
        enrichment_id, 'saas-01', '1.0', ResultStatus.COMPLETE,
        {'assertions': tuple(assertions), 'evidence': (evidence,),
         'unknowns': tuple(unknowns)},
    )


def test_builder_merges_categories_conflicts_and_unknowns(tmp_path: Path) -> None:
    ev1 = EvidenceRef('ev-one', 'https://acme.example/one', NOW, 'a' * 64, 'one')
    ev2 = EvidenceRef('ev-two', 'https://acme.example/two', NOW, 'b' * 64, 'two')
    results = (
        _result('one', (
            FieldAssertion('description', 'First', ('ev-one',), .8,
                           Visibility.MESSAGE_SAFE),
        ), ('pricing',), ev1),
        _result('two', (
            FieldAssertion('description', 'Conflicting', ('ev-two',), .9,
                           Visibility.MESSAGE_SAFE),
        ), ('technology',), ev2),
    )
    builder = DossierBuilder(
        load_results=lambda fixture, scope: results,
        output_dir=tmp_path, as_of=date(2026, 8, 12),
    )
    with pytest.raises(ValueError, match='missing required dossier fields'):
        builder.build(_fixture(), 'corpus-build')
    assert not (tmp_path / 'saas-01.yaml').exists()

    complete = tuple(
        FieldAssertion(field, f'value-{field}', ('ev-one',), .8,
                       Visibility.MESSAGE_SAFE)
        for field in REQUIRED_DOSSIER_FIELDS if field != 'description'
    )
    dossier = DossierBuilder(
        load_results=lambda fixture, scope: (*results, _result('all', complete, (), ev1)),
        output_dir=tmp_path, as_of=date(2026, 8, 12),
    ).build(_fixture(), 'corpus-build')
    assert [item.value for item in dossier.assertions if item.field == 'description'] == [
        'First', 'Conflicting',
    ]
    assert dossier.unknowns == ('pricing', 'technology')
    payload = yaml.safe_load((tmp_path / 'saas-01.yaml').read_text())
    assert payload['company_id'] == 'saas-01'
    assert len(payload['assertions']) == len(dossier.assertions)


def test_builder_rejects_failed_result_without_persisting(tmp_path: Path) -> None:
    failed = EnrichmentResult(
        'company-description', 'saas-01', '1.0', ResultStatus.FAILED, {},
        failure=FailureKind.TERMINAL,
    )
    builder = DossierBuilder(
        load_results=lambda fixture, scope: (failed,), output_dir=tmp_path,
        as_of=date(2026, 8, 12),
    )
    with pytest.raises(ValueError, match='failed enrichment'):
        builder.build(_fixture(), 'corpus-build')
    assert not (tmp_path / 'saas-01.yaml').exists()
