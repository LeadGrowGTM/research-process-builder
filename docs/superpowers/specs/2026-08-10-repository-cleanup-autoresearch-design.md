# Repository Cleanup and Resumable Autoresearch Design

**Date:** 2026-08-10
**Status:** Approved by the explicit goal and continuation directive

## Purpose

Modernize `research-process-builder` without changing its purpose: it builds reusable research processes, validates them against ground truth until accuracy is at least 90%, and then requires human review before approval. The work also makes the initial dirty workspace recoverable, adds a local Parallel Search MCP integration, and provides deterministic evidence that autoresearch roles receive fresh compact context.

## Scope

In scope:

- A complete inventory and recovery map for the initial dirty paths, backed by durable local Git object references.
- Repository guidance, domain language, artifact policy, safety rules, commands, dependencies, and approval lifecycle.
- Repository-scoped Parallel Search MCP configuration from official documentation, with OAuth or environment references and no credential values.
- A provider-neutral read seam for Search Flows, Site Extraction Flows, and Source Adapters.
- A resumable autoresearch state machine with fresh role context and deterministic gates.
- Deterministic tests and CLI proofs using local doubles at zero paid API cost.

Out of scope:

- A future flow catalog.
- A complete GTM provider.
- Any remote write, production job, monitor, Supabase mutation, Clay/GTM mutation, shared-sheet change, credential change, push, PR, merge, or deployment.

## Domain Language

- **Research Flow**: a validated, portable sequence that produces research evidence for a stated goal.
- **Search Flow**: a Research Flow that begins with a query and returns ranked source candidates.
- **Site Extraction Flow**: a Research Flow that begins with known URLs and extracts evidence deterministically before any optional LLM interpretation.
- **Source Adapter**: an adapter at the provider seam that executes read-only discovery or extraction and returns provider-neutral evidence.
- **Experiment**: one proposed change to a research process, identified by a stable content-derived key.
- **Evidence**: bounded, source-attributed observations used to evaluate an Experiment.
- **Approval**: the lifecycle state reached only after at least 90% ground-truth validation and explicit human review.

These definitions belong in root `CONTEXT.md`; implementation details do not.

## Recovery Design

The original dirty state remains immutable under:

- `refs/recovery/repo-cleanup-full-update/initial-dirty`
- `refs/recovery/repo-cleanup-full-update/dashboard-metadata`

The inventory is generated from the recovery commit trees, not from the now-clean checkout. Every old and new path receives a classification, disposition, recovery object, and recovery command. Generated or campaign-specific files remain recoverable through these refs and an ignored local quarantine before any removal. Durable reusable knowledge is selectively restored into reviewed repository locations. The inventory must explain the recorded 3,558-path baseline and any Git rename/path-count normalization explicitly; no path is silently dropped to force a count.

## Orchestration Architecture

`autoresearch_agent.py` becomes a thin CLI and composition root. `autocontext_runner.py` becomes a compatibility CLI over a deep orchestration module. Core behavior lives behind one interface:

```python
class AutoresearchOrchestrator:
    def run(self, request: RunRequest) -> RunSummary: ...
```

The module owns role order, schema validation, idempotency, persistence, budgets, failures, and gate transitions. Callers provide adapters; callers do not coordinate roles themselves.

Each cycle is:

1. **Inventor** receives only the immutable run brief, current validated baseline summary, bounded prior decisions, and budget remainder. It produces one Experiment artifact.
2. **In-bounds Checker** independently receives the immutable constraints plus the Experiment. It cannot see Inventor reasoning.
3. **Novelty Checker** independently receives the Experiment plus compact fingerprints of prior experiments. It cannot see Inventor reasoning or the In-bounds transcript.
4. **Executor** runs only after both checkers accept and produces versioned Evidence. Its idempotency key is derived from the validated Experiment and execution inputs.
5. **Evaluator** independently receives the immutable rubric, Experiment, and Evidence. It cannot see role transcripts and cannot execute providers.
6. **Gate** is pure and deterministic. It consumes validated checker/evaluator results, transition history, approval threshold, retry limits, rollback availability, and budgets, then emits exactly one of `advance`, `retry`, `rollback`, or `halt_for_review` plus a reason code.

