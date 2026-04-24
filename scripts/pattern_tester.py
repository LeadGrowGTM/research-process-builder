"""
Serper Pattern Tester

Tests search query patterns against real companies via Serper.dev API.
Scores results automatically. Stores everything for analysis.

Usage:
    py scripts/pattern_tester.py                          # run all patterns from default config
    py scripts/pattern_tester.py --dry-run                # preview queries only
    py scripts/pattern_tester.py --category tech_stack    # single category
    py scripts/pattern_tester.py --company Clay           # single company
    py scripts/pattern_tester.py --report                 # classification table
    py scripts/pattern_tester.py --generate-doc           # generate serper-patterns.md
    py scripts/pattern_tester.py --sources                # generate source-analysis.md (domain frequency)
    py scripts/pattern_tester.py --migrate                # backfill all_domains on old entries

    # Custom config/output:
    py scripts/pattern_tester.py --config dns_patterns.json --output ../searches/raw-results-dns.json
    py scripts/pattern_tester.py --config combo_patterns.json --output ../searches/raw-results-combo.json
"""

import json
import time
import sys
import os
import re
import hashlib
import argparse
from pathlib import Path
from datetime import datetime

# Add shared scripts to path for serper import
_script_dir = Path(__file__).resolve().parent

from dotenv import load_dotenv
load_dotenv(_script_dir.parent / ".env")
load_dotenv(Path.home() / ".env", override=False)

_shared = os.environ.get("SHARED_SCRIPTS_PATH", str(_script_dir))
sys.path.insert(0, _shared)

try:
    import serper_search
except ImportError:
    print("ERROR: Could not import serper_search.")
    print("Set SHARED_SCRIPTS_PATH env var to the directory containing serper_search.py")
    sys.exit(1)

# Paths
SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_FILE = SCRIPT_DIR / "patterns_config.json"
RESULTS_FILE = SCRIPT_DIR.parent / "searches" / "raw-results.json"
DOC_FILE = SCRIPT_DIR.parent / "searches" / "serper-patterns.md"


# ---------------------------------------------------------------------------
# Pattern Expander
# ---------------------------------------------------------------------------

class PatternExpander:
    """Expands {{variable}} templates with company data."""

    def expand(self, template: str, company: dict) -> str:
        query = template
        # For templates with bare {{company_name}} and disambiguation needed,
        # prepend category to reduce noise
        if company.get("disambiguation_needed"):
            # Only add category if template uses bare company_name without category already
            if "{{company_name}}" in query and "{{category}}" not in query:
                # Check if it's a site: query — don't disambiguate those
                if not query.strip().startswith("site:") and "site:" not in query.split("{{company_name}}")[0]:
                    query = query.replace("{{company_name}}", "{{company_name}} {{category}}")

        query = query.replace("{{company_name}}", company["company_name"])
        query = query.replace("{{domain}}", company["domain"])
        query = query.replace("{{category}}", company["category"])
        query = query.replace("{{current_year}}", str(datetime.now().year))
        query = query.replace("{{role_title}}", company.get("role_title", "Software Engineer"))
        return query


# ---------------------------------------------------------------------------
# Auto Scorer
# ---------------------------------------------------------------------------

