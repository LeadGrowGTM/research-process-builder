"""Provider-neutral, read-only seams for bounded source research.

The production adapter is deliberately a deterministic pipeline shell: callers
provide local/test doubles for search and extraction stages.  It performs no
network or paid-provider work itself.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Protocol, runtime_checkable
from urllib.parse import urlsplit, urlunsplit

from .budgets import BudgetExceeded
from .contracts import CanonicalContract, MAX_LIST_ITEMS, MAX_TEXT_CHARS, SCHEMA_VERSION, SchemaError


class AdapterFailure(StrEnum):
    """The complete, stable set of source-provider failure categories."""

    RETRYABLE = "retryable"
    TERMINAL = "terminal"
    BUDGET_EXHAUSTED = "budget_exhausted"
    CONTRACT_INVALID = "contract_invalid"


_PUBLIC_ERROR_MESSAGES: Mapping[AdapterFailure, str] = MappingProxyType(
    {
        AdapterFailure.RETRYABLE: "source operation may be retried",
        AdapterFailure.TERMINAL: "source operation cannot continue",
        AdapterFailure.BUDGET_EXHAUSTED: "source budget is exhausted",
        AdapterFailure.CONTRACT_INVALID: "source contract is invalid",
    }
)


def _require_schema_version(schema_version: str) -> None:
    if schema_version != SCHEMA_VERSION:
        raise SchemaError(f"unsupported schema version: {schema_version!r}")


def _require_text(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SchemaError(f"{field_name} must be a non-empty string")
    if len(value) > MAX_TEXT_CHARS:
        raise SchemaError(f"text exceeds {MAX_TEXT_CHARS} characters: {field_name}")
    return value.strip()


def _require_count(value: int, field_name: str, *, minimum: int = 0, maximum: int = MAX_LIST_ITEMS) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        if minimum == 1:
            raise SchemaError(f"{field_name} must be between one and {maximum}")
        raise SchemaError(f"{field_name} must be between {minimum} and {maximum}")


def _canonical_http_url(value: str) -> str:
    """Reject ambiguous URLs rather than silently changing a remote target."""
    if not isinstance(value, str) or not value or len(value) > MAX_TEXT_CHARS:
        raise SchemaError("urls must contain canonical absolute HTTP URLs")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise SchemaError("urls must contain canonical absolute HTTP URLs") from error
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or (parsed.scheme == "http" and port == 80)
        or (parsed.scheme == "https" and port == 443)
    ):
        raise SchemaError("urls must contain canonical absolute HTTP URLs")
    canonical = urlunsplit((parsed.scheme, parsed.netloc.lower(), parsed.path or "/", parsed.query, ""))
    if value != canonical:
        raise SchemaError("urls must contain canonical absolute HTTP URLs")
    return canonical


@dataclass(frozen=True, slots=True)
class AdapterError(CanonicalContract, RuntimeError):
    """Serializable normalized error safe to retain in orchestration artifacts."""

    category: AdapterFailure
    message: str
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_schema_version(self.schema_version)
        if not isinstance(self.category, AdapterFailure):
            raise SchemaError("category must be a declared adapter failure")
        _require_text(self.message, "message")

    def __str__(self) -> str:
        return f"{self.category.value}: {self.message}"


def normalize_adapter_error(error: Exception) -> AdapterError:
    """Rebuild a fixed public error without retaining provider text, payload, or cause."""
    if isinstance(error, AdapterError):
        category = error.category
    elif isinstance(error, BudgetExceeded):
        category = AdapterFailure.BUDGET_EXHAUSTED
    elif isinstance(error, (SchemaError, TypeError, ValueError)):
        category = AdapterFailure.CONTRACT_INVALID
    elif isinstance(error, (TimeoutError, ConnectionError)):
        category = AdapterFailure.RETRYABLE
    else:
        category = AdapterFailure.TERMINAL
    return AdapterError(category=category, message=_PUBLIC_ERROR_MESSAGES[category])


@dataclass(frozen=True, slots=True)
class SearchRequest(CanonicalContract):
    schema_version: str
    query: str
    max_results: int

    def __post_init__(self) -> None:
        _require_schema_version(self.schema_version)
        object.__setattr__(self, "query", _require_text(self.query, "query"))
        _require_count(self.max_results, "max_results", minimum=1)


@dataclass(frozen=True, slots=True)
class SearchHit(CanonicalContract):
    schema_version: str
    source_url: str
    title: str
    excerpt: str

    def __post_init__(self) -> None:
        _require_schema_version(self.schema_version)
        object.__setattr__(self, "source_url", _canonical_http_url(self.source_url))
        object.__setattr__(self, "title", _require_text(self.title, "title"))
        object.__setattr__(self, "excerpt", _require_text(self.excerpt, "excerpt"))


@dataclass(frozen=True, slots=True)
class SearchResult(CanonicalContract):
    schema_version: str
    query: str
    hits: tuple[SearchHit, ...]
    source_adapter: str

    def __post_init__(self) -> None:
        _require_schema_version(self.schema_version)
        object.__setattr__(self, "query", _require_text(self.query, "query"))
        object.__setattr__(self, "source_adapter", _require_text(self.source_adapter, "source_adapter"))
        if isinstance(self.hits, (str, bytes)) or not isinstance(self.hits, Sequence):
            raise SchemaError("hits must be a non-string sequence")
        hits = tuple(self.hits)
        _require_count(len(hits), "hits")
        if not all(isinstance(hit, SearchHit) for hit in hits):
            raise SchemaError("hits must contain SearchHit values")
        object.__setattr__(self, "hits", hits)


@dataclass(frozen=True, slots=True)
class ExtractionRequest(CanonicalContract):
    schema_version: str
    urls: tuple[str, ...]
    min_evidence_items: int
    allow_llm: bool = False

    def __post_init__(self) -> None:
        _require_schema_version(self.schema_version)
        if isinstance(self.urls, (str, bytes)) or not isinstance(self.urls, Sequence):
            raise SchemaError("urls must be a non-string sequence")
        urls = tuple(_canonical_http_url(url) for url in self.urls)
        _require_count(len(urls), "urls", minimum=1)
        if len(set(urls)) != len(urls):
            raise SchemaError("urls must be unique canonical HTTP URLs")
        _require_count(self.min_evidence_items, "min_evidence_items", minimum=1)
        if not isinstance(self.allow_llm, bool):
            raise SchemaError("allow_llm must be a boolean")
        object.__setattr__(self, "urls", urls)


class ExtractionStage(StrEnum):
    FETCH_SCRAPE = "fetch/scrape"
    SELECTOR = "selector"
    REGEX = "regex"
    PATTERN = "pattern"
    LLM = "llm"

    @classmethod
    def deterministic(cls) -> tuple["ExtractionStage", ...]:
        return (cls.FETCH_SCRAPE, cls.SELECTOR, cls.REGEX, cls.PATTERN)


@dataclass(frozen=True, slots=True)
class ExtractionEvidence(CanonicalContract):
    schema_version: str
    source_url: str
    excerpt: str
    method: str

    def __post_init__(self) -> None:
        _require_schema_version(self.schema_version)
        object.__setattr__(self, "source_url", _canonical_http_url(self.source_url))
        object.__setattr__(self, "excerpt", _require_text(self.excerpt, "excerpt"))
        object.__setattr__(self, "method", _require_text(self.method, "method"))


@dataclass(frozen=True, slots=True)
class ExtractionStageRecord(CanonicalContract):
    schema_version: str
    stage: ExtractionStage
    outcome: str
    evidence_count: int

    def __post_init__(self) -> None:
        _require_schema_version(self.schema_version)
        if not isinstance(self.stage, ExtractionStage):
            raise SchemaError("stage must be a declared extraction stage")
        if self.outcome not in {"sufficient", "insufficient", *(failure.value for failure in AdapterFailure)}:
            raise SchemaError("outcome must be a declared stage outcome")
        _require_count(self.evidence_count, "evidence_count")


@dataclass(frozen=True, slots=True)
class ExtractionResult(CanonicalContract):
    schema_version: str
    evidence: tuple[ExtractionEvidence, ...]
    stages: tuple[ExtractionStageRecord, ...]
    sufficient: bool
    source_adapter: str

    def __post_init__(self) -> None:
        _require_schema_version(self.schema_version)
        if isinstance(self.evidence, (str, bytes)) or not isinstance(self.evidence, Sequence):
            raise SchemaError("evidence must be a non-string sequence")
        if isinstance(self.stages, (str, bytes)) or not isinstance(self.stages, Sequence):
            raise SchemaError("stages must be a non-string sequence")
        evidence = tuple(self.evidence)
        stages = tuple(self.stages)
        _require_count(len(evidence), "evidence")
        _require_count(len(stages), "stages")
        if not all(isinstance(item, ExtractionEvidence) for item in evidence):
            raise SchemaError("evidence must contain ExtractionEvidence values")
        if not all(isinstance(item, ExtractionStageRecord) for item in stages):
            raise SchemaError("stages must contain ExtractionStageRecord values")
        if not isinstance(self.sufficient, bool):
            raise SchemaError("sufficient must be a boolean")
        object.__setattr__(self, "evidence", evidence)
        object.__setattr__(self, "stages", stages)
        object.__setattr__(self, "source_adapter", _require_text(self.source_adapter, "source_adapter"))


SearchRunner = Callable[[SearchRequest], Sequence[SearchHit]]
StageRunner = Callable[[ExtractionRequest, tuple[ExtractionEvidence, ...]], Sequence[ExtractionEvidence]]
BudgetReservation = Callable[[ExtractionStage], None]


@runtime_checkable
class SourceAdapter(Protocol):
    """The full read-only provider surface; mutations do not cross this seam."""

    def search(self, request: SearchRequest) -> SearchResult: ...

    def extract(self, request: ExtractionRequest) -> ExtractionResult: ...


class DeterministicSourceAdapter:
    """A no-network adapter shell for local doubles and deterministic extraction logic."""

    def __init__(
        self,
        *,
        search_runner: SearchRunner | None = None,
        stage_runners: Mapping[ExtractionStage, StageRunner] | None = None,
        llm_runner: StageRunner | None = None,
        reserve_budget: BudgetReservation | None = None,
        source_adapter: str = "deterministic",
    ) -> None:
        self._search_runner = search_runner
        supplied_runners = {} if stage_runners is None else dict(stage_runners)
        if any(not isinstance(stage, ExtractionStage) for stage in supplied_runners):
            raise SchemaError("stage_runners keys must be declared extraction stages")
        if ExtractionStage.LLM in supplied_runners:
            raise SchemaError("llm_runner must be supplied separately")
        if any(not callable(runner) for runner in supplied_runners.values()):
            raise SchemaError("stage_runners values must be callable")
        if llm_runner is not None and not callable(llm_runner):
            raise SchemaError("llm_runner must be callable")
        if reserve_budget is not None and not callable(reserve_budget):
            raise SchemaError("reserve_budget must be callable")
        self._stage_runners = MappingProxyType(supplied_runners)
        self._llm_runner = llm_runner
        self._reserve_budget = reserve_budget
        self._source_adapter = _require_text(source_adapter, "source_adapter")

    def search(self, request: SearchRequest) -> SearchResult:
        if not isinstance(request, SearchRequest):
            raise normalize_adapter_error(SchemaError("request must be SearchRequest"))
        try:
            hits = () if self._search_runner is None else tuple(self._search_runner(request))
            if len(hits) > request.max_results:
                raise SchemaError("search result exceeds request max_results")
            if not all(isinstance(hit, SearchHit) for hit in hits):
                raise SchemaError("search runner must return SearchHit values")
            return SearchResult(
                schema_version=SCHEMA_VERSION,
                query=request.query,
                hits=hits,
                source_adapter=self._source_adapter,
            )
        except Exception as error:
            raise normalize_adapter_error(error) from None

    def extract(self, request: ExtractionRequest) -> ExtractionResult:
        if not isinstance(request, ExtractionRequest):
            raise normalize_adapter_error(SchemaError("request must be ExtractionRequest"))
        evidence: tuple[ExtractionEvidence, ...] = ()
        records: list[ExtractionStageRecord] = []
        for stage in ExtractionStage.deterministic():
            evidence, record = self._run_stage(stage, request, evidence, self._stage_runners.get(stage))
            records.append(record)
            if len(evidence) >= request.min_evidence_items:
                return self._result(evidence, records, sufficient=True)
            if record.outcome in {failure.value for failure in AdapterFailure}:
                return self._result(evidence, records, sufficient=False)

        if request.allow_llm:
            evidence, record = self._run_stage(ExtractionStage.LLM, request, evidence, self._llm_runner)
            records.append(record)
        return self._result(evidence, records, sufficient=len(evidence) >= request.min_evidence_items)

    def _run_stage(
        self,
        stage: ExtractionStage,
        request: ExtractionRequest,
        prior: tuple[ExtractionEvidence, ...],
        runner: StageRunner | None,
    ) -> tuple[tuple[ExtractionEvidence, ...], ExtractionStageRecord]:
        try:
            if stage is ExtractionStage.LLM:
                if runner is None or self._reserve_budget is None:
                    raise SchemaError("optional LLM requires a runner and budget reservation")
                reservation = self._reserve_budget(stage)
                if reservation is not None and not reservation:
                    raise SchemaError("LLM budget reservation was denied")
            additions = () if runner is None else tuple(runner(request, prior))
            if not all(isinstance(item, ExtractionEvidence) for item in additions):
                raise SchemaError("stage runner must return ExtractionEvidence values")
            if any(item.source_url not in request.urls for item in additions):
                raise SchemaError("evidence source_url must be requested")
            combined = self._unique_evidence((*prior, *additions))
            if len(combined) > MAX_LIST_ITEMS:
                raise SchemaError("evidence exceeds maximum items")
            outcome = "sufficient" if len(combined) >= request.min_evidence_items else "insufficient"
            return combined, ExtractionStageRecord(SCHEMA_VERSION, stage, outcome, len(combined))
        except Exception as error:
            normalized = normalize_adapter_error(error)
            return prior, ExtractionStageRecord(SCHEMA_VERSION, stage, normalized.category.value, len(prior))

    @staticmethod
    def _unique_evidence(values: Sequence[ExtractionEvidence]) -> tuple[ExtractionEvidence, ...]:
        unique: dict[tuple[str, str, str], ExtractionEvidence] = {}
        for item in values:
            unique.setdefault((item.source_url, item.excerpt, item.method), item)
        return tuple(unique.values())

    def _result(
        self,
        evidence: tuple[ExtractionEvidence, ...],
        records: Sequence[ExtractionStageRecord],
        *,
        sufficient: bool,
    ) -> ExtractionResult:
        return ExtractionResult(
            schema_version=SCHEMA_VERSION,
            evidence=evidence,
            stages=tuple(records),
            sufficient=sufficient,
            source_adapter=self._source_adapter,
        )


__all__ = [
    "AdapterError",
    "AdapterFailure",
    "BudgetReservation",
    "DeterministicSourceAdapter",
    "ExtractionEvidence",
    "ExtractionRequest",
    "ExtractionResult",
    "ExtractionStage",
    "ExtractionStageRecord",
    "SearchHit",
    "SearchRequest",
    "SearchResult",
    "SourceAdapter",
    "normalize_adapter_error",
]
