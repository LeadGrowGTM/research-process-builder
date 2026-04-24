"""
Pattern Annealing — autonomous mutation and testing loop.

Generates rule-based mutations of champion patterns, tests them against
5 companies, keeps improvements (avg_q >= current + 0.2), repeats until
budget is exhausted or no improvement is found.

Zero LLM cost. All mutations are rule-based, all scoring deterministic.
Only cost: Serper API at $0.0001/search.

Usage:
    py scripts/anneal.py                          # all categories, $0.50 budget
    py scripts/anneal.py --budget 2.00            # $2 = 20,000 searches
    py scripts/anneal.py --category news_press    # single category
    py scripts/anneal.py --rounds 10              # cap iterations
    py scripts/anneal.py --n-mutations 12         # mutations per round (default 8)
    py scripts/anneal.py --dry-run                # show mutations, no API calls
"""

import json
import re
import time
import sys
import argparse
from pathlib import Path
from datetime import datetime

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from pattern_tester import (
    PatternExpander, AutoScorer, ResultStore,
    RESULTS_FILE, SCRIPT_DIR as PT_SCRIPT_DIR,
)

from dotenv import load_dotenv
load_dotenv(SCRIPT_DIR.parent / ".env")
load_dotenv(Path.home() / ".env", override=False)

_shared = os.environ.get("SHARED_SCRIPTS_PATH", str(SCRIPT_DIR))
sys.path.insert(0, _shared)

import serper_search

# Paths
PATTERNS_CONFIG = SCRIPT_DIR / "patterns_config.json"
COMBO_CONFIG = SCRIPT_DIR / "combo_patterns.json"
DNS_PATTERNS = SCRIPT_DIR / "dns_patterns.json"
SOURCE_ANALYSIS = SCRIPT_DIR.parent / "searches" / "source-analysis.md"
ANNEAL_OUTPUT = SCRIPT_DIR.parent / "searches" / "anneal-results.json"

RESULTS_FILES = [
    SCRIPT_DIR.parent / "searches" / "raw-results.json",
    SCRIPT_DIR.parent / "searches" / "raw-results-combo.json",
]

COST_PER_SEARCH = 0.0001
IMPROVEMENT_THRESHOLD = 0.2

HARD_KILL_OPERATORS = ["intitle:", "after:", "AROUND(", "inurl:"]

EXCLUSIONS_UNIVERSAL = ["-careers", "-jobs", "-salary", "-glassdoor"]
EXCLUSIONS_BY_CATEGORY = {
    "customer_complaints": ["-site:{{domain}}"],
    "news_press": ['-"press release"'],
    "hiring_signals": ["-interview"],
    "reviews_sentiment": ["-glassdoor", "-indeed"],
}

TIME_SENSITIVE_CATEGORIES = [
    "news_press", "press_releases", "hiring_signals",
    "events_conferences", "growth_marketing", "funding_financial",
]

OR_SYNONYMS = {
    "competitors": "alternatives OR competitors OR vs",
    "review": "review OR rating OR pros cons",
    "funding": "funding OR raised OR series",
    "news": "news OR announcement OR launch",
    "hiring": "hiring OR careers OR jobs OR open roles",
    "community": "discord OR slack OR community",
    "blog": "blog OR resources OR guides",
    "newsletter": "newsletter OR subscribe OR email updates",
}


# ---------------------------------------------------------------------------
# Baseline Loader
# ---------------------------------------------------------------------------

