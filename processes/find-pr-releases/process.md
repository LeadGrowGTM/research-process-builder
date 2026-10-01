# find press releases and official announcements

model: gpt-6-luna
service_tier: flex

> **validated:** 25 companies across 4 tiers (3,357 searches). PRIMARY at Q3.9. T1:Q4.0, T2:Q4.0, T3:Q4.0, T4:Q3.5. solid across all tiers, slight drop for micro companies.

surface official company communications: press releases, blog announcements, wire service distributions.

## inputs

- `{{company_name}}` — the company to research
- `{{domain}}` — their website domain (required for site: searches)
- `{{category}}` — what they do in 2-3 words. required if name is ambiguous.
- `{{current_year}}` — the current year (e.g. 2026). in Clay: `YEAR({Created At})`.

## steps

### step 1: general announcement search

search: `{{company_name}} {{category}} announces`

25-company tier test: `{{company_name}} "press release" OR "announces" OR "newsroom"` (best_pr) scored PRIMARY Q3.9. T1-T3 all Q4.0, T4 drops to Q3.5.

extract from results:

- every official announcement found
- for each: date, title, source with URL (company blog / wire service / news outlet), and a three sentence summary of what was announced
- note the announcement cadence (how frequently do they announce things?)

**stop if:** you found 3+ official announcements with dates and summaries. skip to output.

### step 2: company blog

search: `site:{{domain}}/blog`

extract from results:

- blog URL
- recent post titles and dates
- posting frequency (weekly, monthly, quarterly, sporadic)
- three sentence summary of the most recent post

### step 3: company newsroom / press page

search: `site:{{domain}}/newsroom`

if no results, try in order: `site:{{domain}}/press`, then `site:{{domain}}/news`, then `site:{{domain}}/media`

extract from results:

- newsroom URL (or "not found")
- most recent press release title and date
- three sentence summary

**stop if:** you have blog content from step 2 AND press content from step 3. skip to output.

### step 4: wire service check (skip for small/bootstrapped companies)

search: `{{company_name}} site:businesswire.com`

extract from results:

- any formal press releases on businesswire
- three sentence summary per release

if no results, try: `{{company_name}} site:prnewswire.com`

### step 5: year-filtered press releases

search: `{{company_name}} press release {{current_year}}`

extract from results:

- any recent releases not found in previous steps
- three sentence summary per release

## do not search

- `{{company_name}} media release` — american tech companies don't use this phrase
- `{{company_name}} official announcement` — weaker duplicate of "announces"
- `{{company_name}} annual report` — private companies don't publish these
- `site:apollo.io {{company_name}}` — gated data, returns SEO blog posts

## output

```
## press releases and announcements for {{company_name}}

**communication style:** [active PR machine / regular blogger / occasional announcements / mostly dark]

**channels found:**
- blog: [url or "not found"]
- newsroom: [url or "not found"]
- wire services: [businesswire / prnewswire / none]

**recent releases:**

1. [date] — [source name](url) — [three sentence summary of what was announced, the key details, and why it matters]

2. [date] — [source name](url) — [three sentence summary]

3. [date] — [source name](url) — [three sentence summary]

(continue for all releases found)

**last known communication:** [date or "unknown"]

**sources:**
- [source name](url) — what was found there
- [source name](url) — what was found there
```
