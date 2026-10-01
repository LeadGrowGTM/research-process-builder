# find department heads and sales operations leads

model: gpt-6-luna
service_tier: flex

find "Head of" roles across growth, operations, partnerships, and customer success, plus RevOps/Sales Operations leaders.

## inputs

- `{{company_name}}` — the company to research
- `{{domain}}` — their website domain (e.g. clay.com)
- `{{category}}` — what they do in 2-3 words. required if the company name is a common word or 6 characters or fewer.

## steps

### step 1: head of department (broad sweep)

search: `{{company_name}} "Head of" growth OR operations OR partnerships OR "customer success" -jobs -careers`

broad runner pattern that catches department heads across multiple functions in one query. tested at Q3.9 across 25 companies (PRIMARY).

extract from results:

- full name and exact title of every "Head of" person found
- the source where they were mentioned — include the URL
- which function they lead (growth, ops, partnerships, CS, people, etc.)
- any context about their team, mandate, or background
- date of mention if visible (helps identify current vs former)

**stop if:** you found 3+ department heads with names confirmed by 2+ sources. skip to step 3 for sales ops.

### step 2: linkedin head of department search

search: `site:linkedin.com/in {{company_name}} "Head of" growth OR revenue OR operations`

LinkedIn-specific search for department heads. useful when step 1 returns results from less structured sources.

extract from results:

- LinkedIn profile URLs for any department heads not found in step 1
- current title as listed on LinkedIn
- any additional "Head of" roles not covered in step 1 (e.g. Head of Design, Head of Data)
- location and background visible in snippets

### step 3: sales operations and RevOps leads

search: `{{company_name}} "RevOps" OR "Revenue Operations" OR "Sales Operations" -jobs -careers`

targets the operations layer of the sales org. tested at Q3.6 across 25 companies (ENRICHMENT, not PRIMARY). T3 companies are weak here (Q3.3) because smaller companies rarely have dedicated RevOps or SDR Manager titles publicly indexed.

extract from results:

- full name and title of any RevOps, Sales Ops, or SDR Management person found
- the source where they were mentioned — include the URL
- any context about their ops function (tools, process, team size)
- whether this is a standalone role or combined with another function

**stop if:** you found a RevOps or Sales Ops lead. skip to output. if nothing found, note it in coverage — this is expected for T3-T4 companies.

## do not search

- `site:zoominfo.com {{company_name}} "Sales Operations"` — scored KILL (Q2.2) for sales ops queries on this platform
- `{{company_name}} "Head of" -jobs -careers` — without function keywords, returns too many irrelevant results
- `{{company_name}} SDR manager OR BDR manager` — too specific, rarely indexed for smaller companies

## output

```
## department heads: {{company_name}}

**heads of department:**
- [name] — [title, e.g. Head of Growth]
- [name] — [title, e.g. Head of Customer Success]
- [name] — [title, e.g. Head of Partnerships]

**sales operations / RevOps:**
- [name] — [title, e.g. Director of Revenue Operations]
- or: "no dedicated RevOps/Sales Ops role identified — common for T3-T4 companies"

**coverage notes:** [note which functions returned results and which were empty. sales ops is ENRICHMENT tier and expected to be sparse for smaller companies.]

**sources:**
- [source name](url) — what was found there
- [source name](url) — what was found there
```
