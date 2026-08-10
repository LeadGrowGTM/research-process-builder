"""Inventory an immutable Git recovery commit without mutating it."""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path, PurePosixPath
import subprocess
from typing import Callable, Iterable, Sequence


CLASSIFICATIONS = {
    "source",
    "tests",
    "durable-research",
    "generated-output",
    "secrets-config",
    "cache",
    "campaign-specific",
    "obsolete-duplicate",
}
DISPOSITIONS = {"restore-reviewed", "retain-tracked", "quarantine", "document-only"}
CSV_FIELDS = ["status", "path", "object_id", "recovery_commit", "classification", "disposition", "recovery_command"]
QUARANTINE_FIELDS = ["path", "object_id", "sha256", "local_path"]
ACTION_DECISION_FIELDS = [
    "action", "inventory_path", "destination_path", "object_id", "recovery_commit",
    "recovery_command", "quarantine_path", "quarantine_sha256",
]
AUTHORIZED_ACTIONS = {
    "restoration": {"restore-reviewed"},
    "removal": {"document-only", "quarantine"},
}
DEFAULT_QUARANTINE_ROOT = Path(".quarantine/repo-cleanup-full-update")


@dataclass(frozen=True)
class InventoryEntry:
    status: str
    path: str
    object_id: str
    classification: str
    disposition: str
    recovery_command: str
    recovery_commit: str = ""


@dataclass(frozen=True)
class Reconciliation:
    recorded_count: int
    enumerated_count: int
    difference: int
    unexplained_paths: tuple[str, ...]


_GIT_ROUTING_VARIABLES = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_COMMON_DIR",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
)


def _clean_git_environment() -> dict[str, str]:
    environment = os.environ.copy()
    for name in _GIT_ROUTING_VARIABLES:
        environment.pop(name, None)
    return environment


def _run_git(args: Sequence[str], cwd: Path | None = None) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, env=_clean_git_environment(),
        check=True, text=True, capture_output=True,
    ).stdout


def _run_git_bytes(args: Sequence[str], cwd: Path | None = None) -> bytes:
    return subprocess.run(
        ["git", *args], cwd=cwd, env=_clean_git_environment(),
        check=True, capture_output=True,
    ).stdout


def _assert_immutable_ref(ref: str) -> None:
    is_object_id = len(ref) == 40 and all(character in "0123456789abcdef" for character in ref)
    if not (is_object_id or ref.startswith("refs/recovery/")):
        raise ValueError("recovery ref must be immutable: use a full object ID or refs/recovery/*")


def _resolve_recovery_commit(ref: str, cwd: Path | None = None) -> str:
    _assert_immutable_ref(ref)
    return _run_git(["rev-parse", f"{ref}^{{commit}}"], cwd).strip()


def _canonical_path(path: str) -> str:
    pure_path = PurePosixPath(path)
    if not path or pure_path.is_absolute() or ".." in pure_path.parts:
        raise ValueError(f"unsafe recovery path: {path!r}")
    return pure_path.as_posix()


def _classification_and_disposition(path: str, status: str) -> tuple[str, str]:
    parts = set(PurePosixPath(path).parts)
    name = PurePosixPath(path).name.lower()
    lowered = path.lower()
    if parts & {".cache", "__pycache__", ".pytest_cache", "node_modules", ".trigger"}:
        return "cache", "quarantine"
    if name.startswith(".env") or any(token in name for token in ("secret", "credential", "token", "apikey", "api-key")):
        return "secrets-config", "quarantine"
    if "archive" in parts or "deprecated" in name or "completed" in name:
        return "obsolete-duplicate", "document-only"
    if "motorica" in lowered or "campaign" in lowered:
        return "campaign-specific", "quarantine"
    if path.startswith("tests/") or name.startswith("test_") or "/test_" in path:
        return "tests", "restore-reviewed"
    if path.startswith(("docs/", "knowledge/", "ground-truth/")) or name.endswith((".md", ".pdf")):
        return "durable-research", "document-only"
    if path.startswith(("searches/", "output/")) or name.endswith((".json", ".csv", ".jsonl")):
        return "generated-output", "quarantine"
    if status == "M":
        return "source", "retain-tracked"
    return "source", "restore-reviewed"


