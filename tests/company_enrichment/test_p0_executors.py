from datetime import datetime, timezone

import pytest

from scripts.company_enrichment.contracts import EvidenceRef, SellerContext, Visibility
from scripts.company_enrichment.executors import Finding, P0_ENRICHMENTS, execute_p0


def _evidence():
    return EvidenceRef('ev-1', 'https://acme.example',
                       datetime(2026, 8, 12, tzinfo=timezone.utc), 'a' * 64,
                       'Acme helps revenue teams automate work.')


def _context():
    return SellerContext(
        'B2B SaaS', ('VP Sales',), ('research',), 'Pipeline Sprint', '30 days',
        'more pipeline', ('case study',), 'pilot', ('consumer',), 'invest in growth',
    )


@pytest.mark.parametrize('enrichment_id', sorted(P0_ENRICHMENTS))
def test_each_p0_executor_returns_meaningful_cited_message_safe_output(
    enrichment_id: str,
) -> None:
    output = execute_p0(enrichment_id, (_evidence(),), seller_context=_context())
    assert output.assertions
    assert all(item.value and item.evidence_ids == ('ev-1',) for item in output.assertions)
    assert all(item.visibility is Visibility.MESSAGE_SAFE for item in output.assertions)
    assert any('Pipeline Sprint' in str(item.value) for item in output.assertions)


@pytest.mark.parametrize('enrichment_id', sorted(P0_ENRICHMENTS))
def test_each_p0_executor_accepts_all_required_fields_as_explicit_unknowns(
    enrichment_id: str,
) -> None:
    output = execute_p0(
        enrichment_id, (_evidence(),), seller_context=_context(),
        findings=(), unknowns=P0_ENRICHMENTS[enrichment_id],
    )
    assert output.assertions == ()
    assert output.unknowns == P0_ENRICHMENTS[enrichment_id]


@pytest.mark.parametrize('enrichment_id', sorted(P0_ENRICHMENTS))
def test_every_executor_requires_cited_evidence_and_supplied_seller_context(
    enrichment_id: str,
) -> None:
    with pytest.raises(ValueError, match='cited evidence'):
        execute_p0(enrichment_id, (), seller_context=_context())
    with pytest.raises(ValueError, match='seller_context'):
        execute_p0(enrichment_id, (_evidence(),), seller_context=None)


def test_message_safe_output_excludes_filter_only_findings() -> None:
    output = execute_p0(
        'company-description', (_evidence(),), seller_context=_context(),
        findings=(
            Finding('description', 'Safe summary', Visibility.MESSAGE_SAFE),
            Finding('personal_email', 'secret@example.com', Visibility.FILTER_ONLY),
        ),
        output_visibility='message_safe',
    )
    assert [item.field for item in output.assertions] == ['description']
    assert 'secret@example.com' not in repr(output)


def test_executor_rejects_unknown_or_untyped_output() -> None:
    with pytest.raises(ValueError, match='unknown P0 enrichment'):
        execute_p0('made-up', (_evidence(),), seller_context=_context())
    with pytest.raises(ValueError, match='Finding'):
        execute_p0('company-description', (_evidence(),), seller_context=_context(),
                   findings=('bad',))
