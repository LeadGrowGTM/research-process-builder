from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .contracts import EvidenceRef, FieldAssertion, SellerContext, Visibility


P0_ENRICHMENTS: Mapping[str, tuple[str, ...]] = {
    'analogy-value-translator': ('pricing', 'technology'),
    'buying-trigger-analysis': ('campaign_idea_1', 'campaign_idea_2'),
    'company-description': ('identity', 'description', 'offers'),
    'competitor-intelligence': ('competitors',),
    'growth-signals': ('growth',),
    'icp-persona-analysis': ('icp', 'personas'),
    'job-opportunity-mining': ('hiring',),
    'news-product-launches': ('news', 'launches'),
    'running-ads-offer-intelligence': ('ads',),
}


@dataclass(frozen=True, slots=True)
class Finding:
    field: str
    value: Any
    visibility: Visibility = Visibility.MESSAGE_SAFE

    def __post_init__(self) -> None:
        if not isinstance(self.field, str) or not self.field.strip():
            raise ValueError('finding field must be non-empty text')
        if self.value is None or self.value == '':
            raise ValueError('finding value must be meaningful')
        if not isinstance(self.visibility, Visibility):
            raise ValueError('finding visibility must be typed')


@dataclass(frozen=True, slots=True)
class ExecutionOutput:
    assertions: tuple[FieldAssertion, ...]
    unknowns: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, 'assertions', tuple(self.assertions))
        object.__setattr__(self, 'unknowns', tuple(self.unknowns))
        if not all(isinstance(item, FieldAssertion) for item in self.assertions):
            raise ValueError('assertions must contain FieldAssertion values')


def execute_p0(
    enrichment_id: str,
    evidence: Sequence[EvidenceRef],
    *,
    seller_context: SellerContext | None,
    findings: Sequence[Finding] = (),
    unknowns: Sequence[str] = (),
    output_visibility: str = 'message_safe',
) -> ExecutionOutput:
    fields = P0_ENRICHMENTS.get(enrichment_id)
    if fields is None:
        raise ValueError(f'unknown P0 enrichment: {enrichment_id}')
    evidence = tuple(evidence)
    if not evidence:
        raise ValueError('P0 execution requires cited evidence')
    if not all(isinstance(item, EvidenceRef) for item in evidence):
        raise ValueError('evidence must contain EvidenceRef values')
    if not isinstance(seller_context, SellerContext):
        raise ValueError(f'{enrichment_id} requires seller_context')
    if not all(isinstance(item, Finding) for item in findings):
        raise ValueError('findings must contain Finding values')
    unknowns = tuple(unknowns)
    if any(not isinstance(item, str) or not item for item in unknowns):
        raise ValueError('unknowns must contain non-empty field names')
    if set(unknowns) - set(fields):
        raise ValueError('unknowns contain fields outside enrichment scope')
    if output_visibility not in {'message_safe', 'filter_only'}:
        raise ValueError('invalid output visibility')

    evidence_ids = tuple(item.evidence_id for item in evidence)
    if findings or unknowns:
        selected = tuple(
            item for item in findings
            if output_visibility == 'filter_only'
            or item.visibility is Visibility.MESSAGE_SAFE
        )
    else:
        excerpt = evidence[0].excerpt.strip()
        offer = seller_context.named_offer
        selected = tuple(
            Finding(field, f'{excerpt} Relevant to {offer}.') for field in fields
        )
    assertions = tuple(
        FieldAssertion(item.field, item.value, evidence_ids, 0.8, item.visibility)
        for item in selected
    )
    if not assertions and not unknowns:
        raise ValueError('typed output contains no covered fields')
    return ExecutionOutput(assertions, unknowns)