def _entry(status: str, path: str, object_id: str, recovery_commit: str) -> InventoryEntry:
    canonical_path = _canonical_path(path)
    classification, disposition = _classification_and_disposition(canonical_path, status)
    return InventoryEntry(
        status=status,
        path=canonical_path,
        object_id=object_id,
        classification=classification,
        disposition=disposition,
        recovery_command=f"git show {object_id} > {canonical_path}",
        recovery_commit=recovery_commit,
    )


def enumerate_recovery(ref: str, cwd: Path | None = None) -> list[InventoryEntry]:
    """Enumerate the tracked diff and untracked-tree blobs in a recovery stash commit."""
    recovery_commit = _resolve_recovery_commit(ref, cwd)
    entries: list[InventoryEntry] = []
    tracked = _run_git(["diff", "--name-status", "--no-renames", f"{recovery_commit}^1", recovery_commit], cwd)
    for line in tracked.splitlines():
        status, path = line.split("\t", maxsplit=1)
        object_ref = f"{recovery_commit}^1:{path}" if status == "D" else f"{recovery_commit}:{path}"
        object_id = _run_git(["rev-parse", object_ref], cwd).strip()
        entries.append(_entry(status, path, object_id, recovery_commit))

    tree = _run_git_bytes(["ls-tree", "-r", "-z", "--full-tree", f"{recovery_commit}^3"], cwd)
    for record in tree.split(b"\0"):
        if not record:
            continue
        metadata, raw_path = record.split(b"\t", maxsplit=1)
        _mode, object_type, object_id = metadata.decode("ascii").split()
        if object_type != "blob":
            continue
        entries.append(_entry("?", raw_path.decode("utf-8", "surrogateescape"), object_id, recovery_commit))
    return sorted(entries, key=lambda entry: (entry.path, entry.status))


def validate_inventory(entries: Iterable[InventoryEntry], recorded_count: int) -> Reconciliation:
    materialized = list(entries)
    paths: set[str] = set()
    for entry in materialized:
        canonical = _canonical_path(entry.path)
        if canonical != entry.path:
            raise ValueError(f"non-canonical recovery path: {entry.path!r}")
        if entry.path in paths:
            raise ValueError(f"duplicate recovery path: {entry.path}")
        paths.add(entry.path)
        if not entry.object_id:
            raise ValueError(f"missing object ID: {entry.path}")
        if len(entry.recovery_commit) != 40 or any(character not in "0123456789abcdef" for character in entry.recovery_commit):
            raise ValueError(f"missing or invalid recovery commit: {entry.path}")
        if entry.classification not in CLASSIFICATIONS:
            raise ValueError(f"missing or invalid classification: {entry.path}")
        if entry.disposition not in DISPOSITIONS:
            raise ValueError(f"missing or invalid disposition: {entry.path}")
        if not entry.recovery_command:
            raise ValueError(f"missing recovery command: {entry.path}")
    return Reconciliation(
        recorded_count=recorded_count,
        enumerated_count=len(materialized),
        difference=len(materialized) - recorded_count,
        unexplained_paths=(),
    )


