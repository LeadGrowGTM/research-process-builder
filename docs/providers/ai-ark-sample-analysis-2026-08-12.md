# AI-Ark Corpus Seed Snapshot

**Analyzed:** 2026-08-12
**Source:** user-provided local CSV; source file is not copied into the repository.
**SHA-256:** `8CEEA40D917C8B6674CAF40B07B8365B39722E5ED1C802FFC8E3E0EBFEB520C4`
**Privacy:** aggregate analysis only; no row values, company emails, or phone numbers retained here.

## Seed interpretation

- 9,340 returned company rows and 28 columns.
- The list contains North American companies selected through offshorable job-title criteria.
- This export is starting data for selecting and pre-populating the 60-company corpus.
- AI-Ark is not being evaluated as a provider in this work.
- Populated values are retained as `unverified_seed`; blanks drive the next research steps.
- No hit-rate, latency, cost, or general-market coverage analysis is required.

## Aggregate coverage

| Field | Filled |
|---|---:|
| Company name | 100.0% |
| Headcount | 100.0% |
| Employee-size band | 100.0% |
| Industry | 99.3% |
| Industry tags | 99.6% |
| Products and services | 71.3% |
| Description | 99.6% |
| SEO description | 58.3% |
| Website | 99.2% |
| LinkedIn | 100.0% |
| X/Twitter | 43.9% |
| Facebook | 51.0% |
| Instagram | 0.0% |
| Company type | 97.8% |
| Number of locations | 100.0% |
| Address | 100.0% |
| Country | 100.0% |
| State | 96.5% |
| City | 98.7% |
| Company email | 52.3% |
| Company phone | 93.4% |
| Founding year | 91.7% |
| Revenue band | approximately 100.0% |
| Total funding | 15.4% |
| Last funding type/date | 17.5% |
| Last funding amount | 12.2% |
| Technologies | 92.9% |

## Data-shape findings

- Website values are mostly bare domains; normalize to canonical absolute URLs.
- LinkedIn values are absolute URLs, but 87 of 9,340 are not standard `/company/` paths and require identity validation.
- There are 12 duplicate normalized-name groups and 34 duplicate normalized-domain groups.
- Revenue is a categorical range, not a measured numeric value.
- Funding dates are parseable where present, but funding coverage is sparse.
- `Industry Tags`, `Product and Services`, and `Technologies` are comma-heavy text, not JSON arrays.
- Product/service values may contain prose commas and must not be naively split.
- Employee-size includes a `null+` anomaly in 0.6% of rows; normalize that value to unknown.
- The export has no field-level source URLs or observation timestamps.

## Seed role decisions

### Retain as starting data

- Company identity and canonical-locator candidates.
- Company-description comparison and structured filler.
- Industry/category and firmographic support for ICP analysis.
- Headcount, size band, location, company type, and founding-year snapshots.
- Revenue-band and funding hints that require corroboration.
- Technology detection comparison in the deferred P1 technology enrichment.

### Must be filled or corroborated by research

- Cited ground truth by itself because field-level source URLs are absent.
- Growth/change claims without historical snapshots and observation dates.
- Current jobs or job-post opportunity mining.
- Active ads.
- News and launches.
- Competitor relationships.
- Target personas.
- Analogy/value translation without a cited dossier and SellerContext.

### Excluded from this P0 interface

- Company email and phone are not needed for the eight P0 research enrichments.
- Social profile URLs may aid identity matching, but social activity remains filter-only.

## Gap-filling flow

1. Select the 60 B2B companies from the export.
2. Copy allowed company fields into the corpus as `unverified_seed`.
3. Exclude company email and phone.
4. Compute explicit field gaps.
5. Use homepage scraping first to fill and corroborate the seed.
6. Use lg_free for supported structured blanks.
7. Use targeted search only for remaining research fields.
8. Promote a value to dossier evidence only after citation and identity checks.

The generated corpus currently has all 60 domains, descriptions, and industries; all 60 need target-customer research, and 12 need products/services.
