# Phase 02 — Domain model and deep-module architecture

**Status:** complete ? reviewed; commits `def8588`, `c1f60a7`, `2787235`
**Canonical source:** `.harness/goals/repo-cleanup-full-update/PLAN.md` → `## Phases` → Phase 2
**Routing:** `codebase-design` → `domain-modeling`
**Commit:** `docs: define resumable autoresearch domain`

## Deliverable

Create canonical root domain language and an ADR selecting the persisted state-machine/deep-module interface for fresh-context autoresearch.

## Acceptance

- [x] `CONTEXT.md` canonically defines Research Flow, Search Flow, Site Extraction Flow, Source Adapter, Experiment, Evidence, and Approval.
- [x] Approval requires ≥90% ground-truth validation and explicit human review; Gate never approves.
- [x] ADR compares persisted state machine, event-sourced reducer, and directory-per-cycle options with tradeoffs, decision, and consequences.
- [x] ADR fixes `AutoresearchOrchestrator.run(request: RunRequest) -> RunSummary`, pure Gate, artifact-only role seams, and compatibility CLIs.
- [x] Existing glossary/architecture docs link or align without conflicting duplicate definitions.
- [x] `py -m pytest tests/test_domain_contract_docs.py -q` passes after a recorded red run.
- [x] Proof and phase commit SHA are appended to `PROGRESS.md`.

## Prohibitions

Do not implement code or the future flow catalog in this phase.
