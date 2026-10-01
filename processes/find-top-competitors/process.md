# find top competitors (lean output)

model: gpt-6-luna
service_tier: flex

> uses the same search depth as find-competitors but outputs only the top 3 direct competitors. lean output for enrichment pipelines.

find the top 3 direct competitors of a company. output only names, sources, and confidence. no positioning analysis.

## inputs

- `{{company_name}}` — the company to research
- `{{domain}}` — their website domain (e.g. clay.com)
- `{{category}}` — what they do in 2-3 words (e.g. "GTM data enrichment", "legal AI"). required if the company name is a common word or 6 characters or fewer.

## steps

### step 1: broad competitor and alternatives sweep

search: `{{company_name}} {{category}} alternatives OR competitors OR "vs" OR "compared to"`

extract from results:

- every company named as a competitor or alternative
- which source mentioned them (G2, blog, Tracxn, company's own site, etc.) and the URL
- if any results come from `{{domain}}` itself (the company's own comparison or "vs" pages), flag those as highest-signal

**stop if:** you found 5+ competitors from structured sources (G2, Capterra, Tracxn, or the company's own site). skip to filtering.

### step 2: direct competitor search

search: `{{company_name}} {{category}} competitors`

extract from results:

- any competitors not found in step 1
- which source mentioned them

**stop if:** combined with step 1, you have 5+ unique competitors with clear positioning. skip to filtering.

### step 3: category market map

search: `best {{category}} tools`

extract from results:

- full list of tools mentioned in the category
- which ones overlap with {{company_name}}'s core function

### step 4: G2 structured data (software companies only)

search: `site:g2.com {{company_name}} alternatives`

extract from results:

- G2 alternative listings
- category ranking if visible

skip this step if `{{company_name}}` is not a software company.

### step 5: head-to-head validation

search: `{{company_name}} vs {{top_competitor_from_above}}`

use this to validate whether your top candidate is actually a direct competitor. if the "vs" search returns no meaningful comparison content, downgrade that competitor's confidence.

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

## filtering

before outputting, filter your full list down to the top 3:

1. **same category check** — does this competitor actually do the same thing as {{company_name}}? if {{company_name}} is an AI platform, a sales intelligence tool is NOT a competitor even if G2 lists them together. G2 miscategorizes frequently. use {{category}} as the filter.
2. **mention frequency** — competitors mentioned across multiple independent sources rank higher than single-source mentions.
3. **source quality** — company's own "vs" page > G2/Capterra > dedicated comparison blog > general listicle.

output exactly 3. if you genuinely cannot find 3 direct competitors, output fewer and explain why.

## output

```
1. [competitor name] — [source name](url) — confidence: [high/medium/low]
2. [competitor name] — [source name](url) — confidence: [high/medium/low]
3. [competitor name] — [source name](url) — confidence: [high/medium/low]
```

## confidence scoring

- **high** — named on G2/Capterra alternatives page, company's own "vs" page, or mentioned as competitor in 2+ independent sources. must pass same-category check.
- **medium** — mentioned in one blog/listicle or comparison article. passes same-category check.
- **low** — only appeared in a general "best tools" list or tangential mention. borderline category match.
