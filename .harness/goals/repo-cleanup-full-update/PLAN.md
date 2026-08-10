# Harness Plan — Repository Cleanup and Resumable Autoresearch

## Planning basis

Canonical implementation plan: `docs/superpowers/plans/2026-08-10-repository-cleanup-autoresearch.md`.

The Maker must work only in `C:/Users/mitch/Everything_CC/tools/data/research-process-builder/.worktrees/repo-cleanup-full-update` on `wt/repo-cleanup-full-update`, inspect overlapping recovered changes before editing, and commit once at each phase boundary. The named preflight stash and both `refs/recovery/repo-cleanup-full-update/*` refs are immutable inputs.

## Skill routing resolution

The following routing evidence is embedded verbatim as supplied to Planner:

```json
{
  "status": "resolved",
  "selectedSource": "canonical",
  "normalizedPath": "C:/Users/mitch/Everything_CC/tools/agent/agent-harness/skills/write-goal-prompt/references/skill-routing.md",
  "fallback": "project-local-absent",
  "sha256": "e8a6eb4dab4fd02ef615ae78a08fe38961f62ce121cf10e7712a50e5ef77440",
  "evidence": [
    {"source":"project-local","normalizedPath":"C:/Users/mitch/Everything_CC/tools/data/research-process-builder/.harness/skill-routing.md","state":"absent","sha256":null},
    {"source":"canonical","normalizedPath":"C:/Users/mitch/Everything_CC/tools/agent/agent-harness/skills/write-goal-prompt/references/skill-routing.md","state":"valid","sha256":"e8a6eb4dab4fd02ef615ae78a08fe38961f62ce121cf10e7712a50e5ef77440"}
  ],
  "errors": []
}
```

Routing consumed: direct work for preservation and repository policy; `codebase-design` then `domain-modeling` for terminology and module boundaries; `test-driven-development` for all code/config behavior; `verification-before-completion` for the final evidence gate. The benchmark reference is adapted as a design pattern only; its injected workflow runtime is not imported.

## Global constraints

- Preserve ≥90% programmed ground-truth validation followed by explicit human approval; the Gate never approves.
- Default paid-cost ceiling is zero, dry run is the safe default, and all plan verification uses deterministic local doubles.
- No live remote writes, credential values, flow catalog, full GTM provider, unrelated rewrites, or stash mutation.
- Versioned schema-validated artifacts are the only role-to-role transport. Raw transcripts and inherited chat histories never cross seams.
- Every phase begins with `git status --short` and recovered-overlap inspection and ends with its focused tests, a diff review, a phase commit, and proof appended to `PROGRESS.md`.

## Phases

### Phase 1 — Inventory, preservation, and reversible quarantine

**Status:** complete ? reviewed; commits `88be158`, `307d34c`, `6853794`
**Routing:** direct implementation with TDD for the inventory verifier
**Durable slice:** `issues/01-inventory-preservation.md`

Build an object-tree inventory from `refs/recovery/repo-cleanup-full-update/initial-dirty`: compare parent 1 to the stash commit without rename folding for tracked changes and enumerate parent 3 for untracked blobs. Create `scripts/recovery_inventory.py`, `tests/test_recovery_inventory.py`, `docs/recovery/repo-cleanup-full-update/inventory.csv`, `manifest.md`, and `quarantine-map.csv`; add `.quarantine/` to `.gitignore`. Each inventory row records status, canonical path, category, disposition, blob/tree object, immutable ref, SHA-256 where materialized, and exact `git show`/`git archive` recovery command. Materialize only disposable/generated/campaign material into ignored `.quarantine/repo-cleanup-full-update/`, verify hashes, then leave it ignored and local.

Count acceptance is exact and non-destructive: tests and manifest must show 42 tracked entries + 3,519 untracked blobs = 3,561 unique object-tree paths, alongside the recorded 3,558 status-era baseline and an evidence-backed explanation of the +3 difference. Never omit three paths to force equality. Verify refs remain at `e3932d...` and `7bca503...` and the named stashes still exist.

