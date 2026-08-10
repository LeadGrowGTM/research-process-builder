from __future__ import annotations

from dataclasses import replace
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
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, text=True, capture_output=True
    ).stdout.strip()


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
