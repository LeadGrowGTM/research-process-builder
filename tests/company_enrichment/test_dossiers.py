from dataclasses import FrozenInstanceError
from datetime import date, datetime, timezone

import pytest

from scripts.company_enrichment.contracts import (
    CompanyDossier, CompanyFixture, EvidenceRef, FieldAssertion,
    HumanCorrection, Visibility,
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


def _complete_dossier() -> CompanyDossier:
    return CompanyDossier(
        'saas-01', '1.0',
        tuple(_assertion(field) for field in REQUIRED_DOSSIER_FIELDS),
        (_evidence(),),
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


@pytest.mark.parametrize(
    ('overrides', 'message'),
    (
        ({'seed_status': 'unverified_seed'}, 'verified identity'),
        ({'b2b_buyer': None}, 'b2b_buyer'),
        ({'business_offer': None}, 'business_offer'),
        ({'cohort_evidence_url': None}, 'cohort evidence URL'),
        ({'secondary_tags': ()}, 'secondary tags'),
        ({'expected_ad_channels': ()}, 'expected ad channels'),
        ({'primary_cohort': 'local_b2b_services'}, 'local listing URL'),
        ({
            'primary_cohort': 'recently_funded_b2b',
            'primary_funding_url': 'https://acme.example/funding',
            'primary_funding_date': date(2025, 8, 11),
        }, 'within 12 months'),
    ),
)
def test_complete_dossier_cannot_rescue_unqualified_fixture(
    overrides: dict[str, object], message: str,
) -> None:
    fixture = _fixture()
    values = {
        field: getattr(fixture, field)
        for field in fixture.__dataclass_fields__
    }
    values.update(overrides)
    with pytest.raises(ValueError, match=message):
        validate_research_complete(
            CompanyFixture(**values), _complete_dossier(), as_of=date(2026, 8, 12),
        )


def test_funded_fixture_requires_explicit_as_of_despite_matching_old_evidence() -> None:
    fixture = _fixture()
    values = {
        field: getattr(fixture, field)
        for field in fixture.__dataclass_fields__
    }
    values.update({
        'id': 'funded-01',
        'primary_cohort': 'recently_funded_b2b',
        'primary_funding_url': 'https://acme.example/funding',
        'primary_funding_date': date(2025, 8, 11),
    })
    old_evidence = EvidenceRef(
        'ev-1', 'https://acme.example/research',
        datetime(2025, 8, 11, tzinfo=timezone.utc), 'a' * 64, 'Old evidence',
    )
    dossier = CompanyDossier(
        'funded-01', '1.0',
        tuple(_assertion(field) for field in REQUIRED_DOSSIER_FIELDS),
        (old_evidence,),
    )

    with pytest.raises(ValueError, match='as_of is required'):
        validate_research_complete(CompanyFixture(**values), dossier)


def test_human_corrections_append_and_retain_superseded_history() -> None:
    first = HumanCorrection(
        'correction-1', 'pricing', '$10', 'reviewer-1',
        datetime(2026, 8, 12, 10, tzinfo=timezone.utc),
    )
    second = HumanCorrection(
        'correction-2', 'pricing', '$12', 'reviewer-2',
        datetime(2026, 8, 12, 11, tzinfo=timezone.utc), 'correction-1',
    )

    once = _complete_dossier().append_correction(first)
    twice = once.append_correction(second)

    assert once.corrections == (first,)
    assert twice.corrections == (first, second)
    with pytest.raises(FrozenInstanceError):
        second.reviewer_id = 'replacement'  # type: ignore[misc]


def test_human_correction_cannot_replace_omitted_history() -> None:
    replacement = HumanCorrection(
        'correction-2', 'pricing', '$12', 'reviewer-2',
        datetime(2026, 8, 12, 11, tzinfo=timezone.utc), 'correction-1',
    )
    with pytest.raises(ValueError, match='superseded correction.*history'):
        CompanyDossier(
            'saas-01', '1.0', (), (), corrections=(replacement,),
        )


def test_human_correction_replacement_requires_explicit_supersession() -> None:
    corrected_at = datetime(2026, 8, 12, 10, tzinfo=timezone.utc)
    first = HumanCorrection(
        'correction-1', 'pricing', '$10', 'reviewer-1', corrected_at,
    )
    implicit_replacement = HumanCorrection(
        'correction-2', 'pricing', '$12', 'reviewer-2',
        datetime(2026, 8, 12, 11, tzinfo=timezone.utc),
    )
    with pytest.raises(ValueError, match='explicitly supersede'):
        _complete_dossier().append_correction(first).append_correction(
            implicit_replacement,
        )


def test_superseding_correction_requires_a_later_timestamp() -> None:
    corrected_at = datetime(2026, 8, 12, 10, tzinfo=timezone.utc)
    first = HumanCorrection(
        'correction-1', 'pricing', '$10', 'reviewer-1', corrected_at,
    )
    simultaneous = HumanCorrection(
        'correction-2', 'pricing', '$12', 'reviewer-2', corrected_at,
        'correction-1',
    )
    with pytest.raises(ValueError, match='later timestamp'):
        _complete_dossier().append_correction(first).append_correction(simultaneous)
