from __future__ import annotations

from collections.abc import Callable, Iterable
import os

from ..providers import (
    AuthenticationFailure, SearchRequest, SearchResult, SourceObservation,
)

PARALLEL_PROVIDER = "parallel"


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
    *, search: Callable[[str], Iterable[SourceObservation]] | None = None,
) -> ParallelSearchAdapter:
    """Build the Parallel fallback from ``PARALLEL_API_KEY``.

    No secret project currently holds a Parallel key, so this raises a typed
    ``AuthenticationFailure`` that a router records as a normalized failure
    instead of crashing the collection stage. The key is read only here.
    """
    api_key = os.environ.get("PARALLEL_API_KEY", "").strip()
    if not api_key:
        raise AuthenticationFailure("PARALLEL_API_KEY is not configured")
    if search is None:
        raise AuthenticationFailure(
            "parallel search transport is not wired; supply search=... explicitly"
        )
    return ParallelSearchAdapter(search)