Red/green: first add fixtures proving tracked deletion/modification and untracked enumeration, classification completeness, recovery commands, unique-path checks, and count-reconciliation failure; run `py -m pytest tests/test_recovery_inventory.py -q` and observe failure; implement the smallest inventory module; rerun to green. Then generate and validate the real manifest with `py scripts/recovery_inventory.py verify --manifest docs/recovery/repo-cleanup-full-update/inventory.csv --expected-recorded-count 3558`.

Phase commit: `chore: preserve preflight repository inventory`.

### Phase 2 — Domain model and deep-module architecture

**Status:** complete ? reviewed; commits `def8588`, `c1f60a7`, `2787235`
**Routing:** `codebase-design` → `domain-modeling`
**Durable slice:** `issues/02-domain-architecture.md`

Create root `CONTEXT.md` with the canonical definitions of Research Flow, Search Flow, Site Extraction Flow, Source Adapter, Experiment, Evidence, and Approval. Add an ADR at `docs/domain/adr/0003-resumable-autoresearch-orchestration.md` covering alternatives, tradeoffs, the chosen persisted state-machine/deep-module boundary, compatibility CLIs, fresh request envelopes, pure gate, provider seams, and consequences. Update `docs/domain/glossary.md`, `docs/architecture/interface-depth.md`, and `docs/architecture/deepening-opportunities.md` only where required to make the root context canonical and prevent duplicate/conflicting definitions.

Add `tests/test_domain_contract_docs.py` first to fail on missing/conflicting terms, absent ≥90% + human-review lifecycle, and ADR omissions; then write docs to green with `py -m pytest tests/test_domain_contract_docs.py -q`. Phase commit: `docs: define resumable autoresearch domain`.

### Phase 3 — Cleanup policy and verified repository guidance

**Status:** pending
**Routing:** direct implementation, with deterministic repository-policy tests
**Durable slice:** `issues/03-cleanup-documentation.md`

Use Phase 1 dispositions—not filename guesses—to restore durable knowledge and remove only reviewed duplicates/generated material from the tracked tree. Verify quarantine hashes before any removal. Update `.gitignore` for generated run artifacts, caches, secrets, quarantine, and task worktrees without normalizing unrelated lines. Rewrite root `CLAUDE.md` from observed files and CLI help, covering purpose, domain terms (linking `CONTEXT.md`), architecture, exact commands, dependencies, safety, artifact lifecycle, 90% validation, and human approval. Make the smallest corresponding README/SKILL corrections needed for command truth.

Add `tests/test_repository_policy.py` first; it fails on unclassified removals, missing recovery references, unignored generated outputs, stale commands, or a CLAUDE lifecycle mismatch. Run red then green with `py -m pytest tests/test_repository_policy.py -q`; additionally run every documented `--help` command. Phase commit: `chore: clarify repository policy and guidance`.

### Phase 4 — Parallel Search MCP and GTM read-interface documentation

**Status:** pending
**Routing:** direct documentation/configuration plus TDD contract checks
**Durable slice:** `issues/04-provider-configuration.md`

Consult current official Parallel and host MCP documentation at implementation time and record source URL/access date in `docs/providers/parallel-search-mcp.md`. Add the host-supported repository-scoped MCP configuration (expected location `.mcp.json`, but official docs decide) using OAuth or `${PARALLEL_API_KEY}`-style environment references only. It must parse without a secret and fail clearly when authentication is unavailable. Discover the actually installed GTM MCP tools read-only and document exact read operations, schemas, auth absence behavior, and unverified gaps in `docs/providers/gtm-mcp-read-interface.md`; invoke no remote mutation.

Write `tests/test_mcp_configuration.py` before configuration. Tests reject literal secret-like values, assert repository scope and expected Parallel endpoint/transport from cited docs, and exercise no-auth error messaging through a local double. Run red then green with `py -m pytest tests/test_mcp_configuration.py -q`. Do not treat a network check as required evidence and do not spend paid credits. Phase commit: `feat: configure repository search providers`.

