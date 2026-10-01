# find job role insights and strategic signals

model: gpt-6-luna
service_tier: flex

> **validated:** 25 companies across 4 tiers (3,357 searches). hiring_signals PRIMARY Q4.0 across all tiers. role-specific JD extraction patterns confirmed reliable for ATS-hosted and company-hosted JDs.

companion to find-hiring. takes a specific role title and extracts what the job description reveals about the company's priorities, tech stack, pain points, and growth trajectory. run find-hiring first to identify which roles exist, then use this process to go deep on the ones that matter.

## inputs

- `{{company_name}}` — the company to research
- `{{domain}}` — their website domain
- `{{category}}` — what they do in 2-3 words. required if name is ambiguous.
- `{{role_title}}` — the specific role to investigate (e.g. "Solutions Engineer", "Head of RevOps", "Staff Software Engineer")
- `{{ats_url}}` — optional. the ATS board URL from find-hiring output (e.g. `jobs.ashbyhq.com/claylabs`, `boards.greenhouse.io/cursor`). if provided, enables the most reliable search pattern.

## steps

### step 1: ATS-specific role search (if `{{ats_url}}` is provided)

search: `site:{{ats_url}} {{role_title}}`

this is the highest-accuracy pattern. when you know the ATS URL from find-hiring, searching within it returns the exact job posting with a direct link. tested Q5/C5 across Clay (ashby), Lovable (ashby), Cursor (custom).

extract from results:

- the direct URL to the job posting
- role title (exact, may differ from your search term)
- any JD content visible in the search snippet (responsibilities, requirements, team description)
- related roles (other postings that appeared alongside it)

if the JD URL is found, visit it directly to get the full description. extract everything in the output section below.

**stop if:** you found the full JD with responsibilities, requirements, and team context. skip to output.

### step 2: company careers page search

search: `{{company_name}} "{{role_title}}" responsibilities requirements site:{{domain}}/careers`

if no results, try: `{{company_name}} "{{role_title}}" site:{{domain}}`

this catches companies that host JDs on their own domain rather than (or in addition to) an ATS. tested Q5/C4 across Cursor (cursor.com/careers/), SpaceX (spacex.com/careers).

extract from results:

- careers page JD URL
- any JD content visible in search snippets
- other roles listed alongside the target role

if the JD URL is found, visit it directly to get the full description.

**stop if:** you found the full JD. skip to output.

### step 3: combined ATS sweep (if `{{ats_url}}` is NOT provided)

search: `{{company_name}} "{{role_title}}" site:jobs.ashbyhq.com OR site:boards.greenhouse.io OR site:jobs.lever.co`

this catches the role across the three most common startup ATS platforms in one query. tested Q5/C4 across Clay, Lovable, Cursor.

extract from results:

- which ATS platform the role is on
- direct JD URL
- any snippet content

if no results, try adding the category: `{{company_name}} {{category}} "{{role_title}}" site:jobs.ashbyhq.com OR site:boards.greenhouse.io OR site:jobs.lever.co`

### step 4: broad job board fallback

search: `{{company_name}} "{{role_title}}" job description`

this catches JDs reposted on aggregator sites (Indeed, ZipRecruiter, Glassdoor, Built In, spacecrew.com for aerospace). less reliable for exact JD content but useful for T1 companies with wide distribution.

extract from results:

- any JD content from aggregator snippets
- salary or compensation data (aggregators often show this when the original posting doesn't)
- location and remote/hybrid/onsite details
- three sentence summary of what was found

### step 5: role context enrichment (optional, skip if you already have a full JD)

search: `{{company_name}} "{{role_title}}" OR "{{similar_title}}" hiring`

use `{{similar_title}}` for role variants (e.g. if searching for "Solutions Engineer", also try "Sales Engineer" or "Technical Account Manager").

extract from results:

- any interview experiences or glassdoor reviews mentioning this role
- blog posts or social posts from the hiring manager about the role
- team context not in the formal JD
- three sentence summary

## claygent enhancement: linkedin direct visit

if your agent can visit URLs directly (Claygent, browser agent), this is the highest-accuracy approach for role discovery and JD extraction:

1. visit `linkedin.com/company/[company-slug]/jobs/` — shows all open positions with titles
2. click into the specific role — full JD with responsibilities, requirements, and team info
3. extract everything in the output section below

the company slug comes from the LinkedIn company URL (found via find-profiles). for Clay: `linkedin.com/company/grow-with-clay/jobs/`.

this does NOT work via web search — `site:linkedin.com/jobs` returns zero relevant results. direct visit only.

## do not search

- `site:linkedin.com/jobs {{company_name}}` — LinkedIn job pages are not indexed by search engines. returns generic LinkedIn job search pages, not company-specific listings. tested Q1/C0.
- `{{company_name}} "{{role_title}}" salary` — returns salary estimate sites (levels.fyi, glassdoor estimates), not the actual JD. only useful if you specifically need compensation data.
- `{{company_name}} "{{role_title}}" interview questions` — returns interview prep content, not JD insights. different process entirely.

## what to extract from a job description

when you find the full JD (from any step above), extract these specific signals:

**role basics:**

- exact title, department, location, remote/hybrid/onsite
- salary range if posted
- seniority level (IC vs manager, junior vs senior vs staff)

**tech stack and tools:**

- every technology, tool, language, or platform mentioned
- which are "required" vs "nice to have"
- what this reveals about their infrastructure

**pain points implied:**

- what problems does this role solve? ("we need someone to build...", "you will fix...", "we're scaling...")
- what's broken or missing that this hire addresses?

**team and org context:**

- who does this role report to?
- what team do they join and how big is it?
- is this a new role or backfill?
- what does the team currently look like?

**strategic signals:**

- is this a new capability being built? ("first hire for this function")
- does this signal market expansion? (new geo, new segment)
- does this signal a strategic pivot? (hiring ML engineers for a non-AI company)
- budget signals: equity, salary band, benefits mentioned

## output

```
## job role insights: {{role_title}} at {{company_name}}

**role:** {{role_title}}
**department:** [department or team name]
**location:** [city / remote / hybrid]
**seniority:** [junior / mid / senior / staff / lead / manager / director / VP]
**salary range:** [range if posted, or "not disclosed"]
**jd source:** [url to the actual job description]

**what they want:**
[three sentences. what is this role actually doing day-to-day? what are the key responsibilities? what does success look like in this role?]

**tech stack and tools mentioned:**
- required: [comma separated list]
- nice to have: [comma separated list]

**pain points this role addresses:**
[two sentences. what problem is this hire solving? what's the gap in the org that created this role?]

**team context:**
[two sentences. who does this person work with? is this a new function or an existing team?]

**strategic signals:**
[three sentences. what does this hire tell you about where the company is headed? is this a growth signal, a pivot signal, or a scaling signal? how could you use this in a sales conversation or competitive analysis?]

**sources:**
- [source name](url) — what was found there
- [source name](url) — what was found there
```
