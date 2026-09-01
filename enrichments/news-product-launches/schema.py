"""Sidecar schemas for the ``news-product-launches`` enrichment package.

Shipped inside the package so the package is portable: the GTM orchestrator's
prompt loader binds ``InputModel``/``OutputModel`` from here via the
``schema_module`` frontmatter key rather than from a module hardcoded in
``lg_runtime.prompts.schemas``.

The authority on the returned shape stays
``scripts.company_enrichment.news_contracts``; these models are the transport
declaration for consumers that cannot import this repository.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

from scripts.company_enrichment.news_contracts import (
    LAUNCH_EVENT_TYPES,
    NEWS_EVENT_TYPES,
)

NewsEventType = Literal[NEWS_EVENT_TYPES]  # type: ignore[valid-type]
LaunchEventType = Literal[LAUNCH_EVENT_TYPES]  # type: ignore[valid-type]


class InputModel(BaseModel):
    """Per-company input the prompt reads."""

    company_name: str = Field(..., description="Legal or trading name of the subject company")
    domain: str = Field(..., description="Website host, used to reject same-named companies")
    as_of: Optional[str] = Field(
        default=None, description="ISO date the run is anchored to; defaults to the run date"
    )


class _Event(BaseModel):
    """One dated, cited event."""

    date: str = Field(..., description="YYYY-MM-DD or YYYY-MM")
    headline: str = Field(..., description="<= 16 words")
    why_it_matters: str = Field(..., description="<= 20 words")
    source_url: str = Field(..., description="absolute http(s) URL")
    evidence_ids: list[str] = Field(..., min_length=1, description="retained Evidence IDs")


class NewsEvent(_Event):
    event_type: NewsEventType


class LaunchEvent(_Event):
    event_type: LaunchEventType


class OutputModel(BaseModel):
    """Two typed collections plus explicit unknowns."""

    news: list[NewsEvent] = Field(default_factory=list)
    launches: list[LaunchEvent] = Field(default_factory=list)
    unknowns: list[Literal["news", "launches"]] = Field(default_factory=list)
