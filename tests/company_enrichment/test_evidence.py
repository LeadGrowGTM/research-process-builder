from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import threading
import time

import pytest

from scripts.company_enrichment.evidence import (
    EvidenceStore,
    SearchAngleResult,
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


def test_material_content_deduplicates_across_retrieval_metadata(tmp_path) -> None:
    store = EvidenceStore(tmp_path)
    first = SourceRecord(
        "https://acme.example/about",
        datetime(2026, 8, 12, tzinfo=timezone.utc),
        "first_party",
        "homepage-scrape",
        "Same material",
        "Same material",
        30,
        "0",
    )
    second = SourceRecord(
        "https://mirror.example/acme",
        first.retrieved_at + timedelta(days=1),
        "independent",
        "parallel-search",
        "Same material",
        "Same material",
        7,
        "0.01",
    )

    assert store.put(first).content_hash == store.put(second).content_hash
    assert len(list((tmp_path / "objects").glob("*.json"))) == 1
    assert len((tmp_path / "sources.jsonl").read_text(encoding="utf-8").splitlines()) == 2


def test_concurrent_identical_puts_are_idempotent(tmp_path) -> None:
    store = EvidenceStore(tmp_path)
    source = SourceRecord(
        "https://acme.example",
        datetime(2026, 8, 12, tzinfo=timezone.utc),
        "first_party",
        "homepage-scrape",
        "Concurrent material",
        "Concurrent material",
        30,
        "0",
    )

    with ThreadPoolExecutor(max_workers=8) as executor:
        refs = list(executor.map(lambda _index: store.put(source), range(20)))

    assert len({ref.content_hash for ref in refs}) == 1
    assert len(list((tmp_path / "objects").glob("*.json"))) == 1
    assert len((tmp_path / "sources.jsonl").read_text(encoding="utf-8").splitlines()) == 1


def test_fresh_cache_hit_skips_reservation_and_provider_collection(tmp_path) -> None:
    store = EvidenceStore(tmp_path)
    retrieved = datetime(2026, 8, 12, tzinfo=timezone.utc)
    source = SourceRecord(
        "https://acme.example",
        retrieved,
        "first_party",
        "homepage-scrape",
        "Cached material",
        "Cached material",
        30,
        "0",
    )
    store.put(source)
    calls = []

    reference, cache_hit = store.resolve(
        url=source.url,
        provider=source.provider,
        freshness_days=30,
        as_of=retrieved + timedelta(days=29),
        collect=lambda: calls.append("reserve/provider") or source,
    )

    assert cache_hit is True
    assert reference.content_hash == store.put(source).content_hash
    assert calls == []


def test_stale_cache_collects_and_refreshes(tmp_path) -> None:
    store = EvidenceStore(tmp_path)
    old = SourceRecord(
        "https://acme.example",
        datetime(2026, 7, 1, tzinfo=timezone.utc),
        "first_party",
        "homepage-scrape",
        "Old material",
        "Old material",
        30,
        "0",
    )
    fresh = SourceRecord(
        old.url,
        datetime(2026, 8, 12, tzinfo=timezone.utc),
        old.source_type,
        old.provider,
        "Fresh material",
        "Fresh material",
        30,
        "0",
    )
    store.put(old)
    calls = []

    reference, cache_hit = store.resolve(
        url=old.url,
        provider=old.provider,
        freshness_days=30,
        as_of=fresh.retrieved_at,
        collect=lambda: calls.append("collect") or fresh,
    )

    assert cache_hit is False
    assert store.get(reference.content_hash).content == "Fresh material"
    assert calls == ["collect"]


def test_concurrent_cache_misses_collect_only_once(tmp_path) -> None:
    store = EvidenceStore(tmp_path)
    retrieved = datetime(2026, 8, 12, tzinfo=timezone.utc)
    source = SourceRecord(
        "https://acme.example",
        retrieved,
        "first_party",
        "homepage-scrape",
        "Single-flight material",
        "Single-flight material",
        30,
        "0.01",
    )
    calls = 0
    calls_lock = threading.Lock()

    def collect() -> SourceRecord:
        nonlocal calls
        with calls_lock:
            calls += 1
        time.sleep(0.02)
        return source

    def resolve(_index):
        return store.resolve(
            url=source.url,
            provider=source.provider,
            freshness_days=30,
            as_of=retrieved,
            collect=collect,
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(resolve, range(16)))

    assert calls == 1
    assert sum(not cache_hit for _reference, cache_hit in results) == 1


def test_missing_cache_projection_repairs_from_authoritative_source_journal(tmp_path) -> None:
    store = EvidenceStore(tmp_path)
    retrieved = datetime(2026, 8, 12, tzinfo=timezone.utc)
    source = SourceRecord(
        "https://acme.example",
        retrieved,
        "first_party",
        "homepage-scrape",
        "Recoverable material",
        "Recoverable material",
        30,
        "0",
    )
    store.put(source)
    store.cache_journal.unlink()
    calls = []

    _reference, cache_hit = store.resolve(
        url=source.url,
        provider=source.provider,
        freshness_days=30,
        as_of=retrieved,
        collect=lambda: calls.append("recollect") or source,
    )

    assert cache_hit is True
    assert calls == []
    assert store.cache_journal.exists()


def test_saturation_requires_cited_fields_distinct_sources_and_two_dry_angles() -> None:
    tracker = SaturationTracker(required_fields=("description", "target_customer"))
    tracker.observe(
        SearchAngleResult(
            source_id="homepage",
            source_type="first_party",
            field_citations=(("description", "ev-home"),),
            material_facts_added=True,
        )
    )
    tracker.observe(
        SearchAngleResult(
            source_id="news-1",
            source_type="independent",
            field_citations=(("target_customer", "ev-news-1"),),
            material_facts_added=False,
            angle_id="customer-search",
        )
    )
    tracker.observe(
        SearchAngleResult(
            source_id="news-1",
            source_type="independent",
            field_citations=(),
            material_facts_added=False,
            angle_id="customer-search",
        )
    )
    assert tracker.is_saturated is False
    tracker.observe(
        SearchAngleResult(
            source_id="news-2",
            source_type="independent",
            field_citations=(),
            material_facts_added=False,
            angle_id="category-search",
        )
    )
    assert tracker.is_saturated is True
    tracker.observe(SearchAngleResult(material_facts_added=True))
    assert tracker.is_saturated is False
