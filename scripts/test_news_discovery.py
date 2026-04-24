"""
Series A Daily Discovery — News Endpoint Tester

Tests SerperDev /news and /search endpoints against ground truth to measure
which query patterns surface real Series A announcements within a 24h window.

Usage:
    py scripts/test_news_discovery.py                    # run all queries, both endpoints
    py scripts/test_news_discovery.py --tbs qdr:d        # last 24h only
    py scripts/test_news_discovery.py --tbs qdr:w        # last week (backward test)
    py scripts/test_news_discovery.py --endpoint news    # news endpoint only
    py scripts/test_news_discovery.py --endpoint search  # web search only
    py scripts/test_news_discovery.py --report           # generate comparison report
    py scripts/test_news_discovery.py --dry-run          # preview queries
"""

import json
import sys
import os
import time
import argparse
from pathlib import Path
from datetime import datetime

_script_dir = Path(__file__).resolve().parent

from dotenv import load_dotenv
load_dotenv(_script_dir.parent / ".env")
load_dotenv(Path.home() / ".env", override=False)

sys.path.insert(0, os.environ.get("SHARED_SCRIPTS_PATH", str(_script_dir)))

import serper_search

SCRIPT_DIR = Path(__file__).resolve().parent
RESULTS_DIR = SCRIPT_DIR.parent / "searches"
RESULTS_FILE = RESULTS_DIR / "news-discovery-results.json"

# ---------------------------------------------------------------------------
# Ground Truth — April 2026
# ---------------------------------------------------------------------------

GROUND_TRUTH = [
    {
        "company_name": "Zenskar",
        "amount": "$15M",
        "round_type": "Series A",
        "domain": "zenskar.com",
        "match_patterns": ["zenskar"],
        "region": "US",
    },
    {
        "company_name": "Spektr",
        "amount": "$20M",
        "round_type": "Series A",
        "domain": "spektr.com",
        "match_patterns": ["spektr"],
        "region": "EU",
    },
    {
        "company_name": "Ethermed",
        "amount": "$8.5M",
        "round_type": "Series A",
        "domain": "ethermed.ai",
        "match_patterns": ["ethermed"],
        "region": "US",
    },
    {
        "company_name": "Hata",
        "amount": "$8M",
        "round_type": "Series A",
        "domain": "hata.com",
        "match_patterns": ["hata"],
        "region": "APAC",
    },
    {
        "company_name": "Archangel Lightworks",
        "amount": "£10M",
        "round_type": "Series A",
        "domain": "archangel.lightworks",
        "match_patterns": ["archangel lightworks", "archangel"],
        "region": "UK",
    },
    {
        "company_name": "Wamo",
        "amount": "€10M",
        "round_type": "Series A",
        "domain": "wamo.io",
        "match_patterns": ["wamo"],
        "region": "EU",
    },
    {
        "company_name": "Creao AI",
        "amount": "$10M",
        "round_type": "Growth",
        "domain": "creao.ai",
        "match_patterns": ["creao"],
        "region": "US",
    },
    {
        "company_name": "Capsule Security",
        "amount": "$7M",
        "round_type": "Seed",
        "domain": "capsulesecurity.com",
        "match_patterns": ["capsule security", "capsule"],
        "region": "US/IL",
    },
]

# ---------------------------------------------------------------------------
# Discovery Queries
# ---------------------------------------------------------------------------

