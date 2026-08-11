# Company Enrichment Library and Benchmark Corpus Design

**Date:** 2026-08-11
**Status:** Approved through the enrichment-priority review and the user's
directive to proceed. The user retained all proposed P1 enrichments, accepted
the recommended defaults, and changed the dossier-construction cap to $2 total.

## Purpose

Build a company-first library of reusable research enrichments and prove each
one against a versioned, deeply researched company corpus. Cheap production
models compete against stronger comparators on factual quality, cost, latency,
and human happiness. Automated gates may create candidates; only a human may
approve AI-generated output.

Direct people/contact discovery is not part of this release. Target-persona
analysis remains in scope when it describes who a company sells to. Social
activity is filter-only and must never appear in outbound copy.

## Chosen Approach

Three shapes were considered:

1. **Independent scripts per enrichment.** Fast initially, but each script
   duplicates search, scraping, budgets, evidence, model routing, and review.
2. **Manifest-driven library over shared deep modules.** Each YAML definition
   declares inputs, outputs, execution mode, providers, budgets, and gates.
   Shared evidence, runner, benchmark, and review modules own the difficult
   policy. This is the selected design.
3. **Implement everything inside GTM Orchestrator.** This maximizes immediate
   reuse but couples a portable research library to campaign-stage state and
   makes independent benchmarking awkward.

The library therefore lives in this repository and treats GTM Orchestrator and
Nexus as mandatory capability-discovery surfaces and provider dependencies, not
as the library's state owner.

## Required Capability Discovery

Every enrichment begins with a recorded discovery preflight:

1. Inspect the installed GTM Orchestrator plugin/pipeline, active source, skills,
   CLIs, provider registry, and relevant Git history.
2. Search Nexus across clients and global knowledge for proven tools, patterns,
   providers, and failure modes.
3. Record the selected reusable capability or a precise verified gap.
4. Only then design a new adapter or fallback.

Nexus authentication failure is non-fatal but never silent. The run records the
error and continues with GTM/local discovery. The current session demonstrated
this behavior: Nexus lacked `NEXUS_BOUNDARY_TOKEN`.

Known reusable capabilities include:

- Parallel Search MCP for search only; `web_fetch` is disabled.
- GTM Orchestrator `web-scraping` v2.1.0 for known URLs:
  HTTP/html2text -> Crawl4AI -> Firecrawl -> Firecrawl JS.
- `lg_free` company signals, including Google Ads Transparency.
- TechSight for deterministic technology detection.
- LinkedIn Jobs and LinkedIn Ads Apify paths.
- Historical `apify-meta-ads`, backed by
  `leadsbrary/meta-ads-library-scraper`, for Meta/Facebook/Instagram validation.
  It was replaced because LinkedIn was preferred for B2B, not because Meta was
  irrelevant. Reuse it for ecommerce/CPG after a 1-3 URL schema/cost smoke test.

## Library Contracts

Each enrichment is a versioned YAML document validated before execution. It
declares:

- stable id, name, owner, version, status, family, priority, entity scopes;
- input/output schema versions and required/optional prerequisites;
- execution mode: deterministic, LLM-only, web search, search-and-scrape, or
  parallel search;
- provider candidates, fallback order, freshness window, source requirements;
- query, scrape, retry, token, latency, and paid-cost caps;
- early-stop and failure rules;
- message-safe versus filter-only outputs;
- benchmark dataset version and automated/human approval gates.

The public runner accepts one request and returns one result:

```python
class EnrichmentRunner:
    def run(self, request: EnrichmentRequest) -> EnrichmentResult: ...
```

It owns routing, caching, evidence collection, cost accounting, retry limits,
model resolution, schema validation, and failure normalization. Enrichment
definitions never call providers directly.

## Evidence and Ground Truth

The evidence store is content-addressed and append-only. Each source record
contains URL, retrieval time, source type, provider, content hash, bounded
excerpt or artifact pointer, freshness, citation status, and collection cost.
Provider responses are cached and reused; benchmarks never repurchase evidence
merely to test another model.

A company dossier contains identity, cohort/tags, description, offers/products,
ICP and target personas, recent news/launches, growth evidence, ads, hiring,
competitors, technology, pricing, source snapshots, unknowns, and human
corrections. Facts are field-level assertions tied to evidence, not one prose
blob.

Research continues free-first until source saturation: required fields have
cited evidence, at least one first-party source and two independent sources
have been checked where available, and two additional search angles add no
material facts. Paid calls stop at the budget even if gaps remain; gaps remain
explicitly unknown.

## Benchmark Population

The corpus contains 60 unique companies with one primary cohort and any number
of secondary tags:

