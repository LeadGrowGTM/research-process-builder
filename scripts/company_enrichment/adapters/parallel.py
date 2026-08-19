"""Parallel Search API adapter for signal collection.

``ParallelSearchClient`` implements ``SearchProvider`` over the Parallel
Search API (``POST https://api.parallel.ai/v1/search``, verified against
https://docs.parallel.ai on 2026-08-19). A ``SearchRequest`` only carries a
query, so the SERP mode (``web``/``news``) and the Serper-style ``tbs``
freshness window live on the client instance; ``for_query`` derives a sibling
client, mirroring the Serper adapter so ``FallbackSearch`` can route both.
Parallel has no news vertical or ``tbs`` parameter, so ``news`` mode steers
via the request ``objective`` and ``tbs`` maps to ``source_policy.after_date``.

Results fold into the same labelled excerpt shape the Serper adapter emits
(``Title:`` / ``Date:`` / ``Source:`` / ``Snippet:``) because ground-truth
drafting and the evaluators' date-citation checks recover dates from those
labels.

The API key is read from ``PARALLEL_API_KEY`` only inside
``build_parallel_search`` so callers inject it via ``lg run``; the key is
never logged or persisted. Search pricing (docs, 2026-08-19): ``turbo`` and
``fast`` cost $0.001 per request, ``basic`` and ``advanced`` $0.005, at the
default 10 results.

``ParallelSearchAdapter`` remains the search-only bridge over an injected
callable for tests and custom transports.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import date, timedelta
import json
import os
import re
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request
from urllib.parse import urlparse

from ..contracts import MAX_EXCERPT_CHARS
from ..providers import (
    AuthenticationFailure,
    ContractFailure,
    RetryableFailure,
    SearchRequest,
    SearchResult,
    SourceObservation,
)

PARALLEL_PROVIDER = "parallel"
PARALLEL_BASE_URL = "https://api.parallel.ai"
PARALLEL_SEARCH_MODES = ("turbo", "fast", "basic", "advanced")
PARALLEL_SEARCH_COST_USD = {
    "turbo": "0.001", "fast": "0.001", "basic": "0.005", "advanced": "0.005",
}
DEFAULT_SEARCH_MODE = "advanced"
DEFAULT_RESULTS = 10
_SERP_MODES = ("web", "news")
_TBS_DAYS = {"qdr:d": 1, "qdr:w": 7, "qdr:m": 30, "qdr:y": 365}

HttpPost = Callable[[str, dict[str, Any], dict[str, str]], dict[str, Any]]


def _clean(value: Any) -> str:
    return " ".join(str(value).split()) if isinstance(value, str) else ""


def _source_domain(url: str) -> str:
    host = urlparse(url).netloc.split("@")[-1].split(":")[0].lower()
    return host[4:] if host.startswith("www.") else host


def parallel_observation_excerpt(item: dict[str, Any]) -> str:
    """Fold title, publish date, source domain, and excerpts into one labelled
    excerpt using the same labels as the Serper adapter."""
    lines = []
    title = _clean(item.get("title"))
    if title:
        lines.append(f"Title: {title}")
    published = _clean(item.get("publish_date"))
    if published:
        lines.append(f"Date: {published}")
    url = item.get("url")
    if isinstance(url, str):
        source = _source_domain(url)
        if source:
            lines.append(f"Source: {source}")
    excerpts = item.get("excerpts")
    if isinstance(excerpts, list):
        snippet = _clean(" ".join(part for part in excerpts if isinstance(part, str)))
        if snippet:
            lines.append(f"Snippet: {snippet}")
    return "\n".join(lines)[:MAX_EXCERPT_CHARS]


def _after_date(tbs: str | None, today: date) -> str | None:
    if tbs is None:
        return None
    days = _TBS_DAYS.get(tbs)
    match = re.fullmatch(r"qdr:(\d+)([dwmy])", tbs) if days is None else None
    if match:
        days = int(match.group(1)) * {"d": 1, "w": 7, "m": 30, "y": 365}[match.group(2)]
    if days is None:
        return None
    return (today - timedelta(days=days)).isoformat()


class ParallelSearchClient:
    """``SearchProvider`` over the Parallel Search API."""

    provider = PARALLEL_PROVIDER

    def __init__(
        self,
        http_post: HttpPost,
        api_key: str,
        *,
        search_mode: str = DEFAULT_SEARCH_MODE,
        serp_mode: str = "web",
        tbs: str | None = None,
        num: int = DEFAULT_RESULTS,
        base_url: str = PARALLEL_BASE_URL,
        today: Callable[[], date] = date.today,
    ) -> None:
        if not isinstance(api_key, str) or not api_key.strip():
            raise AuthenticationFailure("PARALLEL_API_KEY is not configured")
        if search_mode not in PARALLEL_SEARCH_MODES:
            raise ValueError(f"search_mode must be one of {PARALLEL_SEARCH_MODES}")
        if serp_mode not in _SERP_MODES:
            raise ValueError(f"serp_mode must be one of {_SERP_MODES}")
        if tbs is not None and (not isinstance(tbs, str) or not tbs.strip()):
            raise ValueError("tbs must be non-empty text or None")
        if not isinstance(num, int) or not 1 <= num <= 40:
            raise ValueError("num must be between 1 and 40")
        self._http_post = http_post
        self._api_key = api_key
        self.search_mode = search_mode
        self.mode = serp_mode
        self.tbs = tbs
        self.num = num
        self._base_url = base_url.rstrip("/")
        self._today = today
        self.cost_per_query_usd = PARALLEL_SEARCH_COST_USD[search_mode]

    def for_query(self, *, mode: str, tbs: str | None) -> "ParallelSearchClient":
        """Return a client for another SERP mode/window sharing transport and key."""
        if mode == self.mode and tbs == self.tbs:
            return self
        return ParallelSearchClient(
            self._http_post, self._api_key, search_mode=self.search_mode,
            serp_mode=mode, tbs=tbs, num=self.num, base_url=self._base_url,
            today=self._today,
        )

    def search(self, request: SearchRequest) -> SearchResult:
        payload: dict[str, Any] = {
            "search_queries": [request.query],
            "mode": self.search_mode,
            "advanced_settings": {"max_results": self.num},
        }
        if self.mode == "news":
            payload["objective"] = (
                "Find dated news coverage, press releases, and announcements for "
                f"this query: {request.query}"
            )
        after = _after_date(self.tbs, self._today())
        if after:
            payload["advanced_settings"]["source_policy"] = {"after_date": after}
        headers = {"x-api-key": self._api_key, "Content-Type": "application/json"}
        try:
            response = self._http_post(f"{self._base_url}/v1/search", payload, headers)
        except (AuthenticationFailure, RetryableFailure, ContractFailure):
            raise
        except Exception as error:  # transport failures never carry the key
            raise RetryableFailure(
                f"parallel search request failed: {type(error).__name__}"
            ) from None
        if not isinstance(response, dict):
            raise ContractFailure("parallel search returned a non-object response")
        items = response.get("results")
        if items is None:
            items = []
        if not isinstance(items, list):
            raise ContractFailure("parallel search results must be a list")
        observations: list[SourceObservation] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            url = item.get("url")
            if not isinstance(url, str) or not url.startswith(("http://", "https://")):
                continue
            excerpt = parallel_observation_excerpt(item)
            if not excerpt:
                continue
            try:
                observations.append(SourceObservation(url, excerpt))
            except ValueError:
                continue
        return SearchResult(tuple(observations))


def urllib_http_post(timeout_seconds: float = 60) -> HttpPost:
    def post(url: str, payload: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        http_request = urllib_request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib_request.urlopen(http_request, timeout=timeout_seconds) as response:
                raw = response.read()
        except urllib_error.HTTPError as error:
            if error.code in {401, 403}:
                raise AuthenticationFailure("parallel rejected the API key") from None
            raise RetryableFailure(f"parallel HTTP {error.code}") from None
        except (urllib_error.URLError, TimeoutError, OSError) as error:
            raise RetryableFailure(
                f"parallel transport failure: {type(error).__name__}"
            ) from None
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ContractFailure("parallel returned invalid JSON") from None
        if not isinstance(decoded, dict):
            raise ContractFailure("parallel returned a non-object response")
        return decoded

    return post


class ParallelSearchAdapter:
    """Search-only Parallel bridge; known URL retrieval is intentionally absent."""

    provider = PARALLEL_PROVIDER

    def __init__(
        self, search: Callable[[str], Iterable[SourceObservation]]
    ) -> None:
        self._search = search

    def search(self, request: SearchRequest) -> SearchResult:
        return SearchResult(tuple(self._search(request.query)))


def build_parallel_search(
    *,
    search: Callable[[str], Iterable[SourceObservation]] | None = None,
    search_mode: str = DEFAULT_SEARCH_MODE,
    http_post: HttpPost | None = None,
) -> ParallelSearchAdapter | ParallelSearchClient:
    """Build the Parallel search from ``PARALLEL_API_KEY`` (injected via ``lg run``).

    The key lives in the gtm-orchestrator prod and staging secret projects
    (verified 2026-08-19 via ``lg has``); a missing key raises a typed
    ``AuthenticationFailure`` that a router records as a normalized failure
    instead of crashing the collection stage. The key is read only here.
    Passing ``search=`` keeps the legacy injected-callable bridge for tests.
    """
    api_key = os.environ.get("PARALLEL_API_KEY", "").strip()
    if not api_key:
        raise AuthenticationFailure("PARALLEL_API_KEY is not configured")
    if search is not None:
        return ParallelSearchAdapter(search)
    return ParallelSearchClient(
        http_post or urllib_http_post(), api_key, search_mode=search_mode,
    )