DISCOVERY_QUERIES = [
    {
        "id": "q1_broad_series_a",
        "query": '"Series A" raises OR raised OR funding OR round million',
        "description": "broad Series A sweep",
        "num": 20,
    },
    {
        "id": "q2_announcement_language",
        "query": '"Series A" announces OR secures OR closes OR completes funding',
        "description": "announcement language",
        "num": 20,
    },
    {
        "id": "q3_thesaasnews",
        "query": "site:thesaasnews.com Series A",
        "description": "TheSaaSNews aggregator",
        "num": 10,
    },
    {
        "id": "q4_finsmes",
        "query": "site:finsmes.com Series A",
        "description": "FinSMEs aggregator",
        "num": 10,
    },
    {
        "id": "q5_alleywatch",
        "query": "site:alleywatch.com funding report",
        "description": "AlleyWatch daily digest",
        "num": 10,
    },
    {
        "id": "q6_press_wires",
        "query": '"Series A" site:businesswire.com OR site:prnewswire.com OR site:einpresswire.com',
        "description": "press wire sweep",
        "num": 10,
    },
    {
        "id": "q7_vc_language",
        "query": '"led the round" OR "led the Series A" OR "led a" Series A investment startup',
        "description": "VC/investor announcement language",
        "num": 20,
    },
    {
        "id": "q8_european",
        "query": '"Series A" startup funding site:eu-startups.com OR site:tech.eu OR site:techround.co.uk',
        "description": "European coverage",
        "num": 10,
    },
    {
        "id": "q9_tech_press",
        "query": '"Series A" funding startup site:venturebeat.com OR site:siliconangle.com OR site:pulse2.com',
        "description": "broader tech press",
        "num": 10,
    },
    {
        "id": "q10_infotechlead",
        "query": "site:infotechlead.com venture capital funding",
        "description": "InfotechLead daily VC roundup",
        "num": 10,
    },
]


def check_gt_match(text: str, gt_entry: dict) -> bool:
    """Check if text contains any match pattern for a ground truth company."""
    text_lower = text.lower()
    for pattern in gt_entry["match_patterns"]:
        if pattern.lower() in text_lower:
            # "capsule" is too generic — require "capsule security" or context
            if pattern == "capsule":
                if "capsule security" in text_lower or ("capsule" in text_lower and "security" in text_lower):
                    return True
                continue
            # "archangel" alone is fine since it's distinctive enough in funding context
            if pattern == "hata":
                if "hata" in text_lower and ("series a" in text_lower or "funding" in text_lower or "bybit" in text_lower or "million" in text_lower):
                    return True
                continue
            return True
    return False


def run_query(query_def: dict, endpoint: str, tbs: str, dry_run: bool = False) -> dict:
    """Run a single discovery query and return results with GT scoring."""
    q = query_def["query"]
    num = query_def.get("num", 10)

    if dry_run:
        return {
            "query_id": query_def["id"],
            "query": q,
            "endpoint": endpoint,
            "tbs": tbs,
            "dry_run": True,
        }

    is_news = endpoint == "news"

    # serper_search.search doesn't support num param directly, patch it
    original_num = serper_search.DEFAULT_NUM_RESULTS
    serper_search.DEFAULT_NUM_RESULTS = num

    try:
        results = serper_search.search(query=q, news=is_news, tbs=tbs)
    except Exception as e:
        serper_search.DEFAULT_NUM_RESULTS = original_num
        return {
            "query_id": query_def["id"],
            "query": q,
            "endpoint": endpoint,
            "tbs": tbs,
            "error": str(e),
            "gt_hits": [],
            "result_count": 0,
        }

    serper_search.DEFAULT_NUM_RESULTS = original_num

    # Extract result items (news has "news" key, search has "organic" key)
    items = results.get("news", results.get("organic", []))

    # Score against ground truth
    gt_hits = {}
    all_results = []

    for item in items:
        title = item.get("title", "")
        snippet = item.get("snippet", item.get("description", ""))
        link = item.get("link", "")
        source = item.get("source", "")
        combined_text = f"{title} {snippet} {link} {source}"

        result_entry = {
            "title": title,
            "snippet": snippet[:200],
            "link": link,
            "source": source,
        }

        # Check each GT company
        for gt in GROUND_TRUTH:
            if check_gt_match(combined_text, gt):
                company = gt["company_name"]
                if company not in gt_hits:
                    gt_hits[company] = {
                        "source_url": link,
                        "source_domain": source or link.split("/")[2] if "/" in link else "",
                        "title": title,
                    }
                result_entry["gt_match"] = company

        all_results.append(result_entry)

    return {
        "query_id": query_def["id"],
        "description": query_def["description"],
        "query": q,
        "endpoint": endpoint,
        "tbs": tbs,
        "result_count": len(items),
        "gt_hits": gt_hits,
        "gt_hit_count": len(gt_hits),
        "gt_hit_names": list(gt_hits.keys()),
        "results": all_results,
        "timestamp": datetime.now().isoformat(),
    }