CATEGORY_KEYWORDS = {
    "company_profile": ["company", "overview", "founded", "employees", "headquarters", "about", "description"],
    "funding_financial": ["funding", "raised", "series", "investors", "valuation", "round", "capital", "revenue", "arr"],
    "hiring_signals": ["hiring", "careers", "jobs", "open roles", "greenhouse", "lever", "ashby", "recruiting"],
    "competitor_identification": ["competitor", "alternative", "vs", "compared", "similar", "comparison"],
    "reviews_sentiment": ["review", "rating", "opinion", "pros", "cons", "honest", "recommend"],
    "news_press": ["news", "announced", "launch", "acquisition", "partnership", "expansion"],
    "press_releases": ["announce", "press release", "newsroom", "blog", "businesswire", "prnewswire"],
    "social_media": ["twitter", "linkedin", "instagram", "facebook", "x.com", "followers", "social"],
    "community_platforms": ["discord", "slack", "community", "forum", "members"],
    "growth_marketing": ["blog", "pricing", "newsletter", "demo", "free trial", "marketing"],
    "tech_stack": ["built with", "powered by", "stack", "framework", "technology", "api", "react", "python", "node"],
    "leadership_people": ["ceo", "founder", "cto", "vp", "leadership", "team", "executive", "co-founder"],
    "customer_case_studies": ["case study", "customer", "client", "success story", "testimonial", "trusted by"],
    "pricing_intelligence": ["pricing", "cost", "plan", "tier", "free", "enterprise", "per seat", "per month"],
    "partnerships_integrations": ["partner", "integration", "integrates", "marketplace", "plugin", "api"],
    "content_blog": ["blog", "article", "post", "guide", "resource", "whitepaper"],
    "newsletter_email": ["newsletter", "subscribe", "email", "substack", "beehiiv", "digest"],
    "events_conferences": ["event", "conference", "webinar", "podcast", "meetup", "summit", "hackathon"],
    "customer_complaints": ["complaint", "problem", "issue", "negative", "disappointed", "frustrated", "drawback"],
    "awards_recognition": ["award", "recognition", "best", "top", "winner", "ranked", "fastest growing"],
    # People-finding categories
    "founders_ceo": ["founder", "co-founder", "ceo", "president", "owner", "chief executive", "founded", "started"],
    "c_suite_technical": ["cto", "chief technology", "cpo", "chief product", "ciso", "cio", "chief information"],
    "c_suite_commercial": ["cmo", "chief marketing", "cro", "chief revenue", "cfo", "chief financial", "coo", "chief operating"],
    "vp_sales_revenue": ["vp", "vice president", "sales", "revenue", "business development", "partnerships"],
    "vp_marketing_growth": ["vp", "vice president", "marketing", "growth", "demand gen", "brand"],
    "vp_engineering_product": ["vp", "vice president", "engineering", "product", "design", "vpe"],
    "director_sales_bd": ["director", "sales", "business development", "partnerships", "account", "revenue"],
    "director_marketing_content": ["director", "marketing", "content", "demand", "brand", "growth"],
    "director_engineering_product": ["director", "engineering", "product", "design", "platform"],
    "head_of_department": ["head of", "growth", "customer success", "operations", "revenue", "partnerships"],
    "sales_ops_leads": ["sdr", "bdr", "sales manager", "revops", "revenue operations", "sales ops", "account executive"],
    "hr_people_talent": ["people", "talent", "hr", "human resources", "recruiting", "culture"],
    "finance_legal_ops": ["finance", "controller", "operations", "legal", "counsel", "revops", "strategy"],
    "technical_leads": ["staff engineer", "principal", "tech lead", "architect", "engineering manager", "senior engineer"],
    "people_via_media": ["speaker", "podcast", "interview", "keynote", "conference", "said", "according to", "appointed"],
    "people_via_platforms": ["rocketreach", "zoominfo", "apollo", "wellfound", "theorg", "profile", "team"],
    "people_via_content": ["author", "wrote", "published", "blog", "article", "medium", "substack", "maker"],
}


