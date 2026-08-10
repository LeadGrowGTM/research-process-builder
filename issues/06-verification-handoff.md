# Phase 06 — Verification, integrity audit, and local handoff

**Status:** verification and independent review passed; final tracker commit and report publication/export fallback pending
**Canonical source:** `.harness/goals/repo-cleanup-full-update/PLAN.md` → `## Phases` → Phase 6
**Routing:** `verification-before-completion`
**Commit:** `docs: record repository modernization verification`

## Deliverable

Run the exact mechanical gate, capture literal evidence, audit scope/security/preservation, and prepare a local three-format handoff for Prover and fresh Checker.

## Acceptance

- [x] Correct in-repo worktree/branch and unchanged recovery refs/stashes are evidenced.
- [x] Inventory verification reports 3,561 enumerated, recorded 3,558, zero unexplained; quarantine/recovery hashes pass.
- [x] MCP config parses, secret scan is empty, and no remote write/paid spend occurred.
- [x] Focused and authoritative tests pass with no new skips; both CLIs prove help/invalid/dry/stub/resume and zero duplicate paid work.
- [x] Diff audit finds no catalog/full GTM/live write/benchmark-runtime import/unrelated rewrite.
- [x] `HANDOFF.md`, `HANDOFF.html`, and `HANDOFF.excalidraw` contain preservation, architecture, evidence matrix, limitations/DRAFT live gaps, review commands, and commits without secrets or PII.
- [x] Checker receives the seven-dimension rubric; PASS requires each ≥4 and mean ≥4.5.
- [ ] Final post-commit `git status --short` is empty.

## Prohibitions

Shipping is `N/A - shipping not approved`: publish only the sanitized morning report via `lavish-axi share HANDOFF.html` (or the documented export fallback); do not push, open a PR, merge, deploy, mutate application data, or delete the original checkout/worktree/stashes.