class BaselineLoader:
    """Reconstruct current champion per category from raw results + config."""

    def __init__(self):
        with open(PATTERNS_CONFIG, "r", encoding="utf-8") as f:
            self.config = json.load(f)
        # Build template lookup from both configs
        self.template_lookup = {}
        for cfg_path in [PATTERNS_CONFIG, COMBO_CONFIG]:
            if not cfg_path.exists():
                continue
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            for cat in cfg.get("categories", []):
                for var in cat.get("variants", []):
                    self.template_lookup[(cat["id"], var["id"])] = {
                        "template": var["template"],
                        "tbs": var.get("tbs"),
                    }

    def load(self) -> dict:
        """Returns {category_id: {"template", "avg_q", "tbs"}}"""
        all_results = []
        for fpath in RESULTS_FILES:
            if not fpath.exists():
                continue
            with open(fpath, "r", encoding="utf-8") as f:
                all_results.extend(json.load(f))

        # Group by (category, variant) -> list of quality scores
        groups = {}
        for r in all_results:
            key = (r["category_id"], r["variant_id"])
            if key not in groups:
                groups[key] = []
            groups[key].append(r["scores"]["quality"])

        # Pick best variant per category
        champions = {}
        for (cat_id, var_id), scores in groups.items():
            avg_q = sum(scores) / len(scores) if scores else 0
            if cat_id not in champions or avg_q > champions[cat_id]["avg_q"]:
                lookup = self.template_lookup.get((cat_id, var_id), {})
                champions[cat_id] = {
                    "template": lookup.get("template", f"[unknown:{var_id}]"),
                    "avg_q": round(avg_q, 2),
                    "tbs": lookup.get("tbs"),
                    "variant_id": var_id,
                }

        return champions

    def get_companies(self) -> list:
        return self.config["test_companies"]


# ---------------------------------------------------------------------------
# Source Analysis Parser
# ---------------------------------------------------------------------------

