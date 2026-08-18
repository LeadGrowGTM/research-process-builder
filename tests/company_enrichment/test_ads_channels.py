from datetime import date, datetime, timezone

import pytest

from scripts.company_enrichment.adapters.ads_channels import (
    AdChannelOutcome,
    ad_finding_to_source_records,
    ad_finding_value,
    collect_ad_findings,
)
from scripts.company_enrichment.adapters.google_ads import GOOGLE_ADS_PROVIDER
from scripts.company_enrichment.adapters.meta_ads import META_ADS_PROVIDER
from scripts.company_enrichment.contracts import FailureKind
from scripts.company_enrichment.evidence import EvidenceStore
from scripts.company_enrichment.providers import (
    AdFinding,
    AdStatus,
    AdsRequest,
    AuthenticationFailure,
    RetryableFailure,
    SourceObservation,
)

REQUEST = AdsRequest("Acme", "https://acme.example")
RETRIEVED_AT = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)


def _finding(channel: str) -> AdFinding:
    return AdFinding(
        channel=channel,
        status=AdStatus.ACTIVE,
        started_on=date(2026, 1, 2),
        ended_on=None,
        geography=None,
        angle=None,
        offer=None,
        call_to_action="Learn More",
        landing_page="https://acme.example/lp",
        evidence=(SourceObservation(f"https://{channel}.example/evidence", '{"ok":true}'),),
        confidence=0.9,
    )


class StubProvider:
    def __init__(self, result) -> None:
        self.result = result

    def inspect(self, request: AdsRequest) -> AdFinding:
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


def test_collect_converts_failures_to_unknown_with_reason() -> None:
    outcomes = collect_ad_findings(
        REQUEST,
        {
            "google": StubProvider(_finding("google")),
            "meta": StubProvider(RetryableFailure("scraper busy")),
            "linkedin": StubProvider(AuthenticationFailure("no key")),
        },
    )
    assert [type(item) for item in outcomes] == [AdChannelOutcome] * 3
    google, meta, linkedin = outcomes
    assert google.finding.status is AdStatus.ACTIVE and google.failure is None
    assert meta.finding == AdFinding.unknown("meta")
    assert meta.failure.kind is FailureKind.RETRYABLE
    assert meta.failure.message == "scraper busy"
    assert linkedin.finding == AdFinding.unknown("linkedin")
    assert linkedin.failure.kind is FailureKind.AUTHENTICATION_REQUIRED


def test_collect_rejects_wrong_channel_as_unknown() -> None:
    (outcome,) = collect_ad_findings(REQUEST, {"meta": StubProvider(_finding("google"))})
    assert outcome.finding == AdFinding.unknown("meta")
    assert outcome.failure.kind is FailureKind.TERMINAL


def test_non_provider_failures_propagate() -> None:
    with pytest.raises(ValueError):
        collect_ad_findings(REQUEST, {"google": StubProvider(ValueError("bug"))})


@pytest.mark.parametrize(
    "channel, provider",
    [("google", GOOGLE_ADS_PROVIDER), ("meta", META_ADS_PROVIDER)],
)
def test_source_records_use_channel_provider(channel, provider, tmp_path) -> None:
    (record,) = ad_finding_to_source_records(_finding(channel), retrieved_at=RETRIEVED_AT)
    assert record.provider == provider
    assert record.freshness_days == 7
    assert record.source_type == "independent"
    assert record.paid_cost_usd == "0"
    assert record.url == f"https://{channel}.example/evidence"
    assert record.excerpt == '{"ok":true}' == record.content
    assert record.retrieved_at == RETRIEVED_AT
    reference = EvidenceStore(tmp_path).put(record)
    assert reference.url == record.url


def test_source_records_reject_unregistered_channel() -> None:
    with pytest.raises(ValueError):
        ad_finding_to_source_records(_finding("tiktok"), retrieved_at=RETRIEVED_AT)


def test_unknown_finding_yields_no_records() -> None:
    assert ad_finding_to_source_records(AdFinding.unknown("google"), retrieved_at=RETRIEVED_AT) == ()


def test_ad_finding_value_is_deterministic_and_iso() -> None:
    assert ad_finding_value(_finding("google")) == {
        "channel": "google",
        "status": "active",
        "started_on": "2026-01-02",
        "ended_on": None,
        "landing_page": "https://acme.example/lp",
        "call_to_action": "Learn More",
    }
    assert ad_finding_value(AdFinding.unknown("meta")) == {
        "channel": "meta",
        "status": "unknown",
        "started_on": None,
        "ended_on": None,
        "landing_page": None,
        "call_to_action": None,
    }
