# Gate 2A Review Packet — Implemented Foundation

**Review date:** 2026-08-12
**Decision requested:** approve or revise only the implemented contract and manifest foundation described below.
**Not included:** final prompt text, final enrichment output schemas, provider calls, corpus companies, or generated outputs.

## What exists and has been tested

### Public data contracts

| Contract | Actual fields |
|---|---|
| Enrichment request | enrichment ID, company ID, input schema version, input values |
| Enrichment result | enrichment ID, company ID, output schema version, complete/partial/failed status, output values, normalized failure |
| Evidence reference | evidence ID, absolute HTTP(S) URL, timezone-aware retrieval time, SHA-256 content hash, excerpt |
| Field assertion | field name, value, evidence IDs, confidence from 0 to 1, message-safe/filter-only visibility |
| Company dossier | company ID, schema version, field assertions, evidence, explicit unknowns |
| SellerContext | target market, personas, capabilities, named offer, timeline, promised outcome, proof, de-risking, exclusions, current investment/worldview |

All contract objects are immutable after creation. Nested input/output mappings are frozen.

### Allowed result states

- Result: `complete`, `partial`, or `failed`
- Failure: `retryable`, `terminal`, `budget_exhausted`, `authentication_required`, `contract_invalid`, or `insufficient_evidence`
- Visibility: `message_safe` or `filter_only`
- Review lifecycle: `proposed → experiment → candidate → approved|rejected`

A complete result cannot carry a failure. A failed result must carry a normalized failure.

### Evidence and secret safety

- Evidence URLs must be absolute HTTP(S) URLs.
- Retrieval timestamps must include a timezone.
- Content hashes must be valid 64-character SHA-256 hex values.
- Stored excerpts are limited to 2,000 characters.
- Assertions cannot reference evidence missing from the dossier.
- Confidence must be finite and between 0 and 1.
- Canonical serialization recursively rejects keys such as API keys, authorization, credentials, passwords, secrets, tokens, and update keys.
- YAML manifests reject unknown keys, environment interpolation, malformed versions, secrets, and paid experiment caps above $1.

## Eight implemented B2B manifests

| Enrichment | Required inputs | Mode | Provider order | Freshness | Query/scrape cap | Source rule |
|---|---|---|---|---:|---:|---|
| Company description | company, domain | Search + scrape | Parallel → GTM waterfall | 30 days | 8 / 8 | First party + two independent |
| ICP/persona analysis | company, domain | Parallel search | Parallel → GTM waterfall | 90 days | 10 / 8 | First party + two independent |
| News/product launches | company, domain | Parallel search | Parallel → GTM waterfall | 30 days | 10 / 10 | First party + two independent |
| Growth signals | company, domain | Parallel search | LG Free → Parallel → GTM | 30 days | 12 / 10 | First party + two independent |
| Ads/offer intelligence | company, domain | Search + scrape | LG Free → LinkedIn → Meta | 7 days | 6 / 6 | Channel primary + landing page |
| Job opportunity mining | company, domain, SellerContext | Search + scrape | LinkedIn Jobs → Parallel → GTM | 14 days | 8 / 8 | Job primary + company first party |
| Competitor intelligence | company, domain | Parallel search | Parallel → GTM waterfall | 30 days | 12 / 10 | First party + two independent |
| Analogy/value translator | company, domain, SellerContext | LLM-only over cited inputs | Model router | 30 days | 0 / 0 | Cited dossier + SellerContext |

Every manifest:

- Is version `1.0.0` and starts as `proposed`
- Uses B2B benchmark version `b2b-companies-1.0`
- Uses explicit unknowns rather than fabricated values
- Has a $1 maximum experiment budget
- Allows automation to create a candidate only
- Requires a blind human verdict for approval

## Visibility rule being proposed

Enrichment outputs are eligible to be message-safe, but each individual assertion carries its own visibility. Social-activity facts are marked `filter_only` and cannot enter outbound text. This avoids discarding useful cited job or growth facts merely because an enrichment can also observe social signals.

## Known limitations — not being approved yet

- Final output field schemas for each enrichment have not been implemented.
- Prompt text and extraction instructions have not been implemented.
- Provider discovery and routing behavior have not been implemented.
- Budget/evidence persistence has not been implemented.
- No B2B company list, dossier, live result, or model output is being approved.
- No live or paid call has been made.

These items will receive separate review packets before use.

## Decision

Approve in chat with:

> Approve Gate 2A

Request changes with:

> Revise Gate 2A: [specific contract, manifest, field, cap, provider order, or safety change]

Approval authorizes Phase 2 local implementation and tests only. It does not authorize network access, provider execution, spending, corpus research, external sharing, push, merge, or deployment.
