"""
Smart Test Spawner — targeted micro-tests per annealing iteration.

Instead of running 3,357 searches every loop, this picks small targeted tests:
1. Test ONLY the patterns being mutated (changed categories)
2. Against a SUBSET of companies (one per tier)
3. Run regression checks on unchanged patterns (1 company spot-check)
4. Escalate to full sweep only on significant improvement

Cost: ~$0.005-0.02 per iteration vs $0.33 for full sweep.

Usage:
    py scripts/smart_test.py --category competitor_identification
    py scripts/smart_test.py --category competitor_identification --full
    py scripts/smart_test.py --categories competitor_identification,news_press
    py scripts/smart_test.py --regression
    py scripts/smart_test.py --status
    py scripts/smart_test.py --dry-run --category news_press
"""

import json
import sys
import argparse
import random
from pathlib import Path
from datetime import datetime

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
CONFIG_FILE = SCRIPT_DIR / "master_test_config.json"
ANNEAL_FILE = PROJECT_DIR / "searches" / "anneal-results.json"
TEST_HISTORY = PROJECT_DIR / "searches" / "smart-test-history.json"

COST_PER_SEARCH = 0.0001


class _DryRunExpander:
    """Minimal template expander for dry-run mode (no serper_search dependency)."""

    def expand(self, template, company):
        q = template
        q = q.replace("{{company_name}}", company["company_name"])
        q = q.replace("{{domain}}", company["domain"])
        q = q.replace("{{category}}", company.get("category", ""))
        q = q.replace("{{current_year}}", str(datetime.now().year))
        q = q.replace("{{role_title}}", company.get("role_title", "Software Engineer"))
        return q

sys.path.insert(0, str(SCRIPT_DIR))


def load_config():
    with open(CONFIG_FILE) as f:
        return json.load(f)


def load_champions():
    if not ANNEAL_FILE.exists():
        return {}
    with open(ANNEAL_FILE) as f:
        data = json.load(f)
    return data.get("final_champions", {})


def load_history():
    if not TEST_HISTORY.exists():
        return {"runs": [], "regression_baselines": {}}
    with open(TEST_HISTORY) as f:
        return json.load(f)


def save_history(history):
    with open(TEST_HISTORY, "w") as f:
        json.dump(history, f, indent=2)


def pick_tier_subset(companies, n_per_tier=1):
    """Pick n companies per tier for targeted testing."""
    by_tier = {}
    for c in companies:
        tier = c.get("tier", 2)
        by_tier.setdefault(tier, []).append(c)

    selected = []
    for tier in sorted(by_tier.keys()):
        pool = by_tier[tier]
        selected.extend(random.sample(pool, min(n_per_tier, len(pool))))
    return selected


def pick_regression_company(companies):
    """Pick one random company for regression spot-check."""
    return random.choice(companies)


def estimate_cost(n_categories, n_companies, variants_per_cat=3):
    """Estimate search cost for a test run."""
    searches = n_categories * n_companies * variants_per_cat
    return searches * COST_PER_SEARCH, searches


def _load_test_deps():
    """Lazy-load pattern_tester and serper_search (only needed for live tests)."""
    from dotenv import load_dotenv
    import os
    load_dotenv(PROJECT_DIR / ".env")
    load_dotenv(Path.home() / ".env", override=False)

    _shared = os.environ.get("SHARED_SCRIPTS_PATH", str(SCRIPT_DIR))
    sys.path.insert(0, _shared)

    from pattern_tester import PatternExpander, AutoScorer
    import serper_search
    return PatternExpander, AutoScorer, serper_search


def run_targeted_test(categories, companies, config, dry_run=False):
    """Run pattern tests for specific categories against subset of companies."""
    if not dry_run:
        PatternExpander, AutoScorer, serper_search = _load_test_deps()
        expander = PatternExpander()
        scorer = AutoScorer()
    else:
        expander = _DryRunExpander()
        scorer = None
    results = []

    cat_map = {}
    for cat_obj in config.get("categories", []):
        cat_map[cat_obj["id"]] = cat_obj.get("variants", [])
    if config.get("patterns"):
        cat_map.update(config["patterns"])

    tested = 0

    for cat in categories:
        if cat not in cat_map:
            print(f"  SKIP {cat} — not in config")
            continue

        variants = cat_map[cat]
        for company in companies:
            for variant in variants:
                template = variant.get("template", "")
                query = expander.expand(template, company)
                tbs = variant.get("tbs")

                if dry_run:
                    print(f"  [DRY] {cat} | {company['company_name']} | {query}")
                    tested += 1
                    continue

                try:
                    search_results = serper_search.search(query, tbs=tbs)
                    q_score = scorer.score(search_results, cat)

                    results.append({
                        "category": cat,
                        "company": company["company_name"],
                        "template": template,
                        "query": query,
                        "q_score": q_score,
                        "n_results": len(search_results.get("organic", [])),
                        "tbs": tbs,
                        "timestamp": datetime.now().isoformat(),
                    })
                    tested += 1

                    status = "Q{:.1f}".format(q_score)
                    print(f"  {status} | {cat} | {company['company_name']} | {template[:60]}")

                except Exception as e:
                    print(f"  ERR | {cat} | {company['company_name']} | {e}")
                    results.append({
                        "category": cat,
                        "company": company["company_name"],
                        "template": template,
                        "error": str(e),
                        "timestamp": datetime.now().isoformat(),
                    })
                    tested += 1

    return results, tested


