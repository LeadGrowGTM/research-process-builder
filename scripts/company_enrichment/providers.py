from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Any, Protocol, Sequence
from urllib.parse import urlparse

from .contracts import FailureKind, MAX_EXCERPT_CHARS


def _text(name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty text")


def _url(name: str, value: str) -> None:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{name} must be an absolute HTTP(S) URL")


@dataclass(frozen=True, slots=True)
class SourceObservation:
    url: str
    excerpt: str

    def __post_init__(self) -> None:
        _url("observation URL", self.url)
        if not isinstance(self.excerpt, str) or len(self.excerpt) > MAX_EXCERPT_CHARS:
            raise ValueError(f"excerpt exceeds {MAX_EXCERPT_CHARS} characters")


@dataclass(frozen=True, slots=True)
class SearchRequest:
    query: str

    def __post_init__(self) -> None:
        _text("query", self.query)


@dataclass(frozen=True, slots=True)
class SearchResult:
    observations: tuple[SourceObservation, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "observations", tuple(self.observations))
        if not all(isinstance(item, SourceObservation) for item in self.observations):
            raise ValueError("observations must contain SourceObservation values")


@dataclass(frozen=True, slots=True)
class KnownUrlRequest:
    url: str

    def __post_init__(self) -> None:
        _url("known URL", self.url)


@dataclass(frozen=True, slots=True)
class ScrapeResult:
    observations: tuple[SourceObservation, ...]
    waterfall_level: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "observations", tuple(self.observations))
        if not all(isinstance(item, SourceObservation) for item in self.observations):
            raise ValueError("observations must contain SourceObservation values")
        if self.waterfall_level not in {1, 2, 3, 4}:
            raise ValueError("waterfall_level must be between 1 and 4")


class SearchProvider(Protocol):
    def search(self, request: SearchRequest) -> SearchResult: ...


class ScrapeProvider(Protocol):
    def scrape(self, request: KnownUrlRequest) -> ScrapeResult: ...


class ProviderFailure(RuntimeError):
    pass


class RetryableFailure(ProviderFailure):
    pass


class TerminalFailure(ProviderFailure):
    pass


class BudgetFailure(ProviderFailure):
    pass


class AuthenticationFailure(ProviderFailure):
    pass


class ContractFailure(ProviderFailure):
    pass


class InsufficientEvidence(ProviderFailure):
    pass


@dataclass(frozen=True, slots=True)
class NormalizedFailure:
    kind: FailureKind
    message: str


_FAILURE_KINDS: tuple[tuple[type[BaseException], FailureKind], ...] = (
    (RetryableFailure, FailureKind.RETRYABLE),
    (TerminalFailure, FailureKind.TERMINAL),
    (BudgetFailure, FailureKind.BUDGET_EXHAUSTED),
    (AuthenticationFailure, FailureKind.AUTHENTICATION_REQUIRED),
    (ContractFailure, FailureKind.CONTRACT_INVALID),
    (InsufficientEvidence, FailureKind.INSUFFICIENT_EVIDENCE),
)


def normalize_failure(error: BaseException) -> NormalizedFailure:
    for error_type, kind in _FAILURE_KINDS:
        if isinstance(error, error_type):
            return NormalizedFailure(kind, str(error) or kind.value)
    return NormalizedFailure(FailureKind.TERMINAL, str(error) or "provider failed")


@dataclass(frozen=True, slots=True)
class RoutePlan:
    provider_ids: tuple[str, ...]
    known_url_provider: str | None


class ProviderRouter:
    def route(
        self,
        definition: Any,
        discovery: Any,
        *,
        known_urls: Sequence[str] = (),
    ) -> RoutePlan:
        eligible = frozenset(discovery.eligible_capabilities)
        ordered = [
            capability_id
            for capability_id in dict.fromkeys(definition.fallback_order)
            if capability_id in eligible
        ]
        selected = discovery.selected_capability
        if selected in ordered:
            ordered.remove(selected)
            ordered.insert(0, selected)
        known_url_provider = None
        if known_urls:
            known_url_provider = "homepage-scrape"
            if known_url_provider not in eligible:
                raise ProviderFailure("known-URL capability gap: homepage scraper unavailable")
            if known_url_provider in ordered:
                ordered.remove(known_url_provider)
            ordered.insert(0, known_url_provider)
        if not ordered:
            raise ProviderFailure("verified capability gap: no eligible provider route")
        return RoutePlan(tuple(ordered), known_url_provider)


class AdStatus(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class CompanyProfile:
    is_b2b: bool
    is_ecommerce_or_cpg: bool = False


def ad_channels(profile: CompanyProfile) -> tuple[str, ...]:
    channels = ["google", "meta"]
    if profile.is_b2b:
        channels.append("linkedin")
    if profile.is_ecommerce_or_cpg:
        channels.append("tiktok")
    return tuple(channels)


@dataclass(frozen=True, slots=True)
class AdsRequest:
    company_name: str
    url: str

    def __post_init__(self) -> None:
        _text("company_name", self.company_name)
        _url("ads URL", self.url)


@dataclass(frozen=True, slots=True)
class AdFinding:
    channel: str
    status: AdStatus
    started_on: date | None
    ended_on: date | None
    geography: str | None
    angle: str | None
    offer: str | None
    call_to_action: str | None
    landing_page: str | None
    evidence: tuple[SourceObservation, ...]
    confidence: float

    def __post_init__(self) -> None:
        _text("channel", self.channel)
        object.__setattr__(self, "evidence", tuple(self.evidence))
        if not all(isinstance(item, SourceObservation) for item in self.evidence):
            raise ValueError("evidence must contain SourceObservation values")
        if not 0 <= self.confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")
        if self.landing_page is not None:
            _url("landing_page", self.landing_page)
        if self.status is AdStatus.UNKNOWN and self.confidence != 0:
            raise ValueError("unknown ad status must have zero confidence")

    @classmethod
    def unknown(cls, channel: str) -> "AdFinding":
        return cls(channel, AdStatus.UNKNOWN, None, None, None, None, None, None, None, (), 0)


class AdsProvider(Protocol):
    def inspect(self, request: AdsRequest) -> AdFinding: ...


@dataclass(frozen=True, slots=True)
class TechnologyRequest:
    url: str

    def __post_init__(self) -> None:
        _url("technology URL", self.url)


@dataclass(frozen=True, slots=True)
class TechnologyFinding:
    technologies: tuple[str, ...]
    evidence: tuple[SourceObservation, ...]


class TechnologyProvider(Protocol):
    def detect(self, request: TechnologyRequest) -> TechnologyFinding: ...
