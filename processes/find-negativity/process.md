# find customer complaints and negative sentiment

model: gpt-6-luna
service_tier: flex

> **validated:** 25 companies across 4 tiers (3,357 searches). FALLBACK at Q3.4. T1:Q4.0, T2:Q3.4, T3:Q3.5, T4:Q2.8. structurally limited: micro companies rarely have indexed complaints. all combo patterns (reddit, negative review) scored KILL. the current approach is the best available.

surface recent complaints, negative reviews, and controversy about a company from their customer base. this tells you what pain points exist, how severe they are, and whether the negativity is recent or historical.

## inputs

- `{{company_name}}` — the company to research
- `{{domain}}` — their website domain
- `{{category}}` — what they do in 2-3 words. required if the company name is a common word or 6 characters or fewer.
- `{{company_type}}` — one of: `b2b saas`, `b2c consumer`, `enterprise b2b`, `dev tool`, `agency/services`. determines which review platforms to check in step 4.
- `{{current_year}}` — the current year (e.g. 2026). in Clay: `YEAR({Created At})`.

## steps

### step 1: broad negativity sweep

search: `{{company_name}} {{category}} complaints OR "negative reviews" OR problems OR issues`

this single query catches customer complaints, negative review roundups, problem reports, and issue discussions in one shot. tested Q4.25/C3.5 across SpaceX, Clay, Lovable, Cursor, Cluely. returns rich results for T1-T2 companies. 25-company tier test: best_complaints scored FALLBACK Q3.4. reddit and negative review combos all scored KILL. this pattern is the ceiling for negativity research.

extract from results:

- every distinct complaint or negative point mentioned
- for each: the source with URL (review site, blog, press, BBB, social), the severity (minor gripe vs deal-breaker), and a three sentence summary of the specific complaint
- tag each as: pricing, product quality, customer support, reliability, security, billing, or other
- note whether the complaint appears recent (last 12 months) or historical
- overall sentiment signal from the results (isolated complaints vs widespread frustration)

**stop if:** you found 5+ distinct complaints across multiple categories. skip to output.

### step 2: structured criticism from balanced reviews

search: `{{company_name}} {{category}} downsides OR limitations OR drawbacks`

this catches a different type of content than step 1. step 1 surfaces emotional complaints. this surfaces analytical, balanced review content where reviewers list pros and cons. tested Q4.5/C4 across all tiers.

extract from results:

- every downside, limitation, or drawback mentioned
- for each: the source, and a three sentence summary
- note any recurring themes across multiple sources (if 3 reviewers all mention pricing, that's signal)
- note any limitations the company itself acknowledges

**stop if:** combined with step 1, you have complaints AND structured criticism covering multiple themes. skip to output if you only need a high-level negativity signal.

### step 3: community sentiment

search: `{{company_name}} {{category}} problems reddit discussion`

do NOT use `site:reddit.com` — it returns zero results universally. the "reddit discussion" keyword surfaces reddit-synthesis articles and community-voiced opinions.

extract from results:

- unfiltered user opinions not found in formal reviews
- switching stories ("i left X for Y because...")
- recurring frustration patterns from actual users
- three sentence summary per distinct complaint

community complaints tend to be more honest and specific than review site content. if users are frustrated, this is where it shows up.

### step 4: platform-specific reviews

**if `{{company_type}}` is `b2b saas` or `enterprise b2b`:**
search: `site:g2.com {{company_name}} reviews`

**if `{{company_type}}` is `b2c consumer`:**
search: `site:trustpilot.com {{company_name}}`

**if `{{company_type}}` is `dev tool`:**
search: `site:producthunt.com {{company_name}} reviews`

**if `{{company_type}}` is `agency/services`:**
search: `{{company_name}} {{category}} glassdoor reviews`

extract from results:

- platform rating and review count
- top negative review (three sentence summary with specific complaints)
- any recurring negative themes in the structured review data
- tag each complaint by category (pricing, product, support, etc.)

**stop if:** combined with steps 1-3, you have a comprehensive picture of customer negativity. skip to output.

### step 5: controversy and PR-level issues (skip for obscure companies)

search: `{{company_name}} controversy OR scandal OR backlash {{current_year}}`

extract from results:

- any PR controversies, scandals, or public backlash
- regulatory actions, lawsuits, or government complaints
- founder/leadership controversies that affect customer trust
- three sentence summary per event

this pattern returns noise for obscure companies. only run if the company is T1-T2 (well-known enough to generate press coverage). for T3 companies, skip to output.

### step 6: honest review fallback (only if steps 1-2 returned almost nothing)

search: `{{company_name}} {{domain}} honest review`

extract from results:

- any balanced assessments that include negatives
- "good for X, bad for Y" style recommendations
- three sentence summary

if even this returns nothing, that's the finding. "no significant customer negativity found" is a valid and valuable signal. it means either the company is too small to generate public complaints, or their customers are genuinely satisfied.

## do not search

- `{{company_name}} "switched from" OR "left" OR "cancelled"` — returns marketing content about people switching TO the tool, not churn signals. tested Q2/C1.
- `{{company_name}} "do not recommend" OR "not worth" OR "waste of money"` — zero results. people don't use these exact phrases in searchable contexts.
- `{{company_name}} controversy` without category qualifier — ambiguous name pollution for common names (Clay the material, Cursor the mouse pointer)
- `{{company_name}} NPS score` — companies don't publish this
- `{{company_name}} churn rate` — internal metric, not publicly available

## output

```
## customer complaints and sentiment for {{company_name}}

**overall negativity level:** [high (widespread, multi-source) / moderate (recurring themes but not universal) / low (isolated complaints) / minimal (no significant negativity found)]

**complaint categories found:**
- [category 1, e.g. pricing] — [X sources] — [one sentence summary of the complaint pattern]
- [category 2, e.g. product quality] — [X sources] — [one sentence summary]
- [category 3, e.g. customer support] — [X sources] — [one sentence summary]

**most severe complaints:**

1. [severity: deal-breaker / significant / minor] — [source] — [three sentence summary of the specific complaint, what triggered it, and how widespread it appears to be]

2. [severity] — [source] — [three sentence summary]

3. [severity] — [source] — [three sentence summary]

(continue for all significant complaints found)

**recency signal:** [complaints are recent (last 12 months) / complaints are historical (12+ months old) / mix of both / insufficient data to determine]

**controversy or PR issues:** [summary if any found, or "none found"]

**what this tells us:**
[three sentences. is the negativity about the product itself, the business practices, or external factors? is the company actively losing customer trust, or are these growing pains? how could this negativity be used in competitive positioning or sales conversations?]

**sources:**
- [source name](url) — what was found there
- [source name](url) — what was found there
```
