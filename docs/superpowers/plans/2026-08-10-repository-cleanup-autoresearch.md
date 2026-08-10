# Repository Cleanup and Resumable Autoresearch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Safely modernize the repository, add secret-free repository-scoped Parallel Search MCP configuration, and prove a provider-neutral autoresearch state machine with fresh role context, deterministic gates, and idempotent resume while preserving ≥90% ground-truth validation followed by human review.

**Architecture:** A recovery inventory generated directly from immutable stash commit trees governs reversible cleanup. A deep `research_orchestration` module owns strict contracts, budgets, artifact persistence, role sequencing, and a pure gate; the two existing scripts become thin CLIs. Provider-neutral read adapters isolate Parallel, GTM discovery, search, and deterministic-first known-URL extraction.

**Tech Stack:** Python 3, stdlib dataclasses/enum/hashlib/json/pathlib/tempfile, pytest, Git object plumbing, repository-scoped MCP JSON, Markdown/CSV documentation.

## Global Constraints

- Approval remains programmed ground-truth validation of at least 90%, then explicit human review; automation never grants approval.
- Work only at `.worktrees/repo-cleanup-full-update` on `wt/repo-cleanup-full-update` and preserve unrelated changes.
- Treat `refs/recovery/repo-cleanup-full-update/initial-dirty`, `refs/recovery/repo-cleanup-full-update/dashboard-metadata`, and their named stashes as immutable read-only inputs.
- Cost ceiling is `$0` paid API spend; use deterministic doubles and free/no-auth parsing checks.
- Do not touch production Supabase, Clay/GTM records, monitors, shared sheets, live jobs, credentials, remotes, PRs, or the named preflight stash.
- Do not implement the future Research Flow catalog, full GTM provider, remote writes, or the benchmark harness injected runtime.
- Each phase starts with overlap inspection, uses explicit red/green tests for code/config behavior, ends in a focused verification and one phase commit, and appends literal proof to `PROGRESS.md`.

---

## File map

- `scripts/recovery_inventory.py`: deterministic Git-tree enumeration, classification validation, manifest generation, and recovery verification.
- `docs/recovery/repo-cleanup-full-update/{inventory.csv,manifest.md,quarantine-map.csv}`: durable path-level preservation evidence and count reconciliation.
- `CONTEXT.md`, `docs/domain/adr/0003-resumable-autoresearch-orchestration.md`: canonical language and architecture decision.
- `.gitignore`, `CLAUDE.md`, `README.md`, `SKILL.md`: verified repository/operator and artifact policy.
- `.mcp.json`, `docs/providers/{parallel-search-mcp.md,gtm-mcp-read-interface.md}`: repository-scoped configuration and read-only provider evidence.
- `scripts/research_orchestration/contracts.py`: versioned immutable request/result/envelope models and validation.
- `scripts/research_orchestration/budgets.py`: pre-execution budget reservation and normalized exhaustion.
- `scripts/research_orchestration/gate.py`: pure `decide_gate(GateInput) -> GateDecision` transition table.
- `scripts/research_orchestration/artifacts.py`: canonical JSON object store, atomic writes, journal validation, projection, and resume cursor.
- `scripts/research_orchestration/providers.py`: Source Adapter protocol and Search/Site Extraction deterministic-first contracts.
- `scripts/research_orchestration/orchestrator.py`: deep `AutoresearchOrchestrator.run(RunRequest) -> RunSummary` interface.
- `scripts/autoresearch_agent.py`, `scripts/autocontext_runner.py`: thin primary and compatibility CLIs.
- `tests/test_*.py`: focused preservation, documentation, MCP, schema, state-machine, provider, persistence, and CLI evidence.

---

### Task 1: Inventory and reversible preservation

