# Phase 02 — Domain model and deep-module architecture

**Status:** pending  
**Canonical source:** `.harness/goals/repo-cleanup-full-update/PLAN.md` → `## Phases` → Phase 2  
**Routing:** `codebase-design` → `domain-modeling`  
**Commit:** `docs: define resumable autoresearch domain`

## Deliverable

Create canonical root domain language and an ADR selecting the persisted state-machine/deep-module interface for fresh-context autoresearch.

## Acceptance

- [ ] `CONTEXT.md` canonically defines Research Flow, Search Flow, Site Extraction Flow, Source Adapter, Experiment, Evidence, and Approval.
- [ ] Approval requires ≥90% ground-truth validation and explicit human review; Gate never approves.
- [ ] ADR compares persisted state machine, event-sourced reducer, and directory-per-cycle options with tradeoffs, decision, and consequences.
- [ ] ADR fixes `AutoresearchOrchestrator.run(request: RunRequest) -> RunSummary`, pure Gate, artifact-only role seams, and compatibility CLIs.
- [ ] Existing glossary/architecture docs link or align without conflicting duplicate definitions.
- [ ] `py -m pytest tests/test_domain_contract_docs.py -q` passes after a recorded red run.
- [ ] Proof and phase commit SHA are appended to `PROGRESS.md`.

## Prohibitions

Do not implement code or the future flow catalog in this phase.
