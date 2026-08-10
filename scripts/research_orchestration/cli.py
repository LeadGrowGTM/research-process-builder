"""Shared zero-cost composition root for autoresearch command-line adapters."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .artifacts import ArtifactStore
from .budgets import BudgetLimits
from .contracts import BudgetCharge, CheckerResult, EvaluationResult, Evidence, Experiment, Role, RunRequest
from .orchestrator import AutoresearchOrchestrator, RoleRunners


def _stub_roles() -> RoleRunners:
    experiment = Experiment("1.0", "search-flow", "A narrower query improves accuracy.", "Add the known domain term.")
    return RoleRunners(
        lambda _: experiment,
        lambda _: CheckerResult("1.0", "in_bounds", True, "accepted"),
        lambda _: CheckerResult("1.0", "novelty", True, "novel"),
        lambda _: (Evidence("1.0", "https://example.test/source", "deterministic stub evidence", "2026-08-10T00:00:00Z"),),
        lambda _: EvaluationResult("1.0", True, .90, "validated"),
        charges={role: BudgetCharge(stages=1) for role in Role},
    )


def _resume_request(store: ArtifactStore) -> RunRequest:
    value = store.load_and_validate()
    return RunRequest(value["schema_version"], value["run_id"], value["brief"], tuple(value["constraints"]),
                      value["baseline"], BudgetLimits(**value["budget_limits"]), value["approval_threshold"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Deterministic resumable autoresearch")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--stub-run", action="store_true")
    mode.add_argument("--resume", type=Path)
    parser.add_argument("--run-dir", type=Path)
    args = parser.parse_args(argv)
    if not (args.dry_run or args.stub_run or args.resume):
        print(json.dumps({"mode": "dry_run", "paid_cost_ceiling": 0.0}, sort_keys=True))
        return 0
    run_dir = args.resume or args.run_dir
    if args.dry_run:
        result = {"mode": "dry_run", "paid_cost_ceiling": 0.0}
        if run_dir is not None:
            result["run_dir"] = str(run_dir)
        print(json.dumps(result, sort_keys=True))
        return 0
    if args.stub_run and run_dir is None:
        parser.error("--run-dir is required for --stub-run")
    store = ArtifactStore(run_dir)
    request = _resume_request(store) if args.resume else RunRequest(
        "1.0", "stub-run", "Improve a read-only Search Flow.", ("read_only", "no_network", "no_secrets"),
        {"accuracy": .88}, BudgetLimits(max_stages=5), .90)
    print(AutoresearchOrchestrator(store, _stub_roles()).run(request).to_canonical_json())
    return 0