class AutoScorer:
    """Scores search results without LLM calls."""

    def score(self, raw_result: dict, category_id: str, company: dict) -> dict:
        if "error" in raw_result:
            return {"quality": 0, "result_count": 0, "relevance_ratio": 0,
                    "keyword_hits": 0, "has_knowledge_graph": False, "top_domains": [],
                    "all_domains": [], "notes": "API error"}

        organic = raw_result.get("organic", [])
        knowledge_graph = raw_result.get("knowledgeGraph", {})

        if not organic:
            return {"quality": 1, "result_count": 0, "relevance_ratio": 0,
                    "keyword_hits": 0, "has_knowledge_graph": bool(knowledge_graph),
                    "top_domains": [], "all_domains": [], "notes": "zero organic results"}

        # Relevance: how many results mention the company?
        company_lower = company["company_name"].lower()
        company_mentions = 0
        for r in organic:
            text = (r.get("title", "") + " " + r.get("snippet", "")).lower()
            if company_lower in text:
                company_mentions += 1
        relevance_ratio = company_mentions / len(organic) if organic else 0

        # Keyword hits
        keywords = CATEGORY_KEYWORDS.get(category_id, [])
        keyword_hits = 0
        for r in organic[:5]:  # Top 5 only
            text = (r.get("title", "") + " " + r.get("snippet", "")).lower()
            for kw in keywords:
                if kw.lower() in text:
                    keyword_hits += 1
                    break  # One hit per result max

        # Knowledge graph bonus
        kg_bonus = 0.5 if knowledge_graph else 0

        # Compute quality (1-5)
        # Relevance (0-2.5) + keyword density (0-2) + KG bonus (0-0.5)
        relevance_score = relevance_ratio * 2.5
        keyword_score = min(keyword_hits, 4) / 4 * 2
        raw_score = relevance_score + keyword_score + kg_bonus
        quality = min(5, max(1, round(raw_score)))

        # Extract top domains (top 3 for backward compat)
        top_domains = []
        for r in organic[:3]:
            link = r.get("link", "")
            try:
                domain = re.search(r"https?://(?:www\.)?([^/]+)", link)
                if domain:
                    top_domains.append(domain.group(1))
            except Exception:
                pass

        # Extract ALL domains (all organic results) for source analysis
        all_domains = []
        for r in organic:
            link = r.get("link", "")
            try:
                domain = re.search(r"https?://(?:www\.)?([^/]+)", link)
                if domain:
                    all_domains.append(domain.group(1))
            except Exception:
                pass

        return {
            "quality": quality,
            "result_count": len(organic),
            "relevance_ratio": round(relevance_ratio, 2),
            "keyword_hits": keyword_hits,
            "has_knowledge_graph": bool(knowledge_graph),
            "top_domains": top_domains,
            "all_domains": all_domains,
        }


# ---------------------------------------------------------------------------
# Result Store
# ---------------------------------------------------------------------------

class ResultStore:
    """Idempotent storage with query hash dedup."""

    def __init__(self, filepath: Path):
        self.filepath = filepath
        self._results = []
        self._index = set()
        self._load()

    def _load(self):
        if self.filepath.exists():
            with open(self.filepath, "r", encoding="utf-8") as f:
                self._results = json.load(f)
            self._index = {r["hash"] for r in self._results}

    def _flush(self):
        self.filepath.parent.mkdir(parents=True, exist_ok=True)
        with open(self.filepath, "w", encoding="utf-8") as f:
            json.dump(self._results, f, indent=2, ensure_ascii=False)

    @staticmethod
    def query_hash(query: str) -> str:
        return hashlib.md5(query.encode()).hexdigest()[:12]

    def already_tested(self, query: str) -> bool:
        return self.query_hash(query) in self._index

    def save(self, category_id, variant_id, company_name, query, raw_result, scores):
        h = self.query_hash(query)
        # Pop all_domains from scores (it's raw data, not a score dimension)
        all_domains = scores.pop("all_domains", [])
        entry = {
            "hash": h,
            "category_id": category_id,
            "variant_id": variant_id,
            "company": company_name,
            "query": query,
            "timestamp": datetime.utcnow().isoformat(),
            "scores": scores,
            "all_domains": all_domains,
            "result_count": len(raw_result.get("organic", [])),
            "top_results": [
                {"title": r.get("title", ""), "link": r.get("link", ""), "snippet": r.get("snippet", "")[:200]}
                for r in raw_result.get("organic", [])[:3]
            ],
            "has_knowledge_graph": bool(raw_result.get("knowledgeGraph")),
        }
        self._results.append(entry)
        self._index.add(h)
        # Flush every 10 results for safety
        if len(self._results) % 10 == 0:
            self._flush()

    def flush_final(self):
        self._flush()

    def get_all(self):
        return self._results


# ---------------------------------------------------------------------------
# Report Generator
# ---------------------------------------------------------------------------