def run_all(endpoints: list, tbs: str, dry_run: bool = False) -> dict:
    """Run all queries across specified endpoints."""
    all_runs = []
    total_queries = 0

    for endpoint in endpoints:
        print(f"\n{'='*60}")
        print(f"  ENDPOINT: {endpoint.upper()} | TBS: {tbs}")
        print(f"{'='*60}")

        for qdef in DISCOVERY_QUERIES:
            print(f"  [{qdef['id']}] {qdef['description']}...", end=" ", flush=True)
            result = run_query(qdef, endpoint, tbs, dry_run)
            all_runs.append(result)
            total_queries += 1

            if dry_run:
                print("(dry run)")
            elif "error" in result:
                print(f"ERROR: {result['error']}")
            else:
                hits = result["gt_hit_names"]
                print(f"{result['result_count']} results, GT hits: {len(hits)} {hits if hits else ''}")

            if not dry_run:
                time.sleep(0.3)  # rate limit courtesy

    # Aggregate GT coverage per endpoint
    summary = {}
    for endpoint in endpoints:
        endpoint_runs = [r for r in all_runs if r.get("endpoint") == endpoint]
        all_gt_found = set()
        for run in endpoint_runs:
            all_gt_found.update(run.get("gt_hit_names", []))

        summary[endpoint] = {
            "total_queries": len(endpoint_runs),
            "total_results": sum(r.get("result_count", 0) for r in endpoint_runs),
            "unique_gt_hits": sorted(list(all_gt_found)),
            "gt_hit_rate": f"{len(all_gt_found)}/{len(GROUND_TRUTH)} ({len(all_gt_found)/len(GROUND_TRUTH)*100:.0f}%)",
        }

    # Per-query GT coverage
    query_coverage = {}
    for qdef in DISCOVERY_QUERIES:
        qid = qdef["id"]
        query_coverage[qid] = {}
        for endpoint in endpoints:
            matching = [r for r in all_runs if r.get("query_id") == qid and r.get("endpoint") == endpoint]
            if matching:
                query_coverage[qid][endpoint] = matching[0].get("gt_hit_names", [])

    return {
        "run_date": datetime.now().isoformat(),
        "tbs": tbs,
        "endpoints": endpoints,
        "ground_truth_count": len(GROUND_TRUTH),
        "total_queries": total_queries,
        "summary": summary,
        "query_coverage": query_coverage,
        "runs": all_runs,
    }


def print_report(data: dict):
    """Print a formatted comparison report."""
    print(f"\n{'='*70}")
    print(f"  SERIES A DISCOVERY TEST REPORT")
    print(f"  Date: {data['run_date'][:10]} | TBS: {data['tbs']}")
    print(f"{'='*70}")

    # Endpoint comparison
    print(f"\n  ENDPOINT COMPARISON:")
    print(f"  {'Endpoint':<12} {'Queries':<10} {'Results':<10} {'GT Hits':<15} {'Rate':<10}")
    print(f"  {'-'*57}")
    for ep, stats in data["summary"].items():
        print(f"  {ep:<12} {stats['total_queries']:<10} {stats['total_results']:<10} {len(stats['unique_gt_hits']):<15} {stats['gt_hit_rate']:<10}")

    # Per-endpoint GT breakdown
    for ep, stats in data["summary"].items():
        print(f"\n  {ep.upper()} — Found: {stats['unique_gt_hits']}")
        missed = [gt["company_name"] for gt in GROUND_TRUTH if gt["company_name"] not in stats["unique_gt_hits"]]
        if missed:
            print(f"  {ep.upper()} — Missed: {missed}")

    # Per-query breakdown
    print(f"\n  PER-QUERY GT COVERAGE:")
    print(f"  {'Query':<30} ", end="")
    for ep in data["endpoints"]:
        print(f"{ep:<15} ", end="")
    print()
    print(f"  {'-'*60}")

    for qid, ep_hits in data["query_coverage"].items():
        label = qid[:29]
        print(f"  {label:<30} ", end="")
        for ep in data["endpoints"]:
            hits = ep_hits.get(ep, [])
            print(f"{len(hits)} {','.join(h[:8] for h in hits):<13} " if hits else f"{'0':<15} ", end="")
        print()

    # Cost estimate
    total_q = data["total_queries"]
    print(f"\n  COST: {total_q} queries × $0.001 = ${total_q * 0.001:.3f}")
    print(f"{'='*70}\n")


