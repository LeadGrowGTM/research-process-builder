"""
SearchPatternTask — autocontext AgentTaskInterface for search pattern optimization.

Wraps gt_evaluator.py as the deterministic scorer, overriding autocontext's
default LLM judge. The LLM is only used for proposing pattern mutations
(revise_output), never for scoring.

Usage:
    py scripts/search_pattern_task.py --test     # verify evaluate_output matches GT baseline
"""

from __future__ import annotations

import importlib.util
import json
import sys
from collections import defaultdict
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent

# ---------------------------------------------------------------------------
# autocontext imports (direct file load, bypasses broken __init__.py)
# ---------------------------------------------------------------------------

_ac_src_env = os.environ.get("AUTOCONTEXT_SRC_PATH")
AC_SRC = Path(_ac_src_env) if _ac_src_env else None


def _load_ac_module(name: str, file_path: Path):
    spec = importlib.util.spec_from_file_location(name, str(file_path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_task_mod = _load_ac_module("ac_task", AC_SRC / "scenarios" / "agent_task.py")
AgentTaskInterface = _task_mod.AgentTaskInterface
AgentTaskResult = _task_mod.AgentTaskResult

# ---------------------------------------------------------------------------
# Local imports
# ---------------------------------------------------------------------------

if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from gt_evaluator import run_evaluation  # noqa: E402
from autoresearch import compute_scores, load_baseline  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CONFIG_PATH = SCRIPT_DIR / "master_test_config.json"

# Category reliability ranking (from GT analysis)
CATEGORY_RELIABILITY = {
    "leadership_people": "YES",
    "founders_ceo": "YES",
    "funding_financial": "YES",
    "competitor_identification": "YES",
    "company_profile": "PARTIAL",
    "pricing_intelligence": "PARTIAL",
    "c_suite_commercial": "PARTIAL",
    "c_suite_technical": "PARTIAL",
    "customer_case_studies": "PARTIAL",
    "tech_stack": "NO",
    "partnerships_integrations": "NO",
}

# Template variables available for query patterns
TEMPLATE_VARIABLES = [
    "{{company_name}} — company name (with disambiguation if needed)",
    "{{domain}} — company domain (e.g., stripe.com)",
    "{{category}} — company category (e.g., 'payments infrastructure')",
    "{{current_year}} — current year (e.g., 2026)",
    "{{role_title}} — target role title (e.g., 'Software Engineer')",
]

# Mutation strategies (codified from research_prompt.md)
MUTATION_STRATEGIES = """
Mutation strategies for underperforming categories:

1. SPECIFICITY — Add specific keywords that appear in GT data.
   e.g., "CTO" → "Chief Technology Officer" (full title finds more)

2. AGGREGATOR TARGETING — Use site: operators for data-rich platforms.
   e.g., site:stackshare.io, site:builtwith.com, site:rocketreach.co, site:crunchbase.com

3. OR OPERATORS — Combine synonyms to widen the net.
   e.g., "case study OR customer story OR success story"

4. RESULT TYPE HINTS — Signal what format you want.
   e.g., "pricing page", "plans comparison", "cost calculator"

5. SITE-RESTRICTED — Search within the company's own domain.
   e.g., site:{{domain}} integrations OR partners OR marketplace

6. YEAR MODIFIERS — Add {{current_year}} for freshness.
   e.g., "{{company_name}} funding {{current_year}}"

What makes GOOD patterns (from GT data):
- Specific names/titles outperform generic keywords
- site: operators reliably find on-domain content
- OR operators are highest-leverage single mutation
- Aggregator sites (Crunchbase, StackShare) beat company pages for structured data

What makes BAD patterns:
- Generic queries that return marketing content
- site:reddit.com (universally broken)
- Queries without company context (too broad)
- Long queries (>10 words) that over-constrain
"""


class SearchPatternTask(AgentTaskInterface):
    """Optimizes search query templates against ground truth data."""

    def get_task_prompt(self, state: dict) -> str:
        scores = state.get("scores", {})
        categories = scores.get("categories", {})

        # Build category status table
        cat_lines = []
        for cat_id in sorted(categories.keys()):
            cat = categories[cat_id]
            reliability = CATEGORY_RELIABILITY.get(cat_id, "?")
            cat_lines.append(
                f"  {cat_id:<30} GT: {cat.get('gt_avg', 0):.3f}  "
                f"reliability: {reliability}  n={cat.get('n', 0)}"
            )

        return f"""Optimize search query templates to maximize ground truth (GT) accuracy
across 11 company intelligence categories.

Current overall GT mean: {scores.get('overall_gt_mean', 0):.4f}
Current categories:
{chr(10).join(cat_lines)}

Available template variables:
{chr(10).join(f'  {v}' for v in TEMPLATE_VARIABLES)}

{MUTATION_STRATEGIES}

Propose mutations for the 3 weakest categories. Return a JSON array:
[
  {{"category_id": "...", "variant_id": "new_variant_name", "template": "...", "reasoning": "..."}}
]

Rules:
- Only mutate variants for targeted categories
- Each mutation must use at least one template variable
- Prefer site: operators and OR combinations
- Keep templates under 10 words
- variant_id must be unique (use descriptive snake_case)
"""

    def evaluate_output(
        self,
        output: str,
        state: dict,
        reference_context: str | None = None,
        required_concepts: list[str] | None = None,
        calibration_examples: list[dict] | None = None,
        pinned_dimensions: list[str] | None = None,
    ) -> AgentTaskResult:
        """Deterministic evaluation using gt_evaluator. No LLM judge."""
        evaluations = run_evaluation()
        if not evaluations:
            return AgentTaskResult(
                score=0.0,
                reasoning="No evaluations returned from gt_evaluator.",
                dimension_scores={},
            )

        scores = compute_scores(evaluations)
        categories = scores.get("categories", {})

        # Build reasoning breakdown
        lines = [f"Overall GT mean: {scores['overall_gt_mean']:.4f}"]
        lines.append(f"Evaluations: {scores['total_evaluations']}")
        lines.append("")
        for cat_id in sorted(categories.keys()):
            cat = categories[cat_id]
            reliability = CATEGORY_RELIABILITY.get(cat_id, "?")
            lines.append(
                f"  {cat_id}: GT={cat['gt_avg']:.3f} "
                f"auto={cat['auto_avg']:.3f} "
                f"reliability={reliability} n={cat['n']}"
            )

        # Dimension scores = per-category GT averages (autocontext tracks these)
        dimension_scores = {
            cat_id: cat["gt_avg"] for cat_id, cat in categories.items()
        }

        return AgentTaskResult(
            score=scores["overall_gt_mean"],
            reasoning="\n".join(lines),
            dimension_scores=dimension_scores,
        )

    def get_rubric(self) -> str:
        return """Ground Truth Evaluation Rubric:

Scoring is deterministic (no LLM involved):
- name_in_text: exact=1.0, last_name=0.5, fuzzy=0.8
- names_in_text: found/expected ratio
- field_present: keyword overlap (40% threshold) + field-specific patterns
- text_match: substring + 50% word threshold
- boolean_present: quality >= 3

Categories scored on 0-1 scale based on how many GT fields appear in
200-char Serper search snippets. Higher = more verifiable information
extracted from search results."""

    def initial_state(self, seed: int | None = None) -> dict:
        config = {}
        if CONFIG_PATH.exists():
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                config = json.load(f)

        # Load current scores from latest baseline or compute fresh
        baseline = load_baseline()
        scores = {}
        if baseline:
            scores = {
                "overall_gt_mean": baseline.get("overall_gt_mean", 0),
                "overall_auto_mean": baseline.get("overall_auto_mean", 0),
                "categories": baseline.get("categories", {}),
                "per_variant": baseline.get("per_variant", {}),
            }

        return {
            "config": config,
            "scores": scores,
            "iteration": 0,
            "budget": 5000,
            "queries_used": 0,
        }

    def describe_task(self) -> str:
        return (
            "Optimize search query templates to maximize ground truth accuracy "
            "across 11 company intelligence categories."
        )

    def prepare_context(self, state: dict) -> dict:
        """Load playbook and current scores into state."""
        playbook_path = PROJECT_DIR / "knowledge" / "search-patterns" / "playbook.md"
        if playbook_path.exists():
            state["playbook"] = playbook_path.read_text(encoding="utf-8")
        else:
            state["playbook"] = ""

        # Refresh scores if not present
        if not state.get("scores", {}).get("categories"):
            evaluations = run_evaluation()
            if evaluations:
                state["scores"] = compute_scores(evaluations)

        return state

    def validate_context(self, state: dict) -> list[str]:
        errors = []
        if not state.get("config"):
            errors.append("master_test_config.json not loaded")
        if not CONFIG_PATH.exists():
            errors.append(f"Config file missing: {CONFIG_PATH}")
        gt_dir = PROJECT_DIR / "ground-truth"
        gt_files = list(gt_dir.glob("*.json")) if gt_dir.exists() else []
        # Exclude schema.json
        gt_files = [f for f in gt_files if f.name != "schema.json"]
        if len(gt_files) < 5:
            errors.append(f"Only {len(gt_files)} GT files found (need >= 5)")
        return errors

    def revise_output(
        self,
        output: str,
        judge_result: AgentTaskResult,
        state: dict,
    ) -> str:
        """Propose pattern mutations targeting weakest categories.

        This is the ONE place an LLM gets called. The prompt targets specific
        weak categories with playbook context and mutation strategies.
        Returns a JSON array of proposed mutations.
        """
        # Identify worst 3 categories
        dim_scores = judge_result.dimension_scores
        if not dim_scores:
            return output

        worst = sorted(dim_scores.items(), key=lambda x: x[1])[:3]

        # Get current variants for these categories
        config = state.get("config", {})
        category_variants = {}
        for cat in config.get("categories", []):
            if cat["id"] in [w[0] for w in worst]:
                category_variants[cat["id"]] = [
                    {"id": v["id"], "template": v["template"]}
                    for v in cat.get("variants", [])
                ]

        # Build targeted revision prompt
        prompt_parts = [
            "You are optimizing search query templates for company intelligence gathering.",
            "",
            "## Weakest Categories (need improvement)",
            "",
        ]
        for cat_id, score in worst:
            reliability = CATEGORY_RELIABILITY.get(cat_id, "?")
            prompt_parts.append(f"### {cat_id} (GT: {score:.3f}, reliability: {reliability})")
            variants = category_variants.get(cat_id, [])
            if variants:
                for v in variants:
                    prompt_parts.append(f"  Current: {v['id']} = \"{v['template']}\"")
            prompt_parts.append("")

        prompt_parts.append("## Playbook Context")
        playbook = state.get("playbook", "")
        if playbook:
            prompt_parts.append(playbook[:2000])
        else:
            prompt_parts.append("No playbook yet. First iteration.")

        prompt_parts.append("")
        prompt_parts.append("## Available Template Variables")
        for v in TEMPLATE_VARIABLES:
            prompt_parts.append(f"  {v}")

        prompt_parts.append("")
        prompt_parts.append(MUTATION_STRATEGIES)

        prompt_parts.append("")
        prompt_parts.append(
            "Propose 2-3 NEW variant templates for each weak category. "
            "Return ONLY a JSON array:\n"
            '[{"category_id": "...", "variant_id": "...", "template": "...", "reasoning": "..."}]'
        )

        return "\n".join(prompt_parts)

    def verify_facts(self, output: str, state: dict) -> dict | None:
        """Validate proposed mutations are structurally valid."""
        try:
            mutations = json.loads(output)
            if not isinstance(mutations, list):
                return {"verified": False, "issues": ["Output must be a JSON array"]}
            issues = []
            for i, m in enumerate(mutations):
                if not isinstance(m, dict):
                    issues.append(f"Item {i} is not a dict")
                    continue
                for key in ("category_id", "variant_id", "template"):
                    if key not in m:
                        issues.append(f"Item {i} missing '{key}'")
                if "{{" not in m.get("template", ""):
                    issues.append(f"Item {i} template has no template variables")
            return {"verified": len(issues) == 0, "issues": issues}
        except json.JSONDecodeError as e:
            return {"verified": False, "issues": [f"Invalid JSON: {e}"]}


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

def _test():
    """Verify evaluate_output matches known GT baseline."""
    task = SearchPatternTask()

    # Test initial_state
    state = task.initial_state()
    print(f"Config loaded: {len(state['config'].get('categories', []))} categories")
    print(f"Budget: {state['budget']}")

    # Test validate_context
    errors = task.validate_context(state)
    if errors:
        print(f"Validation errors: {errors}")
        return

    # Test prepare_context
    state = task.prepare_context(state)
    print(f"Playbook loaded: {len(state.get('playbook', ''))} chars")

    # Test evaluate_output (the critical one)
    result = task.evaluate_output("", state)
    print(f"\nGT Score: {result.score:.4f}")
    print(f"Dimensions: {len(result.dimension_scores)} categories")
    for cat_id in sorted(result.dimension_scores.keys()):
        print(f"  {cat_id}: {result.dimension_scores[cat_id]:.3f}")
    print(f"\n{result.reasoning}")

    # Cross-check with baseline
    baseline = load_baseline()
    if baseline:
        expected = baseline["overall_gt_mean"]
        delta = abs(result.score - expected)
        status = "MATCH" if delta < 0.001 else f"MISMATCH (delta={delta:.4f})"
        print(f"\nBaseline check: expected {expected:.4f}, got {result.score:.4f} -> {status}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", action="store_true", help="Run self-test")
    args = parser.parse_args()
    if args.test:
        _test()
    else:
        parser.print_help()
