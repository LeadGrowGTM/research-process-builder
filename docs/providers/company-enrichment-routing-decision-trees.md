# Company Enrichment Routing Decision Trees

**Status:** Authoritative routing decision record.
**Last updated:** 2026-08-12 after human correction.

## Terms

- **Homepage scrape** is the stable library interface for retrieving a known company domain.
- **GTM Orchestrator** is the current adapter behind that interface. It is a local pipeline/plugin, not a source of company facts by itself.
- The adapter uses its `web-scraping` skill v2.1.0:
  - Level 1: HTTP + html2text, free
  - Level 2: Crawl4AI browser rendering, free
  - Level 3: Firecrawl, paid
  - Level 4: Firecrawl JS rendering, paid
- Default company research uses Levels 1–2 first. Firecrawl Levels 3–4 were approved on 2026-08-12 for public B2B company URLs when free levels are insufficient, subject to ledger reservation and the existing aggregate caps.
- **lg_free** is a structured company-enrichment source used to fill supported firmographic and signal fields.
- **Parallel** is a novel web-search vehicle. It is especially useful for people search, but people discovery is outside this company library. For company enrichments it is a comparison route and late gap-filling fallback, not the default source.
- **Harvest** is the preferred job-data route. Its current job schema, authentication, price, and terms must be verified during capability discovery before live use.

## Production company-research tree

1. Start with the known company domain.
2. Run a free homepage/site scrape through the `homepage-scrape` interface.
3. Extract and cite all supported facts from first-party pages.
4. If required structured fields remain unknown and lg_free supports those fields, query lg_free.
5. Merge only identity-matched, cited values; retain conflicts and unknowns explicitly.
6. If required facts remain unresolved, run a targeted search route.
7. Parallel may be used as a late fallback or a separately measured search route.
8. Stop at source saturation or the relevant budget cap.

For company description, ICP/personas, and growth signals, the default production waterfall is:

`homepage-scrape → lg-free → parallel-search`

News and competitor research use:

`homepage-scrape → parallel-search`

because lg_free is not assumed to cover those facts until capability discovery proves otherwise.

## Job-opportunity tree

1. Query Harvest for current jobs.
2. Query verified free job-enrichment sources for missing coverage.
3. Scrape the company's own careers pages through the free homepage/site adapter.
4. Use Parallel search only as a later fallback for unresolved jobs.
5. Preserve source, posting date, role, location, status, and uncertainty.
6. Do not infer an opportunity from an expired or unverifiable job.

Default production waterfall:

`harvest-jobs → free-job-enrichment → company-careers-scrape → parallel-search`

## Comparative-analysis tree

Production fallback order and benchmarking are separate:

1. Select fixed B2B fixtures and a fixed as-of date.
2. Run each eligible provider independently against the same requested fields.
3. Reuse cached evidence; do not let one provider's output become another provider's input.
4. Score field coverage, factual correctness, citation validity, freshness, latency, and cost.
5. Retain failures and unknowns.
6. Compare individual providers and the production waterfall.
7. Promote a provider or waterfall per enrichment only after deterministic scoring and blind human review.

This means Parallel, lg_free, homepage scraping, Harvest, and other eligible adapters can be compared without making every comparator part of the default production route.

## Ads tree

Ads remain channel-specific rather than using a generic search-first route:

- lg_free for supported Google Ads signals
- LinkedIn Ads for B2B
- Meta only after schema/cost smoke validation where applicable
- First-party landing pages through homepage scraping

Every channel records active, inactive, or unknown independently.

## Safety

- No people/contact discovery in this library.
- Parallel remains search-only; it never fetches known URLs.
- Firecrawl paid escalation is approved for public B2B company URLs within the existing aggregate caps; every call still records fixture, reason, level, reservation, and actual cost.
- Other paid providers require explicit provider/fixture/payload/cost approval.
- No social/filter-only fact enters outbound copy.
- No provider failure is converted into a success.
