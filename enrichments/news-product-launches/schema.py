"""Sidecar schemas for the ``news-product-launches`` enrichment package.

Shipped inside the package so the package is portable: the GTM orchestrator's
prompt loader binds ``InputModel``/``OutputModel`` from here via the
``schema_module`` frontmatter key rather than from a module hardcoded in
``lg_runtime.prompts.schemas``.

``scripts.company_enrichment.news_contracts`` stays the repo-side authority on
the returned shape, and it is what the scorer enforces. This file may not import
it: the install copies this module into a consumer where
``scripts.company_enrichment`` does not exist, so the event-type literals are
restated here and a test asserts the two stay equal.

Evidence closure is context-dependent. Consumers get retained IDs from the
Evidence records supplied to the model and validate with
``OutputModel.model_validate(payload, context={"retained_evidence_ids": ids})``.
Without that context, ``OutputModel`` validates shape only and does not provide
the Evidence-closure guarantee.
"""

from __future__ import annotations

from datetime import date as calendar_date
import re
from typing import Literal
from urllib.parse import urlsplit

from pydantic import (
    BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator,
)

NEWS_EVENT_TYPES = (
    "funding", "acquisition", "partnership", "leadership", "expansion", "award",
    "positioning", "other",
)
LAUNCH_EVENT_TYPES = ("product", "feature", "integration", "release")

NewsEventType = Literal[NEWS_EVENT_TYPES]  # type: ignore[valid-type]
LaunchEventType = Literal[LAUNCH_EVENT_TYPES]  # type: ignore[valid-type]
_FULL_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MONTH_DATE = re.compile(r"^\d{4}-\d{2}$")


class InputModel(BaseModel):
    """Per-company input the prompt reads."""

    model_config = ConfigDict(extra="forbid")

    company_name: str = Field(..., description="Legal or trading name of the subject company")
    domain: str = Field(..., description="Website host, used to reject same-named companies")

    @field_validator("company_name", "domain")
    @classmethod
    def validate_subject_text(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value:
            raise ValueError("subject text must be non-empty")
        return value

class _Event(BaseModel):
    """One dated, cited event."""

    model_config = ConfigDict(extra="forbid")

    date: str = Field(..., description="YYYY-MM-DD or YYYY-MM")
    headline: str = Field(..., description="<= 16 words")
    why_it_matters: str = Field(..., description="<= 20 words")
    source_url: str = Field(..., description="absolute http(s) URL")
    evidence_ids: list[str] = Field(..., min_length=1, description="retained Evidence IDs")

    @field_validator("date")
    @classmethod
    def validate_date(cls, value: str) -> str:
        value = value.strip()
        if _FULL_DATE.fullmatch(value):
            try:
                calendar_date.fromisoformat(value)
            except ValueError as error:
                raise ValueError("date must be a real calendar date") from error
            return value
        if _MONTH_DATE.fullmatch(value) and 1 <= int(value[5:7]) <= 12:
            return value
        raise ValueError("date must be YYYY-MM-DD or YYYY-MM")

    @field_validator("headline", "why_it_matters")
    @classmethod
    def validate_text(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value:
            raise ValueError("event text must be non-empty")
        return value

    @field_validator("source_url")
    @classmethod
    def validate_source_url(cls, value: str) -> str:
        value = value.strip()
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("source_url must be an absolute HTTP(S) URL")
        return value

    @field_validator("evidence_ids")
    @classmethod
    def validate_evidence_ids(
        cls, value: list[str], info: ValidationInfo,
    ) -> list[str]:
        normalized = [item.strip() for item in value]
        if any(not item for item in normalized):
            raise ValueError("evidence_ids must contain non-empty IDs")
        if len(set(normalized)) != len(normalized):
            raise ValueError("evidence_ids must contain unique IDs")
        if info.context is not None and "retained_evidence_ids" in info.context:
            retained = info.context["retained_evidence_ids"]
            if isinstance(retained, (str, bytes)) or not isinstance(
                retained, (list, tuple, set, frozenset)
            ):
                raise ValueError("retained_evidence_ids context must be a collection")
            if any(
                not isinstance(item, str) or not item.strip() for item in retained
            ):
                raise ValueError("retained_evidence_ids must contain non-empty text IDs")
            retained_ids = set(retained)
            if not set(normalized) <= retained_ids:
                raise ValueError("all events must reference retained Evidence IDs")
        return normalized


class NewsEvent(_Event):
    event_type: NewsEventType


class LaunchEvent(_Event):
    event_type: LaunchEventType


class OutputModel(BaseModel):
    """Two typed collections plus explicit unknowns."""

    model_config = ConfigDict(extra="forbid")

    news: list[NewsEvent]
    launches: list[LaunchEvent]
    unknowns: list[Literal["news", "launches"]]

    @model_validator(mode="after")
    def validate_unknowns(self) -> OutputModel:
        for collection in self.unknowns:
            if getattr(self, collection):
                raise ValueError(
                    f"{collection} cannot contain events when declared unknown"
                )
        return self
