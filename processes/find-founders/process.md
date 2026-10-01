# find founders, CEO, and president

model: gpt-6-luna
service_tier: flex

find the founders and top executive (CEO/president) of a company using media mentions, founding attributions, and LinkedIn profiles.

## inputs

- `{{company_name}}` — the company to research
- `{{domain}}` — their website domain (e.g. clay.com)
- `{{category}}` — what they do in 2-3 words. required if the company name is a common word or 6 characters or fewer.

## steps

### step 1: media mentions and interviews

search: `{{company_name}} CEO OR founder interview OR podcast`

hits media mentions, interviews, and podcasts that name founders. tested at Q4.0 across 25 companies (PRIMARY). strong across all tiers (T1-T2: PRIMARY, T3-T4: PRIMARY).

extract from results:

- full name of every person identified as founder, co-founder, CEO, or president
- their exact title (CEO, co-founder, co-founder & CEO, president, etc.)
- the source where they were mentioned (podcast name, publication, blog) — include the URL
- any context about their background (previous companies, expertise, education) if mentioned
- date of the mention if visible (helps identify current vs former leadership)

**stop if:** you found the CEO/president and at least one founder with names and titles confirmed by 2+ sources. skip to output.

### step 2: founding attribution search

search: `{{company_name}} "founded by" OR "co-founded" OR "started by"`

catches founding stories, about pages, and press that attribute the company's creation to specific people.

extract from results:

- founder names and the year they founded the company
- any co-founders not found in step 1
- the context of the founding (why they started it, what problem they saw)
- source URLs

**stop if:** combined with step 1, you have all founders and the current CEO identified. skip to output.

### step 3: linkedin profile search (fallback)

search: `site:linkedin.com/in {{company_name}} founder OR CEO OR "co-founder" -jobs -careers`

direct LinkedIn profile search. use only if steps 1-2 returned thin results. works but returns less context than media mentions.

extract from results:

- LinkedIn profile URLs for founders and CEO
- current title as listed on LinkedIn
- any additional co-founders not found in steps 1-2
- location and background info visible in snippets

## do not search

- `{{company_name}} CEO email` — returns gated contact databases, not useful profile data
- `site:apollo.io {{company_name}} CEO` — returns SEO pages, not actual contact data

## output

```
## founders and CEO: {{company_name}}

**CEO / president:** [full name] — [title]
**founded:** [year]

**founders:**
- [name] — [title] (e.g. co-founder & CTO)
- [name] — [title]

**founding context:** [one sentence on why/how the company was started, if found]

**background highlights:**
- [name] — [one sentence on relevant background: previous companies, expertise]

**sources:**
- [source name](url) — what was found there
- [source name](url) — what was found there
```
