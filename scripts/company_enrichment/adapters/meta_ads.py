"""Meta Ad Library adapter backed by the local meta-ads-scraper service and its SQLite store."""
from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterator, Mapping, Sequence
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import time
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request
from urllib.parse import quote, urlparse

from ..contracts import MAX_EXCERPT_CHARS, canonical_json
from ..providers import (
    AdFinding,
    AdStatus,
    AdsRequest,
    RetryableFailure,
    SourceObservation,
)

META_ADS_PROVIDER = "meta-ad-library"
META_ADS_FRESHNESS_DAYS = 7
META_ADS_PAID_COST_USD = "0"
META_ADS_CHANNEL = "meta"
DEFAULT_META_SCRAPER_URL = "http://localhost:3001"
DEFAULT_META_SCRAPER_DB = Path(
    r"C:\Users\mitch\Everything_CC\tools\meta-ads-scraper\data\ads.db"
)
_HEALTH_TIMEOUT_SECONDS = 3
_REQUEST_TIMEOUT_SECONDS = 30
_BODY_EXCERPT_CHARS = 300
_CLEAN_RESPONSE_CONFIDENCE = 0.9
# A matched page whose library search returned no ads is a verified "inactive",
# but Meta rate limiting can also produce empty results, so confidence is lower.
_NOT_FOUND_CONFIDENCE = 0.7
_MATCHED_STATUSES = frozenset({"done", "not_found"})
_BULK_REQUIRED_SIGNALS = ("typeahead", "search")
_AD_COLUMNS = (
    "id",
    "status",
    "days_running",
    "started_at",
    "stopped_at",
    "headline",
    "cta_text",
    "link_url",
    "media_type",
    "body_variants",
)

HttpJson = Callable[[str, str, dict[str, Any] | None, float], Any]
HttpStreamLines = Callable[[str, float], Iterator[str]]
ReadAds = Callable[[str], Sequence[Mapping[str, Any]] | None]


def _http_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    parsed = urlparse(value)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return value
    return None


def _as_date(value: Any) -> date | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        seconds = value / 1000 if value > 1e11 else value
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc).date()
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str) and len(value) >= 10:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return [value]
    return list(value) if isinstance(value, (list, tuple)) else []


def read_ads_from_sqlite(db_path: Path, max_ads: int) -> ReadAds:
    """Return a reader over the scraper's `ads` table; yields None when the DB is unavailable."""

    def read(page_id: str) -> Sequence[Mapping[str, Any]] | None:
        path = Path(db_path)
        if not path.is_file():
            return None
        uri = f"file:{path.resolve().as_posix()}?mode=ro"
        try:
            connection = sqlite3.connect(uri, uri=True)
        except sqlite3.Error:
            return None
        try:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                "SELECT " + ", ".join(_AD_COLUMNS) + " FROM ads "
                "WHERE advertiser_page_id = ? "
                "ORDER BY (status = 'ACTIVE') DESC, days_running DESC LIMIT ?",
                (str(page_id), int(max_ads)),
            ).fetchall()
        except sqlite3.Error:
            return None
        finally:
            connection.close()
        return tuple({column: row[column] for column in _AD_COLUMNS} for row in rows)

    return read


