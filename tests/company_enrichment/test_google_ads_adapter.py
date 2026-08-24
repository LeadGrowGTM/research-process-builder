from datetime import date
import json

import pytest

from scripts.company_enrichment.adapters.google_ads import (
    DEFAULT_FREE_ENRICHMENTS_URL,
    GOOGLE_ADS_PROVIDER,
    GoogleAdsTransparencyClient,
    build_google_ads_client,
    domain_from_url,
)
from scripts.company_enrichment.contracts import MAX_EXCERPT_CHARS
from scripts.company_enrichment.providers import (
    AdStatus,
    AdsRequest,
    AuthenticationFailure,
    RetryableFailure,
)

SECRET = "sk-google-secret-value"
REQUEST = AdsRequest("AgencyAnalytics", "https://www.agencyanalytics.com/pricing")
LIVE_RESPONSE = {
    "domain": "agencyanalytics.com",
    "running_ads": True,
    "total_creatives": 197,
    "unique_advertisers": 1,
    "primary_advertiser": {"id": "AR10909288365736067073", "name": "AgencyAnalytics", "creatives": 197},
    "ad_format_codes": {"1": 165, "2": 20, "3": 12},
    "ad_formats": ["image", "text", "video"],
    "first_seen": "2022-08-13T07:00:00.000Z",
    "last_seen": "2026-08-18T13:45:38.000Z",
    "pages_scanned": 5,
    "truncated": True,
    "error": None,
}


class FakePost:
    def __init__(self, response=None, error=None) -> None:
        self.response = response
        self.error = error
        self.calls: list[tuple[str, dict, dict]] = []

    def __call__(self, url, payload, headers):
        self.calls.append((url, payload, headers))
        if self.error is not None:
            raise self.error
        return self.response


def _client(post, api_key=SECRET):
    return GoogleAdsTransparencyClient(
        base_url="https://enrich.example.test/", api_key=api_key, http_post=post
    )


def test_happy_path_maps_live_response() -> None:
    post = FakePost(LIVE_RESPONSE)
    finding = _client(post).inspect(REQUEST)

    assert post.calls == [
        (
            "https://enrich.example.test/enrich/ads",
            {"domain": "agencyanalytics.com"},
            {"Content-Type": "application/json", "x-api-key": SECRET},
        )
    ]
    assert finding.channel == "google"
    assert finding.status is AdStatus.ACTIVE
    assert finding.started_on == date(2022, 8, 13)
    assert finding.ended_on == date(2026, 8, 18)
    assert finding.geography is None
    assert finding.angle is None and finding.offer is None
    assert finding.call_to_action is None and finding.landing_page is None
    assert finding.confidence == 0.9
    assert len(finding.evidence) == 1
    observation = finding.evidence[0]
    assert observation.url == (
        "https://adstransparency.google.com/advertiser/AR10909288365736067073?region=anywhere"
    )
    assert json.loads(observation.excerpt) == LIVE_RESPONSE
    assert SECRET not in observation.excerpt


def test_not_running_without_primary_advertiser_is_inactive_with_domain_url() -> None:
    response = {**LIVE_RESPONSE, "running_ads": False, "primary_advertiser": None, "total_creatives": 0}
    finding = _client(FakePost(response)).inspect(REQUEST)

    assert finding.status is AdStatus.INACTIVE
    assert finding.evidence[0].url == "https://adstransparency.google.com/?domain=agencyanalytics.com"


def test_unauthorized_raises_authentication_failure_without_leaking_key() -> None:
    with pytest.raises(AuthenticationFailure) as raised:
        _client(FakePost({"error": "Unauthorized"})).inspect(REQUEST)
    assert SECRET not in str(raised.value)


def test_missing_key_omits_header() -> None:
    post = FakePost(LIVE_RESPONSE)
    _client(post, api_key=None).inspect(REQUEST)
    assert "x-api-key" not in post.calls[0][2]


def test_other_error_returns_explicit_unknown() -> None:
    finding = _client(FakePost({"error": "scrape failed"})).inspect(REQUEST)
    assert finding.status is AdStatus.UNKNOWN
    assert finding.channel == "google"
    assert finding.confidence == 0
    assert finding.evidence == ()


def test_transport_error_becomes_retryable_without_key() -> None:
    with pytest.raises(RetryableFailure) as raised:
        _client(FakePost(error=ConnectionError(f"boom {SECRET}"))).inspect(REQUEST)
    assert SECRET not in str(raised.value)


def test_oversized_response_excerpt_stays_within_limit_and_valid_json() -> None:
    response = {**LIVE_RESPONSE, "ad_format_codes": {str(i): i for i in range(600)}}
    finding = _client(FakePost(response)).inspect(REQUEST)
    excerpt = finding.evidence[0].excerpt
    assert len(excerpt) <= MAX_EXCERPT_CHARS
    parsed = json.loads(excerpt)
    assert parsed["running_ads"] is True
    assert "ad_format_codes" not in parsed


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://www.Example.com/path", "example.com"),
        ("http://sub.example.com:8080/", "sub.example.com"),
    ],
)
def test_domain_from_url(url, expected) -> None:
    assert domain_from_url(url) == expected


def test_build_reads_env_inside_factory(monkeypatch) -> None:
    monkeypatch.delenv("LG_FREE_ENRICHMENTS_URL", raising=False)
    monkeypatch.setenv("LG_FREE_ENRICHMENTS_API_KEY", SECRET)
    post = FakePost(LIVE_RESPONSE)
    client = build_google_ads_client(http_post=post)
    client.inspect(REQUEST)
    assert post.calls[0][0] == f"{DEFAULT_FREE_ENRICHMENTS_URL}/enrich/ads"
    assert post.calls[0][2]["x-api-key"] == SECRET
    assert GOOGLE_ADS_PROVIDER == "google-ads-transparency"