def generate_report(results: list):
    """Print classification table from stored results."""
    # Group by category + variant
    patterns = {}
    for r in results:
        key = (r["category_id"], r["variant_id"])
        if key not in patterns:
            patterns[key] = {}
        patterns[key][r["company"]] = r["scores"]["quality"]

    # Group by category for display
    categories = {}
    for (cat_id, var_id), scores in patterns.items():
        if cat_id not in categories:
            categories[cat_id] = []
        avg_q = sum(scores.values()) / len(scores) if scores else 0
        # Consistency: how many companies scored Q4+
        q4_plus = sum(1 for v in scores.values() if v >= 4)
        consistency = q4_plus / len(scores) * 5 if scores else 0

        if avg_q >= 4.0 and consistency >= 4.0:
            classification = "PRIMARY"
        elif avg_q >= 4.0 and consistency >= 3.0:
            classification = "ENRICHMENT"
        elif avg_q >= 3.0:
            classification = "FALLBACK"
        else:
            classification = "KILL"

        categories[cat_id].append({
            "variant_id": var_id,
            "scores": scores,
            "avg_q": round(avg_q, 1),
            "consistency": round(consistency, 1),
            "classification": classification,
        })

    # Sort each category by avg_q descending
    for cat_id in categories:
        categories[cat_id].sort(key=lambda x: x["avg_q"], reverse=True)

    # Print
    companies = sorted(set(r["company"] for r in results))
    header = f"{'Variant':<25} | " + " | ".join(f"{c:<8}" for c in companies) + f" | {'Avg Q':<5} | {'C':<3} | {'Class':<11}"
    sep = "-" * len(header)

    stats = {"PRIMARY": 0, "ENRICHMENT": 0, "FALLBACK": 0, "KILL": 0}

    for cat_id in sorted(categories.keys()):
        print(f"\n{'='*60}")
        print(f"CATEGORY: {cat_id}")
        print(sep)
        print(header)
        print(sep)
        for p in categories[cat_id]:
            scores_str = " | ".join(f"Q{p['scores'].get(c, '?'):<7}" for c in companies)
            print(f"{p['variant_id']:<25} | {scores_str} | {p['avg_q']:<5} | {p['consistency']:<3} | {p['classification']:<11}")
            stats[p["classification"]] += 1
        print(sep)

    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"  PRIMARY:    {stats['PRIMARY']}")
    print(f"  ENRICHMENT: {stats['ENRICHMENT']}")
    print(f"  FALLBACK:   {stats['FALLBACK']}")
    print(f"  KILL:       {stats['KILL']}")
    print(f"  TOTAL:      {sum(stats.values())}")


# ---------------------------------------------------------------------------
# Doc Generator
# ---------------------------------------------------------------------------

