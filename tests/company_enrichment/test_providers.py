from datetime import date
from types import SimpleNamespace

import pytest

from scripts.company_enrichment.contracts import FailureKind
from scripts.company_enrichment.providers import (
    AdFinding,
    AdStatus,
    AdsRequest,
    AuthenticationFailure,
    CompanyProfile,
    ContractFailure,
    InsufficientEvidence,
    KnownUrlRequest,
    ProviderFailure,
    ProviderRouter,
    RetryableFailure,
    SearchRequest,
    SearchResult,
    SourceObservation,
    TerminalFailure,
    ad_channels,
    normalize_failure,
)
from scripts.company_enrichment.adapters.ads import MetaAdsAdapter
from scripts.company_enrichment.adapters.gtm_waterfall import GtmWaterfallAdapter
from scripts.company_enrichment.adapters.parallel import ParallelSearchAdapter


def _observation(url: str = "https://acme.example/about") -> SourceObservation:
    return SourceObservation(url=url, excerpt="Acme serves finance teams.")


def test_parallel_bridge_exposes_search_without_known_url_fetch() -> None:
    calls = []
    adapter = ParallelSearchAdapter(
        lambda query: (calls.append(query), [_observation()])[1]
    )

    result = adapter.search(SearchRequest("Acme customers"))

    assert result == SearchResult((_observation(),))
    assert calls == ["Acme customers"]
    assert not hasattr(adapter, "scrape")
    assert not hasattr(result, "provider_payload")


def test_gtm_waterfall_reserves_only_before_paid_levels() -> None:
    events = []

    def execute(level, request):
        events.append(("execute", level, request.url))
        return () if level < 3 else (_observation(request.url),)

    adapter = GtmWaterfallAdapter(
        execute=execute,
        reserve=lambda key, amount: events.append(("reserve", key, amount)),
    )

    result = adapter.scrape(KnownUrlRequest("https://acme.example"))

    assert result.observations == (_observation("https://acme.example"),)
    assert events == [
        ("execute", 1, "https://acme.example"),
        ("execute", 2, "https://acme.example"),
        ("reserve", "gtm-waterfall:3:https://acme.example", "0.01"),
        ("execute", 3, "https://acme.example"),
    ]
    assert adapter.executor_name == "firecrawl_waterfall.py"


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (RetryableFailure("later"), FailureKind.RETRYABLE),
        (TerminalFailure("bad target"), FailureKind.TERMINAL),
        (AuthenticationFailure("token"), FailureKind.AUTHENTICATION_REQUIRED),
        (ContractFailure("shape"), FailureKind.CONTRACT_INVALID),
        (InsufficientEvidence("empty"), FailureKind.INSUFFICIENT_EVIDENCE),
    ],
)
def test_provider_failures_normalize_without_payload_leakage(error, expected) -> None:
    failure = normalize_failure(error)
    assert failure.kind is expected
    assert failure.message
    assert not hasattr(failure, "payload")


def test_router_keeps_known_urls_off_parallel() -> None:
    router = ProviderRouter()
    definition = SimpleNamespace(fallback_order=("parallel-search", "homepage-scrape"))
    discovery = SimpleNamespace(selected_capability="parallel-search")

    plan = router.route(definition, discovery, known_urls=("https://acme.example",))

    assert plan.provider_ids == ("homepage-scrape", "parallel-search")
    assert plan.known_url_provider == "homepage-scrape"


@pytest.mark.parametrize(
    ("profile", "expected"),
    [
        (CompanyProfile(is_b2b=False), ("google", "meta")),
        (CompanyProfile(is_b2b=True), ("google", "meta", "linkedin")),
        (
            CompanyProfile(is_b2b=True, is_ecommerce_or_cpg=True),
            ("google", "meta", "linkedin", "tiktok"),
        ),
    ],
)
def test_ads_routing_is_channel_aware(profile, expected) -> None:
    assert ad_channels(profile) == expected


def test_ad_finding_preserves_normalized_offer_evidence() -> None:
    finding = AdFinding(
        channel="linkedin",
        status=AdStatus.ACTIVE,
        started_on=date(2026, 8, 1),
        ended_on=None,
        geography="Canada",
        angle="Reduce close time",
        offer="Workflow audit",
        call_to_action="Book demo",
        landing_page="https://acme.example/demo",
        evidence=(_observation("https://linkedin.example/ad/1"),),
        confidence=0.9,
    )

    assert finding.status is AdStatus.ACTIVE
    assert finding.offer == "Workflow audit"
    assert finding.evidence[0].url == "https://linkedin.example/ad/1"


def test_meta_requires_small_validation_before_batch_eligibility() -> None:
    inspected = []
    adapter = MetaAdsAdapter(
        inspect_one=lambda request: (
            inspected.append(request.url),
            AdFinding.unknown("meta"),
        )[1]
    )

    assert adapter.batch_eligible is False
    validation = adapter.validate(
        (
            AdsRequest("Acme", "https://acme.example"),
            AdsRequest("Beta", "https://beta.example"),
        )
    )

    assert validation.sample_size == 2
    assert validation.schema_valid is True
    assert validation.cost_valid is True
    assert adapter.batch_eligible is True
    assert inspected == ["https://acme.example", "https://beta.example"]

    with pytest.raises(ValueError, match="1 to 3"):
        adapter.validate(())
