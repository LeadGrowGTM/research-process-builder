from __future__ import annotations

from collections.abc import Callable, Iterable

from ..providers import SearchRequest, SearchResult, SourceObservation


class ParallelSearchAdapter:
    """Search-only Parallel bridge; known URL retrieval is intentionally absent."""

    def __init__(
        self, search: Callable[[str], Iterable[SourceObservation]]
    ) -> None:
        self._search = search

    def search(self, request: SearchRequest) -> SearchResult:
        return SearchResult(tuple(self._search(request.query)))
