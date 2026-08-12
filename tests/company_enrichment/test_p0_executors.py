from datetime import datetime, timezone

import pytest

from scripts.company_enrichment.contracts import (
    EnrichmentRequest,
    FieldAssertion,
    SellerContext,
    Visibility,
)
from scripts.company_enrichment.executors import (
    MODEL_LADDER,
    P0_IDS,
    P0Executor,
    validate_message_safety,
)


def _seller() -> SellerContext:
    return SellerContext(
        target_market="B2B SaaS finance teams",
        personas=("VP Finance",),
        capabilities=("workflow automation",),
        named_offer="Finance workflow audit",
        timeline="30 days",
        promised_outcome="shorter close cycles",
        proof=("Customer reduced close time",),
        de_risking="No long-term contract",
        exclusions=("No guaranteed savings",),
        current_investment_worldview="Teams already invest in ERP tooling",
    )


@pytest.mark.parametrize("enrichment_id", sorted(P0_IDS))
def test_all_eight_p0_executors_produce_grounded_output(enrichment_id) -> None:
    inputs = {
        "company_name": "Acme",
        "domain": "acme.example",
        "seller_context": _seller(),
        "company_dossier": {"evidence_ids": ["ev-1"]},
    }
    request = EnrichmentRequest(enrichment_id, "acme", "1.0", inputs)

    outcome = P0Executor().execute(request, evidence_ids=("ev-1",))

    assert outcome.output["enrichment_id"] == enrichment_id
    assert outcome.output["evidence_ids"] == ("ev-1",)


def test_seller_linkage_uses_supplied_context_without_inventing_claims() -> None:
    seller = _seller()
    executor = P0Executor()

    for enrichment_id in ("job-opportunity-mining", "analogy-value-translator"):
        request = EnrichmentRequest(
            enrichment_id,
            "acme",
            "1.0",
            {
                "company_name": "Acme",
                "domain": "acme.example",
                "seller_context": seller,
                "company_dossier": {"evidence_ids": ["ev-1"]},
            },
        )
        linkage = executor.execute(request, evidence_ids=("ev-1",)).output[
            "seller_linkage"
        ]
        assert linkage == {
            "named_offer": "Finance workflow audit",
            "promised_outcome": "shorter close cycles",
            "timeline": "30 days",
            "proof": ("Customer reduced close time",),
            "de_risking": "No long-term contract",
            "exclusions": ("No guaranteed savings",),
        }


def test_filter_only_fact_cannot_appear_in_message_safe_text() -> None:
    social = FieldAssertion(
        "social_activity",
        "CEO liked a competitor post",
        ("ev-1",),
        0.8,
        Visibility.FILTER_ONLY,
    )
    with pytest.raises(ValueError, match="filter-only"):
        validate_message_safety(
            "Mention that the CEO liked a competitor post.", (social,)
        )


def test_model_ladder_is_fixed_and_auditable() -> None:
    assert MODEL_LADDER == (
        "gpt-5-nano",
        "gpt-4o-mini",
        "gpt-4.1-mini",
        "gpt-5.6-luna",
    )