No raw model transcript crosses a seam. A role invocation receives a freshly constructed request envelope, and tests compare envelope identities and permitted fields.

## Persistence and Resume

Each run uses an artifact directory containing:

- `run.json`: immutable request, schema version, constraints, budgets, and approval threshold.
- `journal.jsonl`: append-only transition records with sequence number, artifact hashes, and idempotency keys.
- `cycles/<cycle-id>/<role>.json`: schema-validated role outputs.
- `objects/<sha256>.json`: immutable canonical JSON payloads.
- `summary.json`: reconstructible projection for CLI display.

Writes use temporary files followed by atomic replacement. Resume validates journal sequence and artifact hashes, reconstructs state, skips completed idempotency keys, and begins at the first missing or invalid stage. Paid work is never repeated merely because the process restarted. Corrupt or incompatible artifacts cause `halt_for_review`; they are not guessed around.

## Gate Semantics

- `advance`: checks accepted, execution and evaluation succeeded, candidate improves or satisfies the declared criterion, and budget remains.
- `retry`: a retryable role or execution failure occurred, or evaluation requests a bounded revision, and retry budget remains.
- `rollback`: execution completed but evaluation shows regression or invalid evidence and a validated prior baseline exists.
- `halt_for_review`: approval threshold is reached; any budget is exhausted; retry exhaustion occurs without safe rollback; artifacts are corrupt/incompatible; or no automatic action is safe.

The gate never grants human approval. Reaching at least 90% ground-truth validation produces `halt_for_review`; only explicit human review can move the process to approved.

## Provider Seams

```python
class SourceAdapter(Protocol):
    def search(self, request: SearchRequest) -> SearchResult: ...
    def extract(self, request: ExtractionRequest) -> ExtractionResult: ...
```

Search and extraction request/result contracts are provider-neutral, bounded, serializable, source-attributed, and read-only. Search Flow starts from a query. Site Extraction Flow rejects missing known URLs, then tries fetch/scrape, selectors, regex, and deterministic patterns in that order; an optional LLM extractor can run only when explicitly enabled and deterministic extraction is insufficient.

Parallel is one search adapter configured through repository MCP metadata. GTM is documented only as a read interface discovered from the installed MCP surface; no full adapter or remote writes are implemented.

## Errors and Budgets

All external work is charged through a budget ledger before execution. Limits cover calls, queries, scrapes, LLM invocations, elapsed stages, retries, and declared cost. The default paid-cost ceiling is zero. Adapter errors are normalized as retryable, terminal, budget-exhausted, or contract-invalid. Secrets are never serialized into requests, artifacts, logs, configuration, or reports.

## Testing

Deterministic doubles prove:

- Every role receives a fresh compact envelope with only allowed fields.
- Invalid schemas and out-of-bounds or duplicate experiments are rejected before Executor invocation.
- All four gate outcomes and reason codes.
- Query, scrape, LLM, retry, and cost budgets.
- Executor/evaluator failures, rollback, retry exhaustion, artifact corruption, and version mismatch.
- Resume skips completed idempotency keys and does not repeat paid work.
- Search, Site Extraction, Parallel, and GTM read contracts without network writes.
- Both affected CLIs support help, invalid-input failure, dry-run, stubbed run, and resume.
- The existing validation and human-approval lifecycle remains at least 90% ground-truth validation followed by review.

## Documentation and Handoff

Root `CLAUDE.md` is verified against actual commands and files. `HANDOFF.md`, `HANDOFF.html`, and `HANDOFF.excalidraw` record preservation evidence, architecture, commands, checker results, limitations, and live-verification gaps. Shipping remains unapproved.
