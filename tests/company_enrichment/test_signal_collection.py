from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path

import pytest
import yaml

from scripts.company_enrichment.contracts import EvidenceRef
from scripts.company_enrichment.evidence import EvidenceStore
from scripts.company_enrichment.providers import (
    AuthenticationFailure, InsufficientEvidence, KnownUrlRequest, ProviderFailure,
    RetryableFailure, ScrapeResult, SearchRequest, SearchResult, SourceObservation,
)
from scripts.company_enrichment.signal_collection import (
    FallbackSearch, QueryTemplate, SearchPlan, bind_collect, collect_search_signals,
    collection_log_path, company_facts, detect_date_line, evidence_ref, is_own_noise_page,
    page_excerpt, url_identity,
)
from scripts.company_enrichment.signal_evidence import (
    load_signal_dossier, save_signal_dossier, signal_dossier_path,
)
from scripts.company_enrichment.signal_loop import CollectRequest
from tests.company_enrichment.test_signal_ground_truth import base_dossier

NOW = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)
COMPANIES = {
    "version": "1.0",
    "companies": [
        {"id": "saas-01", "company_name": "AgencyAnalytics", "domain": "www.agencyanalytics.com",
         "business_offer": "agency reporting"},
        {"id": "saas-02", "company_name": "AgilePoint", "domain": "agilepoint.com"},
    ],
}
PLAN = SearchPlan(
    queries=(
        QueryTemplate("{{domain}} news", "news", "qdr:y"),
        QueryTemplate("{{company_name}} {{category}} launches", "web", "qdr:y"),
        QueryTemplate("{{company_name}} never runs", "web"),
    ),
    first_party_paths=("/blog", "/news"),
    caps={"queries": 2, "scrapes": 2},
    freshness_days=365,
    top_urls_per_query=2,
    excerpt_chars=300,
)


def build_repo(tmp_path: Path) -> Path:
    (tmp_path / "benchmarks" / "dossiers").mkdir(parents=True)
    (tmp_path / "benchmarks" / "companies.yaml").write_text(
        yaml.safe_dump(COMPANIES), encoding="utf-8",
    )
    for company_id in ("saas-01", "saas-02"):
        save_signal_dossier(tmp_path / "benchmarks/dossiers" / f"{company_id}.yaml",
                            base_dossier(company_id))
    return tmp_path


def _request(repo: Path, company_id: str = "saas-01") -> CollectRequest:
    return CollectRequest(company_id, base_dossier(company_id), repo)


class FakeSearch:
    provider = "serper"
    cost_per_query_usd = "0.001"

    def __init__(self, results=None, fail_on=()):
        self.results = results or {}
        self.fail_on = set(fail_on)
        self.calls: list[tuple[str, str, str | None]] = []
        self.mode = "web"
        self.tbs = None

    def for_query(self, *, mode, tbs):
        sibling = FakeSearch(self.results, self.fail_on)
        sibling.calls = self.calls
        sibling.mode, sibling.tbs = mode, tbs
        return sibling

    def search(self, request: SearchRequest) -> SearchResult:
        self.calls.append((request.query, self.mode, self.tbs))
        if request.query in self.fail_on:
            raise RetryableFailure("serper HTTP 500")
        return SearchResult(tuple(
            SourceObservation(url, excerpt) for url, excerpt in self.results.get(request.query, ())
        ))


class FakeScrape:
    provider = "gtm-waterfall-free"

    def __init__(self, pages=None, fail_on=()):
        self.pages = pages or {}
        self.fail_on = set(fail_on)
        self.calls: list[str] = []

    def scrape(self, request: KnownUrlRequest) -> ScrapeResult:
        self.calls.append(request.url)
        if request.url in self.fail_on:
            raise InsufficientEvidence(f"nothing at {request.url}")
        text = self.pages.get(request.url)
        if text is None:
            raise RetryableFailure("HTTP 404")
        return ScrapeResult((SourceObservation(request.url, text),), 1)


NEWS_RESULTS = {
    "agencyanalytics.com news": (
        ("https://www.prnewswire.com/a-launch",
         "Title: AgencyAnalytics Launches Smart Reports\nDate: Jan 30, 2024\nSnippet: new"),
        ("https://www.prnewswire.com/a-launch/",  # duplicate identity (trailing slash)
         "Title: dup\nDate: Jan 30, 2024\nSnippet: dup"),
        ("https://agencyanalytics.com/templates/seo",  # own template page dropped
         "Title: SEO report template\nSnippet: template"),
        ("https://techcrunch.com/agency",
         "Title: TC on AgencyAnalytics\nDate: 2024-04-11\nSnippet: funding"),
        ("https://example.org/off-subject",  # never names the subject: dropped
         "Title: KBRA credit ratings\nSnippet: press release"),
        ("https://example.org/fourth",
         "Title: fourth agencyanalytics\nSnippet: beyond top_urls_per_query"),
    ),
    "AgencyAnalytics agency reporting launches": (
        ("https://techcrunch.com/agency?utm=1",
         "Title: TC again on AgencyAnalytics\nSnippet: same page"),
    ),
}
PAGES = {
    "https://www.prnewswire.com/a-launch": "PR Newswire\nJanuary 30, 2024\n" + "Smart Reports body. " * 30,
    "https://agencyanalytics.com/blog": "Blog\nPosted March 3, 2026\n" + "Blog content here. " * 40,
    "https://agencyanalytics.com/news": "thin",
}