**Files:**
- Create: `scripts/recovery_inventory.py`
- Create: `tests/test_recovery_inventory.py`
- Create: `docs/recovery/repo-cleanup-full-update/inventory.csv`
- Create: `docs/recovery/repo-cleanup-full-update/manifest.md`
- Create: `docs/recovery/repo-cleanup-full-update/quarantine-map.csv`
- Modify: `.gitignore`
- Local ignored output: `.quarantine/repo-cleanup-full-update/`

**Interfaces:**
- Consumes: recovery stash commit, first parent (base), third parent (untracked tree), recorded baseline `3558`.
- Produces: `enumerate_recovery(ref: str) -> list[InventoryEntry]`, `validate_inventory(entries, recorded_count) -> Reconciliation`, and CLI `generate|verify`; CSV rows consumed by cleanup and final integrity checks.

- [ ] **Step 1: Prove the immutable inputs before editing**

Run:

```powershell
git status --short
git branch --show-current
git show-ref refs/recovery/repo-cleanup-full-update/initial-dirty refs/recovery/repo-cleanup-full-update/dashboard-metadata
git stash list --format='%gd %H %gs'
```

Expected: branch `wt/repo-cleanup-full-update`; recovery SHAs `e3932d55217c29ac28eca16fdc7e6f6c5c3e3337` and `7bca5038f31a9427f1781e823a388d0dcf2ac33d`; named preflight stashes present.

- [ ] **Step 2: Write failing inventory tests**

Add fixture repos and assertions equivalent to:

```python
entries = enumerate_recovery(fixture.stash_ref)
assert {(e.status, e.path) for e in entries} == {
    ("M", "tracked.py"), ("D", "deleted.md"), ("?", "new/data.json")
}
assert all(e.object_id and e.recovery_command for e in entries)
assert validate_inventory(entries, recorded_count=2).enumerated_count == 3
assert validate_inventory(entries, recorded_count=2).difference == 1
assert validate_inventory(entries, recorded_count=2).unexplained_paths == ()
```

Also assert duplicate paths, missing classifications/dispositions/objects, mutable ref names, and quarantine hash mismatches fail closed.

- [ ] **Step 3: Run red**

Run: `py -m pytest tests/test_recovery_inventory.py -q`
Expected: FAIL because `scripts.recovery_inventory` does not exist.

- [ ] **Step 4: Implement the minimal Git-object inventory**

Use `git diff --name-status --no-renames <ref>^1 <ref>` for tracked records and `git ls-tree -r --full-tree <ref>^3` for untracked blobs. Preserve exact canonical paths. Classify into `source`, `tests`, `durable-research`, `generated-output`, `secrets-config`, `cache`, `campaign-specific`, or `obsolete-duplicate`; dispositions are `restore-reviewed`, `retain-tracked`, `quarantine`, or `document-only`. Emit recovery commands such as `git show <object-id> > <destination>` without executing them during verification.

- [ ] **Step 5: Run green and generate the real inventory**

```powershell
py -m pytest tests/test_recovery_inventory.py -q
py scripts/recovery_inventory.py generate --ref refs/recovery/repo-cleanup-full-update/initial-dirty --recorded-count 3558 --output docs/recovery/repo-cleanup-full-update/inventory.csv --summary docs/recovery/repo-cleanup-full-update/manifest.md --quarantine-map docs/recovery/repo-cleanup-full-update/quarantine-map.csv
py scripts/recovery_inventory.py verify --manifest docs/recovery/repo-cleanup-full-update/inventory.csv --expected-recorded-count 3558 --expected-ref refs/recovery/repo-cleanup-full-update/initial-dirty --quarantine-map docs/recovery/repo-cleanup-full-update/quarantine-map.csv --quarantine-root .quarantine/repo-cleanup-full-update
```

Expected reconciliation: `42 tracked + 3519 untracked = 3561 unique`; recorded `3558`; difference `+3`; `unexplained=0`. The manifest must state that 3,558 is the earlier status-era observation and 3,561 is authoritative for preserved object-tree coverage; it must not claim a specific cause unsupported by evidence.

- [ ] **Step 6: Materialize and verify quarantine**

