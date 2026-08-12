from datetime import datetime, timezone

import pytest

from scripts.company_enrichment.contracts import (
    CompanyDossier, CompanyFixture, EvidenceRef, FieldAssertion, Visibility,
)
from scripts.company_enrichment.corpus import (
    REQUIRED_DOSSIER_FIELDS, validate_research_complete,
)


def _fixture() -> CompanyFixture:
    return CompanyFixture(
        'saas-01', 'Acme', 'acme.example', None, 'b2b_saas', False, 'easy',
        'verified', {}, (), 'Operations leaders', 'Workflow software',
        'Representative fixture', 'https://acme.example/product',
        ('workflow',), ('linkedin',),
    )


def _evidence() -> EvidenceRef:
    return EvidenceRef(
        'ev-1', 'https://acme.example/research',
        datetime(2026, 8, 12, tzinfo=timezone.utc), 'a' * 64, 'Evidence',
    )


def _assertion(field: str, cited: bool = True) -> FieldAssertion:
    return FieldAssertion(
        field, f'Known {field}', ('ev-1',) if cited else (),
        0.9, Visibility.MESSAGE_SAFE,
    )


def test_empty_dossier_is_not_research_complete() -> None:
    dossier = CompanyDossier('saas-01', '1.0', (), ())
    with pytest.raises(ValueError, match='missing required dossier fields'):
        validate_research_complete(_fixture(), dossier)


def test_each_required_field_must_be_cited_or_unknown() -> None:
    for missing in REQUIRED_DOSSIER_FIELDS:
        assertions = tuple(_assertion(field) for field in REQUIRED_DOSSIER_FIELDS
                           if field != missing)
        dossier = CompanyDossier('saas-01', '1.0', assertions, (_evidence(),))
        with pytest.raises(ValueError, match=missing):
            validate_research_complete(_fixture(), dossier)


def test_explicit_unknowns_satisfy_required_field_coverage() -> None:
    assertions = tuple(_assertion(field) for field in REQUIRED_DOSSIER_FIELDS
                       if field not in {'ads', 'pricing'})
    dossier = CompanyDossier(
        'saas-01', '1.0', assertions, (_evidence(),), ('ads', 'pricing'),
    )
    validate_research_complete(_fixture(), dossier)


def test_uncited_assertion_does_not_satisfy_coverage() -> None:
    assertions = tuple(_assertion(field, field != 'growth')
                       for field in REQUIRED_DOSSIER_FIELDS)
    dossier = CompanyDossier('saas-01', '1.0', assertions, (_evidence(),))
    with pytest.raises(ValueError, match='growth'):
        validate_research_complete(_fixture(), dossier)
