from __future__ import annotations

from collections.abc import Callable, Iterable

from ..budgets import Reservation
from ..providers import (
    ContractFailure,
    InsufficientEvidence,
    KnownUrlRequest,
    ScrapeResult,
    SourceObservation,
    RetryableFailure,
)


class GtmWaterfallAdapter:
    executor_name = "firecrawl_waterfall.py"
    _PAID_LEVEL_COSTS = {3: "0.01", 4: "0.02"}

    def __init__(
        self,
        *,
        execute: Callable[[int, KnownUrlRequest], Iterable[SourceObservation]],
        reserve: Callable[[str, str], Reservation],
    ) -> None:
        self._execute = execute
        self._reserve = reserve

    def scrape(self, request: KnownUrlRequest) -> ScrapeResult:
        for level in range(1, 5):
            if level in self._PAID_LEVEL_COSTS:
                reservation = self._reserve(
                    f"gtm-waterfall:{level}:{request.url}",
                    self._PAID_LEVEL_COSTS[level],
                )
                if not isinstance(reservation, Reservation):
                    raise ContractFailure("paid level requires a typed reservation")
                if not reservation.should_execute:
                    raise RetryableFailure("paid reservation is already owned by another call")
            observations = tuple(self._execute(level, request))
            if observations:
                return ScrapeResult(observations, level)
        raise InsufficientEvidence(f"GTM waterfall returned no evidence for {request.url}")
