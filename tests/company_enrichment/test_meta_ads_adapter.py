from datetime import date
import json
from pathlib import Path
import sqlite3

import pytest

from scripts.company_enrichment.adapters.meta_ads import (
    META_ADS_PROVIDER,
    MetaAdLibraryClient,
    build_meta_ads_client,
)
from scripts.company_enrichment.contracts import MAX_EXCERPT_CHARS
from scripts.company_enrichment.providers import AdStatus, AdsRequest, RetryableFailure

BASE = "http://localhost:3001"
PAGE_ID = "120680864620158"
REQUEST = AdsRequest("AgencyAnalytics", "https://agencyanalytics.com")
COMPANY_ROW = {
    "company_name": "AgencyAnalytics",
    "website": "https://agencyanalytics.com",
    "status": "done",
    "active_ads_count": 90,
    "inactive_ads_count": 15,
    "ad_types": ["carousel", "image", "video"],
    "platforms": ["FACEBOOK", "INSTAGRAM"],
    "spend_range": None,
    "last_ad_date": "2026-08-10T07:00:00.000Z",
    "matched_name": "AgencyAnalytics",
    "matched_page_id": PAGE_ID,
    "fb_handle": "agencyanalytics",
    "ig_handle": "agencyanalytics",
    "match_method": "handle_fb",
}
STREAM_LINES = [
    "event: message\n",
    'data: {"type":"company_start","company_name":"AgencyAnalytics"}\n',
    "\n",
    'data: {"type":"warning","code":"META_RATE_LIMITED"}\n',
    'data: {"type":"company_done","company_name":"AgencyAnalytics","result":{}}\n',
    'data: {"type":"done","total":1}\n',
    'data: {"type":"never_reached"}\n',
]


class FakeHttp:
    def __init__(self, *, health=None, health_error=None, start=None, results=None) -> None:
        self.health = {"status": "ok"} if health is None else health
        self.health_error = health_error
        self.start = {"id": "job-1", "status": "queued"} if start is None else start
        self.results = (
            {"job": {"status": "complete"}, "companies": [COMPANY_ROW]}
            if results is None
            else results
        )
        self.calls: list[tuple[str, str, dict | None, float]] = []

    def __call__(self, method, url, payload, timeout_seconds):
        self.calls.append((method, url, payload, timeout_seconds))
        if url.endswith("/api/health/meta"):
            if self.health_error is not None:
                raise self.health_error
            return self.health
        if url.endswith("/api/bulk/start"):
            return self.start
        if url.endswith("/results"):
            return self.results
        raise AssertionError(f"unexpected call {method} {url}")


class FakeStream:
    def __init__(self, lines=None) -> None:
        self.lines = STREAM_LINES if lines is None else lines
        self.urls: list[str] = []
        self.consumed = 0

    def __call__(self, url, timeout_seconds):
        self.urls.append(url)
        for line in self.lines:
            self.consumed += 1
            yield line


@pytest.fixture
def ads_db(tmp_path: Path) -> Path:
    path = tmp_path / "ads.db"
    connection = sqlite3.connect(path)
    connection.execute(
        """
        CREATE TABLE ads (
            id TEXT PRIMARY KEY, advertiser_name TEXT, advertiser_page_id TEXT,
            body_variants TEXT, headline TEXT, cta_text TEXT, link_url TEXT,
            media_type TEXT, platforms TEXT, status TEXT, started_at TEXT,
            stopped_at TEXT, days_running INTEGER, ad_snapshot_url TEXT
        )
        """
    )
    rows = [
        (
            "ad-1", "AgencyAnalytics", PAGE_ID, json.dumps(["Report faster. " * 40]),
            "Client reporting", "Learn More", "https://agencyanalytics.com/lp-a", "video",
            json.dumps(["FACEBOOK"]), "ACTIVE", "2026-03-01T00:00:00.000Z", None, 170, None,
        ),
        (
            "ad-2", "AgencyAnalytics", PAGE_ID, json.dumps(["Try it free"]),
            "Free trial", "Sign Up", "https://agencyanalytics.com/lp-b", "image",
            json.dumps(["INSTAGRAM"]), "ACTIVE", "2026-01-15T00:00:00.000Z", None, 215, None,
        ),
        (
            "ad-3", "AgencyAnalytics", PAGE_ID, json.dumps(["Old promo"]),
            "Old", "Sign Up", "not-a-url", "image",
            json.dumps(["FACEBOOK"]), "INACTIVE", "2025-06-01T00:00:00.000Z",
            "2025-09-01T00:00:00.000Z", 92, None,
        ),
        (
            "ad-other", "Other Co", "999", json.dumps(["Other"]),
            "Other", "Shop Now", "https://other.example", "image",
            json.dumps(["FACEBOOK"]), "ACTIVE", "2026-05-01T00:00:00.000Z", None, 999, None,
        ),
    ]
    connection.executemany("INSERT INTO ads VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    connection.commit()
    connection.close()
    return path


def _client(http, stream, db_path, **kwargs):
    return MetaAdLibraryClient(
        base_url=BASE, db_path=db_path, http_json=http, http_stream_lines=stream, **kwargs
    )


def test_happy_path_maps_counts_and_copy(ads_db) -> None:
    http = FakeHttp()
    stream = FakeStream()
    finding = _client(http, stream, ads_db).inspect(REQUEST)

    assert [(m, u.removeprefix(BASE)) for m, u, _, _ in http.calls] == [
        ("GET", "/api/health/meta"),
        ("POST", "/api/bulk/start"),
        ("GET", "/api/bulk/job-1/results"),
    ]
    start_payload = http.calls[1][2]
    assert start_payload["name"] == "rpb: AgencyAnalytics"
    assert start_payload["companies"] == [
        {"company_name": "AgencyAnalytics", "website": "https://agencyanalytics.com"}
    ]
    assert start_payload["filters"]["fetch_details"] is True
    assert stream.urls == [f"{BASE}/api/bulk/job-1/stream"]
    assert stream.consumed == 6  # stopped at the done event

    assert finding.channel == "meta"
    assert finding.status is AdStatus.ACTIVE
    assert finding.started_on == date(2026, 1, 15)
    assert finding.ended_on is None
    assert finding.landing_page == "https://agencyanalytics.com/lp-b"
    assert finding.call_to_action == "Learn More"  # tie broken alphabetically, deterministic
    assert finding.angle is None and finding.offer is None and finding.geography is None
    assert finding.confidence == 0.9
    observation = finding.evidence[0]
    assert observation.url == (
        "https://www.facebook.com/ads/library/?active_status=all&ad_type=all"
        f"&view_all_page_id={PAGE_ID}"
    )
    payload = json.loads(observation.excerpt)
    assert payload["summary"]["matched_page_id"] == PAGE_ID
    assert payload["summary"]["active_ads_count"] == 90
    assert [ad["id"] for ad in payload["ads"]] == ["ad-2", "ad-1", "ad-3"]
    assert payload["ads"][1]["body"].startswith("Report faster.")
    assert len(payload["ads"][1]["body"]) <= 300


def test_no_active_ads_is_inactive_with_ended_on(ads_db) -> None:
    row = {**COMPANY_ROW, "active_ads_count": 0}
    http = FakeHttp(results={"job": {"status": "complete"}, "companies": [row]})
    finding = _client(http, FakeStream(), ads_db).inspect(REQUEST)
    assert finding.status is AdStatus.INACTIVE
    assert finding.ended_on == date(2026, 8, 10)


@pytest.mark.parametrize(
    "http",
    [
        FakeHttp(health={"status": "down"}),
        FakeHttp(health_error=ConnectionRefusedError()),
    ],
)
def test_health_down_returns_unknown_without_starting_job(http, ads_db) -> None:
    stream = FakeStream()
    finding = _client(http, stream, ads_db).inspect(REQUEST)
    assert finding.status is AdStatus.UNKNOWN
    assert finding.channel == "meta"
    assert len(http.calls) == 1
    assert stream.urls == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"matched_page_id": ""},
        {"match_method": ""},
        {"match_method": "needs_review"},
        {"status": "error"},
    ],
)
def test_no_match_returns_unknown(overrides, ads_db) -> None:
    row = {**COMPANY_ROW, **overrides}
    http = FakeHttp(results={"job": {"status": "complete"}, "companies": [row]})
    finding = _client(http, FakeStream(), ads_db).inspect(REQUEST)
    assert finding.status is AdStatus.UNKNOWN
    assert finding.evidence == ()