def generate_doc(results: list, config: dict):
    """Generate serper-patterns.md from results."""
    patterns = {}
    for r in results:
        key = (r["category_id"], r["variant_id"])
        if key not in patterns:
            patterns[key] = {"results": {}, "example_results": []}
        patterns[key]["results"][r["company"]] = r["scores"]["quality"]
        if r.get("top_results"):
            patterns[key]["example_results"] = r["top_results"]

    # Build lookup for templates
    template_lookup = {}
    cat_lookup = {}
    for cat in config["categories"]:
        cat_lookup[cat["id"]] = cat
        for var in cat["variants"]:
            template_lookup[(cat["id"], var["id"])] = var["template"]

    lines = []
    lines.append("# serper search pattern reference")
    lines.append("")
    lines.append(f"**validated:** {datetime.now().strftime('%Y-%m-%d')}")
    lines.append(f"**queries tested:** {len(results)}")
    lines.append(f"**cost of validation:** ~${len(results) * 0.0001:.2f}")
    lines.append("**cost per search:** $0.0001 via Serper vs ~$0.50 native Clay enrichment = **5000x savings**")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## how to use this in Clay")
    lines.append("")
    lines.append("each pattern below can be used as a Clay HTTP API column:")
    lines.append("")
    lines.append("1. add column > HTTP API")
    lines.append("2. method: `POST`")
    lines.append("3. URL: `https://google.serper.dev/search`")
    lines.append("4. headers: `X-API-KEY: {{SERPER_API_KEY}}` and `Content-Type: application/json`")
    lines.append('5. body: `{"q": "[pattern with your variables]", "gl": "us", "hl": "en", "num": 10}`')
    lines.append("6. map response: `{{http_response.organic[0].title}}` or `{{http_response.organic[0].link}}`")
    lines.append("")
    lines.append("---")
    lines.append("")

    # Get dominant source data for each category
    dominant_sources = get_dominant_sources()

    # Group by category, then by classification
    for cat in config["categories"]:
        cat_id = cat["id"]
        cat_patterns = []
        for (cid, vid), data in patterns.items():
            if cid != cat_id:
                continue
            scores = data["results"]
            avg_q = sum(scores.values()) / len(scores) if scores else 0
            q4_plus = sum(1 for v in scores.values() if v >= 4)
            consistency = q4_plus / len(scores) * 5 if scores else 0

            if avg_q >= 4.0 and consistency >= 4.0:
                classification = "PRIMARY"
            elif avg_q >= 4.0 and consistency >= 3.0:
                classification = "ENRICHMENT"
            elif avg_q >= 3.0:
                classification = "FALLBACK"
            else:
                classification = "KILL"

            cat_patterns.append({
                "variant_id": vid,
                "template": template_lookup.get((cid, vid), "?"),
                "avg_q": round(avg_q, 1),
                "consistency": round(consistency, 1),
                "classification": classification,
                "scores": scores,
                "examples": data.get("example_results", []),
            })

        cat_patterns.sort(key=lambda x: x["avg_q"], reverse=True)

        lines.append(f"## {cat['name']}")
        if cat.get("clay_enrichment_replaced"):
            lines.append(f"**replaces:** {cat['clay_enrichment_replaced']}")
        dom = dominant_sources.get(cat_id)
        if dom:
            lines.append(f"**dominant sources:** {dom}")
        lines.append("")

        primary = [p for p in cat_patterns if p["classification"] == "PRIMARY"]
        enrichment = [p for p in cat_patterns if p["classification"] == "ENRICHMENT"]
        fallback = [p for p in cat_patterns if p["classification"] == "FALLBACK"]
        kill = [p for p in cat_patterns if p["classification"] == "KILL"]

        if primary:
            lines.append("### PRIMARY (Q4+ / C4+)")
            lines.append("")
            for p in primary:
                lines.append(f"**`{p['template']}`**")
                scores_str = ", ".join(f"{c} Q{q}" for c, q in sorted(p["scores"].items()))
                lines.append(f"- scores: {scores_str} | avg Q{p['avg_q']} C{p['consistency']}")
                if p["examples"]:
                    lines.append(f"- example: {p['examples'][0].get('title', 'N/A')}")
                lines.append("")

        if enrichment:
            lines.append("### ENRICHMENT (Q4+ / C3+)")
            lines.append("")
            for p in enrichment:
                lines.append(f"**`{p['template']}`**")
                scores_str = ", ".join(f"{c} Q{q}" for c, q in sorted(p["scores"].items()))
                lines.append(f"- scores: {scores_str} | avg Q{p['avg_q']} C{p['consistency']}")
                lines.append("")

        if fallback:
            lines.append("### FALLBACK (Q3+)")
            lines.append("")
            for p in fallback:
                lines.append(f"`{p['template']}` — avg Q{p['avg_q']}")
            lines.append("")

        if kill:
            lines.append("### KILL")
            lines.append("")
            for p in kill:
                lines.append(f"~~`{p['template']}`~~ — avg Q{p['avg_q']}")
            lines.append("")

        lines.append("---")
        lines.append("")

    DOC_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(DOC_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"Generated: {DOC_FILE}")


# ---------------------------------------------------------------------------
# Source Analyzer
# ---------------------------------------------------------------------------

RESULTS_FILES = [
    SCRIPT_DIR.parent / "searches" / "raw-results.json",
    SCRIPT_DIR.parent / "searches" / "raw-results-combo.json",
    SCRIPT_DIR.parent / "searches" / "raw-results-dns.json",
    SCRIPT_DIR.parent / "searches" / "raw-results-people.json",
    SCRIPT_DIR.parent / "searches" / "raw-results-people-combo.json",
    SCRIPT_DIR.parent / "searches" / "raw-results-master.json",
    SCRIPT_DIR.parent / "searches" / "raw-results-master-combo.json",
]

SOURCE_ANALYSIS_FILE = SCRIPT_DIR.parent / "searches" / "source-analysis.md"


def _extract_domain(link: str):
    """Extract domain from a URL, stripping www."""
    try:
        m = re.search(r"https?://(?:www\.)?([^/]+)", link)
        return m.group(1) if m else None
    except Exception:
        return None


