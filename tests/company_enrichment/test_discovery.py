import pytest

from scripts.company_enrichment.discovery import (
    AuthenticationRequired,
    CapabilityDiscovery,
    CapabilityRequirement,
    CapabilityRegistry,
    ProbeResult,
    ProbeStatus,
)


class RecordingGtmProbe:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    def probe(self) -> ProbeResult:
        self.calls.append("gtm")
        return ProbeResult(
            name="gtm-orchestrator",
            status=ProbeStatus.AVAILABLE,
            details={
                "adapter": "homepage-scrape",
                "path": "C:/gtm/web-scraping",
                "version": "2.1.0",
                "free_levels": [1, 2],
                "paid_levels": [3, 4],
            },
        )


class MissingNexusProbe:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    def probe(self, enrichment_id: str) -> ProbeResult:
        self.calls.append("nexus")
        raise AuthenticationRequired("NEXUS_BOUNDARY_TOKEN is missing")


def test_discovery_runs_gtm_then_nexus_then_selects_homepage_first() -> None:
    calls: list[str] = []
    recorded = []
    discovery = CapabilityDiscovery(
        gtm_probe=RecordingGtmProbe(calls),
        nexus_probe=MissingNexusProbe(calls),
        registry=CapabilityRegistry.default(),
        record_discovery=recorded.append,
    )

    record = discovery.discover(
        enrichment_id="company-description",
        fallback_order=("homepage-scrape", "lg-free", "parallel-search"),
    )

    assert calls == ["gtm", "nexus"]
    assert record.steps == ("gtm", "nexus", "select")
    assert record.selected_capability == "homepage-scrape"
    assert record.nexus.status is ProbeStatus.AUTHENTICATION_REQUIRED
    assert "NEXUS_BOUNDARY_TOKEN" in record.nexus.message
    assert record.gtm_path == "C:/gtm/web-scraping"
    assert record.gtm_version == "2.1.0"
    assert record.nexus_query == "company-description"
    assert record.nexus_outcome == "authentication_required"
    assert recorded == [record]


def test_default_registry_keeps_seed_data_out_of_runtime_routing() -> None:
    registry = CapabilityRegistry.default()

    assert "ai-ark-seed" not in registry
    assert registry["homepage-scrape"].production_rank == 1
    assert registry["homepage-scrape"].metadata["firecrawl_approved"] is True
    assert registry["lg-free"].role == "structured-gap-filler"
    assert registry["harvest-jobs"].role == "jobs-primary"
    assert registry["parallel-search"].role == "search-fallback-comparator"
    assert registry["parallel-search"].operations == ("search",)


def test_registry_records_provenance_cost_validation_and_eligibility() -> None:
    registry = CapabilityRegistry.default()

    homepage = registry["homepage-scrape"]
    assert homepage.provenance == "gtm-orchestrator/web-scraping"
    assert homepage.cost_class == "free-then-paid"
    assert homepage.validation_state == "approved"
    assert homepage.metadata["executor"] == "firecrawl_waterfall.py"

    meta = registry["meta-ads"]
    assert meta.metadata["actor_id"] == "ZQyDz7154hrOfrDMK"
    assert meta.validation_state == "sample_required"
    assert meta.eligible_enrichments == ("running-ads-offer-intelligence",)

    assert registry["tiktok-ads"].validation_state == "capability_required"
    assert registry["techsight"].validation_state == "authentication_required"
    assert registry["parallel-search"].metadata["known_url_fetch"] is False


def test_discovery_fails_closed_when_required_probe_was_not_checked() -> None:
    calls: list[str] = []

    class SkippedGtm:
        def probe(self) -> ProbeResult:
            calls.append("gtm")
            return ProbeResult("gtm-orchestrator", ProbeStatus.NOT_CHECKED)

    discovery = CapabilityDiscovery(
        gtm_probe=SkippedGtm(),
        nexus_probe=MissingNexusProbe(calls),
        registry=CapabilityRegistry.default(),
        record_discovery=lambda _record: None,
    )

    try:
        discovery.discover("company-description", ("homepage-scrape",))
    except RuntimeError as error:
        assert "GTM probe was not checked" in str(error)
    else:
        raise AssertionError("skipped GTM discovery must fail closed")


def test_registry_skips_unvalidated_or_ineligible_capabilities() -> None:
    registry = CapabilityRegistry.default()

    selected = registry.select(
        CapabilityRequirement(
            enrichment_id="running-ads-offer-intelligence",
            operation="ads",
            fallback_order=("meta-ads", "tiktok-ads", "linkedin-ads"),
        )
    )

    assert selected.id == "linkedin-ads"

    with pytest.raises(RuntimeError, match="verified capability gap"):
        registry.select(
            CapabilityRequirement(
                enrichment_id="technology-detection",
                operation="detect",
                fallback_order=("techsight",),
            )
        )

    with pytest.raises(RuntimeError, match="verified capability gap"):
        registry.select(
            CapabilityRequirement(
                enrichment_id="job-opportunity-mining",
                operation="jobs",
                fallback_order=("free-job-enrichment",),
            )
        )


def test_verified_capability_gap_is_recorded_before_failure() -> None:
    recorded = []

    with pytest.raises(RuntimeError, match="verified capability gap"):
        CapabilityDiscovery(
            gtm_probe=RecordingGtmProbe([]),
            nexus_probe=MissingNexusProbe([]),
            registry=CapabilityRegistry.default(),
            record_discovery=recorded.append,
        ).discover(
            "technology-detection",
            ("techsight",),
            operation="detect",
        )

    assert len(recorded) == 1
    assert recorded[0].selected_capability is None
    assert recorded[0].selection_outcome == "verified_gap"
    assert recorded[0].eligible_capabilities == ()


def test_unavailable_gtm_probe_cannot_select_homepage_scraper() -> None:
    class UnavailableGtm:
        def probe(self) -> ProbeResult:
            return ProbeResult(
                "gtm-orchestrator",
                ProbeStatus.UNAVAILABLE,
                {"path": "C:/gtm/web-scraping", "version": "2.1.0"},
            )

    class AvailableNexus:
        def probe(self, enrichment_id: str) -> ProbeResult:
            return ProbeResult("nexus", ProbeStatus.AVAILABLE)

    record = CapabilityDiscovery(
        gtm_probe=UnavailableGtm(),
        nexus_probe=AvailableNexus(),
        registry=CapabilityRegistry.default(),
        record_discovery=lambda _record: None,
    ).discover(
        "company-description",
        ("homepage-scrape", "parallel-search"),
        operation="search",
    )

    assert record.selected_capability == "parallel-search"
