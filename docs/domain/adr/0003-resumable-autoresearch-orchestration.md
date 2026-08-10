# Persisted orchestration for resumable autoresearch

## Status

Accepted

## Context

Autoresearch needs to survive interruption without repeating paid work, while keeping role inputs isolated and its lifecycle auditable. The approved design requires a single deep Module rather than CLIs that coordinate role order, persistence, budgets, failures, and gate transitions themselves. Canonical domain language is defined in [CONTEXT.md](../../../CONTEXT.md).

## Considered Options

1. **Persisted state machine with an append-only journal.** Persist the run request and immutable artifacts, record each transition in order, and reconstruct state for resume. This makes idempotency, corruption checks, retry limits, and the first missing stage local to one Module.
2. **Event-sourced reducer.** Model every domain change as an event and derive all state through a reducer. It provides a strong audit history, but adds projection and event-schema complexity before the workflow needs independent read models or event consumers.
3. **Directory-per-cycle pipeline.** Let each CLI or cycle directory infer the next role from files already present. It is easy to begin, but spreads lifecycle rules, resume logic, and error handling across callers and leaves no authoritative transition contract.

## Decision

Choose the persisted state machine with an append-only journal. Its external Interface is:

```python
AutoresearchOrchestrator.run(request: RunRequest) -> RunSummary
```

`AutoresearchOrchestrator` owns role ordering, schema validation, idempotency, persistence, budgets, failures, and transition selection behind that Interface. CLIs compose it: they construct the request and adapters, call `run`, and render the summary; they do not coordinate roles or infer lifecycle state.

The Gate is pure: validated inputs and prior transition state produce one decision and reason code, without writing artifacts, invoking a provider, or granting Approval. Provider variation stays at read-only Source Adapter seams, so the orchestration Module consumes provider-neutral Evidence and tests can substitute local adapters without changing its Interface.

## Consequences

The orchestration Module gains depth, leverage, and locality: callers learn one operation while resume, budgets, idempotency, and safe halts remain in one place. The journal makes state reconstruction and audit possible, and preserving pure Gate behavior makes all transition outcomes deterministic to test.

This choice requires versioned artifacts, journal validation, and migration discipline as contracts evolve. It intentionally postpones event projections and prevents CLIs from adding role-specific coordination; new provider behavior must be supplied through a read-only Source Adapter rather than embedded in the orchestration Module.
