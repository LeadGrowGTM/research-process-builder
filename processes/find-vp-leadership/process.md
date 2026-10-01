# find VP-level leadership across sales, marketing, and engineering

model: gpt-6-luna
service_tier: flex

find Vice President-level leaders across the three core functions: sales/revenue, marketing/growth, and engineering/product.

## inputs

- `{{company_name}}` — the company to research
- `{{domain}}` — their website domain (e.g. clay.com)
- `{{category}}` — what they do in 2-3 words. required if the company name is a common word or 6 characters or fewer.

## steps

### step 1: VP sales and revenue

search: `{{company_name}} "VP" sales OR revenue OR partnerships -careers -salary`

targets VP-level sales leadership. tested at Q3.9 across 25 companies (PRIMARY). strong across all tiers (T1-T4: PRIMARY).

extract from results:

- full name and exact title of every VP-level sales/revenue person found
- the source where they were mentioned — include the URL
- any context about their sales org, team size, or mandate
- whether they lead sales, revenue, partnerships, or a specific segment

**stop if:** you found a VP Sales or VP Revenue with name confirmed by 2+ sources. continue to step 2 for marketing.

### step 2: VP marketing and growth

search: `{{company_name}} "VP" marketing OR growth OR brand OR content -careers`

targets VP-level marketing leadership. tested at Q3.8 across 25 companies (PRIMARY). T1-T2: PRIMARY, T3-T4: ENRICHMENT (smaller companies often have a Head of Marketing rather than VP).

extract from results:

- full name and exact title of every VP-level marketing/growth person found
- the source where they were mentioned — include the URL
- any context about their marketing focus (demand gen, brand, content, product marketing)
- date of mention if visible

### step 3: VP engineering and product

search: `{{company_name}} "VP" OR "Vice President" engineering OR product -jobs -careers`

targets VP-level technical leadership. tested at Q3.9 across 25 companies (PRIMARY). strong across all tiers (T1-T4: PRIMARY).

extract from results:

- full name and exact title of every VP-level engineering/product person found
- the source where they were mentioned — include the URL
- any context about their technical domain (platform, infrastructure, product, data)
- previous companies or notable projects mentioned

### step 4: linkedin fallback (use only if steps 1-3 returned thin results for a specific function)

search: `site:linkedin.com/in {{company_name}} "VP" OR "Vice President" [function]`

replace `[function]` with the specific area that returned thin results (e.g. "sales", "marketing", "engineering"). scored lower than direct queries but useful for companies with minimal press coverage.

extract from results:

- LinkedIn profile URLs for any VP-level people not found above
- current title as listed on LinkedIn
- location and background visible in snippets

## do not search

- `site:zoominfo.com {{company_name}} VP sales` — platform combo queries scored lower than plain search queries
- `{{company_name}} VP salary` — returns compensation data sites, not people
- `{{company_name}} "Vice President" -jobs -careers -salary -glassdoor` — too many exclusions dilute results

## output

```
## VP-level leadership: {{company_name}}

**VP sales / revenue:**
- [name] — [title, e.g. VP of Sales]

**VP marketing / growth:**
- [name] — [title, e.g. VP of Marketing]

**VP engineering / product:**
- [name] — [title, e.g. VP of Engineering]

**coverage notes:** [note which functions had no VP found, e.g. "no VP Marketing identified — T3 companies often use Head of Marketing instead"]

**sources:**
- [source name](url) — what was found there
- [source name](url) — what was found there
```