def parse_source_analysis() -> dict:
    """Parse source-analysis.md -> {category_id: {"PRIMARY": [domains], "SECONDARY": [domains]}}"""
    if not SOURCE_ANALYSIS.exists():
        return {}

    result = {}
    current_cat = None

    with open(SOURCE_ANALYSIS, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("## ") and not line.startswith("## source"):
                current_cat = line[3:].strip()
                result[current_cat] = {"PRIMARY": [], "SECONDARY": []}
            elif current_cat and line.startswith("|") and "PRIMARY" in line:
                domain = line.split("|")[1].strip()
                result[current_cat]["PRIMARY"].append(domain)
            elif current_cat and line.startswith("|") and "SECONDARY" in line:
                domain = line.split("|")[1].strip()
                result[current_cat]["SECONDARY"].append(domain)

    return result


# ---------------------------------------------------------------------------
# Kill List Loader
# ---------------------------------------------------------------------------

def load_kill_templates() -> set:
    """Load all templates from dns_patterns.json as a flat set."""
    if not DNS_PATTERNS.exists():
        return set()

    with open(DNS_PATTERNS, "r", encoding="utf-8") as f:
        config = json.load(f)

    kills = set()
    for cat in config.get("categories", []):
        for var in cat.get("variants", []):
            kills.add(var["template"])
    return kills


# ---------------------------------------------------------------------------
# Mutation Generator
# ---------------------------------------------------------------------------

class MutationGenerator:
    """Rule-based mutation of champion patterns."""

    def __init__(self, kill_templates: set, source_data: dict):
        self.kill_templates = kill_templates
        self.source_data = source_data

    def generate(self, template: str, category_id: str, tbs: str = None, n: int = 8) -> list:
        """Generate up to N mutation candidates. Returns list of {"template", "tbs"} dicts."""
        candidates = []

        # Rule 1: Exclusion injection
        for excl in EXCLUSIONS_UNIVERSAL:
            excl_word = excl.lstrip("-")
            # Don't add exclusion if the word is already a positive keyword in the template
            if excl not in template and excl_word not in template.lower():
                candidates.append({"template": f"{template} {excl}", "tbs": tbs})
                if len(candidates) >= 2:
                    break  # Max 2 exclusion mutations per round

        for excl in EXCLUSIONS_BY_CATEGORY.get(category_id, []):
            if excl not in template:
                candidates.append({"template": f"{template} {excl}", "tbs": tbs})

        # Rule 2: site: injection from source analysis
        src = self.source_data.get(category_id, {})
        for domain in src.get("PRIMARY", [])[:2]:
            if f"site:{domain}" not in template:
                candidates.append({"template": f"{template} site:{domain}", "tbs": tbs})
        for domain in src.get("SECONDARY", [])[:2]:
            if f"site:{domain}" not in template:
                # Try a pure site: version
                candidates.append({"template": f"site:{domain} {{{{company_name}}}}", "tbs": tbs})

        # Rule 3: tbs filter injection (time-sensitive categories)
        if category_id in TIME_SENSITIVE_CATEGORIES and tbs is None:
            candidates.append({"template": template, "tbs": "qdr:y"})

        # Rule 4: OR synonym expansion
        for keyword, expanded in OR_SYNONYMS.items():
            # If template contains the keyword standalone (not already OR-expanded)
            if keyword in template.lower() and " OR " not in template:
                new_template = re.sub(
                    re.escape(keyword), expanded, template, count=1, flags=re.IGNORECASE
                )
                if new_template != template:
                    candidates.append({"template": new_template, "tbs": tbs})
                break  # One synonym expansion per round

        # Rule 5: Simplification — strip one operator
        if " -" in template:
            # Remove the last exclusion
            parts = template.rsplit(" -", 1)
            simplified = parts[0].strip()
            if simplified and simplified != template:
                candidates.append({"template": simplified, "tbs": tbs})
        if "site:" in template and "OR" in template:
            # Remove site: constraint, keep OR
            simplified = re.sub(r"site:\S+\s*", "", template).strip()
            if simplified and simplified != template:
                candidates.append({"template": simplified, "tbs": tbs})

        # Rule 6: Domain anchor swap
        if "{{company_name}}" in template and "{{domain}}" not in template:
            swapped = template.replace("{{company_name}}", "{{domain}}")
            candidates.append({"template": swapped, "tbs": tbs})
        elif "{{domain}}" in template and "{{company_name}}" not in template:
            swapped = template.replace("{{domain}}", "{{company_name}}")
            candidates.append({"template": swapped, "tbs": tbs})

        # Filter: kill list and hard kill operators
        filtered = []
        seen = set()
        for c in candidates:
            t = c["template"]
            key = f"{t}|{c.get('tbs', '')}"
            if key in seen:
                continue
            seen.add(key)
            if t in self.kill_templates:
                continue
            if any(op in t for op in HARD_KILL_OPERATORS):
                continue
            if t == template and c.get("tbs") == tbs:
                continue  # Skip identity mutation
            filtered.append(c)

        return filtered[:n]


# ---------------------------------------------------------------------------
# Anneal Loop
# ---------------------------------------------------------------------------

class AnnealLoop:
    """Orchestrates mutation testing with spend tracking."""

    def __init__(self, budget: float, store: ResultStore, expander: PatternExpander,
                 scorer: AutoScorer, companies: list):
        self.budget = budget
        self.spent = 0.0
        self.searches_run = 0
        self.store = store
        self.expander = expander
        self.scorer = scorer
        self.companies = companies
        # Build hash -> quality lookup from existing results
        self._score_cache = {}
        for r in store.get_all():
            self._score_cache[r["hash"]] = r["scores"]["quality"]

    def can_spend(self, n: int = 1) -> bool:
        return self.spent + (n * COST_PER_SEARCH) <= self.budget

    def _hash_key(self, query: str, tbs: str = None) -> str:
        """Hash that includes tbs to avoid collisions."""
        key = f"{query}|tbs:{tbs}" if tbs else query
        return ResultStore.query_hash(key)

    def test_mutation(self, mutation: dict, category_id: str, round_num: int) -> dict | None:
        """Test a mutation across all companies. Returns {"avg_q", "per_company"} or None if budget hit."""
        template = mutation["template"]
        tbs = mutation.get("tbs")
        results = {}

        for company in self.companies:
            query = self.expander.expand(template, company)
            h = self._hash_key(query, tbs)

            # Check cache first (free)
            if h in self._score_cache:
                results[company["company_name"]] = self._score_cache[h]
                continue

            if not self.can_spend():
                return None

            try:
                raw = serper_search.search(query=query, tbs=tbs)
                scores = self.scorer.score(raw, category_id, company)
                self.store.save(category_id, f"anneal_r{round_num}",
                               company["company_name"], query, raw, scores)
                q = scores.get("quality", 0)
                # all_domains was popped by save(), but quality is still in scores before pop
                # Actually scores was mutated by save() — read quality before save
                results[company["company_name"]] = q
                self._score_cache[h] = q
                self.spent += COST_PER_SEARCH
                self.searches_run += 1
                time.sleep(0.2)
            except Exception as e:
                print(f"    [ERR] {company['company_name']}: {e}")
                results[company["company_name"]] = 0

        if not results:
            return None

        avg_q = sum(results.values()) / len(results)
        return {"avg_q": round(avg_q, 2), "per_company": results}

    def run(self, champions: dict, generator: MutationGenerator,
            categories: list, max_rounds: int, n_mutations: int, dry_run: bool) -> dict:
        """Main annealing loop. Returns updated champions dict."""
        improvements = {}
        original = {k: v.copy() for k, v in champions.items()}

        for round_num in range(1, max_rounds + 1):
            print(f"\n{'='*60}")
            print(f"ROUND {round_num} | spent: ${self.spent:.4f} / ${self.budget:.2f} | searches: {self.searches_run}")
            print(f"{'='*60}")

            improved_any = False

            for cat_id in categories:
                if cat_id not in champions:
                    continue

                champ = champions[cat_id]
                mutations = generator.generate(
                    champ["template"], cat_id, tbs=champ.get("tbs"), n=n_mutations
                )

                if dry_run:
                    print(f"\n  {cat_id} (current Q{champ['avg_q']}):")
                    for i, m in enumerate(mutations):
                        tbs_note = f" [tbs:{m['tbs']}]" if m.get("tbs") else ""
                        print(f"    {i+1}. {m['template']}{tbs_note}")
                    continue

                print(f"\n  {cat_id} (Q{champ['avg_q']}) — {len(mutations)} mutations")

                for m in mutations:
                    if not self.can_spend(len(self.companies)):
                        print(f"\n  BUDGET HIT at ${self.spent:.4f}")
                        self.store.flush_final()
                        return champions

                    result = self.test_mutation(m, cat_id, round_num)
                    if result is None:
                        print(f"\n  BUDGET HIT at ${self.spent:.4f}")
                        self.store.flush_final()
                        return champions

                    tbs_note = f" [tbs:{m['tbs']}]" if m.get("tbs") else ""
                    delta = result["avg_q"] - champ["avg_q"]

                    if delta >= IMPROVEMENT_THRESHOLD:
                        print(f"    IMPROVED Q{champ['avg_q']} -> Q{result['avg_q']} (+{delta:.1f})")
                        print(f"    NEW: {m['template']}{tbs_note}")
                        champions[cat_id] = {
                            "template": m["template"],
                            "avg_q": result["avg_q"],
                            "tbs": m.get("tbs"),
                            "variant_id": f"anneal_r{round_num}",
                        }
                        improvements[cat_id] = {
                            "old_template": champ["template"],
                            "old_avg_q": champ["avg_q"],
                            "new_template": m["template"],
                            "new_avg_q": result["avg_q"],
                            "new_tbs": m.get("tbs"),
                            "delta": round(delta, 2),
                            "round": round_num,
                        }
                        improved_any = True
                        break  # Accept improvement, next category
                    else:
                        marker = "." if result["avg_q"] >= champ["avg_q"] else "-"
                        print(f"    {marker} Q{result['avg_q']} ({delta:+.1f}) {m['template'][:60]}{tbs_note}")

            if dry_run:
                break

            if not improved_any:
                print(f"\nNo improvements in round {round_num}. Stopping.")
                break

        self.store.flush_final()
        return champions


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def print_summary(original: dict, final: dict, improvements: dict, loop: AnnealLoop):
    """Print results table to stdout."""
    print(f"\n{'='*70}")
    print(f"ANNEAL COMPLETE | ${loop.spent:.4f} / ${loop.budget:.2f} | {loop.searches_run} searches")
    print(f"{'='*70}")

    if improvements:
        print(f"\nIMPROVED ({len(improvements)}):")
        for cat_id, imp in sorted(improvements.items()):
            print(f"  {cat_id:<30} Q{imp['old_avg_q']} -> Q{imp['new_avg_q']} (+{imp['delta']:.1f})")
            tbs = f" [tbs:{imp['new_tbs']}]" if imp.get("new_tbs") else ""
            print(f"    {imp['new_template']}{tbs}")
    else:
        print("\nNo improvements found.")

    unchanged = [c for c in final if c not in improvements]
    if unchanged:
        print(f"\nUNCHANGED ({len(unchanged)}):")
        print(f"  {', '.join(unchanged)}")


def write_results(original: dict, final: dict, improvements: dict, loop: AnnealLoop, output_path: Path):
    """Write anneal-results.json."""
    result = {
        "run_date": datetime.now().isoformat(),
        "budget": loop.budget,
        "spent": round(loop.spent, 6),
        "searches_run": loop.searches_run,
        "improvements": improvements,
        "unchanged": [c for c in final if c not in improvements],
        "final_champions": {
            cat_id: {"template": v["template"], "avg_q": v["avg_q"], "tbs": v.get("tbs")}
            for cat_id, v in final.items()
        },
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print(f"\nResults written to: {output_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Pattern Annealer")
    parser.add_argument("--budget", type=float, default=0.50, help="Max spend in dollars (default: $0.50)")
    parser.add_argument("--category", type=str, help="Single category ID to anneal")
    parser.add_argument("--rounds", type=int, default=50, help="Max rounds (default: 50)")
    parser.add_argument("--n-mutations", type=int, default=8, help="Mutations per category per round")
    parser.add_argument("--dry-run", action="store_true", help="Show mutations without testing")
    parser.add_argument("--output", type=str, help="Output file path")
    args = parser.parse_args()

    print(f"Pattern Annealer | budget: ${args.budget:.2f} | rounds: {args.rounds}")
    print(f"Improvement threshold: +{IMPROVEMENT_THRESHOLD} avg Q\n")

    # Load components
    loader = BaselineLoader()
    champions = loader.load()
    companies = loader.get_companies()

    print(f"Loaded {len(champions)} category champions from {len(RESULTS_FILES)} result files")

    # Filter to single category if specified
    if args.category:
        if args.category not in champions:
            print(f"Category '{args.category}' not found. Available: {', '.join(sorted(champions.keys()))}")
            return
        categories = [args.category]
    else:
        categories = sorted(champions.keys())

    # Print baseline
    print(f"\nBaseline champions:")
    for cat_id in categories:
        c = champions[cat_id]
        tbs = f" [tbs:{c['tbs']}]" if c.get("tbs") else ""
        print(f"  {cat_id:<30} Q{c['avg_q']} | {c['template'][:50]}{tbs}")

    # Load mutation inputs
    source_data = parse_source_analysis()
    kill_templates = load_kill_templates()
    print(f"\nSource data: {len(source_data)} categories | Kill list: {len(kill_templates)} patterns")

    # Setup
    generator = MutationGenerator(kill_templates, source_data)
    store = ResultStore(RESULTS_FILE)
    expander = PatternExpander()
    scorer = AutoScorer()
    loop = AnnealLoop(args.budget, store, expander, scorer, companies)

    original = {k: v.copy() for k, v in champions.items()}

    # Run
    final = loop.run(champions, generator, categories, args.rounds, args.n_mutations, args.dry_run)

    if not args.dry_run:
        improvements = {}
        for cat_id in categories:
            if cat_id in final and cat_id in original:
                if final[cat_id]["avg_q"] > original[cat_id]["avg_q"]:
                    improvements[cat_id] = {
                        "old_template": original[cat_id]["template"],
                        "old_avg_q": original[cat_id]["avg_q"],
                        "new_template": final[cat_id]["template"],
                        "new_avg_q": final[cat_id]["avg_q"],
                        "new_tbs": final[cat_id].get("tbs"),
                        "delta": round(final[cat_id]["avg_q"] - original[cat_id]["avg_q"], 2),
                    }

        output_path = Path(args.output).resolve() if args.output else ANNEAL_OUTPUT
        print_summary(original, final, improvements, loop)
        write_results(original, final, improvements, loop, output_path)


if __name__ == "__main__":
    main()
