# Company Enrichment Library Execution Plan

**Execution shape:** phased implementation with human review gates.
**Branch:** `wt/company-enrichment-b2b`
**Sources:** approved design, nine-task implementation plan, harness, and the user's B2B-only correction.

## Dependency order

1. Contracts and eight P0 manifests.
2. GTM/Nexus capability discovery.
3. Evidence, cache, saturation, and aggregate budgets.
4. Provider-neutral adapters.
5. Runner and eight P0 executors.
6. B2B-only 60-company corpus, with the 15-company core first.
7. Benchmark scoring, blind review, and human-only approval.
8. Operator CLI and documentation.
9. Live smoke tests, full verification, and handoff.

## Human gates

- Gate 1: B2B scope accepted; exact 60-company list and 15-company core remain pending.
- Gate 2: review contracts, manifests, SellerContext, and visibility boundaries after Phase 1.
- Gate 3: approve each live provider, fixture set, payload scope, and spend before paid calls.
- Gate 4: approve the 15 core dossiers before building the remaining 45.
- Gate 5: blind human verdict per enrichment; automation cannot approve.
- Gate 6: final release decision after complete verification.

## Safety boundaries

- No direct contact discovery, P1 enrichments, push, merge, deploy, or automated approval.
- Parallel is search-only; known URLs use the GTM waterfall.
- Corpus paid spend is capped at $2 aggregate; each experiment at $1 aggregate.
- Credentials and raw provider payloads never enter committed artifacts.