def migrate_all_domains():
    """Backfill all_domains field on existing entries from top_results links."""
    total_migrated = 0
    for fpath in RESULTS_FILES:
        if not fpath.exists():
            continue
        with open(fpath, "r", encoding="utf-8") as f:
            results = json.load(f)

        changed = 0
        for entry in results:
            if "all_domains" in entry:
                continue
            domains = []
            for r in entry.get("top_results", []):
                d = _extract_domain(r.get("link", ""))
                if d:
                    domains.append(d)
            entry["all_domains"] = domains
            changed += 1

        if changed:
            with open(fpath, "w", encoding="utf-8") as f:
                json.dump(results, f, indent=2, ensure_ascii=False)
            print(f"Migrated {changed} entries in {fpath.name}")
            total_migrated += changed
        else:
            print(f"No migration needed for {fpath.name}")

    print(f"\nTotal migrated: {total_migrated}")


def analyze_sources(min_quality: int = 3) -> dict:
    """Analyze domain frequency across all result files, filtered to Q{min_quality}+.

    Returns: {category_id: {"total": N, "domains": {domain: count}}}
    """
    all_results = []
    for fpath in RESULTS_FILES:
        if not fpath.exists():
            continue
        with open(fpath, "r", encoding="utf-8") as f:
            all_results.extend(json.load(f))

    # Group by category, filter to min quality
    categories = {}
    for r in all_results:
        q = r.get("scores", {}).get("quality", 0)
        if q < min_quality:
            continue
        cat = r.get("category_id", "unknown")
        if cat not in categories:
            categories[cat] = {"total": 0, "domains": {}}
        categories[cat]["total"] += 1

        domains = r.get("all_domains", r.get("scores", {}).get("top_domains", []))
        seen = set()
        for d in domains:
            if d not in seen:
                categories[cat]["domains"][d] = categories[cat]["domains"].get(d, 0) + 1
                seen.add(d)

    return categories


def generate_source_analysis():
    """Generate searches/source-analysis.md from all result files."""
    categories = analyze_sources(min_quality=3)

    if not categories:
        print("No Q3+ results found across any result files.")
        return

    total_entries = sum(c["total"] for c in categories.values())

    lines = []
    lines.append("# source analysis")
    lines.append("")
    lines.append(f"**generated:** {datetime.now().strftime('%Y-%m-%d')}")
    lines.append(f"**entries analyzed:** {total_entries} (Q3+ only)")
    lines.append(f"**categories:** {len(categories)}")
    lines.append(f"**files:** {', '.join(f.name for f in RESULTS_FILES if f.exists())}")
    lines.append("")
    lines.append("---")
    lines.append("")

    for cat_id in sorted(categories.keys()):
        data = categories[cat_id]
        total = data["total"]
        if total == 0:
            continue

        # Sort domains by frequency descending
        sorted_domains = sorted(data["domains"].items(), key=lambda x: x[1], reverse=True)

        lines.append(f"## {cat_id}")
        lines.append(f"**Q3+ results analyzed:** {total}")
        lines.append("")
        lines.append("| Domain | Frequency | Tier |")
        lines.append("|--------|-----------|------|")

        primary_domains = []
        for domain, count in sorted_domains[:15]:
            pct = count / total * 100
            if pct >= 60:
                tier = "PRIMARY"
                primary_domains.append(domain)
            elif pct >= 30:
                tier = "SECONDARY"
            else:
                tier = "occasional"
            lines.append(f"| {domain} | {pct:.0f}% ({count}/{total}) | {tier} |")

        lines.append("")
        if primary_domains:
            site_suggestions = ", ".join(f"`site:{d}`" for d in primary_domains)
            lines.append(f"**recommendation:** Include {site_suggestions} as primary patterns for this category.")
        else:
            lines.append("**recommendation:** No single source dominates. Use broad queries.")
        lines.append("")
        lines.append("---")
        lines.append("")

    SOURCE_ANALYSIS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(SOURCE_ANALYSIS_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"Generated: {SOURCE_ANALYSIS_FILE}")


def get_dominant_sources(min_quality: int = 3) -> dict:
    """Returns {category_id: "domain1 (X%), domain2 (Y%)"} for generate-doc integration."""
    categories = analyze_sources(min_quality)
    result = {}
    for cat_id, data in categories.items():
        total = data["total"]
        if total == 0:
            continue
        sorted_domains = sorted(data["domains"].items(), key=lambda x: x[1], reverse=True)
        top = []
        for domain, count in sorted_domains[:5]:
            pct = count / total * 100
            if pct >= 20:
                top.append(f"{domain} ({pct:.0f}%)")
        if top:
            result[cat_id] = ", ".join(top)
    return result