Add `.quarantine/` to `.gitignore`, export only rows marked `quarantine`, record SHA-256 and local relative path in `quarantine-map.csv`, then run verifier. Do not apply/pop/drop a stash and do not delete source refs.

- [ ] **Step 7: Commit the phase**

```powershell
git add .gitignore scripts/recovery_inventory.py tests/test_recovery_inventory.py docs/recovery/repo-cleanup-full-update
git commit -m "chore: preserve preflight repository inventory"
```

Append command output and commit SHA to `PROGRESS.md`.

---

### Task 2: Canonical domain model and orchestration ADR

**Files:**
- Create: `CONTEXT.md`
- Create: `docs/domain/adr/0003-resumable-autoresearch-orchestration.md`
- Create: `tests/test_domain_contract_docs.py`
- Modify: `docs/domain/glossary.md`
- Modify: `docs/architecture/interface-depth.md`
- Modify: `docs/architecture/deepening-opportunities.md`

**Interfaces:**
- Consumes: approved design and current module behavior.
- Produces: canonical definitions and the stable names/signatures used by Task 5.

- [ ] **Step 1: Write failing documentation-contract tests**

Assert root `CONTEXT.md` defines exactly Research Flow, Search Flow, Site Extraction Flow, Source Adapter, Experiment, Evidence, and Approval; Approval includes `>= 90%` (or `at least 90%`) plus explicit human review; ADR records alternatives/tradeoffs/decision/consequences and `AutoresearchOrchestrator.run(request: RunRequest) -> RunSummary`.

- [ ] **Step 2: Run red**

Run: `py -m pytest tests/test_domain_contract_docs.py -q`
Expected: FAIL because root context and ADR are absent.

- [ ] **Step 3: Apply codebase-design and domain-modeling**

Document three considered shapes—persisted state machine, event-sourced reducer, and directory-per-cycle pipeline—and choose the persisted state machine with an append-only journal. Describe why CLIs compose rather than coordinate, why the Gate is pure, and why providers sit behind read-only adapter seams. Keep domain definitions in `CONTEXT.md` and link rather than duplicate them elsewhere.

- [ ] **Step 4: Run green and commit**

```powershell
py -m pytest tests/test_domain_contract_docs.py -q
git add CONTEXT.md docs/domain docs/architecture tests/test_domain_contract_docs.py
git commit -m "docs: define resumable autoresearch domain"
```

Append proof and SHA to `PROGRESS.md`.

---

### Task 3: Cleanup policy and verified guidance

**Files:**
- Create: `tests/test_repository_policy.py`
- Modify: `.gitignore`
- Modify: `CLAUDE.md`
- Modify: `README.md` only for verified command/policy corrections
- Modify: `SKILL.md` only for verified command/policy corrections
- Modify/remove: only paths whose Phase 1 disposition explicitly authorizes it

**Interfaces:**
- Consumes: Phase 1 inventory/dispositions and Phase 2 terminology.
- Produces: enforceable artifact/ignore policy and accurate operator documentation.

- [ ] **Step 1: Write failing repository-policy tests**

Assert every removed recovered path has a manifest disposition and recovery object; quarantine destinations are ignored; `.env*`, run artifacts, caches, and `.worktrees/` are ignored; every documented Python entry point exists and returns help; CLAUDE links CONTEXT and states the validation/review lifecycle.

- [ ] **Step 2: Run red**

Run: `py -m pytest tests/test_repository_policy.py -q`
Expected: FAIL on stale or missing policy/documentation.

- [ ] **Step 3: Perform disposition-driven cleanup**

For each candidate removal, look up the exact inventory row, verify its quarantine/recovery hash first, and avoid bulk normalization. Selectively restore reusable knowledge into reviewed locations. Record every removal/restoration in `manifest.md`.

- [ ] **Step 4: Rewrite operator guidance from evidence**

