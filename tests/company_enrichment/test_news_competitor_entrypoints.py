from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path

import pytest
import yaml

from scripts import company_enrichment_competitor_loop, company_enrichment_news_loop
from scripts.company_enrichment.signal_collection import bind_collect
from scripts.company_enrichment.signal_entrypoints import (
    COMPETITOR_PLAN, NEWS_PLAN, build_competitor_spec, build_news_spec,
    draft_competitor_ground_truth, draft_news_ground_truth, parse_date_text, run_entrypoint,
)
from scripts.company_enrichment.signal_ground_truth import ALL_IDS, DEVELOPMENT_IDS, HOLDOUT_IDS
from tests.company_enrichment.test_signal_collection import (
    COMPANIES, NEWS_RESULTS, PAGES, FakeScrape, FakeSearch, NOW,
)
from tests.company_enrichment.test_signal_ground_truth import base_dossier, build_signal_repo

REPO_ROOT = Path(__file__).resolve().parents[2]


def _companies_yaml(root: Path) -> None:
    (root / "benchmarks").mkdir(parents=True, exist_ok=True)
    companies = {"version": "1.0", "companies": [
        {"id": company_id, "company_name": f"Company {company_id[-2:]}",
         "domain": f"{company_id}.example"} for company_id in ALL_IDS
    ]}
    companies["companies"][0].update(COMPANIES["companies"][0])
    (root / "benchmarks" / "companies.yaml").write_text(yaml.safe_dump(companies), encoding="utf-8")


def test_specs_are_wired_to_the_locked_corpus_and_plans():
    news = build_news_spec()
    assert news.enrichment_id == "news-product-launches" and news.fields == ("news", "launches")
    assert news.rubric == "events:0.60,citation:0.25,kind:0.15"
    assert [item.template for item in NEWS_PLAN.queries] == [
        "{{domain}} news",
        '{{company_name}} "press release" OR "announces" OR "newsroom"',
        '{{company_name}} launches OR "new feature" OR "introducing" OR "now available"',
    ]
    assert [(item.mode, item.tbs) for item in NEWS_PLAN.queries] == [
        ("news", None), ("web", "qdr:y"), ("web", "qdr:y"),
    ]
    assert NEWS_PLAN.first_party_paths == (
        "/blog", "/news", "/newsroom", "/press", "/changelog", "/product-updates", "/whats-new",
    )
    competitors = build_competitor_spec()
    assert competitors.enrichment_id == "competitor-intelligence"
    assert competitors.rubric == "named_set:0.50,citation:0.30,labeling:0.20"
    assert [item.template for item in COMPETITOR_PLAN.queries] == [
        '{{company_name}} {{category}} alternatives OR competitors OR "vs" OR "compared to"',
        "{{company_name}} alternatives", "{{company_name}} vs",
    ]
    assert all(item.mode == "web" and item.tbs is None for item in COMPETITOR_PLAN.queries)
    assert COMPETITOR_PLAN.first_party_paths == ("/competitors", "/compare", "/alternatives")
    for spec in (news, competitors):
        assert (REPO_ROOT / spec.prompt_path).is_file()
        rubric = yaml.safe_load((REPO_ROOT / spec.benchmark_dir / "rubric.yaml").read_text())
        assert {key: str(value) for key, value in rubric["weights"].items()} == {
            key: str(float(value)) for key, value in spec.weights.items()
        }
        split = yaml.safe_load((REPO_ROOT / spec.benchmark_dir / "split.yaml").read_text())
        assert split == {"development": list(DEVELOPMENT_IDS), "holdout": list(HOLDOUT_IDS)}


@pytest.mark.parametrize("module", [company_enrichment_news_loop, company_enrichment_competitor_loop])
def test_collect_dry_run_plans_without_network(module, tmp_path: Path, capsys, monkeypatch):
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    (tmp_path / "benchmarks" / "dossiers").mkdir(parents=True)
    from scripts.company_enrichment.signal_evidence import save_signal_dossier
    for company_id in ALL_IDS:
        save_signal_dossier(tmp_path / "benchmarks/dossiers" / f"{company_id}.yaml",
                            base_dossier(company_id))
    code = module.main(["--collect", "--dry-run", "--company", "saas-01"], repo_root=tmp_path)
    assert code == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["planned"] == ["saas-01"] and plan["dry_run"] is True
    assert not list((tmp_path / "benchmarks" / "signals").rglob("*.yaml"))


