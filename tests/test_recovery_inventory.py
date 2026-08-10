from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.recovery_inventory import (
    InventoryEntry,
    enumerate_recovery,
    export_quarantine,
    validate_inventory,
    verify_quarantine_map,
)


def git(repo: Path, *args: str) -> str:
    env = os.environ.copy()
    for name in (
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_COMMON_DIR",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    ):
        env.pop(name, None)
    return subprocess.run(
        ["git", *args], cwd=repo, env=env, check=True, text=True, capture_output=True
    ).stdout.strip()


def test_git_fixture_ignores_inherited_git_routing_variables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    poison_git_dir = tmp_path / "poison.git"
    subprocess.run(["git", "init", "--bare", "-q", str(poison_git_dir)], check=True)
    poison_config_before = (poison_git_dir / "config").read_bytes()
    poison_index = poison_git_dir / "index"
    poison_index_before = poison_index.read_bytes() if poison_index.exists() else None
    for name, value in {
        "GIT_DIR": str(poison_git_dir),
        "GIT_WORK_TREE": str(tmp_path / "poison-work-tree"),
        "GIT_INDEX_FILE": str(poison_index),
        "GIT_COMMON_DIR": str(poison_git_dir),
        "GIT_OBJECT_DIRECTORY": str(poison_git_dir / "objects"),
        "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(poison_git_dir / "alternate-objects"),
    }.items():
        monkeypatch.setenv(name, value)
    fixture_repo = tmp_path / "fixture"
    fixture_repo.mkdir()

    git(fixture_repo, "init", "-q")

    assert (fixture_repo / ".git").is_dir()
    assert (poison_git_dir / "config").read_bytes() == poison_config_before
    assert (poison_index.read_bytes() if poison_index.exists() else None) == poison_index_before


@pytest.fixture
def recovery_fixture(tmp_path: Path) -> Path:
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "inventory@example.test")
    git(tmp_path, "config", "user.name", "Inventory test")
    (tmp_path / "tracked.py").write_text("before\n", encoding="utf-8")
    (tmp_path / "deleted.md").write_text("remove me\n", encoding="utf-8")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "base")
    (tmp_path / "tracked.py").write_text("after\n", encoding="utf-8")
    (tmp_path / "deleted.md").unlink()
    (tmp_path / "new").mkdir()
    (tmp_path / "new" / "data.json").write_text('{"new": true}\n', encoding="utf-8")
    git(tmp_path, "stash", "push", "--include-untracked", "-m", "fixture")
    return tmp_path


def fixture_ref(repo: Path) -> str:
    return git(repo, "rev-parse", "stash@{0}")


def test_enumerates_tracked_and_untracked_recovery_objects(recovery_fixture: Path) -> None:
    entries = enumerate_recovery(fixture_ref(recovery_fixture), cwd=recovery_fixture)

    assert {(entry.status, entry.path) for entry in entries} == {
        ("M", "tracked.py"),
        ("D", "deleted.md"),
        ("?", "new/data.json"),
    }
    assert all(entry.object_id and entry.recovery_command for entry in entries)
    reconciliation = validate_inventory(entries, recorded_count=2)
    assert reconciliation.enumerated_count == 3
    assert reconciliation.difference == 1
    assert reconciliation.unexplained_paths == ()


@pytest.mark.parametrize(
    "broken",
    [
        lambda entries: entries + [entries[0]],
        lambda entries: [replace(entries[0], classification="")] + entries[1:],
        lambda entries: [replace(entries[0], disposition="")] + entries[1:],
        lambda entries: [replace(entries[0], object_id="")] + entries[1:],
    ],
    ids=["duplicate-path", "missing-classification", "missing-disposition", "missing-object"],
)
def test_validation_fails_closed_for_incomplete_entries(recovery_fixture: Path, broken) -> None:
    entries = enumerate_recovery(fixture_ref(recovery_fixture), cwd=recovery_fixture)

    with pytest.raises(ValueError):
        validate_inventory(broken(entries), recorded_count=3)