Document purpose, canonical domain link, actual directory/module layout, install/import dependencies observed in code, local safe commands, artifact locations, no-secret/no-remote-write rules, and the ≥90% + explicit-review approval lifecycle. Correct README/SKILL only where their commands contradict `--help` or files.

- [ ] **Step 5: Run green and commit**

```powershell
py -m pytest tests/test_repository_policy.py -q
py scripts/pattern_tester.py --help
py scripts/gt_evaluator.py --help
py scripts/validate.py --help
py scripts/autoresearch.py --help
git diff --check
git add -A
git commit -m "chore: clarify repository policy and guidance"
```

Append proof and SHA to `PROGRESS.md`.

---

### Task 4: Repository-scoped Parallel MCP and GTM read documentation

**Files:**
- Create: `.mcp.json` (or the currently documented repository-scoped host path)
- Create: `docs/providers/parallel-search-mcp.md`
- Create: `docs/providers/gtm-mcp-read-interface.md`
- Create: `tests/test_mcp_configuration.py`

**Interfaces:**
- Consumes: current official Parallel Search MCP/host documentation and locally discoverable GTM MCP tool schemas.
- Produces: parseable secret-free configuration and provider-read boundary documentation for Task 5.

- [ ] **Step 1: Verify official configuration facts**

Use current primary vendor/host docs. Record direct URL, access date `2026-08-10`, transport, endpoint, auth method, and repository-scoped file semantics. Do not copy a credential or run a paid search.

- [ ] **Step 2: Write failing MCP tests**

Tests load configuration as JSON, assert the official server name/transport/endpoint, require OAuth or an environment reference, reject literal values matching token/key/secret patterns, and simulate a missing-auth error that tells the operator which environment/OAuth step is needed.

- [ ] **Step 3: Run red**

Run: `py -m pytest tests/test_mcp_configuration.py -q`
Expected: FAIL because repository config/docs are absent.

- [ ] **Step 4: Add configuration and read-only docs**

Create the repository-scoped entry exactly as current docs specify. Enumerate the installed GTM MCP surface without invoking writes; document confirmed read tool names/inputs/outputs and mark absent/unverified details as limitations. Explicitly prohibit mutation and full-provider implementation.

- [ ] **Step 5: Run green and commit**

```powershell
py -m pytest tests/test_mcp_configuration.py -q
py -c "import json, pathlib; json.loads(pathlib.Path('.mcp.json').read_text(encoding='utf-8')); print('mcp json ok')"
py scripts/credential_scan.py
git add .mcp.json docs/providers tests/test_mcp_configuration.py
git commit -m "feat: configure repository search providers"
```

The secret scan must return no credential value. Append proof and SHA to `PROGRESS.md`.

---

### Task 5: Contracts, budgets, and deterministic Gate

**Files:**
- Create: `scripts/research_orchestration/__init__.py`
- Create: `scripts/research_orchestration/contracts.py`
- Create: `scripts/research_orchestration/budgets.py`
- Create: `scripts/research_orchestration/gate.py`
- Create: `tests/test_autoresearch_contracts.py`
- Create: `tests/test_autoresearch_gate.py`

**Interfaces:**
- Produces: `RunRequest`, `RunSummary`, `RoleEnvelope`, `Experiment`, `Evidence`, checker/evaluator results, `BudgetLimits`, `BudgetLedger`, `GateInput`, `GateDecision`, `GateAction`, and `decide_gate`.

- [ ] **Step 1: Write schema/envelope tests and run red**

Assert strict schema versions, missing/extra-field rejection, bounded list/text sizes, stable content-derived Experiment keys, unique envelope invocation IDs, and exact role field allowlists. Run `py -m pytest tests/test_autoresearch_contracts.py -q`; expect import failure.

- [ ] **Step 2: Implement immutable strict contracts and run green**

Canonical serialization must sort keys, exclude secrets/transcripts, and reject non-finite/unbounded values. Re-run the focused test.

- [ ] **Step 3: Write gate/budget table tests and run red**