### Phase 5 — Test-first resumable orchestration and provider seams

**Status:** pending
**Routing:** `test-driven-development`
**Durable slice:** `issues/05-resumable-orchestration.md`

Create focused modules under `scripts/research_orchestration/`: `contracts.py` (versioned dataclasses/enums and strict validation), `budgets.py` (pre-charge ledger), `gate.py` (pure transition function), `artifacts.py` (canonical JSON, content hashes, atomic writes, journal validation/projection), `providers.py` (Source Adapter plus Search/Site Extraction contracts and normalized errors), and `orchestrator.py` (the single `AutoresearchOrchestrator.run(RunRequest) -> RunSummary` coordination boundary). Keep `scripts/autoresearch_agent.py` as composition/CLI and change `scripts/autocontext_runner.py` into a compatibility CLI. Never import the benchmark harness runtime.

Build in independent red/green increments:

1. Schema tests reject extra/missing/unbounded fields and prove unique fresh envelope identities and role-specific allowlists.
2. Gate table tests cover `advance|retry|rollback|halt_for_review` and reason codes, ≥90% halt-for-human-review, retry exhaustion, rollback availability, corrupt/version-invalid artifacts, and every budget family.
3. Artifact tests cover canonical hashes, atomic replacement, monotonically sequenced journal records, tamper/version detection, projection reconstruction, and idempotent resume.
4. Provider tests prove Search Flow starts from a query; Site Extraction requires known URLs and orders fetch/scrape → selector → regex → deterministic patterns before optional explicit LLM; Parallel/GTM doubles remain read-only and provider-neutral.
5. Orchestrator tests prove out-of-bounds and duplicate rejection before Executor, accepted execution, independent Evaluator, executor/evaluator failures, rollback, retry exhaustion, and resume without repeating an idempotency key or charged call.
6. CLI tests prove `--help`, invalid input, zero-cost dry-run default, deterministic `--stub-run`, `--run-dir`, and `--resume` for both affected CLIs.

Use focused commands after each failing test and implementation, then run `py -m pytest tests/test_autoresearch_contracts.py tests/test_autoresearch_gate.py tests/test_autoresearch_artifacts.py tests/test_source_adapters.py tests/test_autoresearch_orchestrator.py tests/test_autoresearch_clis.py scripts/test_autoresearch_agent.py -q`. The final stub transcript and artifacts must demonstrate every role/gate without network or paid work. Phase commit: `feat: add resumable autoresearch orchestration`.

### Phase 6 — Verification, integrity audit, and local handoff

**Status:** pending
**Routing:** `verification-before-completion`
**Durable slice:** `issues/06-verification-handoff.md`

Run the full mechanical gate from the implementation plan; capture command, exit code, relevant output, and artifact path in `PROGRESS.md`. Verify branch/worktree identity; unchanged recovery refs/stashes; inventory completeness and recovery hashes; clean secret scan; parsed MCP config; no unapproved remote activity; CLI help/invalid/dry/stub/resume; focused tests; all authoritative repository tests with no new skips; and a clean post-commit worktree. Create/update local `HANDOFF.md`, `HANDOFF.html`, and `HANDOFF.excalidraw` with DRAFT/live-verification gaps, preservation manifest, architecture, verification matrix, limitations, review commands, and proposed commit structure. Shipping is `N/A - shipping not approved`; publish only the sanitized morning report via `lavish-axi share HANDOFF.html` (or record the required export fallback), and do not push, open a PR, merge, or delete worktrees/stashes.

Phase commit: `docs: record repository modernization verification`.

## Mechanical gate

Run from the task worktree (PowerShell):