def test_collect_builds_dossier_from_serp_pages_and_first_party(tmp_path: Path):
    repo = build_repo(tmp_path)
    search = FakeSearch(NEWS_RESULTS)
    scrape = FakeScrape(PAGES, fail_on=("https://techcrunch.com/agency",))
    probe = FakeScrape(PAGES)

    outcome = collect_search_signals(
        _request(repo), plan=PLAN, search=search, scrape=scrape, now=NOW,
        first_party_scrape=probe,
    )

    # queries: capped at 2, templates substituted, mode/tbs honoured
    assert search.calls == [
        ("agencyanalytics.com news", "news", "qdr:y"),
        ("AgencyAnalytics agency reporting launches", "web", "qdr:y"),
    ]
    # scrapes: dedupe by identity, drop own template page, top 2 per query, cap 2
    assert scrape.calls == ["https://www.prnewswire.com/a-launch", "https://techcrunch.com/agency"]
    assert probe.calls == ["https://agencyanalytics.com/blog", "https://agencyanalytics.com/news"]

    dossier = outcome.dossier
    base_ids = {item.evidence_id for item in base_dossier("saas-01").evidence}
    assert base_ids <= {item.evidence_id for item in dossier.evidence}
    urls = [item.url for item in dossier.evidence]
    assert "https://www.prnewswire.com/a-launch" in urls
    assert "https://agencyanalytics.com/blog" in urls
    assert "https://agencyanalytics.com/news" not in urls  # thin first-party content dropped
    assert any(url.startswith("https://www.google.com/search?q=") for url in urls)
    # SERP results are kept as small dated records even when the scrape fails
    assert not [item for item in dossier.evidence if item.url == "https://example.org/off-subject"]
    serp = [item for item in dossier.evidence if item.url == "https://techcrunch.com/agency"]
    assert serp and serp[0].excerpt.startswith("Detected date: 2024-04-11\nTitle: TC")
    # scraped page excerpt starts with the detected date line and is bounded
    page = next(item for item in dossier.evidence
                if item.url == "https://www.prnewswire.com/a-launch" and "Smart Reports body" in item.excerpt)
    assert page.excerpt.startswith("Detected date: January 30, 2024\n")
    assert len(page.excerpt) <= 300 + len("Detected date: January 30, 2024\n")
    # every ref is content addressed like the base dossiers
    for item in dossier.evidence:
        if item.evidence_id not in base_ids:
            assert item.evidence_id == f"ev-{item.content_hash[:16]}"
    # cost: one paid SERP page per query, scrapes free
    assert outcome.paid_cost_usd == "0.002"
    statuses = {(entry["step"], entry.get("target")): entry["status"] for entry in outcome.log}
    assert statuses[("scrape", "https://techcrunch.com/agency")] == "failed"
    assert statuses[("first_party", "https://agencyanalytics.com/news")] == "thin_content"
    failed = next(entry for entry in outcome.log if entry.get("status") == "failed")
    assert failed["kind"] == "insufficient_evidence"


def test_search_failures_are_logged_not_raised(tmp_path: Path):
    repo = build_repo(tmp_path)
    search = FakeSearch({}, fail_on={"agencyanalytics.com news", "AgencyAnalytics agency reporting launches"})
    outcome = collect_search_signals(
        _request(repo), plan=PLAN, search=search, scrape=FakeScrape(), now=NOW,
        first_party_scrape=FakeScrape(),
    )
    failures = [entry for entry in outcome.log if entry["status"] == "failed"]
    assert {entry["step"] for entry in failures} == {"search", "first_party"}
    assert all(entry["kind"] in {"retryable", "insufficient_evidence"} for entry in failures)
    assert len(outcome.dossier.evidence) == len(base_dossier("saas-01").evidence)
    assert outcome.paid_cost_usd == "0"


def test_fallback_search_routes_serper_then_parallel_as_normalized_failure():
    def build_parallel():
        raise AuthenticationFailure("PARALLEL_API_KEY is not configured")

    primary = FakeSearch({"q": (("https://a.example/x", "Title: A"),)})
    fallback = FallbackSearch((("serper", primary), ("parallel", build_parallel)))
    news = fallback.for_query(mode="news", tbs="qdr:y")
    result = news.search(SearchRequest("q"))
    assert result.observations[0].url == "https://a.example/x"
    assert news.provider == "serper" and news.last_failures == []
    assert primary.calls == [("q", "news", "qdr:y")]

    broken = FakeSearch({}, fail_on={"q"})
    fallback = FallbackSearch((("serper", broken), ("parallel", build_parallel)))
    with pytest.raises(AuthenticationFailure):
        fallback.search(SearchRequest("q"))
    assert [item["provider"] for item in fallback.last_failures] == ["serper", "parallel"]
    assert [item["kind"] for item in fallback.last_failures] == [
        "retryable", "authentication_required",
    ]