# ---------------------------------------------------------------------------
# Main Runner
# ---------------------------------------------------------------------------

def run(args):
    config_path = SCRIPT_DIR / (args.config if args.config else "patterns_config.json")
    results_path = Path(args.output).resolve() if args.output else RESULTS_FILE

    # Handle relative output paths from script dir
    if args.output and not Path(args.output).is_absolute():
        results_path = (SCRIPT_DIR / args.output).resolve()

    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    if args.report:
        store = ResultStore(results_path)
        results = store.get_all()
        if not results:
            print(f"No results found in {results_path}. Run tests first.")
            return
        generate_report(results)
        return

    if args.generate_doc:
        store = ResultStore(results_path)
        results = store.get_all()
        if not results:
            print(f"No results found in {results_path}. Run tests first.")
            return
        generate_doc(results, config)
        return

    # Test run
    expander = PatternExpander()
    scorer = AutoScorer()
    store = ResultStore(results_path)

    total = 0
    skipped = 0
    run_count = 0
    errors = 0

    for category in config["categories"]:
        if args.category and category["id"] != args.category:
            continue

        for variant in category["variants"]:
            for company in config["test_companies"]:
                if args.company and company["company_name"] != args.company:
                    continue

                query = expander.expand(variant["template"], company)
                total += 1

                if store.already_tested(query):
                    skipped += 1
                    continue

                if args.dry_run:
                    print(f"[{total:04d}] {category['id']}/{variant['id']}/{company['company_name']}: {query}")
                    continue

                # Execute search
                try:
                    search_mode = variant.get("search_mode", "web")
                    is_news = search_mode == "news"
                    tbs = variant.get("tbs")
                    raw = serper_search.search(query=query, news=is_news, tbs=tbs)
                    scores = scorer.score(raw, category["id"], company)
                    store.save(category["id"], variant["id"], company["company_name"], query, raw, scores)
                    run_count += 1

                    q = scores["quality"]
                    marker = "+" if q >= 4 else "." if q >= 3 else "-"
                    print(f"[{run_count:04d}] Q{q} {marker} {category['id']}/{variant['id']}/{company['company_name']}")

                except Exception as e:
                    errors += 1
                    print(f"[ERR] {category['id']}/{variant['id']}/{company['company_name']}: {e}")
                    store.save(category["id"], variant["id"], company["company_name"], query,
                              {"error": str(e)}, {"quality": 0, "result_count": 0, "relevance_ratio": 0,
                                                   "keyword_hits": 0, "has_knowledge_graph": False,
                                                   "top_domains": [], "all_domains": []})

                time.sleep(0.2)  # Rate limit

                if run_count % 50 == 0 and run_count > 0:
                    print(f"\n--- Progress: {run_count} run, {skipped} skipped, {errors} errors, ~${run_count * 0.0001:.4f} spent ---\n")

    store.flush_final()

    if args.dry_run:
        print(f"\nDry run complete. {total} queries would be executed ({skipped} already done).")
    else:
        print(f"\nDone. {run_count} queries run, {skipped} skipped, {errors} errors.")
        print(f"Total cost: ~${run_count * 0.0001:.4f}")
        print(f"Results saved to: {RESULTS_FILE}")


def main():
    parser = argparse.ArgumentParser(description="Serper Pattern Tester")
    parser.add_argument("--dry-run", action="store_true", help="Preview queries without executing")
    parser.add_argument("--category", type=str, help="Run single category only")
    parser.add_argument("--company", type=str, help="Run single company only")
    parser.add_argument("--report", action="store_true", help="Print classification report")
    parser.add_argument("--generate-doc", action="store_true", help="Generate serper-patterns.md")
    parser.add_argument("--sources", action="store_true", help="Generate source-analysis.md from all result files")
    parser.add_argument("--migrate", action="store_true", help="Backfill all_domains on existing entries")
    parser.add_argument("--config", type=str, help="Config file (default: patterns_config.json)")
    parser.add_argument("--output", type=str, help="Results output file (default: searches/raw-results.json)")
    args = parser.parse_args()

    if args.migrate:
        migrate_all_domains()
        return

    if args.sources:
        generate_source_analysis()
        return

    run(args)


if __name__ == "__main__":
    main()