Parameterize all actions and reason codes: accepted improvement, bounded retryable failure, regression with baseline, ≥90% threshold, budget exhausted, retry exhausted without rollback, corrupt/version-invalid artifact, and unsafe ambiguity. Assert reservation happens before calls for query/scrape/LLM/retry/cost/stage counters.

- [ ] **Step 4: Implement pure gate and budget ledger and run green**

`decide_gate` has no I/O and emits exactly one of `advance`, `retry`, `rollback`, `halt_for_review`. Threshold success emits halt-for-human-review, never approved.

- [ ] **Step 5: Commit this independently reviewable increment**

```powershell
py -m pytest tests/test_autoresearch_contracts.py tests/test_autoresearch_gate.py -q
git add scripts/research_orchestration tests/test_autoresearch_contracts.py tests/test_autoresearch_gate.py
git commit -m "feat: define autoresearch contracts and gate"
```

This is an intermediate Task 5 commit inside canonical Phase 5; record it, but do not declare Phase 5 complete yet.

---

### Task 6: Artifact store and idempotent resume

**Files:**
- Create: `scripts/research_orchestration/artifacts.py`
- Create: `tests/test_autoresearch_artifacts.py`

**Interfaces:**
- Produces: `ArtifactStore.create_run`, `put_role_artifact`, `append_transition`, `load_and_validate`, `resume_cursor`, and `project_summary` over `run.json`, `journal.jsonl`, `cycles/*/*.json`, `objects/<sha256>.json`, and `summary.json`.

- [ ] **Step 1: Write failing persistence tests**

Test canonical object hashes, temp-file + atomic replace, monotonic journal sequence, artifact-hash/idempotency references, projection reconstruction, first-missing-stage resume, completed-key skipping, tamper detection, truncated JSONL, and incompatible schema version.

- [ ] **Step 2: Run red**

Run: `py -m pytest tests/test_autoresearch_artifacts.py -q`
Expected: FAIL because artifact store is absent.

- [ ] **Step 3: Implement the artifact store**

Never persist raw transcripts or secret-bearing config. Treat objects as immutable; reject collisions/tampering. Corruption and version mismatch return a typed halt condition rather than repair guesses.

- [ ] **Step 4: Run green and commit**

```powershell
py -m pytest tests/test_autoresearch_artifacts.py -q
git add scripts/research_orchestration/artifacts.py tests/test_autoresearch_artifacts.py
git commit -m "feat: persist autoresearch artifacts safely"
```

---

### Task 7: Provider-neutral Search and Site Extraction seams

**Files:**
- Create: `scripts/research_orchestration/providers.py`
- Create: `tests/test_source_adapters.py`

**Interfaces:**
- Produces: `SourceAdapter.search(SearchRequest) -> SearchResult`, `SourceAdapter.extract(ExtractionRequest) -> ExtractionResult`, normalized `AdapterError`, and deterministic extraction stage records.

- [ ] **Step 1: Write failing adapter contract tests**

Assert Search requires a bounded query; Extraction rejects empty known URLs; results are bounded, serializable, source-attributed, and read-only; provider-specific payloads do not leak. With recording doubles, assert order `fetch/scrape`, `selector`, `regex`, `pattern`, and only then optional LLM when explicitly enabled and deterministic evidence is insufficient. Assert no mutation method exists.

- [ ] **Step 2: Run red**

Run: `py -m pytest tests/test_source_adapters.py -q`
Expected: FAIL because provider contracts are absent.

- [ ] **Step 3: Implement protocols and deterministic pipeline**

Normalize provider failures into retryable, terminal, budget-exhausted, or contract-invalid. Keep Parallel and GTM as test/documentation adapters only; do not implement a full GTM provider or perform a live call.

- [ ] **Step 4: Run green and commit**

```powershell
py -m pytest tests/test_source_adapters.py -q
git add scripts/research_orchestration/providers.py tests/test_source_adapters.py
git commit -m "feat: add provider-neutral source contracts"
```

