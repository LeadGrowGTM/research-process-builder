"""Immutable, evidence-closed output contract for news and product launches.

Mirrors ``prompts/company-enrichment/news-product-launches.md``: two
collections (``news`` and ``launches``) of dated, cited events plus an
optional top-level ``unknowns`` list. ``news_output_contract`` renders the
strict JSON schema the model client sends; ``parse_news_output`` validates a
returned payload strictly (ISO dates, per-collection event types, retained
Evidence IDs) so evaluators score typed values, never raw dicts.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import re
from typing import Any, Mapping

from .contracts import CompanyDossier

NEWS_FIELDS = ("news", "launches")
NEWS_EVENT_TYPES = (
    "funding", "acquisition", "partnership", "leadership", "expansion", "award",
    "positioning", "other",
)
LAUNCH_EVENT_TYPES = ("product", "feature", "integration", "release")
EVENT_TYPES_BY_KIND = {"news": NEWS_EVENT_TYPES, "launches": LAUNCH_EVENT_TYPES}
HEADLINE_MAX_WORDS = 16
WHY_MAX_WORDS = 20
_FULL_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MONTH_DATE = re.compile(r"^\d{4}-\d{2}$")


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-empty text")
    return " ".join(value.split())


def _ids(value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or not value or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise ValueError(f"{field} must contain evidence IDs")
    result = tuple(dict.fromkeys(item.strip() for item in value))
    if len(result) != len(value):
        raise ValueError(f"{field} must contain unique evidence IDs")
    return result


def normalize_event_date(value: Any) -> str:
    """Return ``YYYY-MM-DD`` or ``YYYY-MM`` text, or raise ``ValueError``."""
    if isinstance(value, date):
        return value.isoformat()
    if not isinstance(value, str):
        raise ValueError("date must be YYYY-MM-DD or YYYY-MM text")
    text = value.strip()
    if _FULL_DATE.fullmatch(text):
        try:
            date.fromisoformat(text)
        except ValueError as error:
            raise ValueError(f"date is not a real calendar date: {text}") from error
        return text
    if _MONTH_DATE.fullmatch(text):
        month = int(text[5:7])
        if not 1 <= month <= 12:
            raise ValueError(f"date month is out of range: {text}")
        return text
    raise ValueError("date must be YYYY-MM-DD or YYYY-MM")


@dataclass(frozen=True, slots=True)
class NewsEvent:
    date: str
    headline: str
    event_type: str
    why_it_matters: str
    source_url: str
    evidence_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "date", normalize_event_date(self.date))
        # HEADLINE_MAX_WORDS / WHY_MAX_WORDS are the prompt's target lengths;
        # over-long prose is a style flaw for human review, not a contract failure.
        object.__setattr__(self, "headline", _text(self.headline, "headline"))
        object.__setattr__(self, "event_type", _text(self.event_type, "event_type").lower())
        object.__setattr__(self, "why_it_matters", _text(self.why_it_matters, "why_it_matters"))
        source_url = _text(self.source_url, "source_url")
        if not source_url.startswith(("http://", "https://")):
            raise ValueError("source_url must be an absolute HTTP(S) URL")
        object.__setattr__(self, "source_url", source_url)
        object.__setattr__(self, "evidence_ids", _ids(self.evidence_ids, "evidence_ids"))

    @property
    def month(self) -> str:
        return self.date[:7]

    @property
    def is_full_date(self) -> bool:
        return len(self.date) == 10


@dataclass(frozen=True, slots=True)
class NewsOutput:
    news: tuple[NewsEvent, ...]
    launches: tuple[NewsEvent, ...]
    unknowns: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for kind in NEWS_FIELDS:
            events = tuple(getattr(self, kind))
            allowed = EVENT_TYPES_BY_KIND[kind]
            for event in events:
                if not isinstance(event, NewsEvent):
                    raise ValueError(f"{kind} must contain NewsEvent values")
                if event.event_type not in allowed:
                    raise ValueError(
                        f"{kind} event_type must be one of {allowed}: {event.event_type}"
                    )
            object.__setattr__(self, kind, events)
        unknowns = tuple(dict.fromkeys(self.unknowns))
        if any(item not in NEWS_FIELDS for item in unknowns):
            raise ValueError("unknowns may only name news or launches")
        for kind in unknowns:
            if getattr(self, kind):
                raise ValueError(f"{kind} is declared unknown but contains events")
        object.__setattr__(self, "unknowns", unknowns)

    @property
    def events(self) -> tuple[tuple[str, NewsEvent], ...]:
        """Every event tagged with its collection name."""
        return tuple((kind, event) for kind in NEWS_FIELDS for event in getattr(self, kind))


def parse_news_event(value: Any, kind: str) -> NewsEvent:
    """One event object from a ``news`` or ``launches`` array; raises ValueError
    for a malformed entry so callers can drop it individually."""
    if not isinstance(value, Mapping):
        raise ValueError(f"{kind} entries must be objects")
    extra = set(value) - {"date", "headline", "event_type", "why_it_matters", "source_url",
                          "evidence_ids"}
    if extra:
        raise ValueError(f"{kind} entry has unexpected keys: {sorted(extra)}")
    return NewsEvent(
        value.get("date"), value.get("headline"), value.get("event_type"),
        value.get("why_it_matters"), value.get("source_url"),
        _ids(value.get("evidence_ids"), "evidence_ids"),
    )


def parse_news_output(
    value: Mapping[str, Any], retained_evidence_ids: set[str] | frozenset[str],
) -> NewsOutput:
    if not isinstance(value, Mapping):
        raise ValueError("news output must be an object")
    if set(value) - {"unknowns"} != set(NEWS_FIELDS):
        raise ValueError("news output must contain exactly news and launches")
    collections = {}
    for kind in NEWS_FIELDS:
        items = value.get(kind)
        if not isinstance(items, (list, tuple)):
            raise ValueError(f"{kind} must be an array")
        collections[kind] = tuple(parse_news_event(item, kind) for item in items)
    unknowns = value.get("unknowns", [])
    if not isinstance(unknowns, (list, tuple)) or any(
        not isinstance(item, str) for item in unknowns
    ):
        raise ValueError("unknowns must be an array of field names")
    output = NewsOutput(collections["news"], collections["launches"], tuple(unknowns))
    retained = set(retained_evidence_ids)
    for _, event in output.events:
        if not set(event.evidence_ids) <= retained:
            raise ValueError("all events must reference retained Evidence IDs")
    return output


def _event_schema(evidence_ids: list[str], event_types: tuple[str, ...]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "date": {"type": "string", "description": "YYYY-MM-DD or YYYY-MM as stated"},
            "headline": {"type": "string"},
            "event_type": {"type": "string", "enum": list(event_types)},
            "why_it_matters": {"type": "string"},
            "source_url": {"type": "string"},
            "evidence_ids": {
                "type": "array", "items": {"type": "string", "enum": evidence_ids},
                "minItems": 1,
            },
        },
        "required": ["date", "headline", "event_type", "why_it_matters", "source_url",
                     "evidence_ids"],
        "additionalProperties": False,
    }


def news_output_contract(dossier: CompanyDossier) -> dict[str, Any]:
    """Strict JSON schema: ``news`` and ``launches`` arrays plus ``unknowns``.

    ``unknowns`` is required by the strict schema (an empty array when every
    collection is supported) and optional for ``parse_news_output``.
    """
    ids = [item.evidence_id for item in dossier.evidence]
    return {
        "type": "object",
        "properties": {
            "news": {"type": "array", "items": _event_schema(ids, NEWS_EVENT_TYPES)},
            "launches": {"type": "array", "items": _event_schema(ids, LAUNCH_EVENT_TYPES)},
            "unknowns": {"type": "array", "items": {"type": "string", "enum": list(NEWS_FIELDS)}},
        },
        "required": ["news", "launches", "unknowns"],
        "additionalProperties": False,
    }