def test_stream_timeout_raises_retryable(ads_db) -> None:
    ticks = iter([0.0, 1.0, 700.0, 701.0, 702.0])

    def clock() -> float:
        return next(ticks)

    endless = FakeStream(lines=['data: {"type":"company_start"}\n'] * 5)
    with pytest.raises(RetryableFailure):
        _client(FakeHttp(), endless, ads_db, poll_timeout_seconds=600, clock=clock).inspect(REQUEST)


def test_stream_ending_without_done_but_complete_job_still_maps(ads_db) -> None:
    finding = _client(FakeHttp(), FakeStream(lines=[]), ads_db).inspect(REQUEST)
    assert finding.status is AdStatus.ACTIVE


def test_incomplete_job_raises_retryable(ads_db) -> None:
    http = FakeHttp(results={"job": {"status": "running"}, "companies": []})
    with pytest.raises(RetryableFailure):
        _client(http, FakeStream(), ads_db).inspect(REQUEST)


def test_missing_db_returns_finding_without_copy(tmp_path) -> None:
    finding = _client(FakeHttp(), FakeStream(), tmp_path / "missing.db").inspect(REQUEST)
    assert finding.status is AdStatus.ACTIVE
    assert finding.landing_page is None
    assert finding.call_to_action is None
    assert finding.started_on is None
    payload = json.loads(finding.evidence[0].excerpt)
    assert payload["ad_copy"] == "unavailable"
    assert payload["summary"]["matched_name"] == "AgencyAnalytics"


def test_excerpt_truncates_ads_list_to_limit_and_stays_valid_json(ads_db) -> None:
    def many_ads(page_id):
        return [
            {
                "id": f"ad-{i}",
                "status": "ACTIVE",
                "days_running": 10,
                "started_at": "2026-01-01T00:00:00.000Z",
                "headline": "H" * 80,
                "cta_text": "Learn More",
                "link_url": "https://agencyanalytics.com/x",
                "media_type": "image",
                "body_variants": json.dumps(["B" * 500]),
            }
            for i in range(60)
        ]

    finding = _client(FakeHttp(), FakeStream(), ads_db, read_ads=many_ads).inspect(REQUEST)
    excerpt = finding.evidence[0].excerpt
    assert len(excerpt) <= MAX_EXCERPT_CHARS
    payload = json.loads(excerpt)
    assert 0 < len(payload["ads"]) < 60
    assert all(len(ad["body"]) == 300 for ad in payload["ads"])


def test_build_reads_db_env_inside_factory(monkeypatch, ads_db) -> None:
    monkeypatch.setenv("META_ADS_SCRAPER_DB", str(ads_db))
    monkeypatch.setenv("META_ADS_SCRAPER_URL", BASE)
    client = build_meta_ads_client(http_json=FakeHttp(), http_stream_lines=FakeStream())
    finding = client.inspect(REQUEST)
    assert finding.landing_page == "https://agencyanalytics.com/lp-b"
    assert META_ADS_PROVIDER == "meta-ad-library"
