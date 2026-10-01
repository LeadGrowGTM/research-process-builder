# find specialist roles: HR/people, finance/legal, and technical leads

model: gpt-6-luna
service_tier: flex

find specialist leadership across HR/people/talent, finance/legal/ops, and senior technical individual contributors (staff engineers, architects).

## inputs

- `{{company_name}}` — the company to research
- `{{domain}}` — their website domain (e.g. clay.com)
- `{{category}}` — what they do in 2-3 words. required if the company name is a common word or 6 characters or fewer.

## steps

### step 1: HR, people, and talent leadership (linkedin-first)

search: `site:linkedin.com/in {{company_name}} "People" OR "Talent" OR "HR" VP OR director OR head`

LinkedIn is the strongest source for HR/people roles. tested at Q4.0 across 25 companies (PRIMARY all tiers). HR leaders are well-indexed on LinkedIn because recruiting is a core LinkedIn use case.

extract from results:

- full name and exact title of every HR/people/talent leader found
- LinkedIn profile URL
- their specific function (recruiting, people ops, HR business partner, talent acquisition, DEIB)
- seniority level (VP, Director, Head of, Manager)

**stop if:** you found a VP People or Head of HR with name confirmed. continue to step 2 for broader HR search if needed.

### step 2: HR and talent search (broad, runner)

search: `{{company_name}} "Head of Recruiting" OR "Talent Acquisition" OR "VP HR" -careers`

catches HR leaders mentioned outside LinkedIn (press, company blogs, conference appearances).

extract from results:

- any HR/people names not found in step 1
- source URLs and context about the role
- any org structure details (team size, reporting structure)

### step 3: finance, legal, and operations leadership

search: `{{company_name}} site:zoominfo.com OR site:rocketreach.co finance OR legal OR operations`

this is the hardest category. finance/legal/ops roles at T3-T4 companies are rarely publicly indexed. tested at Q3.3 across 25 companies (FALLBACK). T1 hits Q4.0 but T4 drops to Q2.8. multiple patterns were tested and all scored FALLBACK. accept this as a known gap.

extract from results:

- full name and title of any finance, legal, or operations leader found
- the platform that surfaced them (ZoomInfo or RocketReach) — include the URL
- their specific function (CFO, General Counsel, VP Finance, Controller, VP Legal)
- any company size or revenue data visible alongside the person

**stop if:** you found a finance or legal leader. if nothing found after this step, note it in coverage — this gap is expected, especially for T3-T4 companies.

### step 4: technical leads and senior ICs

search: `{{company_name}} "Engineering Manager" OR "Staff Engineer" OR "Architect" -jobs -careers`

targets senior technical individual contributors and engineering managers. tested at Q3.9 across 25 companies (PRIMARY).

extract from results:

- full name and title of every senior technical person found
- the source where they were mentioned — include the URL
- their technical domain (backend, frontend, infrastructure, ML/AI, data, security)
- any notable projects, open source contributions, or conference talks mentioned

### step 5: technical leads linkedin (runner)

search: `site:linkedin.com/in {{company_name}} "Staff Engineer" OR "Principal Engineer" OR "Tech Lead"`

LinkedIn search for senior ICs. useful when step 4 returned results from conference sites or blog posts but not LinkedIn profiles.

extract from results:

- LinkedIn profile URLs for any technical leads not found in step 4
- current title and technical domain as listed on LinkedIn
- location and background visible in snippets

## do not search

- `{{company_name}} "senior developer"` — too generic, floods with job postings rather than named individuals
- `{{company_name}} HR email` — returns gated contact databases
- `{{company_name}} legal counsel contact` — returns law firm SEO pages, not in-house counsel
- `{{company_name}} accountant OR bookkeeper` — wrong seniority level for leadership research

## output

```
## specialist roles: {{company_name}}

**HR / people / talent:**
- [name] — [title, e.g. VP of People]
- [name] — [title, e.g. Head of Talent Acquisition]

**finance / legal / operations:**
- [name] — [title, e.g. VP of Finance]
- or: "no finance/legal leadership identified — this is the hardest category to surface publicly (FALLBACK Q3.3). expected gap for T3-T4 companies."

**technical leads / senior ICs:**
- [name] — [title, e.g. Staff Engineer] — [domain, e.g. infrastructure]
- [name] — [title, e.g. Principal Architect] — [domain, e.g. ML/AI]

**coverage notes:** [note tier-specific gaps. HR is strong across all tiers (PRIMARY Q4.0). finance/legal is consistently the hardest to find (FALLBACK). technical leads are strong (PRIMARY Q3.9).]

**sources:**
- [source name](url) — what was found there
- [source name](url) — what was found there
```
