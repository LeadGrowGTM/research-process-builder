from __future__ import annotations

from collections.abc import Callable

from ..providers import (
    AuthenticationFailure,
    TechnologyFinding,
    TechnologyRequest,
)


class TechSightAdapter:
    def __init__(
        self,
        *,
        detect_one: Callable[[TechnologyRequest], TechnologyFinding] | None,
        unavailable_reason: str = "TechSight is not locally available",
    ) -> None:
        self._detect_one = detect_one
        self._unavailable_reason = unavailable_reason

    def detect(self, request: TechnologyRequest) -> TechnologyFinding:
        if self._detect_one is None:
            raise AuthenticationFailure(self._unavailable_reason)
        return self._detect_one(request)
