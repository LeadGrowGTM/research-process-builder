"""Specs, search plans, ground-truth drafters, and CLI wrapper for the news and
competitor signal loops.

``scripts/company_enrichment_news_loop.py`` and
``scripts/company_enrichment_competitor_loop.py`` are thin shells over
``run_entrypoint``. Besides the generic ``--collect`` / ``--evaluate`` phases
from ``signal_loop.main`` they add ``--draft-ground-truth``, which reads the
collected signal dossiers and writes reviewer drafts to
``benchmarks/signals/<enrichment_id>/ground-truth-drafts/<company>.yaml``.
Drafts are marked ``TODO_HUMAN`` and never touch the sealed ``ground-truth``
directory.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import replace
from datetime import date
import json
import os
from pathlib import Path
import re
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlparse

import yaml

from .adapters.known_url_scrape import build_free_waterfall
from .packages import resolve_prompt_path
from .adapters.parallel import build_parallel_search
from .adapters.serper import build_serper_search
from .competitor_contracts import competitors_output_contract, normalize_name
from .competitor_evaluator import (
    COMPETITOR_ENRICHMENT_ID, COMPETITOR_WEIGHTS, ground_competitor_payload,
    score_competitors, validate_competitor_record,
)
from .contracts import CompanyDossier, canonical_json
from .news_contracts import news_output_contract
from .news_evaluator import (
    DEFAULT_RECENT_WINDOW_DAYS, NEWS_ENRICHMENT_ID, NEWS_WEIGHTS,
    ground_news_payload, score_news, validate_news_record,
)
from .signal_collection import (
    DATE_PATTERN, FallbackSearch, QueryTemplate, SearchPlan, bind_collect, company_facts,
    registrable_domain,
)
from .signal_evidence import load_signal_dossier, signal_dossier_path
from .signal_ground_truth import ALL_IDS, dataset_loader
from .signal_loop import SEARCH_PROVIDER_ENV, SignalSpec, main as signal_main

NEWS_PLAN = SearchPlan(
    queries=(
        QueryTemplate("{{domain}} news", "news", None),
        QueryTemplate('{{company_name}} "press release" OR "announces" OR "newsroom"',
                      "web", "qdr:y"),
        QueryTemplate('{{company_name}} launches OR "new feature" OR "introducing" OR '
                      '"now available"', "web", "qdr:y"),
    ),
    first_party_paths=("/blog", "/news", "/newsroom", "/press", "/changelog",
                       "/product-updates", "/whats-new"),
    caps={"queries": 3, "scrapes": 6},
    freshness_days=365,
    objective=(
        "Find dated news about {{company_name}} ({{domain}}) from the last 12 "
        "months: product launches, new features, funding, leadership, and "
        "partnership announcements. Prefer the company's own changelog, "
        "product-updates, newsroom, and blog pages plus dated press releases "
        "and wire coverage. Every result should carry a publish date."
    ),
)
COMPETITOR_PLAN = SearchPlan(
    queries=(
        QueryTemplate('{{company_name}} {{category}} alternatives OR competitors OR "vs" OR '
                      '"compared to"', "web", None),
        QueryTemplate("{{company_name}} alternatives", "web", None),
        QueryTemplate("{{company_name}} vs", "web", None),
    ),
    first_party_paths=("/competitors", "/compare", "/alternatives"),
    caps={"queries": 3, "scrapes": 6},
    freshness_days=180,
    objective=(
        "Find pages that name direct competitors and alternatives to "
        "{{company_name}} ({{domain}}), a {{category}} company: 'best "
        "alternatives' listicles, 'X vs Y' comparison pages, review-site "
        "category pages, and the company's own compare pages. Prefer pages "
        "that name several competing products."
    ),
)
_MONTH_INDEX = {name: index for index, name in enumerate((
    "jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}
_TITLE_LINE = re.compile(r"^Title:\s*(.+)$", re.MULTILINE)
_DATE_LINE = re.compile(r"^(?:Date|Detected date):\s*(.+)$", re.MULTILINE)
_COMPETITOR_CUE = re.compile(r"\b(vs\.?|versus|alternatives?|competitors?|compared?)\b", re.I)
_CANDIDATE_TOKEN = re.compile(r"\b([A-Z][A-Za-z0-9]{2,}(?: [A-Z][A-Za-z0-9]{2,})?)\b")
_DOMAIN_TOKEN = re.compile(r"\b((?:[a-z0-9-]+\.)+(?:com|io|ai|co|net|org|app|dev))\b", re.I)
_STOP_TOKENS = frozenset(
    "The And For With Best Top Alternatives Alternative Competitors Competitor Compare Compared "
    "Comparison Review Reviews Pricing Features Software Platform Tool Tools Free Guide Vs "
    "Versus What Which Why How When Who Your You Our Their This That These Those From Into "
    "Read More See Get Try Now New Learn About Home Blog News Press Product Products Solutions "
    "Google Facebook LinkedIn Twitter YouTube Instagram Reddit Capterra GetApp SourceForge Gartner "
    "TrustRadius Medium Forbes Inc Ltd LLC Corp Company Companies Business Businesses Marketing "
    "Sales Data Analytics Reporting Report Reports Agency Agencies Client Clients Team Teams "
    "Small Enterprise Cloud Online Digital Web Website Sites Site App Apps January February March "
    "April May June July August September October November December Mon Tue Wed Thu Fri Sat Sun "
    "Title Date Source Snippet Query Mode Detected".split()
)
_STOP_DOMAINS = frozenset((
    "google.com", "linkedin.com", "g2.com", "capterra.com", "getapp.com", "youtube.com",
    "facebook.com", "twitter.com", "x.com", "reddit.com", "medium.com", "sourceforge.net",
    "trustradius.com", "gartner.com", "prnewswire.com", "businesswire.com", "globenewswire.com",
    "crunchbase.com", "wikipedia.org", "softwareadvice.com", "producthunt.com",
))
_DRAFT_HEADER = (
    "# DRAFT ground truth - TODO_HUMAN: review every entry, then move this file to\n"
    "# ../ground-truth/ only after a human seals it. Generated from the signal dossier;\n"
    "# nothing here is validated. Delete candidates that are not real, fix dates and\n"
    "# kinds, keep evidence_ids pointing at the Evidence that proves the entry.\n"
)


_SEARCH_BUILDERS = {"serper": build_serper_search, "parallel": build_parallel_search}
DEFAULT_SEARCH_PROVIDERS = "serper,parallel"


def _search_factory() -> FallbackSearch:
    """Ordered providers from ``SIGNAL_SEARCH_PROVIDER`` (default serper,parallel).

    Read lazily at first collect (the factory is invoked by ``bind_collect``),
    so ``--search-provider`` on the loop CLI takes effect without re-wiring.
    """
    raw = os.environ.get(SEARCH_PROVIDER_ENV, "").strip() or DEFAULT_SEARCH_PROVIDERS
    names = tuple(dict.fromkeys(name.strip() for name in raw.split(",") if name.strip()))
    unknown = sorted(set(names) - set(_SEARCH_BUILDERS))
    if not names or unknown:
        raise ValueError(
            f"{SEARCH_PROVIDER_ENV} must name providers from "
            f"{sorted(_SEARCH_BUILDERS)}: {raw!r}"
        )
    return FallbackSearch(tuple((name, _SEARCH_BUILDERS[name]) for name in names))


def build_news_spec() -> SignalSpec:
    return SignalSpec(
        enrichment_id=NEWS_ENRICHMENT_ID, fields=("news", "launches"),
        benchmark_dir=Path("benchmarks/signals") / NEWS_ENRICHMENT_ID,
        output_contract=news_output_contract,
        load_ground_truth=dataset_loader(NEWS_ENRICHMENT_ID, NEWS_WEIGHTS, validate_news_record),
        score=score_news,
        postprocess=ground_news_payload,
        prompt_path=Path("prompts/company-enrichment") / f"{NEWS_ENRICHMENT_ID}.md",
        weights=NEWS_WEIGHTS,
        collect=bind_collect(
            enrichment_id=NEWS_ENRICHMENT_ID, plan=NEWS_PLAN, search_factory=_search_factory,
            scrape_factory=build_free_waterfall,
            first_party_scrape_factory=lambda: build_free_waterfall(max_level=1),
        ),
    )


def build_competitor_spec() -> SignalSpec:
    return SignalSpec(
        enrichment_id=COMPETITOR_ENRICHMENT_ID, fields=("competitors",),
        benchmark_dir=Path("benchmarks/signals") / COMPETITOR_ENRICHMENT_ID,
        output_contract=competitors_output_contract,
        load_ground_truth=dataset_loader(
            COMPETITOR_ENRICHMENT_ID, COMPETITOR_WEIGHTS, validate_competitor_record,
        ),
        score=score_competitors,
        postprocess=ground_competitor_payload,
        prompt_path=Path("prompts/company-enrichment") / f"{COMPETITOR_ENRICHMENT_ID}.md",
        weights=COMPETITOR_WEIGHTS,
        collect=bind_collect(
            enrichment_id=COMPETITOR_ENRICHMENT_ID, plan=COMPETITOR_PLAN,
            search_factory=_search_factory, scrape_factory=build_free_waterfall,
            first_party_scrape_factory=lambda: build_free_waterfall(max_level=1),
        ),
    )


def parse_date_text(text: str) -> str | None:
    """``YYYY-MM-DD`` / ``YYYY-MM`` from an ISO, 'Mar 5, 2024', '5 March 2024', or
    'March 2024' string; ``None`` for relative or missing dates."""
    match = DATE_PATTERN.search(text or "")
    if match is None:
        return None
    iso, month_first, day_first, month_year = match.groups()
    if iso:
        try:
            return date.fromisoformat(iso).isoformat()
        except ValueError:
            return None
    raw = month_first or day_first or month_year
    parts = re.findall(r"[A-Za-z]+|\d+", raw)
    month = next((_MONTH_INDEX[part[:3].lower()] for part in parts
                  if part[:3].lower() in _MONTH_INDEX), None)
    numbers = [int(part) for part in parts if part.isdigit()]
    if month is None or not numbers:
        return None
    year = next((number for number in numbers if number >= 1000), None)
    if year is None:
        return None
    days = [number for number in numbers if number < 1000]
    if days and 1 <= days[0] <= 31:
        try:
            return date(year, month, days[0]).isoformat()
        except ValueError:
            return None
    return f"{year:04d}-{month:02d}"


def _dump(value: Mapping[str, Any]) -> str:
    return yaml.safe_dump(json.loads(canonical_json(value)), allow_unicode=True,
                          sort_keys=False, default_flow_style=False)


def draft_news_ground_truth(
    company_id: str, dossier: CompanyDossier, facts: Mapping[str, str], *, as_of: date,
) -> str:
    """Draft news ground truth: one candidate event per dated Evidence item."""
    events: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in dossier.evidence:
        text = item.excerpt
        if text.startswith("Query:"):
            continue
        date_match = _DATE_LINE.search(text)
        if date_match is None:
            continue
        parsed = parse_date_text(date_match.group(1))
        title_match = _TITLE_LINE.search(text)
        title = title_match.group(1).strip() if title_match else text.splitlines()[0][:120]
        source_domain = registrable_domain(urlparse(item.url).netloc)
        key = (source_domain, parsed or date_match.group(1).strip(), title.lower())
        if key in seen:
            continue
        seen.add(key)
        events.append({
            "date": parsed or f"TODO_HUMAN ({date_match.group(1).strip()})",
            "headline_aliases": [title],
            "source_domain": source_domain,
            "kind": "TODO_HUMAN",
            "event_type": "TODO_HUMAN",
            "evidence_ids": [item.evidence_id],
        })
    events.sort(key=lambda event: str(event["date"]), reverse=True)
    body = {
        "company_id": company_id, "as_of": as_of.isoformat(),
        "recent_window_days": DEFAULT_RECENT_WINDOW_DAYS, "events": events,
    }
    return _DRAFT_HEADER + f"# subject: {facts.get('company_name')} ({facts.get('domain')})\n" + _dump(body)


def draft_competitor_ground_truth(
    company_id: str, dossier: CompanyDossier, facts: Mapping[str, str], *, as_of: date,
) -> str:
    """Draft competitor ground truth: candidate names/domains from cue-bearing Evidence."""
    subject_key = normalize_name(facts.get("company_name", ""))
    subject_domain = facts.get("domain", "")
    names: Counter[str] = Counter()
    display: dict[str, str] = {}
    domains: Counter[str] = Counter()
    evidence_for: dict[str, list[str]] = {}
    for item in dossier.evidence:
        text = item.excerpt
        if text.startswith("Query:") or not _COMPETITOR_CUE.search(text):
            continue
        for token in _CANDIDATE_TOKEN.findall(text):
            words = [word for word in token.split() if word not in _STOP_TOKENS]
            if not words:
                continue
            token = " ".join(words)
            key = normalize_name(token)
            if not key or key == subject_key or key in subject_domain.replace(".", ""):
                continue
            names[key] += 1
            display.setdefault(key, token)
            evidence_for.setdefault(key, []).append(item.evidence_id)
        for domain in _DOMAIN_TOKEN.findall(text):
            host = registrable_domain(domain)
            if host == subject_domain or host in _STOP_DOMAINS or host == "example.com":
                continue
            domains[host] += 1
            evidence_for.setdefault(host, []).append(item.evidence_id)
    candidates = []
    for key, count in names.most_common(25):
        candidates.append({
            "name": f"TODO_HUMAN {display[key]}", "aliases": [], "domain": None,
        })
    for host, count in domains.most_common(15):
        candidates.append({"name": f"TODO_HUMAN {host}", "aliases": [], "domain": host})
    body = {
        "company_id": company_id, "named": candidates, "inferred": [],
        "evidence_ids": [item.evidence_id for item in dossier.evidence[:1]] or ["TODO_HUMAN"],
    }
    notes = (
        f"# subject: {facts.get('company_name')} ({subject_domain}); as_of {as_of.isoformat()}\n"
        "# every 'named' entry is a candidate token; strip the TODO_HUMAN prefix to keep,\n"
        "# move to 'inferred' when no Evidence compares it with the subject, or delete.\n"
        "# candidate evidence: " + json.dumps({key: sorted(set(ids))[:4]
                                               for key, ids in evidence_for.items()})[:1500] + "\n"
    )
    return _DRAFT_HEADER + notes + _dump(body)


Drafter = Callable[..., str]


def write_ground_truth_drafts(
    spec: SignalSpec, drafter: Drafter, *, repo_root: Path, company_ids: Sequence[str],
    overwrite: bool, as_of: date,
) -> dict[str, Any]:
    unknown = sorted(set(company_ids) - set(ALL_IDS))
    if unknown:
        raise ValueError(f"unknown company IDs: {unknown}")
    draft_dir = repo_root / spec.benchmark_dir / "ground-truth-drafts"
    written: list[str] = []
    skipped: list[str] = []
    missing: list[str] = []
    for company_id in company_ids:
        signal_path = signal_dossier_path(repo_root, spec.enrichment_id, company_id)
        if not signal_path.is_file():
            missing.append(company_id)
            continue
        target = draft_dir / f"{company_id}.yaml"
        if target.exists() and not overwrite:
            skipped.append(company_id)
            continue
        dossier = load_signal_dossier(signal_path)
        facts = company_facts(repo_root, company_id, dossier)
        text = drafter(company_id, dossier, facts, as_of=as_of)
        draft_dir.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + f".{os.getpid()}.tmp")
        temporary.write_text(text, encoding="utf-8", newline="\n")
        os.replace(temporary, target)
        written.append(company_id)
    return {
        "approval": False, "draft_dir": str(draft_dir), "enrichment_id": spec.enrichment_id,
        "missing_signal_dossiers": missing, "phase": "draft-ground-truth",
        "sealed_ground_truth_untouched": True, "skipped_existing": skipped, "written": written,
    }


def run_entrypoint(
    spec: SignalSpec, drafter: Drafter, argv: Sequence[str] | None = None, *,
    repo_root: Path | None = None, **loop_kwargs: Any,
) -> int:
    args = list(argv) if argv is not None else None
    if args is None:
        import sys

        args = sys.argv[1:]
    root = Path(repo_root or Path(__file__).resolve().parents[2])
    if "--draft-ground-truth" not in args:
        spec = replace(
            spec, prompt_path=resolve_prompt_path(spec.enrichment_id, root),
        )
        return signal_main(spec, args, repo_root=root, **loop_kwargs)
    parser = argparse.ArgumentParser(
        description=f"{spec.enrichment_id} ground-truth drafts",
        usage="%(prog)s --draft-ground-truth [--company ID ...] [--overwrite] [--as-of YYYY-MM-DD]",
    )
    parser.add_argument("--draft-ground-truth", action="store_true", required=True)
    parser.add_argument("--company", action="append", default=None)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--as-of", default=None)
    parsed = parser.parse_args(args)
    try:
        as_of = date.fromisoformat(parsed.as_of) if parsed.as_of else date.today()
        result = write_ground_truth_drafts(
            spec, drafter, repo_root=root, company_ids=tuple(parsed.company or ALL_IDS),
            overwrite=parsed.overwrite, as_of=as_of,
        )
    except ValueError as error:
        print(canonical_json({"approval": False, "error": type(error).__name__,
                              "message": str(error)}))
        return 2
    print(canonical_json(result))
    return 0
