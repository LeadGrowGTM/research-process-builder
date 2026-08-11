# Phase 05 — Test-first resumable orchestration and provider seams

**Status:** complete — reviewed; final commit `c6a6528`
**Canonical source:** `.harness/goals/repo-cleanup-full-update/PLAN.md` → `## Phases` → Phase 5
**Routing:** `test-driven-development`
**Phase commit:** `feat: add resumable autoresearch orchestration`

## Deliverable

Implement strict contracts, pre-charged budgets, canonical persisted artifacts, provider-neutral read seams, a pure deterministic Gate, the deep orchestrator, and thin primary/compatibility CLIs.

## Acceptance

- [x] Fresh Inventor/checker/evaluator envelopes have unique identities, bounded allowlisted fields, and no raw transcript/inherited chat context.
- [x] Out-of-bounds and duplicate candidates are rejected before Executor; Evaluator is independent and cannot invoke providers.
- [x] All `advance|retry|rollback|halt_for_review` actions and reason codes, ≥90% human-review halt, budgets, retry exhaustion, corruption/version mismatch, and failures have table-driven tests.
- [x] `run.json`, append-only `journal.jsonl`, role artifacts, content-addressed objects, and reconstructible summary use strict versions/hashes and atomic replacement.
- [x] Resume begins at the first missing stage and repeats neither completed idempotency keys nor charges.
- [x] Search starts from a query; extraction requires known URLs and orders fetch/scrape → selector → regex → deterministic patterns → explicitly enabled optional LLM.
- [x] Both CLIs prove help, invalid input, zero-cost dry-run default, stub-run, run-dir, and resume via subprocess tests.
- [x] Focused command in canonical PLAN passes with no new skip/xfail; stub artifacts prove all roles/gates at zero network/paid cost.
- [x] Intermediate commits and final phase proof/SHA are appended to `PROGRESS.md`.

## Prohibitions

Do not import agent-harness injected runtime, build a flow catalog/full GTM provider, serialize secrets/transcripts, or call paid/live providers.
