# Company Enrichment Library Task List

**Source of truth:** `docs/superpowers/specs/2026-08-11-company-enrichment-library-design.md`, `docs/superpowers/plans/2026-08-11-company-enrichment-library.md`, and `HARNESS.md`.

**All human decisions:** `HUMAN_APPROVALS.md`

## Human Gate 1 — Scope and corpus
- [x] Confirm the eight P0 enrichments.
- [x] Confirm six B2B-only cohorts: local services, SaaS, recently funded, well-known, agencies, and commerce/CPG.
- [ ] Approve the proposed 60-company fixture list and 15-company shared core.
- [x] Confirm direct people/contact discovery and standalone P1 enrichments remain excluded.

## Phase 0 — Planning and repository safety
- [x] Read required source documents and relevant provider skill documentation.
- [x] Write `PLAN.md` with execution shape and dependency order.
- [x] Inspect repository status and preserve unrelated work.
- [x] Create dedicated branch `wt/company-enrichment-b2b`; no active worktree policy applies.

## Phase 1 — Contracts and manifests
- [x] Add Python package foundation and dependencies.
- [x] Implement immutable contracts and canonical serialization.
- [x] Implement strict YAML/version validation.
- [x] Create eight B2B P0 manifests.
- [x] Add contract and manifest tests (15 passing).
- [ ] Human Gate 2A: review `GATE_2_REVIEW.md` and approve contract, manifest, SellerContext, and visibility design.
- [ ] Human Gate 2B: approve actual prompts and exact output schemas before P0 executors.

## Phase 2 — Capability discovery
- [ ] Implement mandatory GTM Orchestrator preflight.
- [ ] Implement Nexus query and visible nonfatal auth failure handling.
- [ ] Register verified providers and gaps.
- [ ] Enforce Parallel search-only behavior.
- [ ] Add discovery tests and capability documentation.

## Phase 3 — Evidence, cache, saturation, and budgets
- [ ] Implement content-addressed evidence storage.
- [ ] Implement cache keys, append-only records, tamper detection, and resume.
- [ ] Implement source-saturation tracking.
- [ ] Implement atomic aggregate budget reservations and reconciliation.
- [ ] Test `$2.00` corpus and `$1.00` experiment caps.

## Phase 4 — Provider adapters
- [ ] Implement provider-neutral protocols and normalized failures.
- [ ] Implement GTM known-URL waterfall routing.
- [ ] Implement Parallel search adapter.
- [ ] Implement channel-aware ads routing.
- [ ] Smoke-test TechSight import.
- [ ] Validate Meta adapter schema/cost with 1–3 fixtures before use.
- [ ] Keep TikTok explicitly unknown unless verified.
- [x] Record Firecrawl approval for public B2B company URLs after free L1/L2 fail, within aggregate caps.
- [ ] Human Gate 3: approve provider choices, fixtures, data scope, and spend ceilings before paid calls.

## Phase 5 — Runner and P0 executors
- [ ] Implement validate → discover → cache → reserve → collect → execute → validate → record.
- [ ] Implement retries, early stops, model identity, and failure normalization.
- [ ] Implement eight P0 executors.
- [ ] Enforce message-safe/filter-only boundaries.
- [ ] Require SellerContext for job mining and analogy/value outputs.
- [ ] Add runner and executor tests.

## Phase 6 — Corpus and dossiers
- [ ] Select and document 60 unique companies.
- [ ] Validate 10 companies per cohort and 15 shared-core members.
- [ ] Build and review the 15-company core dossiers first.
- [ ] Human Gate 4: review core dossier quality, citations, unknowns, and cohort assignments.
- [ ] Build the remaining 45 dossiers under the aggregate cap.

## Phase 7 — Benchmarking and human review
- [ ] Implement deterministic field/citation/cost/latency scoring.
- [ ] Implement model ladder comparisons with exact model IDs.
- [ ] Implement append-only experiment history.
- [ ] Implement blind review packs.
- [ ] Enforce `proposed → experiment → candidate → approved|rejected`.
- [ ] Ensure automation cannot create `approved`.
- [ ] Human Gate 5: review each enrichment and choose approve, revise, reject, or redirect.

## Phase 8 — CLI and documentation
- [ ] Implement capability, corpus, run, benchmark, review, and report commands.
- [ ] Default to free-only dry runs.
- [ ] Require explicit paid flags and enforce aggregate caps.
- [ ] Add CLI subprocess tests.
- [ ] Document operations, gaps, costs, and review procedures.

## Phase 9 — Verification and handoff
- [ ] Run focused tests, full pytest, diff checks, credential scan, validators, and CLI proof.
- [ ] Verify exactly 100% acceptance-manifest pass rate.
- [ ] Verify no secret leakage, cap breach, filter-only leakage, or automated approval.
- [ ] Create `docs/reports/company-enrichment-verification.md`.
- [ ] Create `HANDOFF.md`, `HANDOFF.html`, and `HANDOFF.excalidraw`.
- [ ] Human Gate 6: final release decision; accept, request fixes, narrow scope, or reject.
- [ ] Do not push, merge, deploy, or mark outputs approved automatically.

## Current status
- [x] Source documents reread.
- [x] Task list created.
- [x] Human Gate 1 B2B scope approved; exact fixtures/core pending.
- [x] Implementation started on `wt/company-enrichment-b2b`.
