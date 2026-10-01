# find customer and employee reviews

model: gpt-6-luna
service_tier: flex

> **validated:** 25 companies across 4 tiers (3,357 searches). PRIMARY at Q4.0 all tiers. `{{company_name}} review` is the strongest single pattern. site: combos (G2/Trustpilot/Capterra) scored ENRICHMENT Q3.7.

surface customer and employee reviews for a company. each review is its own item, tagged positive or negative.

## inputs

- `{{company_name}}` — the company to research
- `{{domain}}` — their website domain
- `{{category}}` — what they do in 2-3 words. required if name is ambiguous.
- `{{company_type}}` — one of: `b2b saas`, `b2c consumer`, `enterprise b2b`, `dev tool`, `agency/services`. determines which review platforms to check.

## steps

### step 1: general review sweep

search: `{{company_name}} {{category}} review`

25-company tier test: `{{company_name}} review` (best_review) scored PRIMARY Q4.0 across all tiers. combo site: queries across G2/Trustpilot/Capterra (combo_g2_trustpilot) scored ENRICHMENT Q3.7.

extract from results:

- every distinct review or review summary found
- for each: the source (include URL), whether it's positive or negative, and a three sentence summary of what the reviewer said
- overall sentiment signal (mostly positive, mixed, mostly negative)

**stop if:** you found 5+ distinct reviews with clear sentiment. skip to output.

### step 2: complaints and pain points

search: `{{company_name}} complaints`

extract from results:

- specific complaints (not just "it's bad" but what exactly is bad)
- recurring frustration patterns
- for each complaint: source (include URL), and a three sentence summary

### step 3: employee health signal

search: `{{company_name}} glassdoor reviews`

extract from results:

- overall glassdoor rating (X/5)
- CEO approval percentage if visible
- top recurring pro theme (three sentence summary)
- top recurring con theme (three sentence summary)
- tag as positive (4+/5) or negative (below 3.5/5) or mixed (3.5-4)

**stop if:** you have customer reviews from step 1-2 AND employee reviews from step 3. skip to output if you have enough signal.

### step 4: platform-specific reviews

**if `{{company_type}}` is `b2b saas`:**
search: `{{company_name}} reviews site:g2.com`

**if `{{company_type}}` is `b2c consumer`:**
search: `{{company_name}} reviews site:trustpilot.com`

**if `{{company_type}}` is `dev tool`:**
search: `{{company_name}} reviews site:producthunt.com`

**if `{{company_type}}` is `enterprise b2b`:**
search: `{{company_name}} reviews site:gartner.com peer insights`

extract from results:

- platform rating and review count
- top positive review (three sentence summary, tag: positive)
- top negative review (three sentence summary, tag: negative)

### step 5: editorial deep-dive

search: `{{company_name}} honest review`

extract from results:

- balanced editorial assessments
- specific use case recommendations ("good for X, bad for Y")
- three sentence summary, tag positive or negative based on overall recommendation

### step 6: community sentiment (skip if you already have 6+ reviews)

search: `{{company_name}} reddit discussion`

do NOT use `site:reddit.com` — it returns zero results universally.

extract from results:

- community opinions from reddit-synthesis articles
- switching stories ("i left X for Y because...")
- three sentence summary per distinct opinion, tagged positive or negative

## do not search

- `{{company_name}} review site:reddit.com` — broken, zero results
- `{{company_name}} experience site:reddit.com` — broken, same
- `{{company_name}} NPS score` — companies don't publish this
- `{{company_name}} reviews site:sourceforge.net` — open source only

## output

```
## customer and employee reviews for {{company_name}}

**overall sentiment:** [mostly positive / mixed / mostly negative]

**customer reviews:**

1. [positive] — [source name](url) — [three sentence summary of what the reviewer said, what they liked or didn't, and their conclusion]

2. [negative] — [source name](url) — [three sentence summary]

3. [positive] — [source name](url) — [three sentence summary]

(continue for all distinct reviews found)

**employee reviews:**

1. [positive/negative/mixed] — [source name](url) — rating: [X/5], CEO approval: [X%]. [three sentence summary of recurring pro and con themes]

**key takeaway:** [two sentences. what's the dominant signal? any gap between customer and employee sentiment?]

**sources:**
- [source name](url) — what was found there
- [source name](url) — what was found there
```
