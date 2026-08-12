from datetime import datetime, timezone

import pytest

from scripts.company_enrichment.evidence import (
    EvidenceStore,
    SaturationTracker,
    SourceRecord,
    cache_key,
)


def test_evidence_store_deduplicates_content_and_detects_tampering(tmp_path) -> None:
    store = EvidenceStore(tmp_path)
    source = SourceRecord(
        url="https://acme.example/about",
        retrieved_at=datetime(2026, 8, 12, tzinfo=timezone.utc),
        source_type="first_party",
        provider="homepage-scrape",
        content="Acme builds workflow software for finance teams.",
        excerpt="Workflow software for finance teams.",
        freshness_days=30,
        paid_cost_usd="0",
    )

    first = store.put(source)
    second = store.put(source)

    assert first == second
    assert len(list((tmp_path / "objects").glob("*.json"))) == 1
    assert store.get(first.content_hash).content == source.content

    object_path = tmp_path / "objects" / f"{first.content_hash}.json"
    object_path.write_text('{"tampered":true}', encoding="utf-8")
    with pytest.raises(ValueError, match="tampering"):
        store.get(first.content_hash)


def test_cache_key_includes_url_provider_and_freshness() -> None:
    baseline = cache_key("https://acme.example", "homepage-scrape", 30)
    assert baseline != cache_key("https://acme.example", "lg-free", 30)
    assert baseline != cache_key("https://acme.example", "homepage-scrape", 7)
    assert baseline != cache_key("https://acme.example/about", "homepage-scrape", 30)


def test_saturation_requires_fields_sources_and_two_dry_angles() -> None:
    tracker = SaturationTracker(required_fields=("description", "target_customer"))
    tracker.observe_source("first_party")
    tracker.observe_source("independent")
    tracker.observe_source("independent")
    tracker.observe_field("description")
    tracker.observe_field("target_customer")
    tracker.observe_search_angle(material_facts_added=False)
    assert tracker.is_saturated is False
    tracker.observe_search_angle(material_facts_added=False)
    assert tracker.is_saturated is True
    tracker.observe_search_angle(material_facts_added=True)
    assert tracker.is_saturated is False
