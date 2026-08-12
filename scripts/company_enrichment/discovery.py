from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Callable, Mapping, Protocol


class ProbeStatus(str, Enum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    AUTHENTICATION_REQUIRED = "authentication_required"
    NOT_CHECKED = "not_checked"


class AuthenticationRequired(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ProbeResult:
    name: str
    status: ProbeStatus
    details: Mapping[str, Any] = field(default_factory=dict)
    message: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "details", MappingProxyType(dict(self.details)))


@dataclass(frozen=True, slots=True)
class Capability:
    id: str
    role: str
    operations: tuple[str, ...]
    production_rank: int
    metadata: Mapping[str, Any] = field(default_factory=dict)
    provenance: str = "local"
    cost_class: str = "unknown"
    validation_state: str = "unverified"
    eligible_enrichments: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "operations", tuple(self.operations))
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))
        object.__setattr__(self, "eligible_enrichments", tuple(self.eligible_enrichments))


@dataclass(frozen=True, slots=True)
class CapabilityRequirement:
    enrichment_id: str
    fallback_order: tuple[str, ...]
    operation: str | None = None
    unavailable_capabilities: frozenset[str] = frozenset()


class CapabilityRegistry(Mapping[str, Capability]):
    def __init__(self, capabilities: tuple[Capability, ...]) -> None:
        self._capabilities = {item.id: item for item in capabilities}

    def __getitem__(self, key: str) -> Capability:
        return self._capabilities[key]

    def __iter__(self):
        return iter(self._capabilities)

    def __len__(self) -> int:
        return len(self._capabilities)

    @classmethod
    def default(cls) -> "CapabilityRegistry":
        return cls(
            (
                Capability(
                    "homepage-scrape",
                    "known-url-primary",
                    ("scrape",),
                    1,
                    {
                        "implementation": "gtm-orchestrator/web-scraping",
                        "version": "2.1.0",
                        "executor": "firecrawl_waterfall.py",
                        "free_levels": (1, 2),
                        "paid_levels": (3, 4),
                        "firecrawl_approved": True,
                    },
                    provenance="gtm-orchestrator/web-scraping",
                    cost_class="free-then-paid",
                    validation_state="approved",
                ),
                Capability(
                    "lg-free", "structured-gap-filler", ("enrich",), 2,
                    provenance="lg_free", cost_class="free", validation_state="observed",
                ),
                Capability(
                    "harvest-jobs", "jobs-primary", ("jobs",), 1,
                    provenance="harvest", validation_state="preferred",
                ),
                Capability("free-job-enrichment", "jobs-free-filler", ("jobs",), 2),
                Capability("company-careers-scrape", "jobs-first-party", ("scrape",), 3),
                Capability(
                    "parallel-search",
                    "search-fallback-comparator",
                    ("search",),
                    99,
                    {"known_url_fetch": False},
                    provenance="parallel-search-mcp",
                    validation_state="search_only",
                ),
                Capability(
                    "linkedin-ads", "b2b-ads", ("ads",), 2,
                    provenance="local-linkedin-ads",
                    validation_state="observed",
                    eligible_enrichments=("running-ads-offer-intelligence",),
                ),
                Capability(
                    "meta-ads", "applicable-ads", ("ads",), 3,
                    {"actor_id": "ZQyDz7154hrOfrDMK"},
                    provenance="historical-apify-actor",
                    validation_state="sample_required",
                    eligible_enrichments=("running-ads-offer-intelligence",),
                ),
                Capability(
                    "tiktok-ads", "commerce-ads", ("ads",), 4,
                    provenance="runtime-discovery",
                    validation_state="capability_required",
                    eligible_enrichments=("running-ads-offer-intelligence",),
                ),
                Capability(
                    "techsight", "technology-detection", ("detect",), 1,
                    provenance="local-techsight-launcher",
                    validation_state="authentication_required",
                ),
                Capability(
                    "model-router", "model-comparison", ("generate",), 1,
                    validation_state="available",
                ),
            )
        )

    def select(
        self, requirement: CapabilityRequirement | tuple[str, ...]
    ) -> Capability:
        if isinstance(requirement, tuple):
            requirement = CapabilityRequirement("", requirement)
        selectable_states = {"approved", "available", "observed", "preferred", "search_only"}
        for capability_id in requirement.fallback_order:
            if capability_id not in self or capability_id in requirement.unavailable_capabilities:
                continue
            capability = self[capability_id]
            if capability.validation_state not in selectable_states:
                continue
            if requirement.operation and requirement.operation not in capability.operations:
                continue
            if (
                capability.eligible_enrichments
                and requirement.enrichment_id not in capability.eligible_enrichments
            ):
                continue
            return capability
        raise RuntimeError("verified capability gap: no eligible registered capability")


class GtmProbe(Protocol):
    def probe(self) -> ProbeResult: ...


class NexusProbe(Protocol):
    def probe(self, enrichment_id: str) -> ProbeResult: ...


@dataclass(frozen=True, slots=True)
class DiscoveryRecord:
    enrichment_id: str
    steps: tuple[str, ...]
    gtm: ProbeResult
    nexus: ProbeResult
    selected_capability: str
    gtm_path: str
    gtm_version: str
    nexus_query: str
    nexus_outcome: str


class CapabilityDiscovery:
    def __init__(
        self,
        *,
        gtm_probe: GtmProbe,
        nexus_probe: NexusProbe,
        registry: CapabilityRegistry,
        record_discovery: Callable[[DiscoveryRecord], None],
    ) -> None:
        self._gtm_probe = gtm_probe
        self._nexus_probe = nexus_probe
        self._registry = registry
        self._record_discovery = record_discovery

    def discover(
        self,
        enrichment_id: str,
        fallback_order: tuple[str, ...],
        *,
        operation: str | None = None,
    ) -> DiscoveryRecord:
        gtm = self._gtm_probe.probe()
        if gtm.status is ProbeStatus.NOT_CHECKED:
            raise RuntimeError("GTM probe was not checked")
        try:
            nexus = self._nexus_probe.probe(enrichment_id)
        except AuthenticationRequired as error:
            nexus = ProbeResult(
                name="nexus",
                status=ProbeStatus.AUTHENTICATION_REQUIRED,
                message=str(error),
            )
        if nexus.status is ProbeStatus.NOT_CHECKED:
            raise RuntimeError("Nexus probe was not checked")
        try:
            gtm_path = str(gtm.details["path"])
            gtm_version = str(gtm.details["version"])
        except KeyError as error:
            raise RuntimeError("GTM probe must record path and version") from error
        unavailable = (
            frozenset({"homepage-scrape", "company-careers-scrape"})
            if gtm.status is not ProbeStatus.AVAILABLE
            else frozenset()
        )
        selected = self._registry.select(
            CapabilityRequirement(
                enrichment_id=enrichment_id,
                fallback_order=tuple(fallback_order),
                operation=operation,
                unavailable_capabilities=unavailable,
            )
        )
        record = DiscoveryRecord(
            enrichment_id=enrichment_id,
            steps=("gtm", "nexus", "select"),
            gtm=gtm,
            nexus=nexus,
            selected_capability=selected.id,
            gtm_path=gtm_path,
            gtm_version=gtm_version,
            nexus_query=enrichment_id,
            nexus_outcome=nexus.status.value,
        )
        self._record_discovery(record)
        return record
