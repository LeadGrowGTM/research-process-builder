from decimal import Decimal
from types import SimpleNamespace

import pytest

from scripts.company_enrichment.contracts import (
    EnrichmentRequest,
    FailureKind,
    ResultStatus,
)
from scripts.company_enrichment.providers import (
    AuthenticationFailure,
    ContractFailure,
    InsufficientEvidence,
    RetryableFailure,
    SourceObservation,
    TerminalFailure,
)
from scripts.company_enrichment.runner import EnrichmentRunner, ExecutionOutcome


def _request() -> EnrichmentRequest:
    return EnrichmentRequest(
        "company-description",
        "acme",
        "1.0",
        {"company_name": "Acme", "domain": "acme.example"},
    )


def _definition(retries=0):
    return SimpleNamespace(
        id="company-description",
        required_inputs=("company_name", "domain"),
        fallback_order=("homepage-scrape", "parallel-search"),
        caps={"retries": retries},
    )


class MemoryCache:
    def __init__(self, events, value=None):
        self.events = events
        self.value = value

    def load(self, key):
        self.events.append("load cache")
        return self.value

    def store(self, key, result):
        self.events.append("store cache")
        self.value = result


class RecordingBudget:
    def __init__(self, events):
        self.events = events

    def reserve(self, scope, key, amount):
        self.events.append("reserve")
        return (scope, key, amount)

    def reconcile(self, reservation, actual):
        self.events.append("reconcile")


def _runner(events, *, cache=None, execute=None, retries=0):
    cache = cache or MemoryCache(events)

    def validate_request(request, definition):
        events.append("validate request")

    discovery = SimpleNamespace(
        discover=lambda enrichment_id, fallback_order: (
            events.append("discover GTM/Nexus"),
            SimpleNamespace(selected_capability="homepage-scrape"),
        )[1]
    )

    def collect(request, discovery_record):
        events.append("collect evidence")
        return (SourceObservation("https://acme.example", "Acme evidence"),)

    def default_execute(request, evidence):
        events.append("execute")
        return ExecutionOutcome(
            output={"description": "B2B workflow software"},
            requested_model_id="gpt-5-nano",
            resolved_model_id="gpt-5-nano-2026-06-01",
            latency_ms=12,
            cost_usd=Decimal("0.02"),
        )

    def validate_output(outcome):
        events.append("validate output")

    def append(result):
        events.append("append result")

    return EnrichmentRunner(
        definitions={"company-description": _definition(retries)},
        discovery=discovery,
        cache=cache,
        budget=RecordingBudget(events),
        collect_evidence=collect,
        execute=execute or default_execute,
        validate_request=validate_request,
        validate_output=validate_output,
        append_result=append,
        budget_scope="experiment:company-description",
        estimated_attempt_cost="0.10",
    )


def test_runner_owns_order_and_records_exact_model_and_cost() -> None:
    events = []
    result = _runner(events).run(_request())

    assert result.status is ResultStatus.COMPLETE
    assert result.output["_run"] == {
        "requested_model_id": "gpt-5-nano",
        "resolved_model_id": "gpt-5-nano-2026-06-01",
        "latency_ms": 12,
        "cost_usd": "0.02",
        "attempts": 1,
    }
    assert events == [
        "validate request",
        "discover GTM/Nexus",
        "load cache",
        "reserve",
        "collect evidence",
        "execute",
        "reconcile",
        "validate output",
        "store cache",
        "append result",
    ]


def test_cache_resume_skips_reservation_collection_and_execution() -> None:
    events = []
    first = _runner(events).run(_request())
    events.clear()
    cache = MemoryCache(events, first)

    resumed = _runner(events, cache=cache).run(_request())

    assert resumed == first
    assert events == ["validate request", "discover GTM/Nexus", "load cache"]


def test_retryable_execution_is_bounded_and_each_attempt_is_reserved() -> None:
    events = []
    attempts = 0

    def execute(request, evidence):
        nonlocal attempts
        attempts += 1
        events.append("execute")
        if attempts < 2:
            raise RetryableFailure("temporary")
        return ExecutionOutcome({"description": "Acme"})

    result = _runner(events, execute=execute, retries=1).run(_request())

    assert result.status is ResultStatus.COMPLETE
    assert result.output["_run"]["attempts"] == 2
    assert events.count("reserve") == 2
    assert events.count("execute") == 2


@pytest.mark.parametrize(
    ("error", "failure"),
    [
        (TerminalFailure("terminal"), FailureKind.TERMINAL),
        (AuthenticationFailure("auth"), FailureKind.AUTHENTICATION_REQUIRED),
        (ContractFailure("contract"), FailureKind.CONTRACT_INVALID),
        (InsufficientEvidence("evidence"), FailureKind.INSUFFICIENT_EVIDENCE),
    ],
)
def test_runner_normalizes_terminal_failures(error, failure) -> None:
    events = []

    def execute(request, evidence):
        raise error

    result = _runner(events, execute=execute).run(_request())

    assert result.status is ResultStatus.FAILED
    assert result.failure is failure
    assert events[-1] == "append result"
