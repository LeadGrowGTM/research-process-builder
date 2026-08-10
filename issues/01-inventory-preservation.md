# Phase 01 — Inventory, preservation, and reversible quarantine

**Status:** complete ? reviewed; commits `88be158`, `307d34c`, `6853794`
**Canonical source:** `.harness/goals/repo-cleanup-full-update/PLAN.md` → `## Phases` → Phase 1
**Routing:** direct, with TDD for inventory verification
**Commit:** `chore: preserve preflight repository inventory`

## Deliverable

Generate a complete object-backed inventory/recovery map from the immutable initial-dirty recovery ref, classify every tracked and untracked path, and hash-verify an ignored local quarantine before any reviewed removal.

## Acceptance

- [x] Recovery refs and named stashes remain unchanged/read-only.
- [x] `inventory.csv` gives every canonical path a status, category, disposition, object ID, ref, and exact recovery command.
- [x] `quarantine-map.csv` verifies materialized SHA-256 values under ignored `.quarantine/repo-cleanup-full-update/`.
- [x] Count evidence states recorded baseline 3,558 and authoritative enumeration 42 tracked + 3,519 untracked = 3,561 unique; all 3,561 are covered and the +3 is not guessed away.
- [x] `py -m pytest tests/test_recovery_inventory.py -q` passes after a recorded red run.
- [x] `py scripts/recovery_inventory.py verify --manifest docs/recovery/repo-cleanup-full-update/inventory.csv --expected-recorded-count 3558 --expected-ref refs/recovery/repo-cleanup-full-update/initial-dirty --quarantine-map docs/recovery/repo-cleanup-full-update/quarantine-map.csv --quarantine-root .quarantine/repo-cleanup-full-update` reports zero unexplained paths.
- [x] Proof and phase commit SHA are appended to `PROGRESS.md`.

## Prohibitions

Do not apply/pop/drop/rewrite the stash, mutate recovery refs, delete unique work, or classify solely from filenames without reviewing representative contents.
