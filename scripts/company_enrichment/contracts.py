from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from datetime import date, datetime
from enum import Enum
import json
import math
from types import MappingProxyType
from typing import Any, Mapping
from urllib.parse import urlparse

SCHEMA_VERSION = "1.0"
MAX_EXCERPT_CHARS = 2_000
MAX_COLLECTION_ITEMS = 500
SECRET_KEYS = {"api_key", "apikey", "authorization", "credential", "credentials", "password", "secret", "token", "update_key"}


class ResultStatus(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"


class FailureKind(str, Enum):
    RETRYABLE = "retryable"
    TERMINAL = "terminal"
    BUDGET_EXHAUSTED = "budget_exhausted"
    AUTHENTICATION_REQUIRED = "authentication_required"
    CONTRACT_INVALID = "contract_invalid"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class Visibility(str, Enum):
    MESSAGE_SAFE = "message_safe"
    FILTER_ONLY = "filter_only"


class ReviewStatus(str, Enum):
    PROPOSED = "proposed"
    EXPERIMENT = "experiment"
    CANDIDATE = "candidate"
    APPROVED = "approved"
    REJECTED = "rejected"


def _require_text(name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty text")


def _bounded(name: str, values: tuple[Any, ...]) -> None:
    if len(values) > MAX_COLLECTION_ITEMS:
        raise ValueError(f"{name} exceeds {MAX_COLLECTION_ITEMS} items")


def _freeze_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze_value(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_value(item) for item in value)
    return value


@dataclass(frozen=True, slots=True)
class CompanyFixture:
    id: str
    company_name: str
    domain: str
    linkedin_company_url: str | None
    primary_cohort: str
    shared_core: bool
    difficulty: str
    seed_status: str
    seed: Mapping[str, Any]
    gaps: tuple[str, ...]
    b2b_buyer: str | None = None
    business_offer: str | None = None
    selection_reason: str | None = None
    cohort_evidence_url: str | None = None
    secondary_tags: tuple[str, ...] = ()
    expected_ad_channels: tuple[str, ...] = ()
    primary_funding_url: str | None = None
    primary_funding_date: date | None = None
    local_listing_url: str | None = None

    def __post_init__(self) -> None:
        for name in ('id', 'company_name', 'domain', 'primary_cohort',
                     'difficulty', 'seed_status'):
            _require_text(name, getattr(self, name))
        if not isinstance(self.shared_core, bool):
            raise ValueError('shared_core must be boolean')
        if not isinstance(self.seed, Mapping):
            raise ValueError('seed must be a mapping')
        object.__setattr__(self, 'seed', _freeze_value(self.seed))
        for name in ('gaps', 'secondary_tags', 'expected_ad_channels'):
            values = tuple(getattr(self, name))
            if any(not isinstance(value, str) or not value.strip() for value in values):
                raise ValueError(f'{name} must contain non-empty text')
            _bounded(name, values)
            object.__setattr__(self, name, values)


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    evidence_id: str
    url: str
    retrieved_at: datetime
    content_hash: str
    excerpt: str

    def __post_init__(self) -> None:
        _require_text("evidence_id", self.evidence_id)
        parsed = urlparse(self.url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("evidence URL must be an absolute HTTP(S) URL")
        if self.retrieved_at.tzinfo is None:
            raise ValueError("retrieved_at must include a timezone")
        if len(self.content_hash) != 64 or any(char not in "0123456789abcdef" for char in self.content_hash.lower()):
            raise ValueError("content_hash must be a SHA-256 hex digest")
        if len(self.excerpt) > MAX_EXCERPT_CHARS:
            raise ValueError(f"excerpt exceeds {MAX_EXCERPT_CHARS} characters")


@dataclass(frozen=True, slots=True)
class FieldAssertion:
    field: str
    value: Any
    evidence_ids: tuple[str, ...]
    confidence: float
    visibility: Visibility

    def __post_init__(self) -> None:
        _require_text("field", self.field)
        object.__setattr__(self, "evidence_ids", tuple(self.evidence_ids))
        _bounded("evidence_ids", self.evidence_ids)
        if not math.isfinite(self.confidence) or not 0 <= self.confidence <= 1:
            raise ValueError("confidence must be finite and between 0 and 1")


@dataclass(frozen=True, slots=True)
class SellerContext:
    target_market: str
    personas: tuple[str, ...]
    capabilities: tuple[str, ...]
    named_offer: str
    timeline: str
    promised_outcome: str
    proof: tuple[str, ...]
    de_risking: str
    exclusions: tuple[str, ...]
    current_investment_worldview: str

    def __post_init__(self) -> None:
        text_fields = ("target_market", "named_offer", "timeline", "promised_outcome", "de_risking", "current_investment_worldview")
        for name in text_fields:
            _require_text(name, getattr(self, name))
        for name in ("personas", "capabilities", "proof", "exclusions"):
            values = tuple(getattr(self, name))
            if not values or any(not isinstance(value, str) or not value.strip() for value in values):
                raise ValueError(f"{name} must contain non-empty text")
            _bounded(name, values)
            object.__setattr__(self, name, values)


@dataclass(frozen=True, slots=True)
class CompanyDossier:
    company_id: str
    schema_version: str
    assertions: tuple[FieldAssertion, ...]
    evidence: tuple[EvidenceRef, ...]
    unknowns: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_text("company_id", self.company_id)
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(f"unsupported dossier schema version: {self.schema_version}")
        for name in ("assertions", "evidence", "unknowns"):
            values = tuple(getattr(self, name))
            _bounded(name, values)
            object.__setattr__(self, name, values)
        evidence_ids = {item.evidence_id for item in self.evidence}
        missing = sorted({ref for item in self.assertions for ref in item.evidence_ids} - evidence_ids)
        if missing:
            raise ValueError(f"assertions reference missing evidence: {', '.join(missing)}")


@dataclass(frozen=True, slots=True)
class EnrichmentRequest:
    enrichment_id: str
    company_id: str
    input_schema_version: str
    inputs: Mapping[str, Any]

    def __post_init__(self) -> None:
        _require_text("enrichment_id", self.enrichment_id)
        _require_text("company_id", self.company_id)
        if self.input_schema_version != SCHEMA_VERSION:
            raise ValueError(f"unsupported input schema version: {self.input_schema_version}")
        object.__setattr__(self, "inputs", _freeze_value(self.inputs))


@dataclass(frozen=True, slots=True)
class EnrichmentResult:
    enrichment_id: str
    company_id: str
    output_schema_version: str
    status: ResultStatus
    output: Mapping[str, Any]
    failure: FailureKind | None = None

    def __post_init__(self) -> None:
        _require_text("enrichment_id", self.enrichment_id)
        _require_text("company_id", self.company_id)
        if self.output_schema_version != SCHEMA_VERSION:
            raise ValueError(f"unsupported output schema version: {self.output_schema_version}")
        if self.status is ResultStatus.COMPLETE and self.failure is not None:
            raise ValueError("complete result cannot include a failure reason")
        if self.status is ResultStatus.FAILED and self.failure is None:
            raise ValueError("failed result requires a failure reason")
        object.__setattr__(self, "output", _freeze_value(self.output))


def _to_primitive(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _to_primitive(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            normalized = str(key).lower().replace("-", "_")
            if normalized in SECRET_KEYS or normalized.endswith("_token") or normalized.endswith("_secret"):
                raise ValueError(f"secret-bearing key is forbidden: {key}")
            result[str(key)] = _to_primitive(item)
        return result
    if isinstance(value, (tuple, list)):
        return [_to_primitive(item) for item in value]
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(
        _to_primitive(value),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
