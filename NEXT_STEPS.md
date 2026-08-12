# Company Enrichment Library — Next Steps Tree

**Updated:** 2026-08-12
**Branch:** `wt/company-enrichment-b2b`

```text
FOUNDATION
├── DONE: B2B-only scope
├── DONE: immutable contracts and strict manifests
├── DONE: homepage-first routing correction
├── DONE: production routes separated from comparative providers
├── DONE: AGENTS.md/CLAUDE.md workspace compatibility
└── HUMAN GATE 2A: approve the revised foundation
    │
    ├── PHASE 2: CAPABILITY DISCOVERY — local/read-only first
    │   ├── Verify homepage-scrape → GTM Orchestrator v2.1.0 adapter
    │   ├── Verify lg_free fields, schema, identity matching, and current route
    │   ├── Verify Harvest job-data capability
    │   ├── Inventory other free job-enrichment capabilities
    │   ├── Enforce Parallel as search-only and late fallback/comparator
    │   ├── Verify Firecrawl auth, schema, pricing, and level metadata
    │   └── Save capability registry with explicit gaps
    │
    ├── INPUT/OUTPUT DESIGN — iterate with real provider samples
    │   ├── classify caller-required, system-required, and optional inputs
    │   ├── DONE: ingest AI-Ark as the starting corpus snapshot
    │   ├── DONE: generate 60-company seed and explicit gap matrix
    │   ├── retain DiscoLike averages and failure behavior
    │   ├── define outputs independently of provider schemas
    │   └── save every decision in the living logic-tree register
    │
    ├── HUMAN GATE 1C: approve exact B2B benchmark
    │   ├── 60 unique B2B companies
    │   ├── 10 per primary cohort
    │   ├── 15 shared-core companies
    │   └── easy / ambiguous / hard coverage in every cohort
    │
    ├── PHASE 3: EVIDENCE AND BUDGET MODULES
    │   ├── content-addressed evidence and cache
    │   ├── field-level citations and explicit unknowns
    │   ├── source-saturation stopping rule
    │   ├── append-only cost ledger and resume
    │   └── enforce $2 corpus / $1 experiment aggregate caps
    │
    ├── PHASE 4: PROVIDER ADAPTERS
    │   ├── homepage-scrape
    │   │   ├── L1 HTTP/html2text — free
    │   │   ├── L2 Crawl4AI — free
    │   │   ├── L3 Firecrawl standard — APPROVED when L1/L2 insufficient
    │   │   └── L4 Firecrawl JS — APPROVED when L3 insufficient
    │   ├── lg_free structured enrichment
    │   ├── Harvest and free job sources
    │   ├── company careers-page scraping
    │   └── Parallel search fallback/comparator
    │
    ├── HUMAN GATE 3: LIVE PROVIDER SMOKES
    │   ├── Firecrawl provider: APPROVED for public B2B company URLs
    │   ├── Firecrawl still requires ledger reservation and global caps
    │   ├── Start with 1–3 public fixtures and preserve cost/schema proof
    │   └── Other live providers remain individually pending
    │
    ├── PHASE 5A: OUTPUT SCHEMAS AND PROMPTS
    │   ├── exact output fields for all eight enrichments
    │   ├── actual prompt text
    │   └── one representative B2B input/output example each
    │
    ├── HUMAN GATE 2B: approve actual prompts and output schemas
    │
    ├── PHASE 5B: RUNNER AND EXECUTORS
    │   ├── production waterfall execution
    │   ├── independent comparative-provider runs
    │   ├── normalized failures, cache, retries, and resume
    │   └── block filter-only facts from outbound text
    │
    ├── PHASE 6: B2B CORPUS
    │   ├── build and validate 15 shared-core dossiers first
    │   ├── HUMAN GATE 4: review core dossiers
    │   └── build remaining 45 after approval
    │
    ├── PHASE 7: COMPARATIVE BENCHMARKS
    │   ├── compare each eligible source independently
    │   ├── compare production waterfalls
    │   ├── compare model ladder on cached evidence
    │   ├── score accuracy/citations/freshness/cost/latency
    │   └── HUMAN GATE 5: blind verdict per enrichment
    │
    ├── PHASE 8: CLI AND OPERATOR DOCUMENTATION
    │
    └── PHASE 9: FULL PROOF
        ├── tests, validators, credential and cap checks
        ├── acceptance manifest exactly 100%
        ├── verification report and handoff
        └── HUMAN GATE 6: final release decision
```

## Immediate execution order

1. Receive Gate 2A decision on the corrected foundation.
2. Confirm or swap companies in the generated 60-company B2B starting list.
3. Fill the 15 shared-core gaps first: homepage scrape → lg_free → targeted search.
4. Implement Phase 2 capability discovery and evidence/budget modules test-first around that flow.
5. Review the 15 core dossiers, then fill the remaining 45.
6. Continue into the eight enrichment outputs and comparative experiments.

## Firecrawl approval interpretation

The user's approval covers sending public B2B company URLs to Firecrawl for company-site extraction when the free L1/L2 scraper is insufficient. It covers L3 standard and L4 JS escalation within the already agreed aggregate limits:

- Corpus construction: $2.00 total paid cost across all 60 companies.
- Each enrichment experiment: $1.00 total paid cost across its selected fixtures.
- Initial smoke: 1–3 public company URLs.

The implementation must reserve maximum cost before calling, record the level/reason/cost, cache the result, and stop at the cap. This does not approve any sensitive payload, contact data, unrelated URL, deployment, or external sharing.