---

### Task 8: Orchestrator and compatibility CLIs

**Files:**
- Create: `scripts/research_orchestration/orchestrator.py`
- Create: `tests/test_autoresearch_orchestrator.py`
- Create: `tests/test_autoresearch_clis.py`
- Modify: `scripts/autoresearch_agent.py`
- Modify: `scripts/autocontext_runner.py`
- Modify: `scripts/test_autoresearch_agent.py`

**Interfaces:**
- Consumes: contracts, budgets, gate, artifact store, and adapters from Tasks 5–7.
- Produces: `AutoresearchOrchestrator.run(request: RunRequest) -> RunSummary`; CLIs with `--help`, `--dry-run`, `--stub-run`, `--run-dir`, and `--resume`.

- [ ] **Step 1: Write failing role-isolation and state-machine tests**

Use recording role/provider doubles. Assert exact order; Inventor sees only run brief/baseline/bounded decisions/budget remainder; checkers cannot see Inventor reasoning; Evaluator sees only rubric/Experiment/Evidence and cannot invoke providers; out-of-bounds and duplicate candidates never call Executor; executor/evaluator failures map correctly; every Gate outcome is observed.

- [ ] **Step 2: Write failing resume/idempotency tests**

Interrupt after each stage, resume, and assert completed invocation/idempotency keys are not re-run or re-charged. Cover retry exhaustion, safe rollback, journal tamper, and version mismatch.

- [ ] **Step 3: Run red**

Run: `py -m pytest tests/test_autoresearch_orchestrator.py -q`
Expected: FAIL because orchestrator is absent.

- [ ] **Step 4: Implement the deep orchestrator and run green**

Construct a new `RoleEnvelope` per invocation from validated compact artifacts. Orchestrator alone sequences stages and persists transitions. No transcript/history object is accepted by its public interface.

- [ ] **Step 5: Write CLI tests and run red**

Subprocess-test both CLIs for help exit 0, invalid args nonzero with actionable stderr, dry-run without API/client construction, deterministic stub artifacts, and resume with zero duplicate executions/charges.

- [ ] **Step 6: Thin the CLIs and run green**

Keep legacy argument aliases where safe, but default paid cost to zero and require explicit opt-in/config for external providers. `autocontext_runner.py` delegates to the same composition function.

- [ ] **Step 7: Run the complete Phase 5 proof and commit**

```powershell
py -m pytest tests/test_autoresearch_contracts.py tests/test_autoresearch_gate.py tests/test_autoresearch_artifacts.py tests/test_source_adapters.py tests/test_autoresearch_orchestrator.py tests/test_autoresearch_clis.py scripts/test_autoresearch_agent.py -q
py scripts/autoresearch_agent.py --stub-run --run-dir .artifacts/autoresearch/proof
py scripts/autoresearch_agent.py --resume .artifacts/autoresearch/proof
py scripts/autocontext_runner.py --stub-run --run-dir .artifacts/autoresearch/compat-proof
py scripts/autocontext_runner.py --resume .artifacts/autoresearch/compat-proof
git add scripts tests
git commit -m "feat: add resumable autoresearch orchestration"
```

Append proof for Phase 5 and all intermediate SHAs to `PROGRESS.md`.

---

### Task 9: Full verification and local handoff

**Files:**
- Modify: `PROGRESS.md`
- Modify: `HANDOFF.md`
- Create/modify: `HANDOFF.html`
- Create/modify: `HANDOFF.excalidraw`

**Interfaces:**
- Consumes: all artifacts and commits.
- Produces: evidence matrix for Prover/Checker; no shipping side effect.

- [ ] **Step 1: Run identity, preservation, and secret checks**

