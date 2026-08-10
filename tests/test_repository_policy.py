"""Repository policy contracts for recovered artifacts and operator guidance."""

from __future__ import annotations

import csv
from pathlib import Path
import re
import subprocess
import sys

import scripts.recovery_inventory as recovery_inventory


ROOT = Path(__file__).resolve().parents[1]
INVENTORY = ROOT / "docs" / "recovery" / "repo-cleanup-full-update" / "inventory.csv"
QUARANTINE_MAP = ROOT / "docs" / "recovery" / "repo-cleanup-full-update" / "quarantine-map.csv"
ACTION_DECISIONS = ROOT / "docs" / "recovery" / "repo-cleanup-full-update" / "action-decisions.csv"
DOCUMENTED_ENTRY_POINTS = (
    "scripts/pattern_tester.py",
    "scripts/gt_evaluator.py",
    "scripts/validate.py",
    "scripts/autoresearch.py",
)


def _inventory_rows() -> list[dict[str, str]]:
    with INVENTORY.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _is_ignored(path: str) -> bool:
    result = subprocess.run(
        ["git", "check-ignore", "--quiet", "--no-index", path],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def test_removed_recovered_paths_have_disposition_and_recovery_evidence() -> None:
    rows = _inventory_rows()
    removed = [row for row in rows if row["status"] == "D"]

    assert removed, "the recovery inventory must retain deleted-path evidence"
    for row in removed:
        assert row["disposition"], row["path"]
        assert re.fullmatch(r"[0-9a-f]{40}", row["object_id"]), row["path"]
        assert row["recovery_command"].startswith("git show "), row["path"]


def test_recovery_quarantine_destination_is_ignored() -> None:
    assert _is_ignored(".quarantine/repo-cleanup-full-update/payload.bin")


def test_local_secrets_run_artifacts_caches_and_worktrees_are_ignored() -> None:
    for path in (
        ".env",
        ".env.local",
        ".envlocal",
        "output/run.json",
        "run.json.out",
        "__pycache__/module.pyc",
        ".pytest_cache/v/cache/nodeids",
        ".trigger/cache.json",
        "node_modules/package/index.js",
        ".worktrees/task/file.txt",
    ):
        assert _is_ignored(path), path


def test_recovery_action_decisions_are_evidence_backed_and_currently_empty() -> None:
    assert ACTION_DECISIONS.is_file(), "every cleanup phase must record its action decisions"
    validator = getattr(recovery_inventory, "validate_action_decisions", None)
    assert callable(validator), "action decisions must use the recovery inventory validator"

    decisions = recovery_inventory.read_action_decisions(ACTION_DECISIONS)
    quarantine_rows = recovery_inventory.read_quarantine_map(QUARANTINE_MAP)
    validator(recovery_inventory.read_inventory(INVENTORY), decisions, quarantine_rows)
    assert not decisions, "the Phase 3 action record proves no restoration or removal occurred"

def test_documented_python_entry_points_exist_and_offer_help() -> None:
    for relative_path in DOCUMENTED_ENTRY_POINTS:
        entry_point = ROOT / relative_path
        assert entry_point.is_file(), relative_path
        result = subprocess.run(
            [sys.executable, str(entry_point), "--help"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0, f"{relative_path}: {result.stderr}"
        assert "usage:" in result.stdout.lower(), relative_path


def test_claude_guidance_links_canonical_context_and_review_lifecycle() -> None:
    text = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")

    assert "[CONTEXT.md](CONTEXT.md)" in text
    assert re.search(r"programmed ground-truth validation.*>=\s*90%", text, re.IGNORECASE | re.DOTALL)
    assert re.search(r">=\s*90%.*explicit human review", text, re.IGNORECASE | re.DOTALL)