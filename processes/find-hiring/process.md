# find hiring activity

model: gpt-6-luna
service_tier: flex

> **validated:** 25 companies across 4 tiers (3,357 searches). PRIMARY at Q4.0. T1:Q4.0, T2:Q4.0, T3:Q3.8, T4:Q4.0. `{{company_name}} careers` is the strongest single pattern.

surface who a company is currently hiring for — roles, departments, seniority levels, and hiring velocity.

## inputs

- `{{company_name}}` — the company to research
- `{{domain}}` — their website domain
- `{{category}}` — what they do in 2-3 words. required if the company name is a common word or 6 characters or fewer.
- `{{current_year}}` — the current year (e.g. 2026). in Clay: `YEAR({Created At})`.

## steps

### step 1: general careers search

search: `{{company_name}} {{category}} careers`

25-company tier test: `{{company_name}} careers` (best_careers) scored PRIMARY Q4.0 across T1, T2, T4, and Q3.8 for T3. adding `{{category}}` (runner_cat_careers) scored ENRICHMENT Q3.6. the simpler query is the stronger pattern.

extract from results:

- careers page URL (if found)
- every open role mentioned with title and department
- which job boards have listings (Indeed, Glassdoor, LinkedIn, Built In, Wellfound, etc.) — include URLs
- total number of open positions (if visible)
- any hiring signals (e.g. "now hiring", headcount mentioned in press)
- three sentence summary of what they're hiring for and at what scale

**stop if:** you found a careers page with 10+ roles listed and can identify the top hiring departments. skip to output.

### step 2: ATS board search

search: `{{company_name}} site:boards.greenhouse.io OR site:jobs.lever.co OR site:jobs.ashbyhq.com OR site:wellfound.com`

this surfaces the actual applicant tracking system where roles are posted with full descriptions. tested Q5/C4 across SpaceX (greenhouse), Clay (ashby), Lovable (ashby), Cluely (ashby), Hoo.be (wellfound).

extract from results:

- ATS platform used (greenhouse, lever, ashby, workday, etc.)
- ATS board URL
- every role title visible in results
- group roles by department (engineering, sales, marketing, operations, etc.)
- if wellfound: employee count, funding stage, and industry tags
- three sentence summary of hiring focus areas

wellfound (formerly angellist) is especially useful for startups and small companies that don't use traditional ATS platforms.

**bonus:** if you need org chart context (who's already on the team), search `site:rocketreach.co {{company_name}}` — shows current employees with titles and departments. useful for understanding team composition alongside open roles.

**stop if:** combined with step 1, you have a clear picture of what departments are hiring and at what seniority level. skip to output.

### step 3: careers page direct check

search: `site:{{domain}}/careers`

if no results, try: `site:{{domain}} careers OR jobs OR "open positions"`

extract from results:

- careers page URL (definitive)
- roles listed directly on their site (may differ from job boards)
- company culture info or hiring philosophy if visible
- three sentence summary

### step 4: year-filtered hiring activity (skip for very small companies)

search: `{{company_name}} hiring {{current_year}}`

extract from results:

- any recent hiring announcements or press mentions of growth
- new roles not found in steps 1-3
- hiring velocity signals (e.g. "hiring 50 engineers", "doubling the team")
- three sentence summary of recent hiring momentum

this pattern returns generic hiring-trend articles for obscure companies. skip if the company has fewer than ~20 employees.

**stop if:** you have enough data to assess hiring priorities and velocity. skip to output.

### step 5: fallback for obscure companies (only if steps 1-2 returned almost nothing)

search: `{{company_name}} "we're hiring" OR "join our team" OR "open positions"`

extract from results:

- any hiring signals from social media posts, blog posts, or community mentions
- the company may not have a formal careers page — social posts and linkedin are the signal
- three sentence summary

if even this returns nothing, that's the finding. "no active hiring detected" is a valid signal — the company may be early stage, bootstrapped, or not growing headcount.

## claygent enhancement: linkedin company jobs page

if your agent can visit URLs directly (Claygent, browser agent), the LinkedIn company jobs page is the cleanest source for open roles:

visit: `linkedin.com/company/[company-slug]/jobs/`

the company slug comes from the LinkedIn company URL (found via find-profiles or step 1). for Clay: `linkedin.com/company/grow-with-clay/jobs/`.

extract from results:

- every open role with title
- department groupings
- location (remote/hybrid/onsite per role)
- total count of open positions

this does NOT work via web search — `site:linkedin.com/jobs` returns generic LinkedIn pages, not company-specific listings. direct visit only.

**for deeper role analysis:** after identifying roles here, use the companion process `find-job-role-insights.md` to extract strategic signals from specific job descriptions (tech stack, pain points, team context, budget signals).

## do not search

- `{{company_name}} jobs site:linkedin.com` — returns location-based noise for ambiguous names (e.g. "Clay" returns jobs in Clay, NY and clay modeler positions). for LinkedIn jobs data, use the Claygent direct-visit approach above.
- `{{company_name}} glassdoor salary` — returns salary estimates, not hiring activity
- `{{company_name}} internships` — too narrow unless specifically asked for
- `{{company_name}} remote jobs` — returns aggregator noise, not company-specific data

## output

```
## hiring activity for {{company_name}}

**hiring status:** [actively hiring / selectively hiring / no active hiring detected]
**open roles found:** [number or estimate]
**careers page:** [url or "not found"]
**ats platform:** [greenhouse / lever / ashby / workday / custom / unknown]

**top hiring departments:**
- [department 1] — [X roles] — [example titles: senior software engineer, staff engineer, etc.]
- [department 2] — [X roles] — [example titles]
- [department 3] — [X roles] — [example titles]

**seniority breakdown:** [mostly senior / mix of levels / mostly junior / unclear]

**hiring signals:**
[two sentences. what does the hiring pattern tell you? are they scaling engineering? building out sales? expanding to new markets?]

**sources:**
- [source name](url) — what was found there
- [source name](url) — what was found there
```
