# find Product Hunt daily launches — monitoring sweep

model: gpt-6-luna
service_tier: flex

> **validated:** pending — GT built 2026-05-04, awaiting first anneal run.
> **type:** monitoring process (date-in → product list out). date-parameterized URL, no `tbs` time filter needed.
> **target deployment:** TriggerDev cron (daily, after PH leaderboard closes ~midnight PT)
> **cost:** < $0.05/day. Stage 1 is a single HTTP GET (free). Stage 2 is N product-page fetches. Stage 3 is N homepage scrapes + optional SerperDev domain lookups.

surface all products from Product Hunt's daily leaderboard for a given date. classify each as `new_product` or `new_feature` based on launch count. flag AI relevance. enrich with company LinkedIn URL.

## inputs

- `{{date}}` — target date in YYYY-MM-DD format (e.g. `2026-05-04`)
- `{{openai_api_key}}` — OpenAI API key (classification + extraction)
- `{{serper_api_key}}` — SerperDev API key (domain lookup fallback only)

no Spider Cloud needed in standard operation — PH pages are public and unblocked by WebFetch. add spider key if PH begins returning 403s.

## pipeline architecture

three sequential stages. stage 1 produces the product list; stage 2 classifies each product in parallel; stage 3 enriches in parallel.

```
┌──────────────────────────────────────────────────────────────────┐
│  STAGE 1: FETCH LEADERBOARD (1 HTTP GET)                         │
│                                                                  │
│  URL: producthunt.com/leaderboard/daily/YYYY/M/D                 │
│  tool: WebFetch                                                  │
│  extract: rank, name, tagline, score, PH URL, categories,        │
│           maker website                                          │
│  output: list of ~20-30 raw product records                      │
│  cost: $0 (single public page fetch)                             │
├──────────────────────────────────────────────────────────────────┤
│  STAGE 2: CLASSIFY (parallel, 1 fetch per product)               │
│                                                                  │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐             │
│  │  product 1   │ │  product 2   │ │  product N   │             │
│  │  fetch PH    │ │  fetch PH    │ │  fetch PH    │             │
│  │  product page│ │  product page│ │  product page│             │
│  └──────┬───────┘ └──────┬───────┘ └──────┬───────┘             │
│         └────────────────┴────────────────┘                      │
│  model: gpt-4o-mini                                              │
│  classify: launch_type (new_product | new_feature)               │
│            is_ai (true | false)                                  │
│  cost: ~$0.002-0.005 per run (N WebFetch calls, cheap model)     │
├──────────────────────────────────────────────────────────────────┤
│  STAGE 3: ENRICH (parallel, 1 fetch per product)                 │
│                                                                  │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐             │
│  │  product 1   │ │  product 2   │ │  product N   │             │
│  │  fetch maker │ │  fetch maker │ │  fetch maker │             │
│  │  homepage    │ │  homepage    │ │  homepage    │             │
│  └──────┬───────┘ └──────┬───────┘ └──────┬───────┘             │
│         └────────────────┴────────────────┘                      │
│  model: gpt-4o-mini                                              │
│  extract: company_domain (confirmed), linkedin_url               │
│  fallback: SerperDev domain lookup if homepage is ambiguous      │
│  cost: ~$0.01-0.03 per run                                       │
└──────────────────────────────────────────────────────────────────┘
```

---

## stage 1: fetch leaderboard

### objective

fetch the Product Hunt daily leaderboard for `{{date}}` and extract all ranked products. one HTTP GET, no search API needed.

### URL construction

```
date: 2026-05-04
→ year: 2026, month: 5 (no leading zero), day: 4 (no leading zero)
→ URL: https://www.producthunt.com/leaderboard/daily/2026/5/4
```

**important:** month and day use NO leading zeros. `2026/05/04` returns a 404. `2026/5/4` is correct.

### fetch call

```json
{
  "tool": "WebFetch",
  "url": "https://www.producthunt.com/leaderboard/daily/{{year}}/{{month_no_pad}}/{{day_no_pad}}",
  "description": "PH daily leaderboard for {{date}}"
}
```

### extraction prompt (gpt-4o-mini)

system:
```
You are extracting structured product data from a Product Hunt leaderboard page.
Extract every ranked product from the page. Return JSON only — no commentary.
```

