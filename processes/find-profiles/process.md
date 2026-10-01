# find company profile and funding data

model: gpt-6-luna
service_tier: flex

> **validated:** 25 companies across 4 tiers (3,357 searches). company_profile: multi-platform site: queries ENRICHMENT Q3.8 (T3-T4 actually strong at Q3.8-4.0). funding_financial: `{{company_name}} {{category}} funding` PRIMARY Q4.0 all tiers.

build a company fact sheet from structured data platforms. this should run first because it feeds context (category, size, funding) to every other research process.

## inputs

- `{{company_name}}` — the company to research
- `{{domain}}` — their website domain
- `{{category}}` — what they do in 2-3 words. required if name is ambiguous.

## steps

### step 1: multi-platform sweep

search: `{{company_name}} {{category}} company overview`

25-company tier test: multi-platform site: queries (combo_multi_platform) scored ENRICHMENT Q3.8. performs surprisingly well for T3-T4 companies (Q3.8-4.0). the simple `{{company_name}} funding` variant also scored ENRICHMENT Q3.8, while `{{company_name}} {{category}} funding` (runner_cat_funding) scored PRIMARY Q4.0 across all tiers.

extract from results:

- what the company does (one sentence)
- category / industry
- employee count range
- funding stage and total raised (if visible)
- headquarters location
- founded year
- list every third-party platform that has a profile (zoominfo, crunchbase, linkedin, rocketreach, pitchbook, tracxn, owler, cbinsights, g2, etc.) — include the URL for each

count the platforms found:

- 5+ platforms = tier 1 (well-known)
- 2-4 platforms = tier 2 (some coverage)
- 0-1 platforms = tier 3 (obscure)

**stop if:** you have a clear description, category, employee count, and funding info. skip to output.

### step 2: crunchbase funding data

search: `site:crunchbase.com {{company_name}}`

25-company tier test: funding searches with category qualifier (`{{company_name}} {{category}} funding`) consistently outperformed generic funding queries. PRIMARY Q4.0 all tiers.

extract from results:

- funding rounds (dates, amounts, lead investors)
- total raised
- last round date and type
- three sentence summary of the funding history

**stop if:** combined with step 1, you have all core profile data. skip to output.

### step 3: zoominfo company intelligence

search: `site:zoominfo.com {{company_name}}`

extract from results:

- revenue estimate
- employee count
- industry classification
- three sentence summary of any new info not in steps 1-2

zoominfo covers all company sizes, including startups less than a year old.

### step 4: linkedin profile

search: `site:linkedin.com/company {{company_name}}`

extract from results:

- employee count (often more current than other sources)
- about section text
- specialties listed
- three sentence summary of anything new

### step 5: rocketreach org chart and people data

search: `site:rocketreach.co {{company_name}}`

note: the domain is `rocketreach.co` (NOT `.com`). `site:rocketreach.com` returns zero results.

extract from results:

- employee count (often more granular than other sources)
- key people and their titles (CEO, CTO, VP of Sales, etc.)
- department breakdown if visible from titles
- company description and industry tags
- three sentence summary of anything new not in steps 1-4

rocketreach covers even T3 companies. tested: found Hoo.be's CEO, CRO, and team despite having only 5-9 employees. particularly useful for org chart data that other platforms don't surface.

### step 6: company website fallback (only if steps 1-5 returned thin results)

search: `{{company_name}} official website about`

extract from results:

- self-described company purpose and product
- team size indicators
- three sentence summary

only use this for obscure companies where the major platforms have minimal data.

## do not search

- `site:apollo.io {{company_name}}` — gated data, returns SEO blog posts
- `site:rocketreach.com {{company_name}}` — wrong domain, returns zero results. use `site:rocketreach.co` instead
- `{{company_name}} annual report` — useless for private companies
- `site:stackshare.io {{company_name}}` — only dev tools, not company intel

## output

```
## company profile: {{company_name}}

**domain:** {{domain}}
**tier:** [1 / 2 / 3]

**what they do:** [one sentence]
**category:** [industry / sector]
**founded:** [year]
**hq:** [city, country]
**employees:** [range]

**funding:**
- stage: [seed / series a / b / etc. / bootstrapped]
- total raised: [$X or "not disclosed"]
- last round: [$X, date, lead investor]

**profiles found:** [comma separated list, e.g. "zoominfo, crunchbase, linkedin, pitchbook, tracxn"]

**three sentence summary:** [what this company is, what makes them notable, and their current stage/trajectory]

**sources:**
- [source name](url) — what was found there
- [source name](url) — what was found there
```
