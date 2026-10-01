# find recent news and company events

model: gpt-6-luna
service_tier: flex

> **validated:** 25 companies across 4 tiers (3,357 searches). ENRICHMENT at Q3.6. T1:Q4.0, T2:Q3.8, T3:Q4.0, T4:Q2.2. T4 micro companies have inherently thin news coverage. accept this limitation.

surface everything newsworthy about a company in the last 6-12 months. partnerships, funding, acquisitions, product launches, expansions, leadership changes, controversies.

## inputs

- `{{company_name}}` — the company to research
- `{{domain}}` — their website domain
- `{{category}}` — what they do in 2-3 words. required if name is ambiguous.
- `{{current_year}}` — the current year (e.g. 2026). in Clay: `YEAR({Created At})`.

## steps

### step 1: general news sweep

search: `{{company_name}} {{category}} recent news`

25-company tier test: `{{company_name}} {{category}} news OR announced OR launch` (combo_name_news) scored ENRICHMENT Q3.6. `{{domain}} news` (best_domain_news) is a FALLBACK at Q3.5. news is structurally weak for T4 micro companies (Q2.2).

extract from results:

- every distinct news event found
- for each: exact date from the search snippet or article (e.g., "Mar 2, 2026"), event type (partnership/funding/acquisition/launch/expansion/leadership/controversy), source URL, and a three sentence summary
- NEVER fabricate or guess a date. if no date appears in the snippet or article, use "date unknown". getting the date wrong is worse than admitting you don't have it.

**stop if:** you found 4+ distinct news events covering multiple event types. skip to output.

### step 2: M&A and funding activity

search: `{{company_name}} acquisition OR funding {{current_year}}`

extract from results:

- any acquisitions (acquired someone or was acquired)
- any funding rounds (amount, lead investor, exact date, round type)
- source URL and three sentence summary per event

**stop if:** combined with step 1 you have a clear picture of the company's financial trajectory and you also have non-financial news from step 1. skip to output.

### step 3: partnerships and integrations

search: `{{company_name}} partnership OR integration`

extract from results:

- strategic alliances, integration announcements, channel partnerships
- exact date, source URL, and three sentence summary per partnership (who, what, why it matters)

### step 4: product and expansion news

search: `{{company_name}} launches OR "new feature" OR expansion {{current_year}}`

extract from results:

- product launches, feature releases, geographic expansion, new offices, new markets
- exact date, source URL, and three sentence summary per event

**stop if:** you have a solid mix of news across event types. skip to output.

### step 5: leadership and strategic narrative

search: `{{company_name}} CEO interview OR "new hire" OR leadership`

extract from results:

- CEO/founder quotes about company direction
- new C-suite hires or departures
- exact date, source URL, and three sentence summary per event

### step 6: tech press (only if the company is VC-backed / well-known)

search: `{{company_name}} site:techcrunch.com`

extract from results:

- any coverage not already found
- exact date, source URL, and three sentence summary per article

skip if the company is small or bootstrapped.

### step 7: activity signals (only if steps 1-5 returned almost nothing)

for obscure companies where traditional news sources have nothing.

search: `{{company_name}} hiring OR "linkedin posts" OR blog`

extract from results:

- hiring activity (open roles = alive and growing)
- recent social or blog posts from the company
- exact date (if available), source URL, and three sentence summary of activity signal

if even this returns nothing, that's the finding. "no news coverage found" is a signal, not a failure.

## do not search

- `site:businessinsider.com {{company_name}}` — zero results for startups
- `site:reuters.com {{company_name}}` — useless below unicorn tier
- `{{company_name}} breaking news` — identical to "news", wastes a search

## output

```
## recent news and events for {{company_name}}

**company trajectory:** [growing / stable / declining / pivoting / too early to tell]

**news events:**

1. [exact date, e.g. "Mar 2, 2026" or "date unknown"] — [event type] — [three sentence summary of what happened, why it matters, and what it signals] — [source](url)

2. [exact date or "date unknown"] — [event type] — [three sentence summary] — [source](url)

3. [exact date or "date unknown"] — [event type] — [three sentence summary] — [source](url)

(continue for all distinct events found)

**event types found:** [comma separated, e.g. "funding, partnerships, product launches"]
```