user:
```
Page content:
{{leaderboard_page_content}}

Extract all ranked products. For each, return:
{
  "rank": <integer>,
  "product_name": "<string>",
  "tagline": "<string>",
  "score": <integer — upvote count>,
  "ph_url": "<full URL to the product's PH page, e.g. https://www.producthunt.com/products/mindra>",
  "categories": ["<category>", ...],
  "maker_website": "<URL from the product's 'Visit' link, or null if not shown>"
}

Return as JSON array: { "products": [...] }
If the leaderboard has not posted yet (future date), return { "products": [], "error": "leaderboard_not_posted" }.
```

### stage 1 output

```json
{
  "date": "2026-05-04",
  "leaderboard_url": "https://www.producthunt.com/leaderboard/daily/2026/5/4",
  "products": [
    {
      "rank": 1,
      "product_name": "Mindra",
      "tagline": "Agent Teams You Can Actually Delegate To",
      "score": 343,
      "ph_url": "https://www.producthunt.com/products/mindra",
      "categories": ["AI Workflow Automation", "AI Agents", "No-Code AI Agent Builder"],
      "maker_website": "https://mindra.co"
    }
  ]
}
```

---

## stage 2: classify

### objective

for each product, fetch its PH product page to count total launches. if this is the first launch → `new_product`. if second or more → `new_feature`. also determine `is_ai` from categories and tagline.

### why the product page, not the leaderboard

the leaderboard page shows only today's launch. the product page (`/products/[slug]`) shows ALL historical launches with dates. this is the only reliable source for launch count.

### fetch call (per product, run in parallel)

```json
{
  "tool": "WebFetch",
  "url": "{{ph_url from stage 1}}",
  "description": "PH product page for {{product_name}}"
}
```

note: `ph_url` from the leaderboard may be the launch URL (`/posts/[slug]`). if so, navigate to the product page: replace `/posts/` with `/products/` and strip any trailing date slug. when uncertain, try both — the product page has a "Launches" section that the post page does not.

### classification prompt (gpt-4o-mini, per product)

system:
```
You are classifying a Product Hunt launch. Determine launch_type and is_ai.
Return JSON only — no commentary.
```

user:
```
Product: {{product_name}}
Tagline: {{tagline}}
Categories: {{categories}}

Product page content:
{{ph_product_page_content}}

Classify this launch:

1. launch_type:
   - "new_product" — this is the FIRST time this company/team has launched on PH
   - "new_feature" — this company/team has launched on PH before (look for "Launches" section with prior entries)
   
   Rule: count the entries in the Launches section. If exactly 1 launch (today's) → new_product. If 2+ → new_feature.

2. is_ai — true if ANY of these apply:
   - categories include "Artificial Intelligence", "AI Agents", "AI Workflow Automation", or any "AI ..." category
   - tagline contains words like: AI, agent, LLM, GPT, Claude, automated, intelligent, generative
   - product description mentions using AI/LLM as a core feature
   
   false if the product uses no AI (e.g. price tracker, comic store, analytics dashboard with no AI angle).

3. classification_reasoning — 1-2 sentences explaining launch_type decision. Include launch count if visible.
   Example: "Second PH launch. First launched Jan 30 2026. This is v2 with browser agent and voice coach."

Return:
{
  "product_name": "{{product_name}}",
  "launch_type": "new_product|new_feature",
  "is_ai": true|false,
  "classification_reasoning": "...",
  "launch_count": <integer or null if not visible>
}
```

### is_ai classification rules (reference)

| signal | weight | examples |
|--------|--------|---------|
| category contains "AI" prefix | strong | "AI Agents", "AI Headshot Generators" |
| tagline keyword | strong | "agent", "LLM", "generative", "Claude", "GPT" |
| description mentions AI as core mechanism | strong | "trained on", "powered by GPT" |
| category is adjacent but not AI | weak — use judgment | "Analytics", "Developer Tools" |
| no AI signal anywhere | → `false` | price tracker, comic store, privacy analytics |

**do not mark `is_ai: true`** just because the product is in a tech category. require an explicit signal.

### stage 2 output

append to each product record:

```json
{
  "rank": 1,
  "product_name": "Mindra",
  "tagline": "Agent Teams You Can Actually Delegate To",
  "score": 343,
  "ph_url": "https://www.producthunt.com/products/mindra",
  "categories": ["AI Workflow Automation", "AI Agents", "No-Code AI Agent Builder"],
  "maker_website": "https://mindra.co",
  "launch_type": "new_product",
  "is_ai": true,
  "classification_reasoning": "First PH launch. Founder post confirms initial public reveal.",
  "launch_count": 1
}
```

