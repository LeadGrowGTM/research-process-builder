---
title: Repository cleanup and resumable autoresearch — draft handoff
quality: draft
status: live verification pending
as_of: 2026-08-10
---

# DRAFT — Repository cleanup and resumable autoresearch

## Eval Loop Design

- Reward signal: evidenced acceptance-criterion pass percentage
- Mechanical gate: deterministic focused/full tests, schema/config parsing, CLI smoke/stub/resume checks, secret scan, inventory integrity, and clean task-worktree status
- Qualitative gate: fresh Checker scores all seven harness dimensions from 1–5 using artifact and Prover evidence
- Max cycles: 3
- Done condition: 100% criteria pass; every Checker dimension >=4 and mean >=4.5/5
## Published Report

**PENDING.** Publishing is not authorized yet. No hosted URL has been created.

This is a sanitized local handoff at `c6a6528ff2b32063586825631ce2a28676b980ee` on branch `wt/repo-cleanup-full-update`. It contains no credentials, update keys, or client PII. It is not a completion claim.

## Current state

| Gate | State | Evidence boundary |
|---|---|---|
| Maker implementation through `c6a6528` | RECORDED | Local Git history |
| Mechanical gate | **PENDING** | Exact Phase 6 command set has not been run and recorded |
| Prover | **PENDING** | No independent PROOF VERDICT received |
| Checker | **PENDING** | No fresh seven-dimension scorecard received |
| Published URL | **PENDING** | Publishing explicitly deferred |

## What is recorded

- Preservation and recovery inventory, repository policy/guidance, repository-scoped provider configuration, provider-neutral source contracts, durable artifact storage, and resumable orchestration are represented in commits through `c6a6528`.
- The approval lifecycle remains: programmed ground-truth validation of at least 90%, followed by explicit human review.
- Autoresearch is shaped as fresh, bounded roles with versioned schema-validated artifacts rather than inherited chat context.
- Both primary CLI entry points compose over the orchestration layer; final CLI and end-to-end proof remains a Prover responsibility.

## Preservation ledger

Verified local references and counts:

- Recorded status-era observation: **3,558** paths.
- Authoritative preserved object-tree coverage: **3,561** paths.
- Reconciliation: **+3**, documented without deleting evidence to force agreement.
- Unexplained paths: **0**.
- Inventory rows currently parse to **3,561**.
- Quarantine-map rows currently parse to **2,883**.
- Initial dirty recovery ref: `refs/recovery/repo-cleanup-full-update/initial-dirty` → `e3932d55217c29ac28eca16fdc7e6f6c5c3e3337`.
- Dashboard metadata recovery ref recorded by the brief: `refs/recovery/repo-cleanup-full-update/dashboard-metadata` → `7bca5038f31a9427f1781e823a388d0dcf2ac33d` (re-verification pending in the mechanical gate).
- The action ledger contains zero action rows: no restoration or removal was authorized in the reviewed policy phase.
- Named recovery stash remains read-only; do not apply, pop, drop, rewrite, or delete it.

Primary recovery artifacts:

- `docs/recovery/repo-cleanup-full-update/inventory.csv`
- `docs/recovery/repo-cleanup-full-update/quarantine-map.csv`
- `docs/recovery/repo-cleanup-full-update/action-decisions.csv`
- `docs/recovery/repo-cleanup-full-update/manifest.md`

## Architecture flow

```text
Outer assurance loop
Planner → Maker → Mechanical gate → Prover → fresh Checker → local handoff
                         PENDING       PENDING       PENDING

Inner autoresearch loop
compact versioned artifacts
  → fresh Inventor
  → independent In-bounds Checker
  → independent Novelty Checker
  → Executor
  → independent Evaluator
  → deterministic Gate
       ├─ advance
       ├─ retry
       ├─ rollback
       └─ halt

Source boundary
query → Search Flow → provider-neutral Source Adapter
known URL → Site Extraction Flow
           fetch/scrape → selector → regex → deterministic patterns
           → optional LLM only when explicitly enabled and budget-approved
```

Only compact, versioned, schema-validated artifacts cross role seams. Raw transcripts and inherited chat context do not. Resume must begin at the first missing stage and must not repeat a completed idempotency key or paid operation.

## Commit record through `c6a6528`