class MetaAdLibraryClient:
    channel = META_ADS_CHANNEL

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_META_SCRAPER_URL,
        db_path: Path = DEFAULT_META_SCRAPER_DB,
        http_json: HttpJson,
        http_stream_lines: HttpStreamLines,
        read_ads: ReadAds | None = None,
        poll_timeout_seconds: float = 600,
        max_ads: int = 25,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._http_json = http_json
        self._http_stream_lines = http_stream_lines
        self._read_ads = read_ads or read_ads_from_sqlite(Path(db_path), max_ads)
        self.poll_timeout_seconds = poll_timeout_seconds
        self.max_ads = max_ads
        self._clock = clock

    def inspect(self, request: AdsRequest) -> AdFinding:
        self._require_healthy()
        job_id = self._start_job(request)
        self._follow_stream(job_id)
        company = self._company_row(job_id, request)
        if company is None:
            return AdFinding.unknown(self.channel)
        ads = self._read_ads(str(company["matched_page_id"]))
        return self._finding(company, ads)

    def _require_healthy(self) -> None:
        """Raise RetryableFailure when the scraper is unreachable or the signals bulk
        matching depends on (advertiser typeahead, ad search) are down.

        A degraded ``ad_details`` signal alone does not block bulk counting."""
        try:
            health = self._http_json(
                "GET", f"{self._base_url}/api/health/meta", None, _HEALTH_TIMEOUT_SECONDS
            )
        except Exception as error:  # noqa: BLE001 - any transport failure is retryable
            raise RetryableFailure(
                f"meta ad library scraper unreachable at {self._base_url}: "
                f"{type(error).__name__}"
            ) from None
        if not isinstance(health, dict):
            raise RetryableFailure("meta ad library health response was not an object")
        signals = {
            item.get("signal"): item.get("status")
            for item in health.get("signals") or ()
            if isinstance(item, dict)
        }
        down = sorted(
            name for name in _BULK_REQUIRED_SIGNALS if signals.get(name) == "down"
        )
        if down:
            raise RetryableFailure(
                "meta ad library health down for required signals: " + ", ".join(down)
            )
        if not signals and health.get("status") == "down":
            raise RetryableFailure("meta ad library health reports down")

    def _start_job(self, request: AdsRequest) -> str:
        payload = {
            "name": f"rpb: {request.company_name}",
            "companies": [{"company_name": request.company_name, "website": request.url}],
            "filters": {
                "status": "ALL",
                "media_types": [],
                "platforms": [],
                "fetch_details": True,
                "match_pages": True,
                "country": "ALL",
                "workers": 1,
            },
        }
        try:
            started = self._http_json(
                "POST", f"{self._base_url}/api/bulk/start", payload, _REQUEST_TIMEOUT_SECONDS
            )
        except RetryableFailure:
            raise
        except Exception as error:
            raise RetryableFailure(
                f"meta ad library bulk start failed: {type(error).__name__}"
            ) from None
        job_id = started.get("id") if isinstance(started, dict) else None
        if not job_id:
            raise RetryableFailure("meta ad library bulk start returned no job id")
        return str(job_id)

    def _follow_stream(self, job_id: str) -> None:
        deadline = self._clock() + self.poll_timeout_seconds
        url = f"{self._base_url}/api/bulk/{quote(job_id, safe='')}/stream"
        timeout_message = (
            f"meta ad library job {job_id} exceeded {self.poll_timeout_seconds}s"
        )
        try:
            for line in self._http_stream_lines(url, self.poll_timeout_seconds):
                if self._clock() > deadline:
                    raise RetryableFailure(timeout_message)
                event = _sse_event(line)
                if event is not None and event.get("type") == "done":
                    return
        except RetryableFailure:
            raise
        except Exception as error:
            raise RetryableFailure(
                f"meta ad library stream failed: {type(error).__name__}"
            ) from None
        if self._clock() > deadline:
            raise RetryableFailure(timeout_message)

    def _company_row(self, job_id: str, request: AdsRequest) -> Mapping[str, Any] | None:
        url = f"{self._base_url}/api/bulk/{quote(job_id, safe='')}/results"
        try:
            results = self._http_json("GET", url, None, _REQUEST_TIMEOUT_SECONDS)
        except RetryableFailure:
            raise
        except Exception as error:
            raise RetryableFailure(
                f"meta ad library results failed: {type(error).__name__}"
            ) from None
        if not isinstance(results, dict):
            raise RetryableFailure("meta ad library results were not an object")
        job = results.get("job") or {}
        if job.get("status") != "complete":
            raise RetryableFailure(
                f"meta ad library job {job_id} finished with status {job.get('status')!r}"
            )
        companies = [row for row in results.get("companies") or [] if isinstance(row, dict)]
        company = next(
            (row for row in companies if row.get("company_name") == request.company_name),
            companies[0] if companies else None,
        )
        if company is None or company.get("status") not in _MATCHED_STATUSES:
            return None
        if not company.get("matched_page_id"):
            return None
        method = company.get("match_method")
        if not method or method == "needs_review":
            return None
        return company

    def _finding(
        self, company: Mapping[str, Any], ads: Sequence[Mapping[str, Any]] | None
    ) -> AdFinding:
        active_count = int(company.get("active_ads_count") or 0)
        active_ads = [ad for ad in ads or () if ad.get("status") == "ACTIVE"]
        start_dates = [
            found for found in (_as_date(ad.get("started_at")) for ad in active_ads) if found
        ]
        landing_page = None
        if active_ads:
            longest = max(active_ads, key=lambda ad: (ad.get("days_running") or 0))
            landing_page = _http_url(longest.get("link_url"))
        cta_counts = Counter(
            ad["cta_text"].strip()
            for ad in active_ads
            if isinstance(ad.get("cta_text"), str) and ad["cta_text"].strip()
        )
        call_to_action = None
        if cta_counts:
            ranked = sorted(cta_counts.items(), key=lambda item: (-item[1], item[0]))
            call_to_action = ranked[0][0]
        page_id = quote(str(company["matched_page_id"]), safe="")
        url = (
            "https://www.facebook.com/ads/library/"
            f"?active_status=all&ad_type=all&view_all_page_id={page_id}"
        )
        return AdFinding(
            channel=self.channel,
            status=AdStatus.ACTIVE if active_count > 0 else AdStatus.INACTIVE,
            started_on=min(start_dates) if start_dates else None,
            ended_on=None if active_count > 0 else _as_date(company.get("last_ad_date")),
            geography=None,
            angle=None,
            offer=None,
            call_to_action=call_to_action,
            landing_page=landing_page,
            evidence=(SourceObservation(url, _excerpt(company, ads)),),
            confidence=(
                _NOT_FOUND_CONFIDENCE
                if company.get("status") == "not_found"
                else _CLEAN_RESPONSE_CONFIDENCE
            ),
        )