```powershell
git rev-parse --show-toplevel
git branch --show-current
git show-ref refs/recovery/repo-cleanup-full-update/initial-dirty refs/recovery/repo-cleanup-full-update/dashboard-metadata
git stash list --format='%gd %H %gs'
py scripts/recovery_inventory.py verify --manifest docs/recovery/repo-cleanup-full-update/inventory.csv --expected-recorded-count 3558 --expected-ref refs/recovery/repo-cleanup-full-update/initial-dirty --quarantine-map docs/recovery/repo-cleanup-full-update/quarantine-map.csv --quarantine-root .quarantine/repo-cleanup-full-update
py -c "import json, pathlib; json.loads(pathlib.Path('.mcp.json').read_text(encoding='utf-8')); print('mcp json ok')"
py scripts/credential_scan.py
```

Expected: exact worktree/branch, refs and named stashes unchanged, 3,561 enumerated/zero unexplained, MCP parses, no literal secret result.

- [ ] **Step 2: Run focused and full tests**

```powershell
py -m pytest tests/test_recovery_inventory.py tests/test_domain_contract_docs.py tests/test_repository_policy.py tests/test_mcp_configuration.py -q
py -m pytest tests/test_autoresearch_contracts.py tests/test_autoresearch_gate.py tests/test_autoresearch_artifacts.py tests/test_source_adapters.py tests/test_autoresearch_orchestrator.py tests/test_autoresearch_clis.py scripts/test_autoresearch_agent.py -q
py -m pytest -q
```

Expected: all focused tests pass; authoritative suite passes with no new skip/xfail. Record any environment-only legacy limitation precisely rather than weakening focused gates.

- [ ] **Step 3: Exercise CLIs non-interactively**

Run both help, deliberately invalid input, dry-run, stub-run, and resume commands. Capture exit codes, relevant output, and run directories. Verify the second run reports no duplicate role/provider execution or budget charge. Run `py scripts/validate.py --summary` to preserve the approval lifecycle without network.

- [ ] **Step 4: Perform scope/diff audit**

```powershell
git diff --check HEAD~1 HEAD
git log --oneline --decorate -12
git status --short
```

Inspect for flow-catalog work, full GTM implementation, live writes, credential values, unrelated normalization, external-worktree references, and benchmark runtime imports. All must be absent.

- [ ] **Step 5: Write the local handoff**

Include worktree/branch, phase commits, preservation manifest and recovery commands, architecture, command/exit/output evidence matrix, checker rubric, DRAFT/live-auth gaps, limitations, and proposed review structure. Produce the HTML and Excalidraw versions without credentials or PII. Record `N/A - shipping not approved`; publish only the sanitized report with `lavish-axi share HANDOFF.html`, store its update key in ignored `HANDOFF.secret.local`, and use `lavish-axi export HANDOFF.html --out HANDOFF.export.html` with the failure recorded if sharing is unavailable.

- [ ] **Step 6: Commit and require clean status**

```powershell
git add PROGRESS.md HANDOFF.md HANDOFF.html HANDOFF.excalidraw
git commit -m "docs: record repository modernization verification"
git status --short
```

Expected: empty final status. Then hand artifacts to Prover and a fresh Checker. No push/PR/merge/deploy/worktree deletion is authorized.

---

## Self-review checklist

- Spec coverage: all recovery, docs/domain, MCP/GTM read-only, orchestration, provider, CLI, testing, security, and handoff requirements map to Tasks 1–9.
- Count integrity: plan preserves all 3,561 Git-enumerated paths and labels 3,558 as recorded baseline; it does not invent or hide the three-entry discrepancy.
- TDD: every implementation/config task has an explicit failing command, minimal implementation step, and green command; no new skip/xfail is permitted.
- Type consistency: `AutoresearchOrchestrator.run(request: RunRequest) -> RunSummary`, `SourceAdapter.search/extract`, `GateInput`, and `GateDecision` names are stable across tasks.
- Scope: no catalog, full GTM provider, remote writes, paid calls, secrets, shipping, or benchmark-runtime import is planned.
- Placeholders: no TBD/TODO/deferred implementation steps are present; live verification gaps are explicitly evidence limitations, not acceptance substitutes.
