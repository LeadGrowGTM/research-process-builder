# find c-suite executives (technical and commercial)

model: gpt-6-luna
service_tier: flex

find the c-suite leadership of a company across both technical roles (CTO, CPO, CISO) and commercial roles (CMO, CRO, COO, CFO).

## inputs

- `{{company_name}}` — the company to research
- `{{domain}}` — their website domain (e.g. clay.com)
- `{{category}}` — what they do in 2-3 words. required if the company name is a common word or 6 characters or fewer.

## steps

### step 1: technical c-suite (CTO, CPO, CISO)

search: `{{company_name}} "chief technology" OR "chief product" OR "chief security" -jobs -careers`

targets technical c-suite with full title strings. tested at Q3.8 across 25 companies (PRIMARY). T1-T2: PRIMARY, T3-T4: ENRICHMENT (T4 drops to Q3.2 as smaller companies may not have these roles filled or publicly indexed).

extract from results:

- full name and exact title of every technical c-suite member found
- the source where they were mentioned (press, company page, interview) — include the URL
- any context about their technical background, previous roles, or expertise
- whether the result comes from a current or historical reference

**stop if:** you found CTO and/or CPO with names confirmed by 2+ sources. skip to step 3 for commercial c-suite.

### step 2: technical c-suite media mentions (runner)

search: `{{company_name}} CTO OR CPO interview OR podcast OR talk`

catches technical leaders appearing in podcasts, conference talks, and interviews. useful when step 1 returns thin results.

extract from results:

- any technical c-suite names not found in step 1
- the context of their appearance (what they discussed, what event)
- source URLs

### step 3: commercial c-suite (CMO, CRO, COO, CFO)

search: `{{company_name}} "chief marketing" OR "chief revenue" OR "chief operating" -careers`

targets commercial c-suite roles. tested at Q3.7 across 25 companies (ENRICHMENT). T1-T2: PRIMARY, T3-T4: FALLBACK (T4 drops to Q3.0 as commercial c-suite titles are rare at micro companies).

extract from results:

- full name and exact title of every commercial c-suite member found
- the source where they were mentioned — include the URL
- any context about their commercial background, previous roles
- date of mention if visible (helps identify current vs former)

### step 4: commercial c-suite abbreviation search (runner)

search: `{{company_name}} CMO OR CRO OR CFO OR COO -jobs -careers`

catches abbreviation-style mentions that step 3 misses. some sources use "CMO" rather than "Chief Marketing Officer."

extract from results:

- any commercial c-suite names not found in step 3
- source URLs and context

### step 5: linkedin fallback (use only if steps 1-4 returned thin results)

search: `site:linkedin.com/in {{company_name}} "chief" -jobs`

broad LinkedIn search for any c-suite title. scored lower than direct queries (Q2.6) but useful as a last resort for companies with minimal press coverage.

extract from results:

- LinkedIn profile URLs for any c-suite members not found above
- current title as listed on LinkedIn
- location visible in snippets

## do not search

- `site:zoominfo.com OR site:rocketreach.co {{company_name}} CMO OR CRO` — platform combo queries scored FALLBACK (Q2.7), worse than plain search queries
- `{{company_name}} c-suite team` — too vague, returns job postings and generic company pages

## output

```
## c-suite executives: {{company_name}}

**technical c-suite:**
- [name] — [title, e.g. Chief Technology Officer]
- [name] — [title]

**commercial c-suite:**
- [name] — [title, e.g. Chief Marketing Officer]
- [name] — [title]

**coverage notes:** [note which roles were not found, e.g. "no CFO or COO identified — common for T3-T4 companies"]

**background highlights:**
- [name] — [one sentence on relevant background]

**sources:**
- [source name](url) — what was found there
- [source name](url) — what was found there
```