def test_rejects_mutable_ref_names(recovery_fixture: Path) -> None:
    with pytest.raises(ValueError, match="immutable"):
        enumerate_recovery("stash@{0}", cwd=recovery_fixture)


def test_quarantine_hash_mismatch_fails_closed(tmp_path: Path) -> None:
    entry = InventoryEntry(
        status="?",
        path=".cache/output.bin",
        object_id="0123456789012345678901234567890123456789",
        classification="cache",
        disposition="quarantine",
        recovery_command="git show 0123456789012345678901234567890123456789 > .cache/output.bin",
    )
    source = tmp_path / "source.bin"
    source.write_bytes(b"original")
    map_path = tmp_path / "quarantine-map.csv"
    root = tmp_path / ".quarantine"

    export_quarantine([entry], root=root, map_path=map_path, object_reader=lambda _: source.read_bytes())
    verify_quarantine_map([entry], root=root, map_path=map_path, object_reader=lambda _: source.read_bytes())
    root.joinpath(".cache", "output.bin").write_bytes(b"tampered")

    with pytest.raises(ValueError, match="SHA-256"):
        verify_quarantine_map([entry], root=root, map_path=map_path, object_reader=lambda _: source.read_bytes())


def test_verify_rejects_coordinated_inventory_tampering(recovery_fixture: Path, tmp_path: Path) -> None:
    from scripts.recovery_inventory import verify_inventory_against_recovery, write_inventory

    entries = enumerate_recovery(fixture_ref(recovery_fixture), cwd=recovery_fixture)
    tampered = [
        replace(
            entry,
            object_id="0123456789012345678901234567890123456789",
            recovery_command="git show 0123456789012345678901234567890123456789 > tracked.py",
        )
        if entry.path == "tracked.py"
        else entry
        for entry in entries
    ]
    manifest = tmp_path / "inventory.csv"
    write_inventory(tampered, manifest)

    with pytest.raises(ValueError, match="authoritative recovery"):
        verify_inventory_against_recovery(manifest, fixture_ref(recovery_fixture), cwd=recovery_fixture)


def test_verify_rejects_a_moved_recovery_ref(recovery_fixture: Path, tmp_path: Path) -> None:
    from scripts.recovery_inventory import verify_inventory_against_recovery, write_inventory

    first_ref = fixture_ref(recovery_fixture)
    entries = enumerate_recovery(first_ref, cwd=recovery_fixture)
    manifest = tmp_path / "inventory.csv"
    write_inventory(entries, manifest)
    (recovery_fixture / "tracked.py").write_text("later\n", encoding="utf-8")
    git(recovery_fixture, "stash", "push", "-m", "later fixture")
    git(recovery_fixture, "update-ref", "refs/recovery/fixture", fixture_ref(recovery_fixture))

    with pytest.raises(ValueError, match="recovery commit"):
        verify_inventory_against_recovery(manifest, "refs/recovery/fixture", cwd=recovery_fixture)


def test_verify_rejects_coordinated_quarantine_payload_and_map_tampering(tmp_path: Path) -> None:
    entry = InventoryEntry(
        status="?",
        path=".cache/output.bin",
        object_id="0123456789012345678901234567890123456789",
        classification="cache",
        disposition="quarantine",
        recovery_command="git show 0123456789012345678901234567890123456789 > .cache/output.bin",
    )
    map_path = tmp_path / "quarantine-map.csv"
    root = tmp_path / ".quarantine"
    export_quarantine([entry], root=root, map_path=map_path, object_reader=lambda _: b"tampered")

    with pytest.raises(ValueError, match="authoritative object"):
        verify_quarantine_map(
            [entry], root=root, map_path=map_path, object_reader=lambda _: b"original"
        )