```powershell
git rev-parse --show-toplevel
git branch --show-current
git status --short
git show-ref refs/recovery/repo-cleanup-full-update/initial-dirty refs/recovery/repo-cleanup-full-update/dashboard-metadata
py scripts/recovery_inventory.py verify --manifest docs/recovery/repo-cleanup-full-update/inventory.csv --expected-recorded-count 3558
py -m pytest tests/test_recovery_inventory.py tests/test_domain_contract_docs.py tests/test_repository_policy.py tests/test_mcp_configuration.py -q
py -m pytest tests/test_autoresearch_contracts.py tests/test_autoresearch_gate.py tests/test_autoresearch_artifacts.py tests/test_source_adapters.py tests/test_autoresearch_orchestrator.py tests/test_autoresearch_clis.py scripts/test_autoresearch_agent.py -q
py -m pytest -q
py scripts/autoresearch_agent.py --help
py scripts/autoresearch_agent.py --stub-run --run-dir .artifacts/autoresearch/proof
py scripts/autoresearch_agent.py --resume .artifacts/autoresearch/proof
py scripts/autocontext_runner.py --help
py scripts/autocontext_runner.py --stub-run --run-dir .artifacts/autoresearch/compat-proof
py scripts/autocontext_runner.py --resume .artifacts/autoresearch/compat-proof
py scripts/validate.py --summary
git grep -nEI '(api[_-]?key|token|secret|password)[[:space:]]*[:=][[:space:]]*["'"'][^${][^"'"']+' -- ':!docs/recovery/repo-cleanup-full-update/inventory.csv'
git diff --check
git status --short
```

Expected: correct in-repo worktree/branch; refs unchanged; inventory reports `recorded=3558`, `enumerated=3561`, `unexplained=0`; all tests pass with zero new skips; stub resume reports zero duplicate executions/charges; secret scan and `git diff --check` are empty; final status is empty after the Phase 6 commit. If broad legacy tests require unavailable services, isolate and record pre-existing failures, but focused requirements must remain green.

## Checker rubric

Fresh Checker reads BRIEF, PLAN, final diff, inventory/recovery map, tests, and Prover evidence—not Maker self-assessment—and scores 1–5 with file:line or command evidence:

1. **Preservation safety:** 5 = all 3,561 Git-enumerated paths classified with object/recovery commands; the 3,558 baseline is honestly reconciled; quarantine is ignored, hash-verified, and reversible; no unique work lost.
2. **Repository clarity:** 5 = root CONTEXT/CLAUDE/README/SKILL agree with observed commands, architecture, dependencies, artifacts, ≥90% validation, and human approval.
3. **Orchestration correctness:** 5 = fresh bounded envelopes, independent checks/evaluator, pure deterministic gate, validated persistence, budgets, rollback, and idempotent resume are implemented and evidenced.
4. **Validation rigor:** 5 = automated evidence covers both pre-execution rejections, accepted execution, all transitions/reasons, failures, corruption/versioning, retry exhaustion, rollback, resume, context isolation, CLIs, and no new skips.
5. **Provider architecture:** 5 = provider-neutral contracts isolate Parallel and future GTM/site sources; known-URL deterministic extraction order is enforced; no core change is needed for another read adapter.
6. **Security/operations:** 5 = no literal secrets, zero paid spend, pre-charged bounded budgets, safe dry-run behavior, clear no-auth failures, immutable recovery refs, and no remote writes.
7. **Scope discipline:** 5 = no catalog/full GTM provider/live writes/shipping/unrelated normalization; benchmark patterns are adapted without runtime import.

PASS requires every dimension ≥4 and mean ≥4.5. Automatic failure: lost unique work; changed/dropped recovery ref or stash; committed credential; external worktree; unbounded/inherited Inventor context; missing independent checker/evaluator; failed focused/authoritative tests; undocumented destructive cleanup; duplicate paid work on resume; or out-of-scope catalog/live-write work.

## Shipping boundary

Shipping is not approved. After Checker PASS, produce the local handoff and publish only the sanitized `HANDOFF.html` report as required by `MORNING_REPORT`; keep the update key in ignored `HANDOFF.secret.local` and use `lavish-axi export` if sharing is unavailable. Do not push, open a PR, merge, deploy, mutate application data, or remove the original checkout/worktree/stashes without separate explicit approval.