- 10 Google Maps/local businesses;
- 10 SaaS companies;
- 10 companies funded within the prior 12 months;
- 10 well-known companies;
- 10 agencies;
- 10 ecommerce/CPG companies.

Fifteen of the 60 form a shared, cross-category core. The core is used for fast
cross-model and cross-enrichment comparisons. Cohort-specific sets test domain
fit and failure modes. Company selection must avoid near-duplicates, include
easy/ambiguous/hard research cases, and record why each fixture belongs.

The initial corpus build has a **$2 total paid-API ceiling** across all 60
companies. Each enrichment experiment has a separate **$1 total paid-API
ceiling** across the selected benchmark set. These are not per-company budgets.

## P0 Enrichments

The first tranche contains:

1. plain-English company description;
2. specific ICP and target-persona analysis;
3. recent news and product launches;
4. consolidated growth signals;
5. running ads and offer intelligence;
6. job-post opportunity mining;
7. competitor and competitor-change intelligence;
8. analogy/value translator.

Technology changes, pricing changes, third-party review mining, and funding/
traction remain P1. Funding may still appear as a component of consolidated
growth signals and as a benchmark cohort attribute.

## Ads and Seller Context

Ad coverage is channel-aware:

- Google and Meta for applicable companies;
- LinkedIn for B2B;
- TikTok for ecommerce/CPG.

Each result distinguishes active, inactive, and unknown; records observation
dates, geography, creative angle, offer, CTA, landing page, evidence, and
confidence; and avoids treating stale entries or mentions as live ads.

Seller context is a reusable contract containing target market/persona,
capabilities, named offer, timeline, promised outcome, proof, guarantee or
de-risking, exclusions, and current-investment/worldview fields. Job mining and
the analogy translator consume the same contract rather than inventing their
own inputs.

## Experiment and Approval Flow

Every run appends:

- enrichment, prompt, schema, and dataset versions;
- fixture and prerequisites;
- provider route and exact requested/resolved model ids;
- queries, scrapes, retries, tokens, latency, and paid cost;
- structured output, citations, confidence, and failure reason;
- field-level correctness, citation validity/completeness/freshness;
- message-safe/filter-only labels;
- blind human scores for readability, specificity, usefulness, casualness, and
  non-creepiness;
- human verdict: approve, revise, or reject.

Status transitions are:

`proposed -> experiment -> candidate -> approved | rejected`

Mechanical and accuracy gates can create `candidate`. Only explicit blind human
review can create `approved`.

## Model Ladder

Use fixed fixtures and identical prompts/settings. Record synchronous and Batch
API results separately.

- cheap baselines: GPT-5 nano and GPT-4o mini;
- instruction-following comparator: GPT-4.1 mini;
- premium comparator: GPT-5.6 Luna.

Promotion is per enrichment. No model becomes the default merely because it is
newer or stronger elsewhere.

## Rollout

1. Inventory GTM Orchestrator and Nexus capabilities and freeze the provider map.
2. Implement schemas, evidence/cache, budgets, and append-only ledgers.
3. Select all 60 companies and build the 15-company core dossiers first.
4. Validate corpus quality and benchmark machinery on the core.
5. Complete the remaining 45 dossiers under the same $2 aggregate cap.
6. Build and benchmark P0 enrichments, one independently releasable definition
   at a time.
7. Produce blind-review artifacts; do not self-approve outputs.

## Errors and Safety

External failures normalize to retryable, terminal, budget-exhausted,
authentication-required, contract-invalid, or insufficient-evidence. Retries
are bounded and charged. Paid work is reserved in the ledger before execution.
Resume skips content-addressed evidence and completed idempotency keys.

No secret value enters YAML, logs, benchmark records, or Git. No provider,
including an archived one, is used live until its current authentication,
schema, price, and terms are validated. No social signal is inserted into
outbound copy.

## Verification

Deterministic tests prove:

- YAML/schema validation and version compatibility;
- exactly 60 unique fixtures, 10 per primary cohort, and 15 shared-core members;
- tool discovery checks GTM Orchestrator and Nexus before new-provider fallback;
- Parallel is search-only;
- known URLs route through the GTM waterfall and record level/cost/reason;
- provider retry, authentication, budget, cache, resume, and early-stop paths;
- $2 corpus and $1 experiment aggregate caps;
- append-only benchmark history and exact model/provider identity;
- deterministic score calculations and status transitions;
- automated code cannot produce `approved`;
- filter-only fields cannot appear in generated outbound text.

Live smoke tests use 1-3 fixtures per paid provider before any wider run. Meta
Ads specifically revalidates the historical actor schema and transform. Failed
or incomplete live checks remain visible gaps, never claimed successes.
