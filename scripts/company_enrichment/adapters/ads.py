from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from ..providers import AdFinding, AdsRequest


@dataclass(frozen=True, slots=True)
class MetaValidation:
    sample_size: int
    schema_valid: bool
    cost_valid: bool
    measured_cost_usd: Decimal


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
        measure_cost: Callable[[AdsRequest, AdFinding], str | Decimal],
        max_sample_cost_usd: str | Decimal,
    ) -> None:
        super().__init__("meta", inspect_one)
        self._measure_cost = measure_cost
        self._max_sample_cost = _cost(max_sample_cost_usd)
        self._validation: MetaValidation | None = None

    @property
    def batch_eligible(self) -> bool:
        return bool(
            self._validation
            and self._validation.schema_valid
            and self._validation.cost_valid
        )

    def validate(self, requests: Sequence[AdsRequest]) -> MetaValidation:
        if not 1 <= len(requests) <= 3:
            raise ValueError("Meta validation requires 1 to 3 URLs")
        findings: list[tuple[AdsRequest, AdFinding]] = []
        schema_valid = True
        for request in requests:
            try:
                findings.append((request, self.inspect(request)))
            except (TypeError, ValueError):
                schema_valid = False
                break
        measured_cost = Decimal("0")
        cost_valid = schema_valid
        if schema_valid:
            try:
                measured_cost = sum(
                    (
                        _cost(self._measure_cost(request, finding))
                        for request, finding in findings
                    ),
                    Decimal("0"),
                )
            except ValueError:
                cost_valid = False
            else:
                cost_valid = measured_cost <= self._max_sample_cost
        validation = MetaValidation(
            sample_size=len(requests),
            schema_valid=schema_valid,
            cost_valid=cost_valid,
            measured_cost_usd=measured_cost,
        )
        self._validation = validation
        return validation


def _cost(value: str | Decimal) -> Decimal:
    try:
        cost = Decimal(value)
    except (InvalidOperation, TypeError) as error:
        raise ValueError("cost must be a decimal amount") from error
    if not cost.is_finite() or cost < 0:
        raise ValueError("cost must be finite and non-negative")
    return cost
