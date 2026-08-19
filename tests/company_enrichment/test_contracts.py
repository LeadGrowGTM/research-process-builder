from dataclasses import FrozenInstanceError
from datetime import datetime, timezone

import pytest

from scripts.company_enrichment.contracts import (
    CompanyDossier, EnrichmentRequest, EnrichmentResult, EvidenceRef,
    FailureKind, FieldAssertion, ResultStatus, ReviewStatus, SellerContext,
    Visibility, canonical_json,
)


def test_evidence_requires_an_absolute_http_url() -> None:
    with pytest.raises(ValueError, match="absolute HTTP.*URL"):
        EvidenceRef("ev-1", "/about", datetime(2026, 8, 12, tzinfo=timezone.utc), "a" * 64, "B2B evidence")


def test_evidence_rejects_unbounded_excerpts() -> None:
    with pytest.raises(ValueError, match="excerpt"):
        EvidenceRef("ev-1", "https://example.com", datetime(2026, 8, 12, tzinfo=timezone.utc), "a" * 64, "x" * 2001)


def test_contracts_are_immutable_and_serialize_canonically() -> None:
    request = EnrichmentRequest("company-description", "acme", "1.0", {"domain": "acme.example", "company_name": "Acme"})
    with pytest.raises(FrozenInstanceError):
        request.company_id = "changed"  # type: ignore[misc]
    assert canonical_json(request) == '{"company_id":"acme","enrichment_id":"company-description","input_schema_version":"1.0","inputs":{"company_name":"Acme","domain":"acme.example"}}'


@pytest.mark.parametrize("payload", [{"api_key": "x"}, {"nested": {"authorization": "x"}}, {"token": "x"}])
def test_canonical_serialization_rejects_secret_bearing_keys(payload: dict) -> None:
    with pytest.raises(ValueError, match="secret-bearing key"):
        canonical_json(payload)


def test_seller_context_carries_the_complete_b2b_offer_contract() -> None:
    seller = SellerContext(
        "Commercial HVAC contractors", ("VP Operations", "Service Manager"),
        ("dispatch optimization", "job costing"), "Field Operations Audit",
        "14 days", "Identify margin leakage", ("Documented case study",),
        "No migration required", ("Residential-only operators",), "Uses field-service software",
    )
    assert seller.named_offer == "Field Operations Audit"


def test_dossier_keeps_visibility_and_evidence_at_field_level() -> None:
    evidence = EvidenceRef("ev-1", "https://acme.example/customers", datetime(2026, 8, 12, tzinfo=timezone.utc), "b" * 64, "Used by HVAC contractors.")
    assertion = FieldAssertion("target_market", "Commercial HVAC contractors", ("ev-1",), 0.9, Visibility.MESSAGE_SAFE)
    dossier = CompanyDossier("acme", "1.0", (assertion,), (evidence,), ("current pricing",))
    assert dossier.assertions[0].evidence_ids == ("ev-1",)


def test_result_uses_closed_vocabularies_and_validates_failure_state() -> None:
    result = EnrichmentResult("company-description", "acme", "1.0", ResultStatus.FAILED, {}, FailureKind.INSUFFICIENT_EVIDENCE)
    assert result.failure is FailureKind.INSUFFICIENT_EVIDENCE
    assert ReviewStatus.APPROVED.value == "approved"
    assert Visibility.FILTER_ONLY.value == "filter_only"
    with pytest.raises(ValueError, match="complete result"):
        EnrichmentResult("company-description", "acme", "1.0", ResultStatus.COMPLETE, {}, FailureKind.TERMINAL)