def _sse_event(line: str) -> dict[str, Any] | None:
    stripped = line.strip()
    if not stripped.startswith("data:"):
        return None
    try:
        event = json.loads(stripped[len("data:"):].strip())
    except json.JSONDecodeError:
        return None
    return event if isinstance(event, dict) else None


def _summary(company: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: company.get(key)
        for key in (
            "status",
            "matched_name",
            "matched_page_id",
            "match_method",
            "active_ads_count",
            "inactive_ads_count",
            "ad_types",
            "platforms",
            "last_ad_date",
        )
    }


def _ad_row(ad: Mapping[str, Any]) -> dict[str, Any]:
    variants = _json_list(ad.get("body_variants"))
    body = variants[0] if variants and isinstance(variants[0], str) else None
    return {
        "id": ad.get("id"),
        "status": ad.get("status"),
        "days_running": ad.get("days_running"),
        "started_at": ad.get("started_at"),
        "headline": ad.get("headline"),
        "cta_text": ad.get("cta_text"),
        "link_url": ad.get("link_url"),
        "media_type": ad.get("media_type"),
        "body": body[:_BODY_EXCERPT_CHARS] if body else None,
    }


def _excerpt(company: Mapping[str, Any], ads: Sequence[Mapping[str, Any]] | None) -> str:
    summary = _summary(company)
    if ads is None:
        return canonical_json({"summary": summary, "ad_copy": "unavailable"})
    rows = [_ad_row(ad) for ad in ads]
    while True:
        excerpt = canonical_json({"summary": summary, "ads": rows})
        if len(excerpt) <= MAX_EXCERPT_CHARS or not rows:
            return excerpt
        rows.pop()


def urllib_http_json(
    method: str, url: str, payload: dict[str, Any] | None, timeout_seconds: float
) -> Any:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"} if body is not None else {}
    http_request = urllib_request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib_request.urlopen(http_request, timeout=timeout_seconds) as response:
            raw = response.read()
    except urllib_error.HTTPError as error:
        # The health endpoint answers 503 with a JSON body describing which
        # Meta signals are down; surface that body instead of a bare failure.
        if url.endswith("/api/health/meta") and error.code == 503:
            try:
                return json.loads(error.read().decode("utf-8"))
            except (ValueError, OSError):
                return {"status": "down"}
        raise RetryableFailure(f"meta ad library HTTP {error.code} for {method} {url}") from None
    return json.loads(raw.decode("utf-8"))


def urllib_http_stream_lines(url: str, timeout_seconds: float) -> Iterator[str]:
    http_request = urllib_request.Request(url, headers={"Accept": "text/event-stream"})
    with urllib_request.urlopen(http_request, timeout=timeout_seconds) as response:
        for raw_line in response:
            yield raw_line.decode("utf-8", errors="replace")


def build_meta_ads_client(
    *,
    http_json: HttpJson | None = None,
    http_stream_lines: HttpStreamLines | None = None,
    read_ads: ReadAds | None = None,
) -> MetaAdLibraryClient:
    base_url = os.environ.get("META_ADS_SCRAPER_URL", "").strip() or DEFAULT_META_SCRAPER_URL
    db_value = os.environ.get("META_ADS_SCRAPER_DB", "").strip()
    db_path = Path(db_value) if db_value else DEFAULT_META_SCRAPER_DB
    return MetaAdLibraryClient(
        base_url=base_url,
        db_path=db_path,
        http_json=http_json or urllib_http_json,
        http_stream_lines=http_stream_lines or urllib_http_stream_lines,
        read_ads=read_ads,
    )
