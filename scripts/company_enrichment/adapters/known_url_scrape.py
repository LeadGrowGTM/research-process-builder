"""Free-only known-URL scraping over the GTM web-scraping waterfall.

Levels 1 (plain HTTP + html2text) and 2 (Crawl4AI, when installed) come from
``firecrawl_waterfall.py`` in the gtm-orchestrator web-scraping skill. The
scripts directory is added to ``sys.path`` lazily, at call time, so importing
this module never touches the filesystem; override the location with
``GTM_WEB_SCRAPING_SCRIPTS``. Levels 3 and 4 are paid Firecrawl tiers and are
never run here: ``build_free_waterfall`` installs a reserve callable that
raises ``BudgetFailure`` before any paid level can execute.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
import os
from pathlib import Path
import sys
from typing import Any

from ..contracts import MAX_EXCERPT_CHARS
from ..providers import (
    BudgetFailure,
    KnownUrlRequest,
    RetryableFailure,
    SourceObservation,
)
from .gtm_waterfall import GtmWaterfallAdapter

KNOWN_URL_PROVIDER = "gtm-waterfall-free"
DEFAULT_SCRIPTS_DIR = Path(
    r"C:\Users\mitch\Everything_CC\pipelines\gtm-orchestrator\.claude\skills\web-scraping\scripts"
)
FREE_LEVELS = (1, 2)
_PAGE_LIMIT = 1

LevelFn = Callable[[str, int], list[dict[str, Any]]]


def scripts_dir() -> Path:
    override = os.environ.get("GTM_WEB_SCRAPING_SCRIPTS", "").strip()
    return Path(override) if override else DEFAULT_SCRIPTS_DIR


def load_waterfall_module():
    """Import ``firecrawl_waterfall`` from the skill scripts dir at call time."""
    directory = scripts_dir()
    if not (directory / "firecrawl_waterfall.py").is_file():
        raise RetryableFailure(f"firecrawl_waterfall.py not found under {directory}")
    location = str(directory)
    if location not in sys.path:
        sys.path.insert(0, location)
    try:
        import firecrawl_waterfall  # type: ignore[import-not-found]
    except Exception as error:  # missing requests/html2text/_config
        raise RetryableFailure(
            f"firecrawl_waterfall import failed: {type(error).__name__}"
        ) from None
    return firecrawl_waterfall


def page_observations(
    pages: Iterable[dict[str, Any]], fallback_url: str,
    is_quality_failure: Callable[[str], bool] | None = None,
) -> tuple[SourceObservation, ...]:
    """Turn waterfall page dicts into observations, dropping empty or blocked pages.

    The excerpt carries the page title on its first line followed by the
    markdown body, truncated to the shared excerpt cap.
    """
    observations: list[SourceObservation] = []
    for page in pages:
        if not isinstance(page, dict):
            continue
        markdown = page.get("markdown")
        if not isinstance(markdown, str) or not markdown.strip():
            continue
        if is_quality_failure is not None and is_quality_failure(markdown):
            continue
        metadata = page.get("metadata") if isinstance(page.get("metadata"), dict) else {}
        title = " ".join(str(metadata.get("title", "")).split())
        body = markdown.strip()
        excerpt = f"Title: {title}\n{body}" if title else body
        url = page.get("url") if isinstance(page.get("url"), str) else fallback_url
        try:
            observations.append(SourceObservation(url, excerpt[:MAX_EXCERPT_CHARS]))
        except ValueError:
            continue
    return tuple(observations)


class FreeWaterfallExecutor:
    """``execute(level, request)`` for ``GtmWaterfallAdapter`` using free levels only."""

    def __init__(
        self, *, level1: LevelFn | None = None, level2: LevelFn | None = None,
        level2_available: Callable[[], bool] | None = None,
        is_quality_failure: Callable[[str], bool] | None = None,
        max_level: int = 2,
    ) -> None:
        if max_level not in FREE_LEVELS:
            raise ValueError("max_level must be 1 or 2 (free levels only)")
        self._level1 = level1
        self._level2 = level2
        self._level2_available = level2_available
        self._is_quality_failure = is_quality_failure
        self.max_level = max_level

    def _resolve(self) -> None:
        if self._level1 is not None and self._level2 is not None:
            return
        module = load_waterfall_module()
        self._level1 = self._level1 or module.scrape_level1
        self._level2 = self._level2 or module.scrape_level2
        if self._level2_available is None:
            self._level2_available = module.crawl4ai_available
        if self._is_quality_failure is None:
            self._is_quality_failure = module.is_quality_failure

    def __call__(self, level: int, request: KnownUrlRequest) -> tuple[SourceObservation, ...]:
        if level not in FREE_LEVELS:
            raise BudgetFailure(f"paid waterfall level {level} is disabled for signal collection")
        if level > self.max_level:
            return ()
        self._resolve()
        if level == 2 and self._level2_available is not None and not self._level2_available():
            return ()
        function = self._level1 if level == 1 else self._level2
        try:
            pages = function(request.url, _PAGE_LIMIT)
        except BudgetFailure:
            raise
        except Exception as error:
            raise RetryableFailure(
                f"waterfall level {level} failed for {request.url}: {type(error).__name__}"
            ) from None
        return page_observations(pages or (), request.url, self._is_quality_failure)


def refuse_paid_reservation(idempotency_key: str, amount: str):
    raise BudgetFailure(
        f"paid scrape reservation refused ({amount} USD): {idempotency_key}"
    )


def build_free_waterfall(
    reserve: Callable[[str, str], Any] | None = None, *, max_level: int = 2,
    executor: FreeWaterfallExecutor | None = None,
) -> GtmWaterfallAdapter:
    """Return a ``GtmWaterfallAdapter`` that can only run free levels 1 and 2."""
    return GtmWaterfallAdapter(
        execute=executor or FreeWaterfallExecutor(max_level=max_level),
        reserve=reserve or refuse_paid_reservation,
    )