def test_evaluate_dry_run_for_both_loops(tmp_path: Path, capsys):
    news_root = tmp_path / "news"
    build_signal_repo(news_root, enrichment_id="news-product-launches",
                      weights=build_news_spec().weights,
                      ground_truth=lambda company_id, _r: {
                          "company_id": company_id, "as_of": "2026-08-18", "events": []})
    code = company_enrichment_news_loop.main(
        ["--evaluate", "--lineage", "dry-news", "--dry-run"], repo_root=news_root,
    )
    assert code == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["enrichment_id"] == "news-product-launches" and plan["cached_only"] is True

    comp_root = tmp_path / "competitors"
    build_signal_repo(comp_root, enrichment_id="competitor-intelligence",
                      weights=build_competitor_spec().weights,
                      ground_truth=lambda company_id, _r: {
                          "company_id": company_id, "named": [], "inferred": [],
                          "evidence_ids": [f"ev-{company_id}-google"]})
    code = company_enrichment_competitor_loop.main(
        ["--evaluate", "--lineage", "dry-comp", "--dry-run"], repo_root=comp_root,
    )
    assert code == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["enrichment_id"] == "competitor-intelligence" and plan["source_purchases"] == 0


def _fake_spec(spec, plan):
    collect = bind_collect(
        enrichment_id=spec.enrichment_id, plan=plan,
        search_factory=lambda: FakeSearch(NEWS_RESULTS), scrape_factory=lambda: FakeScrape(PAGES),
        first_party_scrape_factory=lambda: FakeScrape(PAGES), clock=lambda: NOW,
    )
    return replace(spec, collect=collect)


def test_collect_then_draft_news_ground_truth(tmp_path: Path, capsys):
    _companies_yaml(tmp_path)
    (tmp_path / "benchmarks" / "dossiers").mkdir(parents=True, exist_ok=True)
    from scripts.company_enrichment.signal_evidence import save_signal_dossier
    save_signal_dossier(tmp_path / "benchmarks/dossiers/saas-01.yaml", base_dossier("saas-01"))
    spec = _fake_spec(build_news_spec(), NEWS_PLAN)

    code = run_entrypoint(spec, draft_news_ground_truth,
                          ["--collect", "--company", "saas-01"], repo_root=tmp_path)
    assert code == 0
    written = json.loads(capsys.readouterr().out)
    assert written["written"] == ["saas-01"]
    signal_dir = tmp_path / "benchmarks/signals/news-product-launches"
    assert (signal_dir / "saas-01.yaml").is_file()
    log = json.loads((signal_dir / "saas-01.collection.json").read_text(encoding="utf-8"))
    assert log["enrichment_id"] == "news-product-launches"

    code = run_entrypoint(spec, draft_news_ground_truth,
                          ["--draft-ground-truth", "--company", "saas-01", "--as-of", "2026-08-18"],
                          repo_root=tmp_path)
    assert code == 0
    result = json.loads(capsys.readouterr().out)
    assert result["written"] == ["saas-01"] and result["sealed_ground_truth_untouched"] is True
    draft_path = signal_dir / "ground-truth-drafts" / "saas-01.yaml"
    text = draft_path.read_text(encoding="utf-8")
    assert text.startswith("# DRAFT ground truth - TODO_HUMAN")
    draft = yaml.safe_load(text)
    assert draft["company_id"] == "saas-01" and draft["as_of"] == "2026-08-18"
    assert draft["recent_window_days"] == 180
    dates = {event["date"] for event in draft["events"]}
    assert {"2024-01-30", "2024-04-11"} <= dates
    assert all(event["kind"] == "TODO_HUMAN" for event in draft["events"])
    assert all(event["evidence_ids"] for event in draft["events"])
    assert not (signal_dir / "ground-truth").exists()

    # existing drafts are not overwritten silently
    code = run_entrypoint(spec, draft_news_ground_truth,
                          ["--draft-ground-truth", "--company", "saas-01"], repo_root=tmp_path)
    assert code == 0
    assert json.loads(capsys.readouterr().out)["skipped_existing"] == ["saas-01"]
    # missing signal dossiers are reported, not raised
    code = run_entrypoint(spec, draft_news_ground_truth,
                          ["--draft-ground-truth", "--company", "saas-02"], repo_root=tmp_path)
    assert code == 0
    assert json.loads(capsys.readouterr().out)["missing_signal_dossiers"] == ["saas-02"]


