"""Deterministic website page-presence signals (careers, blog).

Checks whether conventional pages render on a company website through a
waterfall of candidate paths. No model is involved: presence is decided by
HTTP status, a soft-404 fingerprint taken from a nonsense path, and
redirect analysis. A careers page that redirects to a known applicant
tracking system counts as present and records the ATS host - the strongest
"actively hiring" evidence this check can produce.

States per signal:
- ``present``  - a candidate path rendered a real page (or an ATS).
- ``absent``   - every candidate path 404ed, soft-404ed, or bounced home.
- ``unknown``  - the site could not be fetched or blocks automation; never
  treat unknown as "not hiring".
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
import secrets
import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping, Sequence
from urllib import error as urlerror
from urllib import request as urlrequest
from urllib.parse import urlsplit

USER_AGENT = "LeadGrowResearch/1.0"
FETCH_TIMEOUT_SECONDS = 12.0
MAX_BODY_BYTES = 200_000

CAREERS_PATHS = (
    "/careers",
    "/jobs",
    "/careers/",
    "/company/careers",
    "/about/careers",
    "/join-us",
    "/hiring",
    "/join-the-team",
    "/work-with-us",
)
BLOG_PATHS = (
    "/blog",
    "/news",
    "/insights",
    "/resources/blog",
)
SIGNAL_PATHS: Mapping[str, tuple[str, ...]] = {
    "careers": CAREERS_PATHS,
    "blog": BLOG_PATHS,
}

# Applicant tracking systems: a careers path landing on one of these hosts is
# a live, first-party hiring surface even though it leaves the company domain.
ATS_HOST_SUFFIXES = (
    "greenhouse.io",
    "lever.co",
    "ashbyhq.com",
    "workable.com",
    "bamboohr.com",
    "breezy.hr",
    "jazz.co",
    "applytojob.com",
    "recruitee.com",
    "smartrecruiters.com",
    "myworkdayjobs.com",
    "jobvite.com",
    "rippling-ats.com",
    "teamtailor.com",
    "pinpointhq.com",
)

# Statuses that mean "a bot was refused", not "the page does not exist".
BLOCKED_STATUSES = frozenset({401, 403, 405, 406, 429, 999})

_TITLE_PATTERN = re.compile(
    r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL,
)


@dataclass(frozen=True, slots=True)
class FetchOutcome:
    requested_url: str
    final_url: str = ""
    status: int = 0
    title: str = ""
    body_bytes: int = 0
    error: str | None = None


@dataclass(frozen=True, slots=True)
class PathCheck:
    path: str
    state: str
    reason: str
    status: int
    final_url: str


@dataclass(frozen=True, slots=True)
class SignalResult:
    signal: str
    state: str
    url: str | None
    ats_host: str | None
    checks: tuple[PathCheck, ...] = field(default=())


Fetcher = Callable[[str], FetchOutcome]


def _extract_title(body: bytes) -> str:
    match = _TITLE_PATTERN.search(body.decode("utf-8", errors="replace"))
    if not match:
        return ""
    return re.sub(r"\s+", " ", match.group(1)).strip()[:300]


def fetch_url(url: str, *, timeout: float = FETCH_TIMEOUT_SECONDS) -> FetchOutcome:
    req = urlrequest.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5",
    })
    try:
        with urlrequest.urlopen(req, timeout=timeout) as response:
            body = response.read(MAX_BODY_BYTES)
            return FetchOutcome(
                requested_url=url,
                final_url=response.geturl(),
                status=int(response.status),
                title=_extract_title(body),
                body_bytes=len(body),
            )
    except urlerror.HTTPError as http_error:
        body = b""
        try:
            body = http_error.read(MAX_BODY_BYTES)
        except OSError:
            pass
        return FetchOutcome(
            requested_url=url,
            final_url=http_error.geturl() or url,
            status=int(http_error.code),
            title=_extract_title(body),
            body_bytes=len(body),
        )
    except (urlerror.URLError, socket.timeout, ConnectionError, OSError) as exc:
        return FetchOutcome(requested_url=url, error=type(exc).__name__)


def _host(url: str) -> str:
    return urlsplit(url).netloc.split("@")[-1].split(":")[0].lower()


def _path(url: str) -> str:
    return urlsplit(url).path or "/"


def _same_site(host: str, domain: str) -> bool:
    domain = domain.lower()
    return host == domain or host.endswith("." + domain)


def _ats_host(host: str) -> str | None:
    for suffix in ATS_HOST_SUFFIXES:
        if host == suffix or host.endswith("." + suffix):
            return suffix
    return None


def classify_path(
    outcome: FetchOutcome, *, domain: str, fingerprint: FetchOutcome,
) -> tuple[str, str, str | None]:
    """Return (state, reason, ats_host) for one candidate path fetch."""
    if outcome.error is not None:
        return "unknown", f"fetch_error:{outcome.error}", None
    if outcome.status in BLOCKED_STATUSES:
        return "unknown", f"blocked_status:{outcome.status}", None
    if outcome.status != 200:
        return "absent", f"status:{outcome.status}", None
    final_host = _host(outcome.final_url)
    ats = _ats_host(final_host)
    if ats is not None:
        return "present", "ats_redirect", ats
    if _same_site(final_host, domain) and _path(outcome.final_url) == "/":
        return "absent", "redirected_to_homepage", None
    if (
        fingerprint.error is None
        and fingerprint.status == 200
        and outcome.title
        and outcome.title == fingerprint.title
    ):
        return "absent", "soft_404_title_match", None
    return "present", "renders", None


def check_signal(
    signal: str,
    *,
    base_url: str,
    domain: str,
    fingerprint: FetchOutcome,
    fetcher: Fetcher,
) -> SignalResult:
    checks: list[PathCheck] = []
    fallback_unknown = False
    for path in SIGNAL_PATHS[signal]:
        outcome = fetcher(base_url + path)
        state, reason, ats = classify_path(
            outcome, domain=domain, fingerprint=fingerprint,
        )
        checks.append(PathCheck(
            path, state, reason, outcome.status, outcome.final_url,
        ))
        if state == "present":
            return SignalResult(
                signal, "present", outcome.final_url, ats, tuple(checks),
            )
        if state == "unknown":
            fallback_unknown = True
    return SignalResult(
        signal, "unknown" if fallback_unknown else "absent",
        None, None, tuple(checks),
    )


def check_domain(
    domain: str,
    *,
    fetcher: Fetcher = fetch_url,
    signals: Sequence[str] = ("careers", "blog"),
) -> dict[str, object]:
    base_url = f"https://{domain}"
    fingerprint = fetcher(f"{base_url}/lg-{secrets.token_hex(6)}")
    if fingerprint.error is not None:
        alternate = f"https://www.{domain}"
        alternate_fingerprint = fetcher(f"{alternate}/lg-{secrets.token_hex(6)}")
        if alternate_fingerprint.error is None:
            base_url = alternate
            fingerprint = alternate_fingerprint
    results = {
        signal: check_signal(
            signal, base_url=base_url, domain=domain,
            fingerprint=fingerprint, fetcher=fetcher,
        )
        for signal in signals
    }
    return {
        "domain": domain,
        "base_url": base_url,
        "fingerprint": {
            "status": fingerprint.status,
            "title": fingerprint.title,
            "error": fingerprint.error,
        },
        "signals": {
            signal: {
                "state": item.state,
                "url": item.url,
                "ats_host": item.ats_host,
                "checks": [
                    {
                        "path": check.path,
                        "state": check.state,
                        "reason": check.reason,
                        "status": check.status,
                        "final_url": check.final_url,
                    }
                    for check in item.checks
                ],
            }
            for signal, item in results.items()
        },
    }


def run_corpus(
    companies: Sequence[tuple[str, str]],
    *,
    output_dir: Path,
    fetcher: Fetcher = fetch_url,
) -> dict[str, object]:
    """Check every (company_id, domain) pair and journal results."""
    output_dir.mkdir(parents=True, exist_ok=True)
    results_path = output_dir / "results.jsonl"
    checked_at = datetime.now(timezone.utc).isoformat()
    tallies = {
        "careers": {"present": 0, "absent": 0, "unknown": 0},
        "blog": {"present": 0, "absent": 0, "unknown": 0},
    }
    with results_path.open("w", encoding="utf-8", newline="\n") as stream:
        for company_id, domain in companies:
            record = check_domain(domain, fetcher=fetcher)
            record["company_id"] = company_id
            record["checked_at"] = checked_at
            for signal, tally in tallies.items():
                tally[record["signals"][signal]["state"]] += 1
            stream.write(json.dumps(record, sort_keys=True) + "\n")
    return {
        "checked_at": checked_at,
        "companies": len(companies),
        "results_path": str(results_path),
        "tallies": tallies,
    }
