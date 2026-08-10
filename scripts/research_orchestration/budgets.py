"""Atomic, fail-closed budget reservation for all external orchestration work."""

from __future__ import annotations

from dataclasses import dataclass
import math
from threading import Lock

from .contracts import CanonicalContract, SCHEMA_VERSION, SchemaError


class BudgetExceeded(RuntimeError):
    """Raised before a charge would exceed one of the declared limits."""


def _require_nonnegative_integer(value: int, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SchemaError(f"{name} must be a non-negative integer")


def _require_nonnegative_cost(value: float, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise SchemaError(f"{name} must be a non-negative finite number")


@dataclass(frozen=True, slots=True)
class BudgetLimits(CanonicalContract):
    schema_version: str = SCHEMA_VERSION
    max_calls: int = 0
    max_queries: int = 0
    max_scrapes: int = 0
    max_llm_calls: int = 0
    max_retries: int = 0
    max_cost: float = 0.0
    max_stages: int = 0

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise SchemaError(f"unsupported schema version: {self.schema_version!r}")
        _require_nonnegative_integer(self.max_calls, "max_calls")
        _require_nonnegative_integer(self.max_queries, "max_queries")
        _require_nonnegative_integer(self.max_scrapes, "max_scrapes")
        _require_nonnegative_integer(self.max_llm_calls, "max_llm_calls")
        _require_nonnegative_integer(self.max_retries, "max_retries")
        _require_nonnegative_cost(self.max_cost, "max_cost")
        _require_nonnegative_integer(self.max_stages, "max_stages")


@dataclass(frozen=True, slots=True)
class BudgetUsage(CanonicalContract):
    schema_version: str = SCHEMA_VERSION
    calls: int = 0
    queries: int = 0
    scrapes: int = 0
    llm_calls: int = 0
    retries: int = 0
    cost: float = 0.0
    stages: int = 0

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise SchemaError(f"unsupported schema version: {self.schema_version!r}")
        _require_nonnegative_integer(self.calls, "calls")
        _require_nonnegative_integer(self.queries, "queries")
        _require_nonnegative_integer(self.scrapes, "scrapes")
        _require_nonnegative_integer(self.llm_calls, "llm_calls")
        _require_nonnegative_integer(self.retries, "retries")
        _require_nonnegative_cost(self.cost, "cost")
        _require_nonnegative_integer(self.stages, "stages")


class BudgetLedger:
    """Reserve complete charges atomically; callers must reserve before external work."""

    def __init__(self, limits: BudgetLimits) -> None:
        if not isinstance(limits, BudgetLimits):
            raise SchemaError("limits must be BudgetLimits")
        self._limits = limits
        self._usage = BudgetUsage()
        self._lock = Lock()

    @property
    def limits(self) -> BudgetLimits:
        return self._limits

    @property
    def usage(self) -> BudgetUsage:
        with self._lock:
            return self._usage

    def reserve(
        self,
        *,
        calls: int = 0,
        queries: int = 0,
        scrapes: int = 0,
        llm_calls: int = 0,
        retries: int = 0,
        cost: float = 0.0,
        stages: int = 0,
    ) -> BudgetUsage:
        """Validate a full charge, then commit it as one state transition."""
        charge = BudgetUsage(
            calls=calls,
            queries=queries,
            scrapes=scrapes,
            llm_calls=llm_calls,
            retries=retries,
            cost=cost,
            stages=stages,
        )
        with self._lock:
            candidate = BudgetUsage(
                calls=self._usage.calls + charge.calls,
                queries=self._usage.queries + charge.queries,
                scrapes=self._usage.scrapes + charge.scrapes,
                llm_calls=self._usage.llm_calls + charge.llm_calls,
                retries=self._usage.retries + charge.retries,
                cost=self._usage.cost + charge.cost,
                stages=self._usage.stages + charge.stages,
            )
            pairs = (
                ("calls", candidate.calls, self._limits.max_calls),
                ("queries", candidate.queries, self._limits.max_queries),
                ("scrapes", candidate.scrapes, self._limits.max_scrapes),
                ("llm_calls", candidate.llm_calls, self._limits.max_llm_calls),
                ("retries", candidate.retries, self._limits.max_retries),
                ("cost", candidate.cost, self._limits.max_cost),
                ("stages", candidate.stages, self._limits.max_stages),
            )
            for name, used, limit in pairs:
                if used > limit:
                    raise BudgetExceeded(f"budget exhausted: {name}")
            self._usage = candidate
            return candidate