def generate_report_md(data: dict) -> str:
    """Generate markdown report for the searches/ directory."""
    lines = [
        f"# Series A Discovery Test — {data['run_date'][:10]}",
        f"",
        f"**TBS filter:** `{data['tbs']}`",
        f"**Total queries:** {data['total_queries']}",
        f"**Ground truth:** {data['ground_truth_count']} companies",
        f"**Cost:** ~${data['total_queries'] * 0.001:.3f}",
        f"",
        f"## Endpoint Comparison",
        f"",
        f"| Endpoint | Queries | Results | GT Hits | Rate |",
        f"|----------|---------|---------|---------|------|",
    ]

    for ep, stats in data["summary"].items():
        lines.append(f"| {ep} | {stats['total_queries']} | {stats['total_results']} | {len(stats['unique_gt_hits'])} | {stats['gt_hit_rate']} |")

    for ep, stats in data["summary"].items():
        lines.append(f"")
        lines.append(f"### {ep} — found: {', '.join(stats['unique_gt_hits'])}")
        missed = [gt["company_name"] for gt in GROUND_TRUTH if gt["company_name"] not in stats["unique_gt_hits"]]
        if missed:
            lines.append(f"**missed:** {', '.join(missed)}")

    lines.append(f"")
    lines.append(f"## Per-Query GT Hits")
    lines.append(f"")

    header = "| Query |"
    sep = "|-------|"
    for ep in data["endpoints"]:
        header += f" {ep} |"
        sep += "------|"
    lines.append(header)
    lines.append(sep)

    for qid, ep_hits in data["query_coverage"].items():
        row = f"| {qid} |"
        for ep in data["endpoints"]:
            hits = ep_hits.get(ep, [])
            row += f" {', '.join(hits) if hits else '—'} |"
        lines.append(row)

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Test Series A discovery queries against ground truth")
    parser.add_argument("--tbs", default="qdr:d", help="Time filter (qdr:d=day, qdr:w=week, qdr:m=month)")
    parser.add_argument("--endpoint", choices=["news", "search", "both"], default="both", help="Which endpoint to test")
    parser.add_argument("--dry-run", action="store_true", help="Preview queries without running")
    parser.add_argument("--report", action="store_true", help="Load and display last results")
    args = parser.parse_args()

    if args.report:
        if RESULTS_FILE.exists():
            data = json.loads(RESULTS_FILE.read_text())
            print_report(data)
        else:
            print("No results file found. Run tests first.")
        return

    endpoints = ["news", "search"] if args.endpoint == "both" else [args.endpoint]

    data = run_all(endpoints, args.tbs, args.dry_run)

    if not args.dry_run:
        # Save raw results
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        RESULTS_FILE.write_text(json.dumps(data, indent=2, default=str))
        print(f"\nResults saved to {RESULTS_FILE}")

        # Save markdown report
        report_file = RESULTS_DIR / f"news-discovery-{args.tbs.replace(':', '-')}-{datetime.now().strftime('%Y%m%d')}.md"
        report_file.write_text(generate_report_md(data))
        print(f"Report saved to {report_file}")

        # Print summary
        print_report(data)
    else:
        print(f"\nDry run complete. {len(data['runs'])} queries would be executed.")


if __name__ == "__main__":
    main()
