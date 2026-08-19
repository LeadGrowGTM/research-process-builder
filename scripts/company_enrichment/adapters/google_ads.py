"""Google Ads Transparency adapter backed by the LeadGrow free-enrichments service."""
from __future__ import annotations

from collections.abc import Callable
from datetime import date
import json
import os
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request
from urllib.parse import quote, urlparse

from ..contracts import MAX_EXCERPT_CHARS, canonical_json
from ..providers import (
    AdFinding,
    AdStatus,
    AdsRequest,
    AuthenticationFailure,
    RetryableFailure,
    SourceObservation,
)

GOOGLE_ADS_PROVIDER = "google-ads-transparency"
GOOGLE_ADS_FRESHNESS_DAYS = 7
GOOGLE_ADS_PAID_COST_USD = "0"
GOOGLE_ADS_CHANNEL = "google"
DEFAULT_FREE_ENRICHMENTS_URL = "https://lg-linkedin-enrich-l6qeugwwca-uc.a.run.app"
_CLEAN_RESPONSE_CONFIDENCE = 0.9

HttpPost = Callable[[str, dict[str, Any], dict[str, str]], dict[str, Any]]


def domain_from_url(url: str) -> str:
    host = urlparse(url).netloc.lower().split("@")[-1].split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    return host


def iso_date_part(value: Any) -> date | None:
    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


class GoogleAdsTransparencyClient:
    channel = GOOGLE_ADS_CHANNEL

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None,
        http_post: HttpPost,
        timeout_seconds: float = 90,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._http_post = http_post
        self.timeout_seconds = timeout_seconds

    def inspect(self, request: AdsRequest) -> AdFinding:
        domain = domain_from_url(request.url)
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["x-api-key"] = self._api_key
        try:
            response = self._http_post(
                f"{self._base_url}/enrich/ads", {"domain": domain}, headers
            )
        except (AuthenticationFailure, RetryableFailure):
            raise
        except Exception as error:  # transport failures never carry the key
            raise RetryableFailure(
                f"google ads transparency request failed for {domain}: {type(error).__name__}"
            ) from None
        if not isinstance(response, dict):
            raise RetryableFailure(f"google ads transparency returned a non-object for {domain}")
        error_text = response.get("error")
        if error_text:
            if str(error_text).strip().lower() == "unauthorized":
                raise AuthenticationFailure("google ads transparency rejected the API key")
            return AdFinding.unknown(self.channel)
        return self._finding(domain, response)

    def _finding(self, domain: str, response: dict[str, Any]) -> AdFinding:
        running = bool(response.get("running_ads"))
        advertiser = response.get("primary_advertiser")
        advertiser_id = advertiser.get("id") if isinstance(advertiser, dict) else None
        if advertiser_id:
            url = f"https://adstransparency.google.com/advertiser/{quote(str(advertiser_id), safe='')}?region=anywhere"
        else:
            url = f"https://adstransparency.google.com/?domain={quote(domain, safe='')}"
        return AdFinding(
            channel=self.channel,
            status=AdStatus.ACTIVE if running else AdStatus.INACTIVE,
            started_on=iso_date_part(response.get("first_seen")),
            ended_on=iso_date_part(response.get("last_seen")),
            geography=None,
            angle=None,
            offer=None,
            call_to_action=None,
            landing_page=None,
            evidence=(SourceObservation(url, _excerpt(response)),),
            confidence=_CLEAN_RESPONSE_CONFIDENCE,
        )


def _excerpt(response: dict[str, Any]) -> str:
    excerpt = canonical_json(response)
    if len(excerpt) <= MAX_EXCERPT_CHARS:
        return excerpt
    trimmed = {key: value for key, value in response.items() if key != "ad_format_codes"}
    excerpt = canonical_json(trimmed)
    if len(excerpt) <= MAX_EXCERPT_CHARS:
        return excerpt
    return canonical_json(
        {
            key: trimmed.get(key)
            for key in (
                "domain",
                "running_ads",
                "total_creatives",
                "unique_advertisers",
                "primary_advertiser",
                "first_seen",
                "last_seen",
                "truncated",
            )
        }
    )


def urllib_http_post(timeout_seconds: float) -> HttpPost:
    def post(url: str, payload: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        http_request = urllib_request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib_request.urlopen(http_request, timeout=timeout_seconds) as response:
                raw = response.read()
        except urllib_error.HTTPError as error:
            if error.code in {401, 403}:
                raise AuthenticationFailure("google ads transparency rejected the API key") from None
            raise RetryableFailure(f"google ads transparency HTTP {error.code}") from None
        except (urllib_error.URLError, TimeoutError, OSError) as error:
            raise RetryableFailure(
                f"google ads transparency transport failure: {type(error).__name__}"
            ) from None
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise RetryableFailure("google ads transparency returned invalid JSON") from None
        return decoded if isinstance(decoded, dict) else {"error": "non-object response"}

    return post


def build_google_ads_client(*, http_post: HttpPost | None = None) -> GoogleAdsTransparencyClient:
    base_url = os.environ.get("LG_FREE_ENRICHMENTS_URL", "").strip() or DEFAULT_FREE_ENRICHMENTS_URL
    api_key = os.environ.get("LG_FREE_ENRICHMENTS_API_KEY", "").strip() or None
    timeout_seconds = 90
    return GoogleAdsTransparencyClient(
        base_url=base_url,
        api_key=api_key,
        http_post=http_post or urllib_http_post(timeout_seconds),
        timeout_seconds=timeout_seconds,
    )
