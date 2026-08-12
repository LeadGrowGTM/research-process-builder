from scripts.company_enrichment.discovery import (
    AuthenticationRequired,
    CapabilityDiscovery,
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
    discovery = CapabilityDiscovery(
        gtm_probe=RecordingGtmProbe(calls),
        nexus_probe=MissingNexusProbe(calls),
        registry=CapabilityRegistry.default(),
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
    )

    try:
        discovery.discover("company-description", ("homepage-scrape",))
    except RuntimeError as error:
        assert "GTM probe was not checked" in str(error)
    else:
        raise AssertionError("skipped GTM discovery must fail closed")