---

## stage 3: enrich

### objective

for each product, confirm `company_domain` and find `linkedin_url`. use the `maker_website` from stage 1 as the starting point — scrape the homepage to find the LinkedIn link. if `maker_website` is null or returns a redirect to a third-party page, use SerperDev to find the real homepage.

### fetch call (per product, run in parallel)

```json
{
  "tool": "WebFetch",
  "url": "{{maker_website}}",
  "description": "Homepage for {{company_domain}}"
}
```

if `maker_website` is null, skip to the domain lookup step below.

### enrichment prompt (gpt-4o-mini, per product)

system:
```
You are extracting company identity data from a company homepage.
Return JSON only — no commentary.
```

user:
```
Company: {{product_name}}
Expected domain: {{maker_website}}

Homepage content:
{{homepage_content}}

Extract:
1. company_domain — the bare domain of this company (e.g. "mindra.co", not "https://www.mindra.co")
2. linkedin_url — the LinkedIn company page URL found in the footer or header. 
   Must be in format: https://linkedin.com/company/[slug] or https://www.linkedin.com/company/[slug]
   Return null if not found.

Return:
{
  "company_domain": "...",
  "linkedin_url": "..." or null
}
```

### domain lookup fallback

if `maker_website` is null, or if the homepage fetch fails (404, redirect to App Store, redirect to PH), run a SerperDev search to find the real homepage:

```json
{
  "method": "POST",
  "url": "https://google.serper.dev/search",
  "headers": {
    "X-API-KEY": "{{serper_api_key}}",
    "Content-Type": "application/json"
  },
  "body": {
    "q": "{{product_name}} official website",
    "num": 5
  }
}
```

take result #1 or #2 — the company's own site is almost always the top result. if top results are all third-party profiles (LinkedIn, Crunchbase, PH itself), flag `domain_uncertain: true` and use the most likely candidate.

### linkedin lookup fallback

if homepage fetch succeeds but no LinkedIn link is found in the content, try a targeted search:

```json
{
  "body": {
    "q": "{{product_name}} site:linkedin.com/company",
    "num": 3
  }
}
```

take the first `linkedin.com/company/[slug]` URL. if none found, set `linkedin_url: null`.

### stage 3 output (final record)

```json
{
  "rank": 1,
  "product_name": "Mindra",
  "company_domain": "mindra.co",
  "ph_url": "https://www.producthunt.com/products/mindra",
  "score": 343,
  "tagline": "Agent Teams You Can Actually Delegate To",
  "launch_type": "new_product",
  "is_ai": true,
  "classification_reasoning": "First PH launch. Founder post confirms initial public reveal.",
  "categories": ["AI Workflow Automation", "AI Agents", "No-Code AI Agent Builder"],
  "linkedin_url": "https://linkedin.com/company/mindra"
}
```

---

## output schema

```json
{
  "date": "2026-05-04",
  "leaderboard_url": "https://www.producthunt.com/leaderboard/daily/2026/5/4",
  "products": [
    {
      "rank": 1,
      "product_name": "Mindra",
      "company_domain": "mindra.co",
      "ph_url": "https://www.producthunt.com/products/mindra",
      "score": 343,
      "tagline": "Agent Teams You Can Actually Delegate To",
      "launch_type": "new_product",
      "is_ai": true,
      "classification_reasoning": "First PH launch. Founder post confirms initial public reveal.",
      "categories": ["AI Workflow Automation", "AI Agents", "No-Code AI Agent Builder"],
      "linkedin_url": "https://linkedin.com/company/mindra"
    }
  ],
  "summary": {
    "total": 10,
    "new_products": 4,
    "new_features": 6,
    "ai_related": 7,
    "non_ai": 3
  },
  "metadata": {
    "leaderboard_fetch": "success",
    "products_classified": 10,
    "linkedin_found": 8,
    "domain_uncertain_count": 0,
    "stage1_cost_usd": 0,
    "stage2_cost_usd": 0.003,
    "stage3_cost_usd": 0.012,
    "total_cost_usd": 0.015
  }
}
```

---

## kill list

**do not process these — they pollute output:**

- PH collections, roundups, or "Golden Kitty" awards pages (not product launches)
- PH's own internal product launches (e.g. "Product Hunt for Teams") — include only if requested
- products with score < 10 — not worth enriching, negligible signal
- products where `maker_website` resolves to an App Store / Play Store listing with no company site — flag `domain_uncertain: true`, skip LinkedIn enrichment

