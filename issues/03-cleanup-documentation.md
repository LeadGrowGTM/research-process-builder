# Phase 03 — Cleanup policy and verified repository guidance

**Status:** complete ? reviewed; commits `0a2d9f7`, `fe07f64`, `7cd455f`, `d7e5222`
**Canonical source:** `.harness/goals/repo-cleanup-full-update/PLAN.md` → `## Phases` → Phase 3
**Routing:** direct with repository-policy tests
**Commit:** `chore: clarify repository policy and guidance`

## Deliverable

Perform only manifest-authorized cleanup and make root guidance accurately describe repository purpose, commands, dependencies, architecture, safety, artifacts, and approval lifecycle.

## Acceptance

- [ ] Every removal/restoration maps to a reviewed Phase 1 disposition and recovery object.
- [ ] Quarantine hashes are verified before removal; generated/cache/secret/run paths and `.worktrees/` are ignored.
- [ ] Root `CLAUDE.md` links canonical `CONTEXT.md` and matches observed CLI/file behavior.
- [ ] README/SKILL changes are limited to verified corrections.
- [ ] Documented CLIs return `--help` successfully.
- [ ] `py -m pytest tests/test_repository_policy.py -q` and `git diff --check` pass after a recorded red run.
- [ ] Proof and phase commit SHA are appended to `PROGRESS.md`.

## Prohibitions

No bulk formatting, unrelated normalization, unmanifested deletion, or recovery-ref/stash mutation.
