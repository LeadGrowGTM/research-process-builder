# Company Enrichment Library — Human Approval Sheet

**Last updated:** 2026-08-12
**Active branch:** `wt/company-enrichment-b2b`
**Rule:** Approval of one gate does not approve later gates, paid calls, external sharing, deployment, or enrichment outputs.

## Status at a glance

| Gate | Decision | Status |
|---|---|---|
| 1A — Product scope | Eight P0 enrichments; no contacts or standalone P1 work | Approved |
| 1B — Benchmark market | Every test company and tested offer must be B2B | Approved |
| 1C — Exact corpus | Approve 60 companies and 15 shared-core members | Proposed from AI-Ark seed; pending swaps/confirmation |
| 2A — Foundation | Approve implemented contracts, manifests, SellerContext, and visibility boundary | Pending now; see `GATE_2_REVIEW.md` |
| 2B — Prompts and outputs | Approve actual prompts and output field schemas before executors | Pending; not built yet |
| 3 — Live providers and spend | Approve each provider, payload, fixtures, and maximum cost | Firecrawl approved for public B2B company URLs within existing caps; other providers pending |
| 4 — Core dossiers | Approve the first 15 researched dossiers before the remaining 45 | Pending |
| 5 — Enrichment candidates | Human blind verdict for each enrichment | Pending |
| 6 — Release | Accept, revise, narrow, or reject final library | Pending |

## Gate 1 — Scope and corpus

### Already approved

- Eight P0 enrichments:
  1. Plain-English company description
  2. B2B ICP and target-persona analysis
  3. Recent news and product launches
  4. Consolidated growth signals
  5. Running ads and offer intelligence
  6. Job-post opportunity mining
  7. Competitor and competitor-change intelligence
  8. Analogy/value translator
- All benchmark companies and tested offers must be B2B.
- Exactly 60 unique companies, 10 in each of six B2B cohorts, with 15 shared-core companies.
- Direct contact/people discovery is excluded.
- Standalone P1 technology, pricing, reviews, and funding enrichments are excluded.
- The earlier mixed B2B/B2C company proposal is rejected and must not be used.

### Still pending

The exact revised 60-company list and 15 shared-core members now exist in `benchmarks/company-selection.yaml`; the pre-populated gap matrix is `benchmarks/companies.yaml`.

Approval wording: **“Approve Gate 1C corpus”**, optionally followed by substitutions.

## Gate 2A — Implemented foundation (decision needed now)

The complete plain-language artifact being reviewed is `GATE_2_REVIEW.md`. Approval may be given directly in chat; file access is not required because the agent must paste the material decision summary into the approval request.

Routing correction recorded 2026-08-12:

- Free homepage/site scraping is the default first source for known company domains.
- lg_free fills supported structured gaps.
- Parallel is a novel search comparator and late fallback, not the default company-enrichment route.
- Harvest is the preferred job-data route, followed by verified free job sources and company careers pages; Parallel is the later fallback.
- Production fallback order and comparative benchmark providers are separate manifest fields.
- The durable decision tree is `docs/providers/company-enrichment-routing-decision-trees.md`.

### What has been implemented

- Frozen, immutable Python contracts for requests, results, evidence, field assertions, dossiers, and SellerContext.
- Canonical JSON serialization with recursive rejection of secret-bearing keys.
- Evidence requires absolute HTTP(S) URLs, timezone-aware retrieval dates, SHA-256 hashes, and bounded excerpts.
- Field assertions retain evidence IDs, confidence, and visibility.
- Strict YAML manifests reject unknown keys, environment interpolation, invalid versions, secret fields, and experiment caps above $1.
- Eight P0 manifests use benchmark version `b2b-companies-1.0`.
- Automated gates may create candidates only; a blind human verdict is required for approval.
- Enrichment results are message-safe at the result level. Individual social-activity facts can be marked `filter_only` and must be blocked from outbound text.
- Job mining and analogy/value translation require the shared SellerContext.

### SellerContext fields

