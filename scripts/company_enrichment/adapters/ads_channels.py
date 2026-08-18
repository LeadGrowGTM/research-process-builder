"""Channel fan-out and evidence mapping for ad-library providers."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from ..evidence import SourceRecord
from ..providers import (
    AdFinding,
    AdsProvider,
    AdsRequest,
    NormalizedFailure,
    ProviderFailure,
    normalize_failure,
)
from .google_ads import (
    GOOGLE_ADS_CHANNEL,
    GOOGLE_ADS_FRESHNESS_DAYS,
    GOOGLE_ADS_PAID_COST_USD,
    GOOGLE_ADS_PROVIDER,
)
from .meta_ads import (
    META_ADS_CHANNEL,
    META_ADS_FRESHNESS_DAYS,
    META_ADS_PAID_COST_USD,
    META_ADS_PROVIDER,
)

AD_SOURCE_TYPE = "independent"

_CHANNEL_PROVIDERS: dict[str, tuple[str, int, str]] = {
    GOOGLE_ADS_CHANNEL: (GOOGLE_ADS_PROVIDER, GOOGLE_ADS_FRESHNESS_DAYS, GOOGLE_ADS_PAID_COST_USD),
    META_ADS_CHANNEL: (META_ADS_PROVIDER, META_ADS_FRESHNESS_DAYS, META_ADS_PAID_COST_USD),
}


@dataclass(frozen=True, slots=True)
class AdChannelOutcome:
    finding: AdFinding
    failure: NormalizedFailure | None


def collect_ad_findings(
    request: AdsRequest, providers: Mapping[str, AdsProvider]
) -> tuple[AdChannelOutcome, ...]:
    """Run each channel provider; failures become explicit unknown findings with a recorded reason."""
    outcomes: list[AdChannelOutcome] = []
    for channel, provider in providers.items():
        try:
            finding = provider.inspect(request)
        except ProviderFailure as error:
            outcomes.append(AdChannelOutcome(AdFinding.unknown(channel), normalize_failure(error)))
            continue
        if not isinstance(finding, AdFinding) or finding.channel != channel:
            outcomes.append(
                AdChannelOutcome(
                    AdFinding.unknown(channel),
                    normalize_failure(
                        ProviderFailure(f"{channel} provider returned an invalid finding")
                    ),
                )
            )
            continue
        outcomes.append(AdChannelOutcome(finding, None))
    return tuple(outcomes)


def ad_channel_provider(channel: str) -> tuple[str, int, str]:
    try:
        return _CHANNEL_PROVIDERS[channel]
    except KeyError:
        raise ValueError(f"no ad provider is registered for channel {channel!r}") from None


def ad_finding_to_source_records(
    finding: AdFinding, *, retrieved_at: datetime
) -> tuple[SourceRecord, ...]:
    provider, freshness_days, paid_cost_usd = ad_channel_provider(finding.channel)
    return tuple(
        SourceRecord(
            url=observation.url,
            retrieved_at=retrieved_at,
            source_type=AD_SOURCE_TYPE,
            provider=provider,
            content=observation.excerpt,
            excerpt=observation.excerpt,
            freshness_days=freshness_days,
            paid_cost_usd=paid_cost_usd,
        )
        for observation in finding.evidence
    )


def _iso(value: date | None) -> str | None:
    return value.isoformat() if value is not None else None


def ad_finding_value(finding: AdFinding) -> dict[str, Any]:
    return {
        "channel": finding.channel,
        "status": finding.status.value,
        "started_on": _iso(finding.started_on),
        "ended_on": _iso(finding.ended_on),
        "landing_page": finding.landing_page,
        "call_to_action": finding.call_to_action,
    }