**stop conditions:**

- stage 1 returns `"error": "leaderboard_not_posted"` → halt, log "PH leaderboard not yet posted for {{date}}", retry after 2 hours
- stage 1 returns empty product list with no error → halt, log "unexpected empty leaderboard", flag for manual review
- PH returns 429 rate limit during stage 2/3 parallel fetches → back off 5 seconds, retry once per product before failing

---

## known failure modes

| failure | cause | mitigation |
|---------|-------|------------|
| URL 404 on valid date | leading zeros in month/day | strip leading zeros: `5/4` not `05/04` |
| `ph_url` points to `/posts/` not `/products/` | leaderboard sometimes links to launch post | swap `/posts/` for `/products/`, strip date slug |
| launch count not visible on product page | page JS-rendered, WebFetch gets skeleton | check for "Launches" text; if missing, classify as `unknown` and flag |
| `maker_website` is null | product didn't include a website link | fall back to SerperDev domain lookup |
| `maker_website` redirects to App Store | mobile apps without web presence | flag `domain_uncertain: true`, skip LinkedIn enrichment |
| company domain from leaderboard is a subdomain | e.g. `app.rudel.ai` vs `rudel.ai` | keep as-is — the subdomain is the actual product URL per GT |
| LinkedIn not in homepage | many startups don't show LinkedIn in footer | run `site:linkedin.com/company` fallback search |
| same company with multiple products ranked | e.g. OpenAI with Codex Pets | keep separate records per product — same `company_domain`, different `product_name` |
| leaderboard posts after midnight PT | PH closes voting ~midnight PT | schedule pipeline run for 1am PT, not midnight |
| `is_ai` over-classified | "Developer Tools" category is not AI | require explicit AI signal — category prefix "AI " or tagline keyword |

---

## ground truth — 2026-05-04

10 products, manually verified from `producthunt.com/leaderboard/daily/2026/5/4`.

| rank | product | company_domain | score | launch_type | is_ai |
|------|---------|---------------|------:|-------------|-------|
| 1 | Mindra | mindra.co | 343 | new_product | true |
| 2 | Codex Pets | openai.com | 178 | new_feature | true |
| 3 | Aaavatar | aaavatar.nl | 171 | new_product | true |
| 4 | Flowly | useflowlyapp.com | 151 | new_feature | true |
| 5 | Rudel | app.rudel.ai | 172 | new_feature | true |
| 6 | Dropy | dropy.app | 158 | new_product | false |
| 7 | Croct | croct.com | 123 | new_feature | true |
| 8 | Regulus by Cumbuca | cumbuca.com | 105 | new_feature | true |
| 9 | Panels Store | panels.store | 118 | new_product | false |
| 10 | Sleek Analytics | getsleek.io | 101 | new_feature | false |

**notes:**
- Rudel's `company_domain` is `app.rudel.ai` (subdomain) — this is the correct product URL, keep as-is
- Codex Pets `company_domain` is `openai.com` — same domain as OpenAI's other products, distinguish by `product_name`
- Regulus is a product by Cumbuca — `company_domain` is the parent company (`cumbuca.com`), not a separate domain
- summary: 4 new_products, 6 new_features, 7 ai_related, 3 non_ai

---

## scoring targets (pending first anneal run)

| metric | target | current |
|--------|--------|---------|
| product name accuracy | 100% | pending |
| launch_type accuracy | ≥ 90% | pending |
| is_ai accuracy | ≥ 90% | pending |
| company_domain accuracy | ≥ 90% | pending |
| linkedin_url found rate | ≥ 70% | pending |
| cost per run | < $0.05 | pending |

re-anneal trigger: any metric drops below target on a 5-day rolling sample. add new failure cases to ground truth first.

---

## iteration targets

- [ ] run stage 1 against GT date (2026-05-04) and verify product extraction accuracy
- [ ] run stage 2 classification against GT — measure `launch_type` accuracy
- [ ] run stage 2 `is_ai` against GT — measure accuracy, tune keyword list
- [ ] run stage 3 enrichment — measure LinkedIn found rate
- [ ] measure actual cost per run against $0.05 target
- [ ] build TriggerDev task definition (see `graduate-to-trigger` skill)
- [ ] test PH 429 behavior under parallel stage 2/3 fetches — tune concurrency limit
- [ ] add Supabase write step (table: `ph_launches`)
- [ ] add Slack notification for `new_product` launches with `is_ai: true` (high-signal subset)