def run_regression(config, n_categories=3, dry_run=False):
    """Spot-check random categories against one company to detect regressions."""
    companies = config.get("test_companies", [])
    if not companies:
        print("No test companies in config.")
        return [], 0

    company = pick_regression_company(companies)
    champions = load_champions()

    if not champions:
        print("No champion patterns found. Run anneal first.")
        return [], 0

    cat_keys = list(champions.keys())
    check_cats = random.sample(cat_keys, min(n_categories, len(cat_keys)))

    print(f"\nRegression spot-check: {company['company_name']} (tier {company.get('tier', '?')})")
    print(f"Categories: {', '.join(check_cats)}")

    regression_config = dict(config)
    regression_patterns = {}
    for cat in check_cats:
        champ = champions[cat]
        regression_patterns[cat] = [{"template": champ["template"], "tbs": champ.get("tbs")}]
    regression_config["patterns"] = regression_patterns

    return run_targeted_test(check_cats, [company], regression_config, dry_run=dry_run)


def show_status():
    """Show current test state and recent history."""
    history = load_history()
    champions = load_champions()

    print("=== Smart Test Status ===\n")
    print(f"Champion patterns: {len(champions)}")
    print(f"Test runs logged: {len(history['runs'])}")

    if history["runs"]:
        last = history["runs"][-1]
        print(f"\nLast run: {last.get('timestamp', '?')}")
        print(f"  Categories: {', '.join(last.get('categories', []))}")
        print(f"  Companies: {last.get('n_companies', '?')}")
        print(f"  Searches: {last.get('n_searches', '?')}")
        print(f"  Cost: ${last.get('cost', 0):.4f}")

    if history.get("regression_baselines"):
        print(f"\nRegression baselines: {len(history['regression_baselines'])} categories")
        for cat, baseline in sorted(history["regression_baselines"].items()):
            print(f"  {cat}: Q{baseline['avg_q']:.1f} (set {baseline.get('date', '?')})")


def main():
    parser = argparse.ArgumentParser(description="Smart targeted testing for annealing loops")
    parser.add_argument("--category", help="Single category to test")
    parser.add_argument("--categories", help="Comma-separated categories to test")
    parser.add_argument("--full", action="store_true", help="Test against ALL companies (not subset)")
    parser.add_argument("--regression", action="store_true", help="Run regression spot-check")
    parser.add_argument("--n-regression", type=int, default=3, help="Categories to spot-check (default 3)")
    parser.add_argument("--status", action="store_true", help="Show test status and history")
    parser.add_argument("--dry-run", action="store_true", help="Preview queries without API calls")
    parser.add_argument("--n-per-tier", type=int, default=1, help="Companies per tier for subset (default 1)")
    args = parser.parse_args()

    if args.status:
        show_status()
        return

    config = load_config()
    companies = config.get("test_companies", [])

    if args.regression:
        results, n_searched = run_regression(config, n_categories=args.n_regression, dry_run=args.dry_run)
        if results and not args.dry_run:
            history = load_history()
            history["runs"].append({
                "type": "regression",
                "timestamp": datetime.now().isoformat(),
                "categories": list(set(r["category"] for r in results)),
                "n_companies": 1,
                "n_searches": n_searched,
                "cost": n_searched * COST_PER_SEARCH,
                "results_summary": {
                    r["category"]: r.get("q_score", 0)
                    for r in results if "q_score" in r
                },
            })
            save_history(history)
        return

    cats = []
    if args.category:
        cats = [args.category]
    elif args.categories:
        cats = [c.strip() for c in args.categories.split(",")]
    else:
        print("Specify --category, --categories, --regression, or --status")
        return

    if args.full:
        test_companies = companies
        print(f"Full test: {len(cats)} categories x {len(test_companies)} companies")
    else:
        test_companies = pick_tier_subset(companies, n_per_tier=args.n_per_tier)
        print(f"Targeted test: {len(cats)} categories x {len(test_companies)} companies (subset)")

    est_cost, est_searches = estimate_cost(len(cats), len(test_companies))
    print(f"Estimated: {est_searches} searches, ${est_cost:.4f}\n")

    results, n_searched = run_targeted_test(cats, test_companies, config, dry_run=args.dry_run)

    if results and not args.dry_run:
        history = load_history()

        avg_scores = {}
        for r in results:
            if "q_score" in r:
                cat = r["category"]
                avg_scores.setdefault(cat, []).append(r["q_score"])

        summary = {cat: sum(scores) / len(scores) for cat, scores in avg_scores.items()}

        history["runs"].append({
            "type": "targeted",
            "timestamp": datetime.now().isoformat(),
            "categories": cats,
            "n_companies": len(test_companies),
            "n_searches": n_searched,
            "cost": n_searched * COST_PER_SEARCH,
            "results_summary": {cat: round(avg, 2) for cat, avg in summary.items()},
        })
        save_history(history)

        print(f"\n=== Results ===")
        for cat, avg in sorted(summary.items()):
            champ = load_champions().get(cat, {})
            champ_q = champ.get("avg_q", "?")
            delta = ""
            if isinstance(champ_q, (int, float)):
                d = avg - champ_q
                delta = f" ({'+' if d >= 0 else ''}{d:.1f} vs champion)"
            print(f"  {cat}: Q{avg:.1f}{delta}")

        print(f"\nTotal: {n_searched} searches, ${n_searched * COST_PER_SEARCH:.4f}")


if __name__ == "__main__":
    main()