def test_verify_rejects_duplicate_quarantine_map_rows(tmp_path: Path) -> None:
    entry = InventoryEntry(
        status="?",
        path=".cache/output.bin",
        object_id="0123456789012345678901234567890123456789",
        classification="cache",
        disposition="quarantine",
        recovery_command="git show 0123456789012345678901234567890123456789 > .cache/output.bin",
    )
    map_path = tmp_path / "quarantine-map.csv"
    root = tmp_path / ".quarantine"
    export_quarantine([entry], root=root, map_path=map_path, object_reader=lambda _: b"original")
    with map_path.open("a", encoding="utf-8") as handle:
        handle.write(".cache/output.bin,0123456789012345678901234567890123456789,"
                     "0682c5f2076f099c34a608463c1847d58c13c5b51b11a3fa8f6e2d8c0e8a7d2c,"
                     ".cache/output.bin\n")

    with pytest.raises(ValueError, match="duplicate"):
        verify_quarantine_map([entry], root=root, map_path=map_path, object_reader=lambda _: b"original")


def _action_entry(
    *,
    classification: str = "generated-output",
    disposition: str = "quarantine",
) -> InventoryEntry:
    object_id = "a" * 40
    return InventoryEntry(
        status="?",
        path="output/run.json",
        object_id=object_id,
        classification=classification,
        disposition=disposition,
        recovery_command=f"git show {object_id} > output/run.json",
        recovery_commit="b" * 40,
    )


def _action_row(entry: InventoryEntry, *, action: str = "removal") -> dict[str, str]:
    return {
        "action": action,
        "inventory_path": entry.path,
        "destination_path": "",
        "object_id": entry.object_id,
        "recovery_commit": entry.recovery_commit,
        "recovery_command": entry.recovery_command,
        "quarantine_path": entry.path,
        "quarantine_sha256": "c" * 64,
    }


def _quarantine_row(entry: InventoryEntry) -> dict[str, str]:
    return {
        "path": entry.path,
        "object_id": entry.object_id,
        "sha256": "c" * 64,
        "local_path": entry.path,
    }


def _action_validator():
    from scripts import recovery_inventory

    validator = getattr(recovery_inventory, "validate_action_decisions", None)
    assert callable(validator), "action decision validator must exist"
    return validator


def test_action_decision_validation_accepts_empty_and_fully_evidenced_actions() -> None:
    entry = _action_entry()
    validator = _action_validator()

    validator([entry], [], [_quarantine_row(entry)])
    validator([entry], [_action_row(entry)], [_quarantine_row(entry)])


@pytest.mark.parametrize("field", ["quarantine_path", "quarantine_sha256"])
def test_action_decision_validation_rejects_missing_quarantine_evidence(field: str) -> None:
    entry = _action_entry()
    decision = _action_row(entry)
    decision[field] = ""

    with pytest.raises(ValueError, match="quarantine evidence"):
        _action_validator()([entry], [decision], [_quarantine_row(entry)])


def test_action_decision_validation_rejects_mismatched_or_duplicate_quarantine_evidence() -> None:
    entry = _action_entry()
    mismatched = _action_row(entry)
    mismatched["quarantine_sha256"] = "d" * 64

    with pytest.raises(ValueError, match="quarantine evidence"):
        _action_validator()([entry], [mismatched], [_quarantine_row(entry)])
    with pytest.raises(ValueError, match="duplicate quarantine map"):
        _action_validator()([entry], [_action_row(entry)], [_quarantine_row(entry), _quarantine_row(entry)])


def test_action_decision_validation_rejects_unclassified_or_unauthorized_actions() -> None:
    unclassified = _action_entry(classification="")
    with pytest.raises(ValueError, match="classification"):
        _action_validator()([unclassified], [_action_row(unclassified)], [_quarantine_row(unclassified)])

    quarantined = _action_entry()
    with pytest.raises(ValueError, match="unauthorized action"):
        _action_validator()([quarantined], [_action_row(quarantined, action="restoration")], [_quarantine_row(quarantined)])
