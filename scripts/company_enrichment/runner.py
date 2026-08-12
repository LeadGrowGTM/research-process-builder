from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import hashlib
from types import MappingProxyType
from typing import Any, Callable, Mapping, Protocol

from .budgets import BudgetExhausted
from .contracts import (
    EnrichmentRequest,
    EnrichmentResult,
    FailureKind,
    ResultStatus,
    canonical_json,
)
from .providers import (
    BudgetFailure,
    ProviderFailure,
    RetryableFailure,
    normalize_failure,
)


@dataclass(frozen=True, slots=True)
class ExecutionOutcome:
    output: Mapping[str, Any]
    requested_model_id: str | None = None
    resolved_model_id: str | None = None
    latency_ms: int = 0
    cost_usd: Decimal = Decimal("0")
    status: ResultStatus = ResultStatus.COMPLETE

    def __post_init__(self) -> None:
        object.__setattr__(self, "output", MappingProxyType(dict(self.output)))
        try:
            cost = Decimal(self.cost_usd)
        except (InvalidOperation, TypeError) as error:
            raise ValueError("cost_usd must be a decimal amount") from error
        if not cost.is_finite() or cost < 0:
            raise ValueError("cost_usd must be finite and non-negative")
        object.__setattr__(self, "cost_usd", cost)
        if not isinstance(self.latency_ms, int) or self.latency_ms < 0:
            raise ValueError("latency_ms must be a non-negative integer")
        if (self.requested_model_id is None) != (self.resolved_model_id is None):
            raise ValueError("requested and resolved model IDs must be recorded together")
        if self.status is ResultStatus.FAILED:
            raise ValueError("execution failures must use normalized exceptions")


class ResultCache(Protocol):
    def load(self, key: str) -> EnrichmentResult | None: ...

    def store(self, key: str, result: EnrichmentResult) -> None: ...


class EnrichmentRunner:
    def __init__(
        self,
        *,
        definitions: Mapping[str, Any],
        discovery: Any,
        cache: ResultCache,
        budget: Any,
        collect_evidence: Callable[[EnrichmentRequest, Any], tuple[Any, ...]],
        execute: Callable[[EnrichmentRequest, tuple[Any, ...]], ExecutionOutcome],
        validate_request: Callable[[EnrichmentRequest, Any], None],
        validate_output: Callable[[ExecutionOutcome], None],
        append_result: Callable[[EnrichmentResult], None],
        budget_scope: str,
        estimated_attempt_cost: str | Decimal,
    ) -> None:
        self._definitions = definitions
        self._discovery = discovery
        self._cache = cache
        self._budget = budget
        self._collect_evidence = collect_evidence
        self._execute = execute
        self._validate_request_callback = validate_request
        self._validate_output = validate_output
        self._append_result = append_result
        self._budget_scope = budget_scope
        self._estimated_attempt_cost = Decimal(estimated_attempt_cost)

    def run(self, request: EnrichmentRequest) -> EnrichmentResult:
        definition = self._definition(request.enrichment_id)
        self._validate_request(request, definition)
        discovery_record = self._discovery.discover(
            request.enrichment_id, definition.fallback_order
        )
        run_key = self._run_key(request)
        cached = self._cache.load(run_key)
        if cached is not None:
            return cached

        evidence: tuple[Any, ...] | None = None
        max_attempts = int(definition.caps["retries"]) + 1
        for attempt in range(1, max_attempts + 1):
            try:
                reservation = self._budget.reserve(
                    self._budget_scope,
                    f"{run_key}:attempt:{attempt}",
                    self._estimated_attempt_cost,
                )
                if evidence is None:
                    evidence = tuple(self._collect_evidence(request, discovery_record))
                outcome = self._execute(request, evidence)
                self._budget.reconcile(reservation, outcome.cost_usd)
                self._validate_output(outcome)
                result = self._success_result(request, outcome, attempt)
                self._cache.store(run_key, result)
                self._append_result(result)
                return result
            except RetryableFailure as error:
                if attempt < max_attempts:
                    continue
                return self._failed_result(request, error)
            except BudgetExhausted as error:
                return self._failed_result(request, BudgetFailure(str(error)))
            except ProviderFailure as error:
                return self._failed_result(request, error)
        raise RuntimeError("unreachable retry state")

    def _definition(self, enrichment_id: str) -> Any:
        try:
            return self._definitions[enrichment_id]
        except KeyError as error:
            raise ValueError(f"unknown enrichment: {enrichment_id}") from error

    def _validate_request(self, request: EnrichmentRequest, definition: Any) -> None:
        self._validate_request_callback(request, definition)
        missing = [name for name in definition.required_inputs if name not in request.inputs]
        if missing:
            raise ValueError(f"missing required inputs: {', '.join(missing)}")

    def _failed_result(
        self, request: EnrichmentRequest, error: BaseException
    ) -> EnrichmentResult:
        normalized = normalize_failure(error)
        result = EnrichmentResult(
            request.enrichment_id,
            request.company_id,
            "1.0",
            ResultStatus.FAILED,
            {"error": normalized.message},
            normalized.kind,
        )
        self._append_result(result)
        return result

    @staticmethod
    def _success_result(
        request: EnrichmentRequest, outcome: ExecutionOutcome, attempts: int
    ) -> EnrichmentResult:
        output = dict(outcome.output)
        output["_run"] = {
            "requested_model_id": outcome.requested_model_id,
            "resolved_model_id": outcome.resolved_model_id,
            "latency_ms": outcome.latency_ms,
            "cost_usd": str(outcome.cost_usd),
            "attempts": attempts,
        }
        return EnrichmentResult(
            request.enrichment_id,
            request.company_id,
            "1.0",
            outcome.status,
            output,
        )

    @staticmethod
    def _run_key(request: EnrichmentRequest) -> str:
        return hashlib.sha256(canonical_json(request).encode("utf-8")).hexdigest()