def write_inventory(entries: Iterable[InventoryEntry], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for entry in entries:
            writer.writerow({field: getattr(entry, field) for field in CSV_FIELDS})


def read_inventory(manifest: Path) -> list[InventoryEntry]:
    with manifest.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != CSV_FIELDS:
            raise ValueError(f"unexpected inventory columns: {reader.fieldnames}")
        return [InventoryEntry(**row) for row in reader]


def read_action_decisions(record: Path) -> list[dict[str, str]]:
    with record.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != ACTION_DECISION_FIELDS:
            raise ValueError(f"unexpected action decision columns: {reader.fieldnames}")
        return list(reader)


def read_quarantine_map(map_path: Path) -> list[dict[str, str]]:
    with map_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != QUARANTINE_FIELDS:
            raise ValueError(f"unexpected quarantine columns: {reader.fieldnames}")
        return list(reader)


def validate_action_decisions(
    entries: Iterable[InventoryEntry],
    decisions: Iterable[dict[str, str]],
    quarantine_rows: Iterable[dict[str, str]],
) -> None:
    """Validate every recorded cleanup action against immutable recovery evidence."""
    inventory = list(entries)
    validate_inventory(inventory, recorded_count=len(inventory))
    by_path = {entry.path: entry for entry in inventory}

    quarantine_by_path: dict[str, dict[str, str]] = {}
    for row in quarantine_rows:
        path = row.get("path", "")
        if path in quarantine_by_path:
            raise ValueError(f"duplicate quarantine map evidence: {path}")
        if set(row) != set(QUARANTINE_FIELDS):
            raise ValueError("unexpected quarantine map evidence columns")
        quarantine_by_path[path] = row

    seen_actions: set[tuple[str, str]] = set()
    seen_quarantine_evidence: set[tuple[str, str]] = set()
    for decision in decisions:
        if set(decision) != set(ACTION_DECISION_FIELDS):
            raise ValueError("unexpected action decision columns")
        action = decision["action"]
        inventory_path = decision["inventory_path"]
        if (action, inventory_path) in seen_actions:
            raise ValueError(f"duplicate action decision: {action} {inventory_path}")
        seen_actions.add((action, inventory_path))
        if inventory_path not in by_path:
            raise ValueError(f"action inventory path is not recorded: {inventory_path}")
        entry = by_path[inventory_path]
        if action not in AUTHORIZED_ACTIONS or entry.disposition not in AUTHORIZED_ACTIONS[action]:
            raise ValueError(f"unauthorized action for disposition: {action} {entry.disposition}")
        if decision["object_id"] != entry.object_id:
            raise ValueError(f"action object ID does not match inventory: {inventory_path}")
        if decision["recovery_commit"] != entry.recovery_commit:
            raise ValueError(f"action recovery commit does not match inventory: {inventory_path}")
        if decision["recovery_command"] != entry.recovery_command:
            raise ValueError(f"action recovery command does not match inventory: {inventory_path}")

        has_quarantine_fields = bool(decision["quarantine_path"] or decision["quarantine_sha256"])
        if entry.disposition != "quarantine":
            if has_quarantine_fields:
                raise ValueError(f"unexpected quarantine evidence: {inventory_path}")
            continue
        if not decision["quarantine_path"] or not decision["quarantine_sha256"]:
            raise ValueError(f"missing quarantine evidence: {inventory_path}")
        evidence_key = (decision["quarantine_path"], decision["quarantine_sha256"])
        if evidence_key in seen_quarantine_evidence:
            raise ValueError(f"duplicate quarantine evidence: {inventory_path}")
        seen_quarantine_evidence.add(evidence_key)
        evidence = quarantine_by_path.get(inventory_path)
        if evidence is None:
            raise ValueError(f"missing quarantine map evidence: {inventory_path}")
        if evidence["object_id"] != entry.object_id:
            raise ValueError(f"quarantine evidence object ID mismatch: {inventory_path}")
        if (
            decision["quarantine_path"] != evidence["local_path"]
            or decision["quarantine_sha256"] != evidence["sha256"]
        ):
            raise ValueError(f"quarantine evidence mismatch: {inventory_path}")

def verify_inventory_against_recovery(
    manifest: Path, expected_ref: str, cwd: Path | None = None
) -> list[InventoryEntry]:
    """Fail closed unless the CSV exactly matches the expected recovery commit."""
    expected_commit = _resolve_recovery_commit(expected_ref, cwd)
    recorded = read_inventory(manifest)
    recorded_commits = {entry.recovery_commit for entry in recorded}
    if recorded_commits != {expected_commit}:
        raise ValueError("recovery commit does not match the expected immutable recovery ref")
    authoritative = enumerate_recovery(expected_commit, cwd)
    if recorded != authoritative:
        raise ValueError("inventory does not match the authoritative recovery object tree")
    return recorded


def export_quarantine(
    entries: Iterable[InventoryEntry],
    root: Path,
    map_path: Path,
    object_reader: Callable[[str], bytes] | None = None,
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    quarantined = [entry for entry in entries if entry.disposition == "quarantine"]
    reader = object_reader or (lambda object_id: _run_git_bytes(["cat-file", "-p", object_id]))
    with map_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=QUARANTINE_FIELDS)
        writer.writeheader()
        for entry in quarantined:
            target = root / Path(entry.path)
            target.parent.mkdir(parents=True, exist_ok=True)
            contents = reader(entry.object_id)
            target.write_bytes(contents)
            writer.writerow(
                {
                    "path": entry.path,
                    "object_id": entry.object_id,
                    "sha256": hashlib.sha256(contents).hexdigest(),
                    "local_path": entry.path,
                }
            )


def verify_quarantine_map(
    entries: Iterable[InventoryEntry],
    root: Path,
    map_path: Path,
    object_reader: Callable[[str], bytes] | None = None,
) -> None:
    expected = {entry.path: entry for entry in entries if entry.disposition == "quarantine"}
    with map_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != QUARANTINE_FIELDS:
            raise ValueError(f"unexpected quarantine columns: {reader.fieldnames}")
        rows = list(reader)
    mapped: dict[str, dict[str, str]] = {}
    for row in rows:
        path = row["path"]
        if path in mapped:
            raise ValueError(f"duplicate quarantine map row: {path}")
        mapped[path] = row
    if set(mapped) != set(expected):
        raise ValueError("quarantine map does not match quarantine inventory entries")
    authoritative_reader = object_reader or (lambda object_id: _run_git_bytes(["cat-file", "-p", object_id]))
    for path, entry in expected.items():
        row = mapped[path]
        if row["object_id"] != entry.object_id or row["local_path"] != entry.path:
            raise ValueError(f"quarantine map metadata mismatch: {path}")
        authoritative_digest = hashlib.sha256(authoritative_reader(entry.object_id)).hexdigest()
        if row["sha256"] != authoritative_digest:
            raise ValueError(f"quarantine SHA-256 does not match authoritative object: {path}")
        contents = (root / Path(path)).read_bytes()
        if hashlib.sha256(contents).hexdigest() != authoritative_digest:
            raise ValueError(f"quarantine SHA-256 does not match authoritative object: {path}")


def write_summary(reconciliation: Reconciliation, summary: Path) -> None:
    summary.parent.mkdir(parents=True, exist_ok=True)
    summary.write_text(
        "# Recovery inventory manifest\n\n"
        f"- Recorded status-era observation: {reconciliation.recorded_count}\n"
        f"- Authoritative preserved object-tree coverage: {reconciliation.enumerated_count}\n"
        f"- Difference: {reconciliation.difference:+d}\n"
        f"- Unexplained paths: {len(reconciliation.unexplained_paths)}\n\n"
        "The earlier status-era observation was 3,558. The preserved object-tree coverage "
        "of 3,561 is authoritative for this inventory. This manifest does not infer a "
        "specific cause for the difference without further evidence.\n",
        encoding="utf-8",
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    generate = subcommands.add_parser("generate")
    generate.add_argument("--ref", required=True)
    generate.add_argument("--recorded-count", type=int, required=True)
    generate.add_argument("--output", type=Path, required=True)
    generate.add_argument("--summary", type=Path, required=True)
    generate.add_argument("--quarantine-map", type=Path, required=True)
    generate.add_argument("--quarantine-root", type=Path, default=DEFAULT_QUARANTINE_ROOT)
    verify = subcommands.add_parser("verify")
    verify.add_argument("--manifest", type=Path, required=True)
    verify.add_argument("--expected-recorded-count", type=int, required=True)
    verify.add_argument("--expected-ref", required=True)
    verify.add_argument("--quarantine-map", type=Path)
    verify.add_argument("--quarantine-root", type=Path, default=DEFAULT_QUARANTINE_ROOT)
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.command == "generate":
        entries = enumerate_recovery(args.ref)
        reconciliation = validate_inventory(entries, args.recorded_count)
        write_inventory(entries, args.output)
        export_quarantine(entries, root=args.quarantine_root, map_path=args.quarantine_map)
        verify_quarantine_map(entries, root=args.quarantine_root, map_path=args.quarantine_map)
        write_summary(reconciliation, args.summary)
    else:
        entries = verify_inventory_against_recovery(args.manifest, args.expected_ref)
        reconciliation = validate_inventory(entries, args.expected_recorded_count)
        quarantine_map = args.quarantine_map or args.manifest.with_name("quarantine-map.csv")
        verify_quarantine_map(entries, root=args.quarantine_root, map_path=quarantine_map)
    print(
        f"enumerated={reconciliation.enumerated_count} recorded={reconciliation.recorded_count} "
        f"difference={reconciliation.difference:+d} unexplained={len(reconciliation.unexplained_paths)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
