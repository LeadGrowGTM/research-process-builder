"""Deterministic casing normalization for shipped ICP payloads.

Reviewer feedback (Mitch, 2026-08-24): models emit mid-sentence capitals in
outcomes ("looking for Client retention"). Normalization is code, not prompt.
"""

from types import SimpleNamespace

from scripts.company_enrichment_icp_loop import (
    _normalized_casing,
    _payload_from_execution,
)


def test_spurious_leading_capital_is_lowercased() -> None:
    assert _normalized_casing("Client retention") == "client retention"
    assert _normalized_casing("Proven regulatory compliance") == (
        "proven regulatory compliance"
    )
    assert _normalized_casing("Lower service downtime") == "lower service downtime"


def test_acronyms_and_mixed_case_words_are_preserved() -> None:
    assert _normalized_casing("AI operations") == "AI operations"
    assert _normalized_casing("HR visibility") == "HR visibility"
    assert _normalized_casing("SharePoint governance") == "SharePoint governance"
    assert _normalized_casing("faster deal closure") == "faster deal closure"


def test_payload_normalizes_outcomes_and_capitalizes_buyers() -> None:
    execution = SimpleNamespace(assertions=(
        SimpleNamespace(field="icp", value={
            "primary_icp": {
                "buyer": "marketers", "relationship": "looking for",
                "outcome": "Clear campaign performance", "evidence_ids": ["ev-1"],
            },
            "secondary_icps": [{
                "buyer": "sales teams", "relationship": "who need",
                "outcome": "Faster deal closure", "evidence_ids": ["ev-1"],
            }],
            "outcomes": [],
        }),
        SimpleNamespace(field="personas", value={
            "observed_personas": [], "inferred_personas": [],
        }),
    ))
    payload = _payload_from_execution(execution)
    assert payload["primary_icp"]["outcome"] == "clear campaign performance"
    assert payload["primary_icp"]["buyer"] == "Marketers"
    assert payload["secondary_icps"][0]["outcome"] == "faster deal closure"
    assert payload["secondary_icps"][0]["buyer"] == "Sales teams"