def test_draft_competitor_ground_truth_extracts_candidates(tmp_path: Path, capsys):
    _companies_yaml(tmp_path)
    (tmp_path / "benchmarks" / "dossiers").mkdir(parents=True, exist_ok=True)
    from scripts.company_enrichment.signal_evidence import save_signal_dossier
    save_signal_dossier(tmp_path / "benchmarks/dossiers/saas-01.yaml", base_dossier("saas-01"))
    results = {
        "AgencyAnalytics agency reporting alternatives OR competitors OR \"vs\" OR \"compared to\"": (
            ("https://g2.com/compare/agencyanalytics-vs-dashthis",
             "Title: AgencyAnalytics vs DashThis\nSnippet: Compare DashThis and Whatagraph (whatagraph.com)"),
        ),
        "AgencyAnalytics alternatives": (
            ("https://blog.example/alts", "Title: Top AgencyAnalytics Alternatives\nSnippet: TapClicks, Swydo"),
        ),
    }
    collect = bind_collect(
        enrichment_id="competitor-intelligence", plan=COMPETITOR_PLAN,
        search_factory=lambda: FakeSearch(results), scrape_factory=lambda: FakeScrape(),
        first_party_scrape_factory=lambda: FakeScrape(), clock=lambda: NOW,
    )
    spec = replace(build_competitor_spec(), collect=collect)
    assert run_entrypoint(spec, draft_competitor_ground_truth,
                          ["--collect", "--company", "saas-01"], repo_root=tmp_path) == 0
    capsys.readouterr()
    assert run_entrypoint(spec, draft_competitor_ground_truth,
                          ["--draft-ground-truth", "--company", "saas-01"], repo_root=tmp_path) == 0
    capsys.readouterr()
    draft_path = tmp_path / "benchmarks/signals/competitor-intelligence/ground-truth-drafts/saas-01.yaml"
    draft = yaml.safe_load(draft_path.read_text(encoding="utf-8"))
    names = {entry["name"] for entry in draft["named"]}
    assert {"TODO_HUMAN DashThis", "TODO_HUMAN Whatagraph", "TODO_HUMAN TapClicks",
            "TODO_HUMAN Swydo", "TODO_HUMAN whatagraph.com"} <= names
    assert not any("AgencyAnalytics" in name for name in names)
    assert draft["inferred"] == [] and draft["evidence_ids"]


def test_parse_date_text_variants():
    assert parse_date_text("Mar 5, 2024") == "2024-03-05"
    assert parse_date_text("5 March 2024") == "2024-03-05"
    assert parse_date_text("March 2024") == "2024-03"
    assert parse_date_text("2024-04-11T10:00:00Z") == "2024-04-11"
    assert parse_date_text("2 months ago") is None
    assert parse_date_text("") is None


def test_draft_rejects_unknown_company(tmp_path: Path, capsys):
    code = run_entrypoint(build_news_spec(), draft_news_ground_truth,
                          ["--draft-ground-truth", "--company", "nope"], repo_root=tmp_path)
    assert code == 2
    assert json.loads(capsys.readouterr().out)["error"] == "ValueError"


def test_search_factory_orders_providers_from_env(monkeypatch):
    from scripts.company_enrichment.signal_entrypoints import _search_factory
    from scripts.company_enrichment.signal_loop import SEARCH_PROVIDER_ENV

    monkeypatch.delenv(SEARCH_PROVIDER_ENV, raising=False)
    assert _search_factory().provider == "serper"
    monkeypatch.setenv(SEARCH_PROVIDER_ENV, "parallel")
    assert _search_factory().provider == "parallel"
    monkeypatch.setenv(SEARCH_PROVIDER_ENV, "parallel,serper")
    assert _search_factory().provider == "parallel"
    monkeypatch.setenv(SEARCH_PROVIDER_ENV, "serper,bogus")
    with pytest.raises(ValueError):
        _search_factory()
    monkeypatch.setenv(SEARCH_PROVIDER_ENV, " , ")
    with pytest.raises(ValueError):
        _search_factory()
