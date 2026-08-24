"""Serper (Google SERP API) search adapter for signal collection.

``SerperSearchClient`` implements ``SearchProvider``. A ``SearchRequest`` only
carries a query, so the search mode (``web`` -> ``/search``, ``news`` ->
``/news``) and the optional ``tbs`` freshness window live on the client
instance; ``for_query`` derives a sibling client for another mode/window
without touching the shared request contract.

The API key is read from ``SERPER_API_KEY`` only inside ``build_serper_search``
so callers inject it via ``lg run``; the key is never logged or persisted.
Every call costs one Serper credit, recorded as ``SERPER_COST_PER_QUERY_USD``.
"""

from __future__ import annotations

from collections.abc import Callable
import json
import os
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request

from ..contracts import MAX_EXCERPT_CHARS
from ..providers import (
    AuthenticationFailure,
    ContractFailure,
    RetryableFailure,
    SearchRequest,
    SearchResult,
    SourceObservation,
)

SERPER_PROVIDER = "serper"
SERPER_BASE_URL = "https://google.serper.dev"
SERPER_COST_PER_QUERY_USD = "0.001"
SERPER_MODES = ("web", "news")
_MODE_PATHS = {"web": "/search", "news": "/news"}
_MODE_RESULT_KEYS = {"web": "organic", "news": "news"}
DEFAULT_RESULTS = 10

HttpPost = Callable[[str, dict[str, Any], dict[str, str]], dict[str, Any]]


def _clean(value: Any) -> str:
    return " ".join(str(value).split()) if isinstance(value, str) else ""


def observation_excerpt(item: dict[str, Any]) -> str:
    """Fold title, date, source, and snippet into one labelled excerpt.

    The labels are stable so downstream code (SERP SourceRecords, ground-truth
    drafts) can recover the dated line without re-querying the provider.
    """
    lines = []
    title = _clean(item.get("title"))
    if title:
        lines.append(f"Title: {title}")
    date = _clean(item.get("date"))
    if date:
        lines.append(f"Date: {date}")
    source = _clean(item.get("source"))
    if source:
        lines.append(f"Source: {source}")
    snippet = _clean(item.get("snippet"))
    if snippet:
        lines.append(f"Snippet: {snippet}")
    return "\n".join(lines)[:MAX_EXCERPT_CHARS]


class SerperSearchClient:
    """``SearchProvider`` over Serper ``/search`` (web) or ``/news``."""

    provider = SERPER_PROVIDER
    cost_per_query_usd = SERPER_COST_PER_QUERY_USD

    def __init__(
        self,
        http_post: HttpPost,
        api_key: str,
        *,
        mode: str = "web",
        tbs: str | None = None,
        num: int = DEFAULT_RESULTS,
        base_url: str = SERPER_BASE_URL,
    ) -> None:
        if not isinstance(api_key, str) or not api_key.strip():
            raise AuthenticationFailure("SERPER_API_KEY is not configured")
        if mode not in _MODE_PATHS:
            raise ValueError(f"mode must be one of {SERPER_MODES}")
        if tbs is not None and (not isinstance(tbs, str) or not tbs.strip()):
            raise ValueError("tbs must be non-empty text or None")
        if not isinstance(num, int) or not 1 <= num <= 100:
            raise ValueError("num must be between 1 and 100")
        self._http_post = http_post
        self._api_key = api_key
        self.mode = mode
        self.tbs = tbs
        self.num = num
        self._base_url = base_url.rstrip("/")

    def for_query(self, *, mode: str, tbs: str | None) -> "SerperSearchClient":
        """Return a client for another mode/window sharing transport and key."""
        if mode == self.mode and tbs == self.tbs:
            return self
        return SerperSearchClient(
            self._http_post, self._api_key, mode=mode, tbs=tbs, num=self.num,
            base_url=self._base_url,
        )

    def search(self, request: SearchRequest) -> SearchResult:
        payload: dict[str, Any] = {"q": request.query, "num": self.num}
        if self.tbs:
            payload["tbs"] = self.tbs
        headers = {"X-API-KEY": self._api_key, "Content-Type": "application/json"}
        try:
            response = self._http_post(
                f"{self._base_url}{_MODE_PATHS[self.mode]}", payload, headers,
            )
        except (AuthenticationFailure, RetryableFailure, ContractFailure):
            raise
        except Exception as error:  # transport failures never carry the key
            raise RetryableFailure(
                f"serper {self.mode} request failed: {type(error).__name__}"
            ) from None
        if not isinstance(response, dict):
            raise ContractFailure(f"serper {self.mode} returned a non-object response")
        items = response.get(_MODE_RESULT_KEYS[self.mode])
        if items is None:
            items = []
        if not isinstance(items, list):
            raise ContractFailure(f"serper {self.mode} results must be a list")
        observations: list[SourceObservation] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            url = item.get("link")
            if not isinstance(url, str) or not url.startswith(("http://", "https://")):
                continue
            excerpt = observation_excerpt(item)
            if not excerpt:
                continue
            try:
                observations.append(SourceObservation(url, excerpt))
            except ValueError:
                continue
        return SearchResult(tuple(observations))


def urllib_http_post(timeout_seconds: float = 30) -> HttpPost:
    def post(url: str, payload: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        http_request = urllib_request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib_request.urlopen(http_request, timeout=timeout_seconds) as response:
                raw = response.read()
        except urllib_error.HTTPError as error:
            if error.code in {401, 403}:
                raise AuthenticationFailure("serper rejected the API key") from None
            raise RetryableFailure(f"serper HTTP {error.code}") from None
        except (urllib_error.URLError, TimeoutError, OSError) as error:
            raise RetryableFailure(f"serper transport failure: {type(error).__name__}") from None
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ContractFailure("serper returned invalid JSON") from None
        if not isinstance(decoded, dict):
            raise ContractFailure("serper returned a non-object response")
        return decoded

    return post


def build_serper_search(
    *, mode: str = "web", tbs: str | None = None, http_post: HttpPost | None = None,
) -> SerperSearchClient:
    """Build a Serper client from ``SERPER_API_KEY`` (injected via ``lg run``)."""
    api_key = os.environ.get("SERPER_API_KEY", "").strip()
    if not api_key:
        raise AuthenticationFailure("SERPER_API_KEY is not configured")
    return SerperSearchClient(
        http_post or urllib_http_post(), api_key, mode=mode, tbs=tbs,
    )
