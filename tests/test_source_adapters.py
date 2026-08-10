"""Provider-neutral, read-only search and extraction seam contracts."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import pytest

from research_orchestration.budgets import BudgetExceeded
from research_orchestration.contracts import SCHEMA_VERSION, SchemaError
from research_orchestration.providers import (
    AdapterError,
    AdapterFailure,
    DeterministicSourceAdapter,
    ExtractionEvidence,
    ExtractionRequest,
    ExtractionStage,
    SearchHit,
    SearchRequest,
    normalize_adapter_error,
)


def _evidence(excerpt: str) -> ExtractionEvidence:
    return ExtractionEvidence(
        schema_version=SCHEMA_VERSION,
        source_url="https://example.test/report",
        excerpt=excerpt,
        method="deterministic",
    )


def test_search_request_requires_a_nonempty_bounded_query():
    """Would fail if a blank or unbounded search could reach a provider."""
    with pytest.raises(SchemaError, match="query must be a non-empty string"):
        SearchRequest(schema_version=SCHEMA_VERSION, query="   ", max_results=1)
    with pytest.raises(SchemaError, match="text exceeds"):
        SearchRequest(schema_version=SCHEMA_VERSION, query="q" * 4001, max_results=1)
    with pytest.raises(SchemaError, match="between one and"):
        SearchRequest(schema_version=SCHEMA_VERSION, query="company annual report", max_results=101)


def test_requests_results_and_evidence_are_strict_immutable_and_source_attributed():
    """Would fail if provider payloads or mutable source-less values crossed the seam."""
    hit = SearchHit(
        schema_version=SCHEMA_VERSION,
        source_url="https://example.test/report",
        title="Annual report",
        excerpt="Revenue increased.",
    )
    adapter = DeterministicSourceAdapter(search_runner=lambda _request: (hit,))

    result = adapter.search(SearchRequest(schema_version=SCHEMA_VERSION, query="annual report", max_results=1))

    assert result.source_adapter == "deterministic"
    assert result.hits == (hit,)
    assert '"source_url":"https://example.test/report"' in result.to_canonical_json()
    with pytest.raises((AttributeError, TypeError)):
        result.hits = ()  # type: ignore[misc]
    with pytest.raises(TypeError):
        SearchHit(
            schema_version=SCHEMA_VERSION,
            source_url="https://example.test/report",
            title="Annual report",
            excerpt="Revenue increased.",
            raw_provider_payload={"api_token": "do not leak"},
        )


def test_extraction_rejects_empty_or_noncanonical_known_urls():
    """Would fail if extraction accepted an unknown or ambiguous remote target."""
    with pytest.raises(SchemaError):
        ExtractionRequest(schema_version=SCHEMA_VERSION, urls=(), min_evidence_items=1)
    with pytest.raises(SchemaError, match="canonical absolute HTTP"):
        ExtractionRequest(
            schema_version=SCHEMA_VERSION,
            urls=("https://Example.test/report#section",),
            min_evidence_items=1,
        )


def test_extraction_runs_deterministic_stages_in_order_until_explicit_sufficiency():
    """Would fail if a stage were reordered, skipped, or continued after enough evidence."""
    calls = []

    def runner(stage, evidence):
        def run(_request, _prior):
            calls.append(stage.value)
            return evidence

        return run

    adapter = DeterministicSourceAdapter(
        stage_runners={
            ExtractionStage.FETCH_SCRAPE: runner(ExtractionStage.FETCH_SCRAPE, ()),
            ExtractionStage.SELECTOR: runner(ExtractionStage.SELECTOR, ()),
            ExtractionStage.REGEX: runner(ExtractionStage.REGEX, ()),
            ExtractionStage.PATTERN: runner(ExtractionStage.PATTERN, (_evidence("Evidence from pattern."),)),
        }
    )

    result = adapter.extract(
        ExtractionRequest(
            schema_version=SCHEMA_VERSION,
            urls=("https://example.test/report",),
            min_evidence_items=1,
        )
    )

    assert calls == ["fetch/scrape", "selector", "regex", "pattern"]
    assert [record.outcome for record in result.stages] == [
        "insufficient",
        "insufficient",
        "insufficient",
        "sufficient",
    ]
    assert result.sufficient is True
    assert result.evidence == (_evidence("Evidence from pattern."),)


def test_llm_is_attempted_only_when_explicitly_enabled_after_insufficient_deterministic_evidence():
    """Would fail if an LLM ran by default or before deterministic extraction was exhausted."""
    calls = []

    def empty_runner(stage):
        def run(_request, _prior):
            calls.append(stage.value)
            return ()

        return run

    def llm_runner(_request, _prior):
        calls.append("llm")
        return (_evidence("LLM-supported evidence."),)

    runners = {stage: empty_runner(stage) for stage in ExtractionStage.deterministic()}
    adapter = DeterministicSourceAdapter(stage_runners=runners, llm_runner=llm_runner)
    request = ExtractionRequest(
        schema_version=SCHEMA_VERSION,
        urls=("https://example.test/report",),
        min_evidence_items=1,
    )

    disabled = adapter.extract(request)
    assert calls == ["fetch/scrape", "selector", "regex", "pattern"]
    assert disabled.sufficient is False

    calls.clear()
    enabled = adapter.extract(
        ExtractionRequest(
            schema_version=SCHEMA_VERSION,
            urls=("https://example.test/report",),
            min_evidence_items=1,
            allow_llm=True,
        )
    )
    assert calls == ["fetch/scrape", "selector", "regex", "pattern", "llm"]
    assert [record.stage.value for record in enabled.stages] == calls
    assert enabled.sufficient is True


def test_budget_reservation_hook_runs_before_an_enabled_llm_stage():
    """Would fail if paid optional work bypassed the caller-owned reservation seam."""
    reservations = []
    adapter = DeterministicSourceAdapter(
        stage_runners={stage: lambda _request, _prior: () for stage in ExtractionStage.deterministic()},
        llm_runner=lambda _request, _prior: (_evidence("LLM evidence."),),
        reserve_budget=lambda stage: reservations.append(stage.value),
    )

    adapter.extract(
        ExtractionRequest(
            schema_version=SCHEMA_VERSION,
            urls=("https://example.test/report",),
            min_evidence_items=1,
            allow_llm=True,
        )
    )

    assert reservations == ["llm"]


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (TimeoutError("retry"), AdapterFailure.RETRYABLE),
        (RuntimeError("terminal"), AdapterFailure.TERMINAL),
        (BudgetExceeded("budget"), AdapterFailure.BUDGET_EXHAUSTED),
        (SchemaError("bad contract"), AdapterFailure.CONTRACT_INVALID),
    ],
)
def test_provider_failures_are_normalized_to_the_only_declared_categories(error, expected):
    """Would fail if provider exceptions escaped with an unclassified retry policy."""
    normalized = normalize_adapter_error(error)

    assert isinstance(normalized, AdapterError)
    assert normalized.category is expected
    assert normalized.to_canonical_dict()["category"] == expected.value


def test_source_adapter_exposes_read_only_search_and_extraction_operations_only():
    """Would fail if the provider seam gained a method that can mutate remote source state."""
    adapter = DeterministicSourceAdapter()

    assert callable(adapter.search)
    assert callable(adapter.extract)
    assert not any(hasattr(adapter, name) for name in ("create", "update", "delete", "mutate", "write"))