The modernization history includes preservation (`88be158`, `307d34c`, `6853794`, `36bdc2e`), domain and policy (`def8588`, `3374909`, `0a2d9f7`, `9b96e38`), provider configuration (`d03da55`, `3d02ca8`), orchestration/contracts/artifacts (`00d20e0` through `f0abf2d`), source adapters (`40ebd64` through `695b037`), fixture/tracker hardening (`ea1aca5` through `b48489a`), and resumable orchestration (`c6a6528`). Review Git history for the exact complete sequence and subjects.

## Zero-cost and no-write constraints

- Paid API ceiling: **$0**.
- Deterministic doubles and stub runs are authoritative where live auth/network is absent.
- No production Supabase, Clay/GTM, monitor, sheet, job, credential, remote mutation, push, PR, merge, deploy, or live write.
- No future research-flow catalog and no full GTM provider in this goal.
- Site extraction must prefer deterministic methods before an optional, explicitly enabled LLM fallback.
- Shipping is not approved. Do not delete the original checkout, worktree, recovery refs, or stash.

## Verification matrix

| Area | Recorded evidence | Live/final gap |
|---|---|---|
| Recovery inventory | `enumerated=3561 recorded=3558 difference=+3 unexplained=0`; focused inventory tests previously recorded as 7 passed | Re-run exact verifier and hash checks |
| Repository policy | Focused policy tests previously recorded as 5 passed; documented CLI help checks | Re-run authoritative suite, help, invalid input |
| Provider config | Focused MCP tests previously recorded as 4 passed; TOML parsed; no live call made | Re-run credential scan and auth-unavailable behavior |
| Orchestration | Commits through `c6a6528`; deterministic architecture implemented | Prove rejections, evaluation, every gate outcome, exhaustion, rollback, corruption/version failure, and resume |
| Cost/write safety | Brief and code contract require `$0` and no live writes | Independent audit still pending |
| Final review | Seven-dimension rubric is defined | Prover and fresh Checker verdicts pending |

## DRAFT / live verification gaps

1. `references/morning-report-specs.md` is absent from the repository. This report uses the Tier‑1 manual fallback from `.harness/goals/repo-cleanup-full-update/HARNESS.md` → `MORNING_REPORT` and records the missing reference rather than inventing requirements.
2. The full Phase 6 mechanical gate is not yet recorded; no PASS is claimed.
3. No independent Prover verdict exists yet.
4. No fresh Checker scorecard exists yet; the required threshold remains every dimension ≥4 and mean ≥4.5.
5. No report has been published and no export fallback has been invoked.
6. Live Parallel/GTM authentication and network behavior are optional and were not exercised; remote writes remain prohibited.

## Review commands

Run from the authoritative worktree:

```powershell
git rev-parse --show-toplevel
git branch --show-current
git rev-parse HEAD
git status --short
git worktree list --porcelain
git show-ref refs/recovery/repo-cleanup-full-update/initial-dirty
git show-ref refs/recovery/repo-cleanup-full-update/dashboard-metadata
py scripts/recovery_inventory.py verify --manifest docs/recovery/repo-cleanup-full-update/inventory.csv --expected-recorded-count 3558 --expected-ref refs/recovery/repo-cleanup-full-update/initial-dirty --quarantine-map docs/recovery/repo-cleanup-full-update/quarantine-map.csv --quarantine-root .quarantine/repo-cleanup-full-update
py -m pytest tests/test_recovery_inventory.py tests/test_repository_policy.py tests/test_mcp_configuration.py -q
py scripts/autoresearch_agent.py --help
py scripts/autocontext_runner.py --help
git diff --check
```

Then run the exact mechanical gate in `.harness/goals/repo-cleanup-full-update/PLAN.md`, capture literal outputs and exit codes, obtain the independent Prover verdict, and send artifacts plus proof—not Maker self-assessment—to a fresh Checker.

## Needs my decision

- Whether to authorize another agent to run the final mechanical gate and Prover.
- Whether to accept the Checker verdict if it meets the threshold.
- Whether to authorize public publishing after confirming the report contains no sensitive material.

## Proposed review structure

Keep phase commits reviewable in their current dependency order: preservation → domain/policy → provider configuration → artifact/orchestration core → source adapters → CLI/resume integration → verification-only documentation. Do not squash evidence-bearing phase boundaries before human review.
