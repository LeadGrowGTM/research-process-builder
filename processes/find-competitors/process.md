# find competitors and competitive positioning

model: gpt-6-luna
service_tier: flex

> **validated:** 25 companies across 4 tiers (3,357 searches). PRIMARY at Q4.0. T1:Q4.0, T2:Q4.0, T3:Q4.0, T4:Q4.0. Rock solid across all tiers.

find the direct competitors of a company and explain why each one competes.

## inputs

- `{{company_name}}` — the company to research
- `{{domain}}` — their website domain (e.g. clay.com)
- `{{category}}` — what they do in 2-3 words (e.g. "GTM data enrichment", "legal AI"). required if the company name is a common word or 6 characters or fewer.

## steps

### step 1: broad competitor and alternatives sweep

search: `{{company_name}} {{category}} alternatives OR competitors OR "vs" OR "compared to"`

this single query catches competitor lists, "alternatives to" roundups, and head-to-head comparisons in one shot. tested at Q5/C5 across all company tiers. 25-company tier test: ENRICHMENT Q3.8 (combo_broad_alts variant at Q3.7). strong across all tiers but the simpler `{{company_name}} competitors` pattern scored Q4.0 as PRIMARY.

extract from results:

- every company named as a competitor or alternative
- which source mentioned them (G2, blog, Tracxn, company's own site, etc.) — include the URL
- one sentence on what each competitor does
- if any results come from `{{domain}}` itself (the company's own comparison or "vs" pages), flag those — the company naming its own competitors is the highest-signal source

**stop if:** you found 5+ competitors from structured sources (G2, Capterra, Tracxn, or the company's own site). skip to output.

### step 2: direct competitor search

search: `{{company_name}} {{category}} competitors`

25-company tier test: `{{company_name}} competitors` (runner_competitors) scored PRIMARY Q4.0 across all tiers. the `{{category}}` qualifier adds precision but the name-only variant is the strongest single pattern tested.

extract from results:

- any competitors not found in step 1
- which source mentioned them
- one sentence on what each competitor does

**stop if:** combined with step 1, you have 5+ unique competitors with clear positioning. skip to output.

### step 3: category market map

search: `best {{category}} tools`

extract from results:

- full list of tools mentioned in the category
- how each is positioned relative to `{{company_name}}`
- any market segments or subcategories identified

### step 4: G2 structured data (software companies only)

search: `site:g2.com {{company_name}} alternatives`

extract from results:

- G2 alternative listings with ratings
- category ranking if visible

skip this step if `{{company_name}}` is not a software company.

### step 5: head-to-head positioning

search: `{{company_name}} vs {{top_competitor_from_above}}`

extract from results:

- how the two companies differ (pricing, features, ideal customer)
- which one wins in which scenario
- three sentence summary of the competitive dynamic

**stop if:** you have clear positioning context for the top 3 competitors. skip to output.

### step 6: practitioner opinions

search: `who competes with {{company_name}} {{category}}`

extract from results:

- competitors mentioned by actual users (forums, reddit-synthesis articles, blog comments)
- any competitors the structured platforms missed

### step 7: domain-anchored fallback (use only if steps 1-2 returned noise from an ambiguous name)

search: `{{domain}} competitors`

extract from results:

- competitors identified via domain matching (unambiguous, zero noise)

## do not search

- `{{company_name}} market landscape` — returns industry research papers, not competitors
- `{{company_name}} competitive intelligence` — returns CI vendor marketing
- `site:crunchbase.com {{company_name}} competitors` — description matching is inaccurate
- `{{domain}} competitors site:similarweb.com` — traffic-based, identifies audience sites not competitors

## output

```
## competitive landscape for {{company_name}}

**top competitors:** [competitor a], [competitor b], [competitor c]

**why these three:**
- [competitor a] — [one sentence on why they compete directly. what do they share? where do they differ?]
- [competitor b] — [one sentence on why they compete directly]
- [competitor c] — [one sentence on why they compete directly]

**also mentioned:** [competitor d], [competitor e], [etc.] — [one sentence on why these are secondary/adjacent competitors]

**how {{company_name}} is positioned:**
[three sentences max. what's their angle vs the field? where do they win? where are they weaker?]

**sources:**
- [source name](url) — what it provided
- [source name](url) — what it provided
```
