from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from .contracts import EnrichmentRequest, FieldAssertion, SellerContext, Visibility
from .runner import ExecutionOutcome


P0_IDS = frozenset(
    {
        "analogy-value-translator",
        "company-description",
        "competitor-intelligence",
        "growth-signals",
        "icp-persona-analysis",
        "job-opportunity-mining",
        "news-product-launches",
        "running-ads-offer-intelligence",
    }
)

MODEL_LADDER = (
    "gpt-5-nano",
    "gpt-4o-mini",
    "gpt-4.1-mini",
    "gpt-5.6-luna",
)

_SELLER_CONTEXT_ENRICHMENTS = {
    "analogy-value-translator",
    "job-opportunity-mining",
}


def validate_message_safety(
    message: str, assertions: Sequence[FieldAssertion]
) -> None:
    if not isinstance(message, str):
        raise ValueError("message must be text")
    normalized_message = message.casefold()
    for assertion in assertions:
        if assertion.visibility is not Visibility.FILTER_ONLY:
            continue
        if isinstance(assertion.value, str) and assertion.value.casefold() in normalized_message:
            raise ValueError(f"filter-only field cannot appear in message: {assertion.field}")


class P0Executor:
    """Builds provider-neutral, evidence-linked inputs for later model execution."""

    def execute(
        self,
        request: EnrichmentRequest,
        *,
        evidence_ids: tuple[str, ...],
    ) -> ExecutionOutcome:
        if request.enrichment_id not in P0_IDS:
            raise ValueError(f"unsupported P0 enrichment: {request.enrichment_id}")
        if not evidence_ids or any(not item for item in evidence_ids):
            raise ValueError("P0 execution requires cited evidence")

        output: dict[str, Any] = {
            "enrichment_id": request.enrichment_id,
            "company_id": request.company_id,
            "evidence_ids": tuple(evidence_ids),
        }
        if request.enrichment_id in _SELLER_CONTEXT_ENRICHMENTS:
            seller = request.inputs.get("seller_context")
            if not isinstance(seller, SellerContext):
                raise ValueError("seller_context is required for seller-linked output")
            if (
                request.enrichment_id == "analogy-value-translator"
                and "company_dossier" not in request.inputs
            ):
                raise ValueError("company_dossier is required for analogy translation")
            output["seller_linkage"] = {
                "named_offer": seller.named_offer,
                "promised_outcome": seller.promised_outcome,
                "timeline": seller.timeline,
                "proof": seller.proof,
                "de_risking": seller.de_risking,
                "exclusions": seller.exclusions,
            }
        return ExecutionOutcome(output)
