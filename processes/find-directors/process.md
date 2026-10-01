# find director-level leaders across sales, marketing, and engineering

model: gpt-6-luna
service_tier: flex

find Director-level and senior manager-level people across sales/BD, marketing/content, and engineering/product functions.

## inputs

- `{{company_name}}` — the company to research
- `{{domain}}` — their website domain (e.g. clay.com)
- `{{category}}` — what they do in 2-3 words. required if the company name is a common word or 6 characters or fewer.

## steps

### step 1: director of sales and business development

search: `{{company_name}} "sales" director OR manager OR lead -jobs -careers`

broad combo pattern that catches Director, Senior Manager, and Lead-level sales roles. tested at Q4.0 across 25 companies (PRIMARY). all three director categories hit PRIMARY via these broader keyword approaches.

extract from results:

- full name and exact title of every director/manager/lead-level sales person found
- the source where they were mentioned — include the URL
- any context about their sales function (enterprise, SMB, BD, partnerships, account management)
- team or region they lead if mentioned

**stop if:** you found a Director of Sales or equivalent with name confirmed by 2+ sources. continue to step 2 for marketing.

### step 2: director of marketing and content

search: `{{company_name}} "marketing" director OR manager OR lead -jobs -careers`

same broad combo approach for marketing. tested at Q4.0 across 25 companies (PRIMARY).

extract from results:

- full name and exact title of every director/manager/lead-level marketing person found
- the source where they were mentioned — include the URL
- any context about their marketing focus (demand gen, content, product marketing, brand, growth)
- date of mention if visible

### step 3: director of engineering and product

search: `{{company_name}} "engineering" director OR manager OR lead -jobs -careers`

same broad combo approach for engineering/product. tested at Q4.0 across 25 companies (PRIMARY).

extract from results:

- full name and exact title of every director/manager/lead-level engineering person found
- the source where they were mentioned — include the URL
- any context about their technical domain (platform, infra, frontend, data, ML, product)
- previous companies or notable projects mentioned

### step 4: specific title search (runner, use if steps 1-3 returned generic results)

search: `{{company_name}} "Director of Sales" OR "Director of Business Development" -jobs -careers`

more specific query when the broad combo in step 1 returned too many generic results. repeat pattern for marketing and engineering:
- `{{company_name}} "Director of Marketing" OR "Director of Content" -jobs -careers`
- `{{company_name}} "Director of Engineering" OR "Director of Product" -jobs -careers`

extract from results:

- any names not found in steps 1-3
- more precise title matches
- source URLs

### step 5: linkedin fallback (use only if steps 1-4 returned thin results for a specific function)

search: `site:linkedin.com/in {{company_name}} "Director" [function]`

replace `[function]` with the specific area that returned thin results (e.g. "Sales", "Marketing", "Engineering"). scored as a runner, not primary.

extract from results:

- LinkedIn profile URLs for any director-level people not found above
- current title as listed on LinkedIn
- location and background visible in snippets

## do not search

- `site:zoominfo.com {{company_name}} Director Sales` — platform combo queries scored FALLBACK, worse than plain search
- `{{company_name}} "senior manager" OR "team lead"` — too generic, floods with job postings
- `{{company_name}} director -jobs -careers -salary -glassdoor -indeed -linkedin` — too many exclusions kill useful results

## output

```
## director-level leadership: {{company_name}}

**sales / business development:**
- [name] — [title, e.g. Director of Sales]
- [name] — [title, e.g. Senior Sales Manager]

**marketing / content:**
- [name] — [title, e.g. Director of Marketing]
- [name] — [title, e.g. Content Marketing Manager]

**engineering / product:**
- [name] — [title, e.g. Director of Engineering]
- [name] — [title, e.g. Engineering Manager]

**coverage notes:** [note which functions had gaps, e.g. "no marketing director identified — may report directly to CMO"]

**sources:**
- [source name](url) — what was found there
- [source name](url) — what was found there
```