- Target market
- Personas
- Capabilities
- Named offer
- Timeline
- Promised outcome
- Proof
- De-risking/guarantee
- Exclusions
- Current investment/worldview

### Recommended decision

Approve this architecture as version 1. Changes can still be made through tested schema-version updates; approval does not freeze implementation details forever.

Approval wording: **“Approve Gate 2A”** or **“Revise Gate 2A: …”**

### What Gate 2A does not authorize

- Network calls
- Credential access
- Paid API calls
- Researching the 60-company corpus
- Human approval of generated enrichment output
- Push, merge, deploy, or external sharing

## Gate 2B — Prompts and output schemas

Before P0 executors are implemented, present the actual prompt text and exact output fields for all eight enrichments in one review packet. Include one representative B2B input and expected structured output per enrichment. Do not ask for approval until these artifacts exist.

Required/optional input and output decisions are maintained incrementally in `docs/company-enrichment-input-output-decision-trees.md`. Provider samples inform availability and routing, but do not define the public enrichment interface.

Approval wording: **“Approve Gate 2B”** or **“Revise Gate 2B: …”**

## Gate 3 — Live providers, payloads, and spend

Before any live or paid call, the approval request must state:

- Provider and route
- Exact fixture companies or URLs
- Data sent and data returned
- Destination/account
- Whether credentials are used through the normal provider flow
- Maximum number of calls
- Maximum aggregate cost
- Expected artifact

Hard ceilings remain:

- All 60 corpus dossiers: **$2.00 total aggregate**
- Each enrichment experiment: **$1.00 total aggregate**
- Paid provider smoke test: **1–3 fixtures**

Approval is provider-specific. Example:

> Approve Gate 3 for Meta actor `ZQyDz7154hrOfrDMK`, using the three listed public company URLs, maximum $0.20 aggregate, for schema/cost validation only.

### Firecrawl approval — recorded 2026-08-12

Firecrawl is approved for public B2B company-site extraction when free homepage scraping is insufficient:

- L1 HTTP/html2text and L2 Crawl4AI run first.
- L3 Firecrawl standard may run when L1/L2 are insufficient.
- L4 Firecrawl JS may run when L3 is insufficient.
- Start with 1–3 public company URLs.
- Reserve cost before execution and retain level, reason, schema, and actual-cost proof.
- The existing $2 corpus-wide and $1 experiment-wide aggregate ceilings remain binding.

This approval does not cover sensitive data, contact data, unrelated URLs, cap bypass, deployment, or external sharing. Other live providers remain pending.

## Gate 4 — Fifteen-company core dossiers

Before building the remaining 45 dossiers, present:

- All 15 core companies
- Cohort and B2B qualification
- Field-level citations
- Known facts and explicit unknowns
- Source saturation evidence
- Provider routes and failures
- Ledger total
- Any human corrections needed

Approval wording: **“Approve Gate 4 core dossiers”**, optionally with named corrections.

## Gate 5 — Blind enrichment review

Each of the eight enrichments requires a separate human verdict after blind review. The review hides model/provider identity until the verdict is recorded and scores:

- Factual correctness
- Citation validity and completeness
- Readability
- Specificity
- Usefulness
- Casualness
- Non-creepiness

Allowed verdicts:

- Approve
- Revise
- Reject
- Redirect

Automation cannot issue an approved verdict.

## Gate 6 — Final release

After tests, validators, CLI proof, credential checks, cap proof, and the acceptance manifest are complete, choose:

- Accept the library
- Request fixes
- Narrow scope
- Reject named enrichments
- Keep outputs as candidates

Release approval does not automatically authorize push, merge, deployment, or external publishing. Those require an explicit instruction.

## Standing prohibitions

- No fabricated evidence or hidden provider failures.
- No secret values or raw provider payloads in Git, logs, manifests, or reports.
- No social/filter-only facts in outbound copy.
- No automated approval.
- No cap bypass.
- No unapproved contacts, push, merge, deployment, or external sharing.