def test_fallback_uses_second_provider_when_first_fails():
    broken = FakeSearch({}, fail_on={"q"})
    second = FakeSearch({"q": (("https://b.example/y", "Title: B"),)})
    second.provider = "parallel"
    fallback = FallbackSearch((("serper", broken), ("parallel", second)))
    result = fallback.search(SearchRequest("q"))
    assert result.observations[0].url == "https://b.example/y"
    assert fallback.provider == "parallel"
    assert fallback.last_failures[0]["provider"] == "serper"


def test_company_facts_and_helpers(tmp_path: Path):
    repo = build_repo(tmp_path)
    facts = company_facts(repo, "saas-01", base_dossier("saas-01"))
    assert facts == {"company_name": "AgencyAnalytics", "domain": "agencyanalytics.com",
                     "category": "agency reporting"}
    with pytest.raises(ValueError, match="not in benchmarks/companies.yaml"):
        company_facts(repo, "saas-09", base_dossier("saas-09"))
    assert url_identity("https://WWW.Example.com/A/b/?x=1#f") == "example.com/a/b"
    assert is_own_noise_page("https://acme.example/careers/eng", "acme.example")
    assert is_own_noise_page("https://acme.example/templates", "acme.example")
    assert not is_own_noise_page("https://other.example/templates", "acme.example")
    assert not is_own_noise_page("https://acme.example/blog/templates-guide", "acme.example")
    assert detect_date_line("Posted on 5 March 2024 by staff") == "5 March 2024"
    assert detect_date_line("Updated Sept. 3, 2025") == "Sept. 3, 2025"
    assert detect_date_line("no dates here") is None
    assert page_excerpt("no date " * 100, 20).startswith("no date")
    template = QueryTemplate("{{company_name}} {{category}} vs")
    assert template.render({"company_name": "A", "category": ""}) == "A vs"


def test_plan_and_template_validation():
    with pytest.raises(ValueError):
        QueryTemplate("x", mode="images")
    with pytest.raises(ValueError):
        SearchPlan((), (), {"queries": 1, "scrapes": 1}, 30)
    with pytest.raises(ValueError):
        SearchPlan((QueryTemplate("x"),), ("blog",), {"queries": 1, "scrapes": 1}, 30)
    with pytest.raises(ValueError):
        SearchPlan((QueryTemplate("x"),), (), {"queries": 1}, 30)
    with pytest.raises(ValueError):
        SearchPlan((QueryTemplate("x"),), (), {"queries": 1, "scrapes": 1}, 30, excerpt_chars=5000)


def test_evidence_ref_matches_evidence_store_put(tmp_path: Path):
    from scripts.company_enrichment.evidence import SourceRecord

    source = SourceRecord("https://a.example/p", NOW, "page", "serper", "URL: x\nbody",
                          "body", 30, "0")
    pure = evidence_ref(source)
    stored = EvidenceStore(tmp_path / "evidence").put(source)
    assert pure == stored
    assert isinstance(pure, EvidenceRef)


def test_bind_collect_writes_signal_dossier_and_collection_log(tmp_path: Path):
    repo = build_repo(tmp_path)
    factories = {"search": 0, "scrape": 0}

    def search_factory():
        factories["search"] += 1
        return FakeSearch(NEWS_RESULTS)

    def scrape_factory():
        factories["scrape"] += 1
        return FakeScrape(PAGES)

    collect = bind_collect(
        enrichment_id="news-product-launches", plan=PLAN, search_factory=search_factory,
        scrape_factory=scrape_factory, first_party_scrape_factory=scrape_factory,
        clock=lambda: NOW,
    )
    assert factories == {"search": 0, "scrape": 0}  # lazy: nothing built until first company
    dossier = collect(_request(repo))
    target = signal_dossier_path(repo, "news-product-launches", "saas-01")
    save_signal_dossier(target, dossier)
    assert load_signal_dossier(target) == dossier
    log = json.loads(collection_log_path(target).read_text(encoding="utf-8"))
    assert log["company_id"] == "saas-01" and log["enrichment_id"] == "news-product-launches"
    assert Decimal(log["paid_cost_usd"]) == Decimal("0.002")
    assert any(entry["step"] == "search" for entry in log["entries"])
    store = EvidenceStore(repo / "runs/company-enrichment/news-product-launches/evidence")
    assert store.journal.is_file()
    collect(_request(repo, "saas-02"))
    assert factories == {"search": 1, "scrape": 2}  # factories reused across companies
