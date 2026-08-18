from __future__ import annotations

from pathlib import Path

import pytest

from scripts.company_enrichment.adapters.known_url_scrape import (
    FreeWaterfallExecutor, build_free_waterfall, load_waterfall_module, page_observations,
    scripts_dir,
)
from scripts.company_enrichment.providers import (
    BudgetFailure, KnownUrlRequest, RetryableFailure,
)


def _page(url="https://acme.example/blog", markdown="# Blog\n\nReal content " * 20, title="Blog"):
    return {"url": url, "markdown": markdown, "metadata": {"title": title}}


def test_level1_result_is_returned_at_level_one():
    calls = []

    def level1(url, limit):
        calls.append(("l1", url, limit))
        return [_page()]

    def level2(url, limit):
        raise AssertionError("level 2 must not run when level 1 succeeds")

    adapter = build_free_waterfall(executor=FreeWaterfallExecutor(level1=level1, level2=level2))
    result = adapter.scrape(KnownUrlRequest("https://acme.example/blog"))

    assert result.waterfall_level == 1
    assert calls == [("l1", "https://acme.example/blog", 1)]
    assert result.observations[0].excerpt.startswith("Title: Blog\n# Blog")


def test_level2_runs_only_when_available_and_level1_is_empty():
    executor = FreeWaterfallExecutor(
        level1=lambda url, limit: [], level2=lambda url, limit: [_page(title="Rendered")],
        level2_available=lambda: True,
    )
    result = build_free_waterfall(executor=executor).scrape(KnownUrlRequest("https://acme.example"))
    assert result.waterfall_level == 2
    assert result.observations[0].excerpt.startswith("Title: Rendered")


def test_paid_levels_never_run_and_surface_budget_failure():
    executed = []

    def level1(url, limit):
        executed.append(1)
        return []

    def level2(url, limit):
        executed.append(2)
        return []

    adapter = build_free_waterfall(executor=FreeWaterfallExecutor(
        level1=level1, level2=level2, level2_available=lambda: True,
    ))
    with pytest.raises(BudgetFailure, match="paid scrape reservation refused"):
        adapter.scrape(KnownUrlRequest("https://acme.example"))
    assert executed == [1, 2]


def test_executor_refuses_paid_levels_directly():
    executor = FreeWaterfallExecutor(level1=lambda u, l: [], level2=lambda u, l: [])
    with pytest.raises(BudgetFailure):
        executor(3, KnownUrlRequest("https://acme.example"))
    with pytest.raises(BudgetFailure):
        executor(4, KnownUrlRequest("https://acme.example"))


def test_max_level_one_skips_level_two():
    executor = FreeWaterfallExecutor(
        level1=lambda u, l: [], level2=lambda u, l: [_page()], level2_available=lambda: True,
        max_level=1,
    )
    assert executor(2, KnownUrlRequest("https://acme.example")) == ()
    with pytest.raises(ValueError):
        FreeWaterfallExecutor(level1=lambda u, l: [], level2=lambda u, l: [], max_level=3)


def test_level_exception_becomes_retryable_failure_without_details():
    def level1(url, limit):
        raise RuntimeError("HTTP 500 secret detail")

    executor = FreeWaterfallExecutor(level1=level1, level2=lambda u, l: [])
    with pytest.raises(RetryableFailure) as info:
        executor(1, KnownUrlRequest("https://acme.example"))
    assert "secret detail" not in str(info.value)


def test_page_observations_drop_blocked_or_empty_pages():
    pages = [
        _page(markdown=""),
        _page(markdown="Enable JavaScript to continue"),
        {"markdown": "Fine content " * 20, "metadata": {"title": "T"}},
        "not a dict",
    ]
    observations = page_observations(
        pages, "https://fallback.example/x", lambda text: "Enable JavaScript" in text,
    )
    assert len(observations) == 1
    assert observations[0].url == "https://fallback.example/x"
    assert observations[0].excerpt.startswith("Title: T\nFine content")


def test_scripts_dir_env_override_and_missing_module(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("GTM_WEB_SCRAPING_SCRIPTS", str(tmp_path))
    assert scripts_dir() == tmp_path
    with pytest.raises(RetryableFailure, match="not found"):
        load_waterfall_module()


def test_default_reserve_raises_before_paid_level(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("GTM_WEB_SCRAPING_SCRIPTS", str(tmp_path))
    adapter = build_free_waterfall(max_level=1)
    with pytest.raises(RetryableFailure):
        adapter.scrape(KnownUrlRequest("https://acme.example"))
