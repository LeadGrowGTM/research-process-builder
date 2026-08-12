# AI-Ark Company Export Analysis

**Analyzed:** 2026-08-12
**Source:** user-provided local CSV; source file is not copied into the repository.
**SHA-256:** `8CEEA40D917C8B6674CAF40B07B8365B39722E5ED1C802FFC8E3E0EBFEB520C4`
**Privacy:** aggregate analysis only; no row values, company emails, or phone numbers retained here.

## Sample limits

- 9,340 returned company rows and 28 columns.
- The list contains North American companies selected through offshorable job-title criteria.
- This is a returned-results export, not a list of attempted API lookups.
- It measures field completeness among returned rows, not provider hit rate.
- It is selection-biased and cannot establish general-market coverage, latency, freshness, or cost.

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

## Provider role decisions

### Useful for

- Company identity and canonical-locator candidates.
- Company-description comparison and structured filler.
- Industry/category and firmographic support for ICP analysis.
- Headcount, size band, location, company type, and founding-year snapshots.
- Revenue-band and funding hints that require corroboration.
- Technology detection comparison in the deferred P1 technology enrichment.

### Not sufficient for

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

## Provisional routing

AI-Ark remains **comparator/structured-filler pending live capability validation**:

1. Scrape first-party company pages first.
2. Compare/fill supported structured fields using lg_free and AI-Ark.
3. Preserve conflicts rather than choosing silently.
4. Require first-party or independent corroboration before a field becomes benchmark ground truth.
5. Do not promote AI-Ark into a default route until input requirements, hit rate, freshness, cost, latency, and response-level provenance are measured.

## Evidence still needed

- Raw company-level request/response pair.
- Miss/not-found and partial/error responses.
- Lookup denominator to calculate hit rate.
- Retrieval timestamp or freshness semantics.
- Cost/credit and latency data.
- Whether response fields expose source/provenance URLs.
