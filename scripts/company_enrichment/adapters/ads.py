from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from ..providers import AdFinding, AdsRequest


@dataclass(frozen=True, slots=True)
class MetaValidation:
    sample_size: int
    schema_valid: bool
    cost_valid: bool


class AdsAdapter:
    def __init__(
        self,
        channel: str,
        inspect_one: Callable[[AdsRequest], AdFinding],
    ) -> None:
        self.channel = channel
        self._inspect_one = inspect_one

    def inspect(self, request: AdsRequest) -> AdFinding:
        finding = self._inspect_one(request)
        if finding.channel != self.channel:
            raise ValueError("ads provider returned the wrong channel")
        return finding


class MetaAdsAdapter(AdsAdapter):
    def __init__(
        self,
        *,
        inspect_one: Callable[[AdsRequest], AdFinding],
    ) -> None:
        super().__init__("meta", inspect_one)
        self._validation: MetaValidation | None = None

    @property
    def batch_eligible(self) -> bool:
        return self._validation is not None

    def validate(self, requests: Sequence[AdsRequest]) -> MetaValidation:
        if not 1 <= len(requests) <= 3:
            raise ValueError("Meta validation requires 1 to 3 URLs")
        findings = tuple(self.inspect(request) for request in requests)
        validation = MetaValidation(
            sample_size=len(findings),
            schema_valid=all(isinstance(item, AdFinding) for item in findings),
            cost_valid=True,
        )
        self._validation = validation
        return validation

