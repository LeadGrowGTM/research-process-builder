"""
AutoContext Runner — Closed-loop search pattern optimization.

Replaces the manual Karpathy loop (autoresearch.py + research_prompt.md) with
autocontext infrastructure: BackpressureGate, LessonStore, and playbook accumulation.

The loop: evaluate → propose mutations → test via Serper → gate decision →
persist learnings → repeat.

Usage:
    py scripts/autocontext_runner.py                          # full loop
    py scripts/autocontext_runner.py --max-iterations 5       # limit iterations
    py scripts/autocontext_runner.py --budget 2000            # limit Serper queries
    py scripts/autocontext_runner.py --category tech_stack    # single category focus
    py scripts/autocontext_runner.py --dry-run                # show plan, no Serper calls
    py scripts/autocontext_runner.py --playbook               # show current playbook
    py scripts/autocontext_runner.py --lessons                 # show accumulated lessons
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import shutil
import subprocess
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent

# ---------------------------------------------------------------------------
# autocontext imports (direct file load)
# ---------------------------------------------------------------------------

_ac_src_env = os.environ.get("AUTOCONTEXT_SRC_PATH")
AC_SRC = Path(_ac_src_env) if _ac_src_env else None


def _load_ac_module(name: str, file_path: Path):
    spec = importlib.util.spec_from_file_location(name, str(file_path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_gate_mod = _load_ac_module("ac_gate", AC_SRC / "harness" / "pipeline" / "gate.py")
_lessons_mod = _load_ac_module("ac_lessons", AC_SRC / "knowledge" / "lessons.py")

BackpressureGate = _gate_mod.BackpressureGate
GateDecision = _gate_mod.GateDecision
LessonStore = _lessons_mod.LessonStore
Lesson = _lessons_mod.Lesson
ApplicabilityMeta = _lessons_mod.ApplicabilityMeta

# ---------------------------------------------------------------------------
# Local imports
# ---------------------------------------------------------------------------

if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from search_pattern_task import SearchPatternTask  # noqa: E402
from autoresearch import save_baseline, load_baseline, compute_scores  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CONFIG_PATH = SCRIPT_DIR / "master_test_config.json"
CONFIG_BACKUP_PATH = SCRIPT_DIR / "master_test_config.backup.json"
KNOWLEDGE_DIR = PROJECT_DIR / "knowledge" / "search-patterns"
PLAYBOOK_PATH = KNOWLEDGE_DIR / "playbook.md"
PATTERN_TESTER = SCRIPT_DIR / "pattern_tester.py"

SCENARIO_NAME = "search-patterns"


# ---------------------------------------------------------------------------
# Playbook Manager
# ---------------------------------------------------------------------------

class PlaybookManager:
    """Read/update the playbook markdown file."""

    def __init__(self, path: Path):
        self.path = path

    def read(self) -> str:
        if self.path.exists():
            return self.path.read_text(encoding="utf-8")
        return ""

    def append_iteration(self, iteration: int, gt_before: float, gt_after: float,
                         decision: str, details: str) -> None:
        """Append an iteration result to the playbook."""
        content = self.read()

        entry = (
            f"- iter-{iteration}: GT {gt_before:.4f} -> {gt_after:.4f} "
            f"({decision}) {details}"
        )

        if "## Iteration History" in content:
            content = content.rstrip() + "\n" + entry + "\n"
        else:
            content = content.rstrip() + "\n\n## Iteration History\n\n" + entry + "\n"

        self.path.write_text(content, encoding="utf-8")

    def update_category(self, category_id: str, entry_type: str, text: str) -> None:
        """Add a PROVEN/FAILED/TRY NEXT entry under a category."""
        content = self.read()
        marker = f"### {category_id}"

        if marker in content:
            # Find the section and append before next ### or ## or end
            idx = content.index(marker)
            # Find end of this section
            rest = content[idx + len(marker):]
            next_section = len(content)
            for pattern in ["\n### ", "\n## "]:
                pos = rest.find(pattern)
                if pos >= 0:
                    next_section = min(next_section, idx + len(marker) + pos)

            insert_at = next_section
            new_line = f"\n- {entry_type}: {text}"
            content = content[:insert_at] + new_line + content[insert_at:]
        else:
            # Category not in playbook yet — add before ## Global Patterns or at end
            new_section = f"\n### {category_id}\n- {entry_type}: {text}\n"
            if "## Global Patterns" in content:
                content = content.replace("## Global Patterns", new_section + "\n## Global Patterns")
            else:
                content = content.rstrip() + "\n" + new_section

        self.path.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# Config Manager
# ---------------------------------------------------------------------------

class ConfigManager:
    """Read/write/backup master_test_config.json."""

    def __init__(self, path: Path, backup_path: Path):
        self.path = path
        self.backup_path = backup_path

    def load(self) -> dict:
        with open(self.path, "r", encoding="utf-8") as f:
            return json.load(f)

    def save(self, config: dict) -> None:
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)

    def backup(self) -> None:
        shutil.copy2(self.path, self.backup_path)

    def restore(self) -> None:
        if self.backup_path.exists():
            shutil.copy2(self.backup_path, self.path)

    def apply_mutations(self, config: dict, mutations: list[dict]) -> tuple[dict, list[str]]:
        """Apply mutation patches to config. Returns (new_config, changed_categories)."""
        changed = set()
        for mutation in mutations:
            cat_id = mutation["category_id"]
            var_id = mutation["variant_id"]
            template = mutation["template"]

            # Find or create category
            cat = None
            for c in config["categories"]:
                if c["id"] == cat_id:
                    cat = c
                    break

            if cat is None:
                continue

            # Check if variant already exists
            existing = None
            for v in cat.get("variants", []):
                if v["id"] == var_id:
                    existing = v
                    break

            if existing:
                existing["template"] = template
            else:
                cat.setdefault("variants", []).append({
                    "id": var_id,
                    "template": template,
                })

            changed.add(cat_id)

        return config, sorted(changed)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

class SearchPatternRunner:
    """Closed-loop optimization runner using autocontext building blocks."""

    def __init__(
        self,
        max_iterations: int = 10,
        budget: int = 5000,
        min_improvement: float = 0.005,
        max_retries: int = 2,
        target_category: str | None = None,
        dry_run: bool = False,
    ):
        self.max_iterations = max_iterations
        self.budget = budget
        self.target_category = target_category
        self.dry_run = dry_run

        self.task = SearchPatternTask()
        self.gate = BackpressureGate(min_delta=min_improvement)
        self.lesson_store = LessonStore(
            knowledge_root=KNOWLEDGE_DIR.parent,
            skills_root=SCRIPT_DIR,
        )
        self.playbook = PlaybookManager(PLAYBOOK_PATH)
        self.config_mgr = ConfigManager(CONFIG_PATH, CONFIG_BACKUP_PATH)
        self.max_retries = max_retries

        self.queries_used = 0
        self.trajectory: list[dict] = []

    def _run_pattern_tester(self, categories: list[str]) -> int:
        """Run pattern_tester.py for specific categories. Returns queries executed."""
        total = 0
        for cat in categories:
            cmd = [
                sys.executable, str(PATTERN_TESTER),
                "--config", "master_test_config.json",
                "--category", cat,
            ]
            if self.dry_run:
                cmd.append("--dry-run")

            result = subprocess.run(
                cmd, capture_output=True, text=True,
                cwd=str(SCRIPT_DIR), timeout=300,
            )

            if result.returncode != 0:
                print(f"  [ERR] pattern_tester for {cat}: {result.stderr[:200]}")
                continue

            # Parse query count from output
            for line in result.stdout.split("\n"):
                m = re.search(r"(\d+) queries run", line)
                if m:
                    total += int(m.group(1))
                # Also count dry-run queries
                m2 = re.search(r"(\d+) queries would be executed", line)
                if m2:
                    total += int(m2.group(1))

            if result.stdout.strip():
                # Show last line (summary)
                last_lines = [l for l in result.stdout.strip().split("\n") if l.strip()]
                if last_lines:
                    print(f"  {cat}: {last_lines[-1]}")

        return total

    def _propose_mutations(self, state: dict) -> list[dict]:
        """Use the task's revision logic to propose mutations.

        In the full autocontext loop, this would call an LLM. For now,
        we use a heuristic approach based on category analysis + playbook.
        The revise_output() method builds the prompt; we return it for
        the caller to send to an LLM, or we apply simple heuristics.
        """
        result = self.task.evaluate_output("", state)
        dim_scores = result.dimension_scores

        if not dim_scores:
            return []

        # Filter to target category if specified
        if self.target_category:
            candidates = {k: v for k, v in dim_scores.items() if k == self.target_category}
        else:
            candidates = dim_scores

        # Pick worst 3
        worst = sorted(candidates.items(), key=lambda x: x[1])[:3]

        config = state.get("config", {})
        mutations = []

        for cat_id, score in worst:
            # Get existing variant IDs to avoid collisions
            existing_ids = set()
            for cat in config.get("categories", []):
                if cat["id"] == cat_id:
                    existing_ids = {v["id"] for v in cat.get("variants", [])}
                    break

            # Apply heuristic mutations based on playbook patterns
            new_variants = self._heuristic_mutations(cat_id, score, existing_ids)
            mutations.extend(new_variants)

        return mutations

    def _heuristic_mutations(self, cat_id: str, score: float,
                              existing_ids: set) -> list[dict]:
        """Generate mutations based on proven patterns from the playbook."""
        mutations = []

        # Strategy 1: Aggregator site targeting (if not already tried)
        aggregator_map = {
            "tech_stack": [
                ("ac_stackshare", "site:stackshare.io \"{{company_name}}\" tech stack"),
                ("ac_builtwith", "site:builtwith.com \"{{company_name}}\""),
            ],
            "company_profile": [
                ("ac_crunchbase_profile", "site:crunchbase.com \"{{company_name}}\" company"),
            ],
            "c_suite_commercial": [
                ("ac_rocket_commercial", "site:rocketreach.co \"{{company_name}}\" VP Sales OR CRO OR \"Chief Revenue\""),
            ],
            "c_suite_technical": [
                ("ac_rocket_technical", "site:rocketreach.co \"{{company_name}}\" CTO OR \"VP Engineering\" OR \"Chief Technology\""),
                ("ac_full_title_cto", "\"{{company_name}}\" \"Chief Technology Officer\" OR \"VP of Engineering\""),
            ],
            "partnerships_integrations": [
                ("ac_site_integrations", "site:{{domain}} integrations OR marketplace OR partners OR connect"),
                ("ac_integration_dir", "\"{{company_name}}\" integration directory OR app marketplace"),
            ],
            "customer_case_studies": [
                ("ac_site_casestudy", "site:{{domain}} case-study OR customer-story OR testimonial"),
                ("ac_review_stories", "\"{{company_name}}\" customer review success story {{current_year}}"),
            ],
            "competitor_identification": [
                ("ac_vs_alt", "\"{{company_name}}\" vs OR alternative OR competitor {{current_year}}"),
            ],
            "founders_ceo": [
                ("ac_crunchbase_founder", "site:crunchbase.com \"{{company_name}}\" founder CEO"),
            ],
            "pricing_intelligence": [
                ("ac_site_pricing", "site:{{domain}} pricing OR plans OR cost"),
                ("ac_pricing_compare", "\"{{company_name}}\" pricing comparison {{current_year}}"),
            ],
        }

        for var_id, template in aggregator_map.get(cat_id, []):
            if var_id not in existing_ids:
                mutations.append({
                    "category_id": cat_id,
                    "variant_id": var_id,
                    "template": template,
                    "reasoning": f"Aggregator/site-targeted for {cat_id} (heuristic)",
                })

        return mutations

    def run(self) -> None:
        """Execute the optimization loop."""
        print("=" * 70)
        print("AutoContext Search Pattern Optimizer")
        print("=" * 70)

        # Initialize
        state = self.task.initial_state()
        state = self.task.prepare_context(state)

        errors = self.task.validate_context(state)
        if errors:
            print(f"Context validation failed: {errors}")
            return

        # Save baseline
        initial_result = self.task.evaluate_output("", state)
        best_score = initial_result.score
        print(f"\nBaseline GT mean: {best_score:.4f}")
        print(f"Categories: {len(initial_result.dimension_scores)}")
        print(f"Budget: {self.budget} queries")
        print(f"Max iterations: {self.max_iterations}")
        if self.target_category:
            print(f"Target category: {self.target_category}")
        if self.dry_run:
            print("MODE: DRY RUN (no Serper calls)")

        if not self.dry_run:
            save_baseline(f"ac-pre-run-{datetime.now().strftime('%Y%m%dT%H%M')}")

        retry_count = 0

        for iteration in range(1, self.max_iterations + 1):
            print(f"\n{'-' * 70}")
            print(f"Iteration {iteration}/{self.max_iterations}")
            print(f"{'-' * 70}")

            # Budget check
            if self.queries_used >= self.budget:
                print(f"Budget exhausted ({self.queries_used}/{self.budget} queries)")
                break

            # Propose mutations
            mutations = self._propose_mutations(state)
            if not mutations:
                print("No mutations proposed. Loop complete.")
                break

            print(f"\nProposed {len(mutations)} mutations:")
            for m in mutations:
                print(f"  {m['category_id']}/{m['variant_id']}: \"{m['template']}\"")

            if self.dry_run:
                print("\n[DRY RUN] Would apply mutations and run pattern_tester.")
                # Still track what WOULD happen
                self.trajectory.append({
                    "iteration": iteration,
                    "mutations": len(mutations),
                    "decision": "dry_run",
                })
                continue

            # Backup config, apply mutations
            self.config_mgr.backup()
            config = self.config_mgr.load()
            config, changed_categories = self.config_mgr.apply_mutations(config, mutations)
            self.config_mgr.save(config)

            print(f"\nRunning Serper queries for: {', '.join(changed_categories)}")

            # Run pattern_tester on changed categories
            # Write to master results file so gt_evaluator can see them
            queries = self._run_pattern_tester_to_master(changed_categories)
            self.queries_used += queries
            print(f"Queries: {queries} (total: {self.queries_used}/{self.budget})")

            # Evaluate
            state["config"] = config
            new_result = self.task.evaluate_output("", state)
            new_score = new_result.score

            # Compute per-category scores for changed categories only.
            # Using overall mean is misleading because adding new low-scoring
            # variants to weak categories dilutes the mean even when patterns
            # are better than existing ones.
            prev_dims = initial_result.dimension_scores
            new_dims = new_result.dimension_scores

            changed_before = sum(
                prev_dims.get(c, 0) for c in changed_categories
            ) / max(len(changed_categories), 1)
            changed_after = sum(
                new_dims.get(c, 0) for c in changed_categories
            ) / max(len(changed_categories), 1)

            print(f"\nOverall GT: {best_score:.4f} -> {new_score:.4f}")
            print(f"Changed categories mean: {changed_before:.4f} -> {changed_after:.4f}")
            for cat in changed_categories:
                before = prev_dims.get(cat, 0)
                after = new_dims.get(cat, 0)
                arrow = "+" if after > before + 0.01 else "-" if after < before - 0.01 else "="
                print(f"  {cat}: {before:.3f} -> {after:.3f} {arrow}")

            # Gate uses changed-category mean, not overall mean
            decision = self.gate.evaluate(
                previous_best=changed_before,
                current_best=changed_after,
                retry_count=retry_count,
                max_retries=self.max_retries,
            )

            print(f"Gate: {decision.decision} (delta={decision.delta:+.4f}, reason: {decision.reason})")

            if decision.decision == "advance":
                # Save baseline, update playbook and lessons
                baseline_name = f"ac-iter-{iteration}"
                save_baseline(baseline_name)

                # Record what worked
                for m in mutations:
                    cat_id = m["category_id"]
                    old_score = prev_dims.get(cat_id, 0)
                    new_cat_score = new_dims.get(cat_id, 0)
                    if new_cat_score > old_score + 0.01:
                        self.playbook.update_category(
                            cat_id, "PROVEN",
                            f"{m['variant_id']}: \"{m['template']}\" (GT {old_score:.3f}->{new_cat_score:.3f})"
                        )
                        self._add_lesson(
                            iteration, new_score,
                            f"For {cat_id}, pattern \"{m['template']}\" improved GT by "
                            f"{new_cat_score - old_score:+.3f}",
                            "advance",
                        )

                self.playbook.append_iteration(
                    iteration, best_score, new_score, "ADVANCE",
                    f"changed-cat delta={decision.delta:+.4f} | {', '.join(changed_categories)}",
                )

                best_score = new_score
                # Update initial_result dimensions for next iteration comparison
                initial_result = new_result
                retry_count = 0

                self.trajectory.append({
                    "iteration": iteration,
                    "score": new_score,
                    "decision": "advance",
                    "delta": decision.delta,
                    "queries": queries,
                })

            elif decision.decision == "retry":
                retry_count += 1
                print(f"  Retrying (attempt {retry_count}/{self.max_retries})")

                self.trajectory.append({
                    "iteration": iteration,
                    "score": new_score,
                    "decision": "retry",
                    "delta": decision.delta,
                    "queries": queries,
                })

                # Don't revert — keep changes, try different mutations next round

            elif decision.decision == "rollback":
                # Revert config
                self.config_mgr.restore()
                state["config"] = self.config_mgr.load()

                # Record what failed
                for m in mutations:
                    self.playbook.update_category(
                        m["category_id"], "FAILED",
                        f"{m['variant_id']}: \"{m['template']}\" (no improvement)",
                    )
                    self._add_lesson(
                        iteration, new_score,
                        f"For {m['category_id']}, pattern \"{m['template']}\" "
                        f"did not improve GT (delta={decision.delta:+.4f})",
                        "rollback",
                    )

                self.playbook.append_iteration(
                    iteration, best_score, new_score, "ROLLBACK",
                    f"changed-cat delta={decision.delta:+.4f} | reverted {', '.join(changed_categories)}",
                )

                retry_count = 0

                self.trajectory.append({
                    "iteration": iteration,
                    "score": new_score,
                    "decision": "rollback",
                    "delta": decision.delta,
                    "queries": queries,
                })

            # Refresh state scores
            state["scores"] = {
                "overall_gt_mean": best_score,
                "categories": {
                    k: {"gt_avg": v} for k, v in
                    (new_result if decision.decision == "advance" else initial_result)
                    .dimension_scores.items()
                },
            }

        # Final summary
        self._print_summary(best_score, initial_result.score)

    def _run_pattern_tester_to_master(self, categories: list[str]) -> int:
        """Run pattern_tester writing to raw-results-master.json."""
        total = 0
        for cat in categories:
            cmd = [
                sys.executable, str(PATTERN_TESTER),
                "--config", "master_test_config.json",
                "--category", cat,
                "--output", "../searches/raw-results-master.json",
            ]

            result = subprocess.run(
                cmd, capture_output=True, text=True,
                cwd=str(SCRIPT_DIR), timeout=300,
            )

            if result.returncode != 0:
                print(f"  [ERR] pattern_tester for {cat}: {result.stderr[:200]}")
                continue

            for line in result.stdout.split("\n"):
                m = re.search(r"(\d+) queries run", line)
                if m:
                    total += int(m.group(1))

            if result.stdout.strip():
                last_lines = [l for l in result.stdout.strip().split("\n") if l.strip()]
                if last_lines:
                    print(f"  {cat}: {last_lines[-1]}")

        return total

    def _add_lesson(self, iteration: int, score: float, text: str,
                     operation: str) -> None:
        """Add a structured lesson to the LessonStore."""
        meta = ApplicabilityMeta(
            created_at=datetime.now().isoformat(),
            generation=iteration,
            best_score=score,
            operation_type=operation,
        )
        self.lesson_store.add_lesson(SCENARIO_NAME, text, meta)

    def _print_summary(self, final_score: float, initial_score: float) -> None:
        """Print final run summary."""
        print(f"\n{'=' * 70}")
        print("RUN SUMMARY")
        print(f"{'=' * 70}")
        print(f"  Initial GT: {initial_score:.4f}")
        print(f"  Final GT:   {final_score:.4f}")
        print(f"  Delta:      {final_score - initial_score:+.4f}")
        print(f"  Queries:    {self.queries_used}/{self.budget}")
        print(f"  Iterations: {len(self.trajectory)}")

        if self.trajectory:
            advances = sum(1 for t in self.trajectory if t["decision"] == "advance")
            retries = sum(1 for t in self.trajectory if t["decision"] == "retry")
            rollbacks = sum(1 for t in self.trajectory if t["decision"] == "rollback")
            print(f"  Advances:   {advances}")
            print(f"  Retries:    {retries}")
            print(f"  Rollbacks:  {rollbacks}")

        # Show lessons
        lessons = self.lesson_store.read_lessons(SCENARIO_NAME)
        if lessons:
            print(f"\n  Lessons learned: {len(lessons)}")
            for les in lessons[-5:]:
                print(f"    [{les.meta.operation_type}] {les.text}")

        print(f"\n  Playbook: {PLAYBOOK_PATH}")
        print(f"  Lessons:  {KNOWLEDGE_DIR / 'lessons.json'}")


# ---------------------------------------------------------------------------
# Display commands
# ---------------------------------------------------------------------------

def show_playbook() -> None:
    if PLAYBOOK_PATH.exists():
        print(PLAYBOOK_PATH.read_text(encoding="utf-8"))
    else:
        print("No playbook yet. Run the optimizer first.")


def show_lessons() -> None:
    store = LessonStore(
        knowledge_root=KNOWLEDGE_DIR.parent,
        skills_root=SCRIPT_DIR,
    )
    lessons = store.read_lessons(SCENARIO_NAME)
    if not lessons:
        print("No lessons yet. Run the optimizer first.")
        return

    gen = store.current_generation(SCENARIO_NAME)
    print(f"Lessons for '{SCENARIO_NAME}' (generation: {gen})")
    print(f"{'-' * 60}")

    for les in lessons:
        stale = les.is_stale(gen)
        superseded = les.is_superseded()
        status = "STALE" if stale else "SUPERSEDED" if superseded else "active"
        print(f"  [{les.meta.operation_type:>8}] gen={les.meta.generation} "
              f"score={les.meta.best_score:.3f} ({status})")
        print(f"           {les.text}")
        print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="AutoContext Search Pattern Optimizer"
    )
    parser.add_argument("--max-iterations", type=int, default=10,
                        help="Max optimization iterations (default: 10)")
    parser.add_argument("--budget", type=int, default=5000,
                        help="Max Serper queries (default: 5000)")
    parser.add_argument("--category", type=str,
                        help="Focus on single category")
    parser.add_argument("--min-improvement", type=float, default=0.005,
                        help="Min GT delta to advance (default: 0.005)")
    parser.add_argument("--max-retries", type=int, default=2,
                        help="Max retries before rollback (default: 2)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show mutations without running Serper")
    parser.add_argument("--playbook", action="store_true",
                        help="Show current playbook")
    parser.add_argument("--lessons", action="store_true",
                        help="Show accumulated lessons")

    args = parser.parse_args()

    if args.playbook:
        show_playbook()
        return

    if args.lessons:
        show_lessons()
        return

    runner = SearchPatternRunner(
        max_iterations=args.max_iterations,
        budget=args.budget,
        min_improvement=args.min_improvement,
        max_retries=args.max_retries,
        target_category=args.category,
        dry_run=args.dry_run,
    )
    runner.run()


if __name__ == "__main__":
    main()
