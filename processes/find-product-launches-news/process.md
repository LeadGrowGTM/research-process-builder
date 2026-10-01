# find product launches from news — daily monitoring sweep

model: gpt-6-luna
service_tier: flex

> **validated:** pending — GT built 2026-05-04, awaiting first anneal run.
> **type:** monitoring process (date-in → product list out). fundamentally different from lookup processes (company-in → data out).
> **target deployment:** TriggerDev cron (daily 7am ET)
> **cost:** <$0.05/day for discovery queries. Enrichment/scraping additional.

surface all significant product launches and announcements from the last 24 hours across tech press, developer forums, and startup blogs. extract company name, product name, launch type, AI flag, source URL, and description. NOT Product Hunt — this process catches what PH misses.

## inputs

- `{{date}}` — today's date in YYYY-MM-DD format
- `{{serper_api_key}}` — SerperDev API key
- `{{openai_api_key}}` — OpenAI API key (for GPT agent)
- `{{spider_api_key}}` — Spider Cloud API key (for scraping 403 sources)
- `{{ph_output}}` — optional JSON array from find-product-launches-ph process (for dedup + on_product_hunt flagging)

## pipeline architecture

three-stage pipeline. stage 1 uses a hybrid approach: direct source page scraping (primary) + SerperDev queries (supplement). query testing showed direct scrapes hit 100% GT recall vs Serper's 25% max.

```
┌──────────────────────────────────────────────────────────────────┐
│  STAGE 1: HYBRID DISCOVERY (direct scrapes + Serper supplement)  │
│                                                                  │
│  ┌───────────────────────────┐ ┌────────────────────────────┐   │
│  │  TIER 1: Direct Scrapes   │ │  TIER 2: Serper Supplement │   │
│  │  (primary — ~85-90% recall)│ │  (fills gaps — +5-10%)    │   │
│  │                           │ │                            │   │
│  │  f1 TC today date page    │ │  q1 site:techcrunch.com    │   │
│  │  f2 TC yesterday page     │ │     launches OR announces  │   │
│  │  f3 HN Show feed          │ │  q2 site:venturebeat.com   │   │
│  │  f4 HN front page by date │ │     launches OR announces  │   │
│  │                           │ │  q3 site:theverge.com      │   │
│  │  cost: $0 (HTTP GET)      │ │     launches OR announces  │   │
│  │                           │ │  q4 press wires (BW, PRN)  │   │
│  └─────────────┬─────────────┘ └──────────────┬─────────────┘   │
│                └──────────┬───────────────────┘                  │
│                           ▼                                      │
│  model: gpt-4o-mini (extraction from scraped pages)             │
│  output: raw list of {company_name, product_name, source_url,   │
│          snippet, query_source}                                  │
│  cost: ~$0.04-0.06/run (4 Serper queries + free direct scrapes) │
├──────────────────────────────────────────────────────────────────┤
│  STAGE 2: CONSOLIDATE, CLASSIFY & FILTER                         │
│  model: gpt-4o-mini                                              │
│  input: raw list from stage 1                                    │
│  output: deduped, launch_type classified, non-launches filtered  │
│  cost: ~$0.005/run                                               │
├──────────────────────────────────────────────────────────────────┤
│  STAGE 3: ENRICH                                                 │
│  model: gpt-4o-mini (domain lookup) + gpt-4o-mini (LinkedIn)    │
│  tools: Spider Cloud (scrape), SerperDev (domain lookup)         │
│  input: classified companies from stage 2                        │
│  output: structured records with all required fields             │
│  cost: ~$0.01-0.03/company                                       │
└──────────────────────────────────────────────────────────────────┘
```

if gpt-4o-mini produces poor extraction in stage 1, graduate to gpt-4.1-mini.

---

## stage 1: discovery sweep

### objective

cast a wide net across tech press, Hacker News, and startup blogs to find every product launch from the last 24 hours. prioritize recall over precision — stage 2 filters. NOT Product Hunt (separate process handles PH).

### search tool

**primary:** SerperDev Web Search endpoint (NOT news — search endpoint retrieves developer community pages and company blogs that the news index excludes):

```json
{
  "method": "POST",
  "url": "https://google.serper.dev/search",
  "headers": {
    "X-API-KEY": "{{serper_api_key}}",
    "Content-Type": "application/json"
  },
  "body": {
    "q": "QUERY_HERE",
    "tbs": "qdr:d",
    "gl": "us",
    "hl": "en",
    "num": 20
  }
}
```

`tbs: "qdr:d"` = last 24 hours. this is the daily gate — no date parsing needed.

**important:** `after:YYYY-MM-DD` operator is BROKEN in SerperDev. do not use. `tbs` is the only reliable time filter.

### why search beats news

1. Hacker News Show HN posts rank in web search but NOT in the news index
2. company blogs (airbyte.com/blog, retroguard.ai) rank in web search, not news
3. news endpoint filters too aggressively — demotes developer forums and small company blogs

### discovery queries (run all in parallel)

**AGENT A: Tech Press**

**query 1 — TechCrunch launches:**
```json
{"q": "site:techcrunch.com launches OR announces OR introduces OR debuts", "tbs": "qdr:d", "num": 20}
```

**query 2 — VentureBeat tech launches:**
```json
{"q": "site:venturebeat.com launches OR announces OR introduces OR debuts", "tbs": "qdr:d", "num": 20}
```

**query 3 — broad tech press sweep:**
```json
{"q": "\"launches\" OR \"announces\" OR \"introduces\" new product OR tool OR platform startup 2026", "tbs": "qdr:d", "num": 20}
```

**AGENT B: Developer / HN / Blogs**

**query 4 — Hacker News Show HN:**
```json
{"q": "site:news.ycombinator.com \"Show HN\"", "tbs": "qdr:d", "num": 30}
```

**query 5 — startup press launches:**
```json
{"q": "startup \"now available\" OR \"product launch\" OR \"launching today\" OR \"went live\"", "tbs": "qdr:d", "num": 20}
```

**query 6 — company blog announcements:**
```json
{"q": "\"we launched\" OR \"we built\" OR \"we're launching\" OR \"introducing\" site:*.com/blog", "tbs": "qdr:d", "num": 20}
```

**AGENT C: AI-Specific**

**query 7 — AI product launches:**
```json
{"q": "AI OR \"artificial intelligence\" \"launches\" OR \"announces\" new product OR tool OR platform", "tbs": "qdr:d", "num": 20}
```

**query 8 — agentic / MCP launches:**
```json
{"q": "agentic OR \"AI agent\" OR MCP launches OR announces OR releases", "tbs": "qdr:d", "num": 20}
```

**query 9 — feature and version launches:**
```json
{"q": "\"new feature\" OR \"major update\" OR \"v2\" OR \"redesign\" software launches OR announces", "tbs": "qdr:d", "num": 20}
```

**supplementary queries (run if stage 1 recall is low):**

**query 10 — The Verge product coverage:**
```json
{"q": "site:theverge.com launches OR announces OR debuts", "tbs": "qdr:d", "num": 10}
```

**query 11 — press wire product announcements:**
```json
{"q": "\"now available\" OR \"product launch\" site:businesswire.com OR site:prnewswire.com", "tbs": "qdr:d", "num": 10}
```

**query 12 — open source launches (GitHub):**
```json
{"q": "site:github.com \"Show HN\" OR \"launching\" OR \"introducing\"", "tbs": "qdr:d", "num": 10}
```

**total base: 9 queries. supplementary: 3 more if needed.** 9 queries × $0.001 = ~$0.009 base cost per daily run.

### stage 1 output

for each result, extract:

```json
{
  "company_name_raw": "string — may be unclear from snippet",
  "product_name_raw": "string — product/feature being launched",
  "source_url": "string — the article or post URL",
  "source_domain": "string — e.g. 'techcrunch.com', 'news.ycombinator.com'",
  "snippet": "string — the search result snippet for context",
  "query_source": "string — which query found this (q1-q12)"
}
```

---

## stage 2: consolidate, classify and filter

### objective

take raw discovery results, deduplicate, classify launch type and AI flag, and filter out non-launch content (reviews, opinion pieces, funding announcements, analysis articles).

### model

gpt-4o-mini (no tools needed, pure text analysis).

### filtering rules

**KEEP only if ALL of these are true:**
1. article/post is about a product launch, feature release, or new version — NOT a review, analysis, opinion, or funding announcement
2. the company is real and identifiable (not a FOMO bait listicle like "10 products launched this week")
3. not a duplicate — same product from different sources counts as one entry

**DISCARD if ANY of these are true:**
- it's a product review (the product existed before, this article tests it)
- it's a "top X products" roundup with no new launch
- it's a funding announcement (Series A/B/C — separate pipeline handles those)
- it's a job posting, partnership, or acquisition news
- snippet clearly indicates: opinion, analysis, industry report, market commentary

### launch type classification

**launch_type rules:**
- `new_product` — product did not exist publicly before this announcement. includes: brand new companies launching their first product, entirely new product lines, open-source repos with initial public release
- `new_feature` — extends an existing product. includes: feature drops, major version updates, redesigns, platform expansions to existing companies

**when in doubt:** if the announcement is from a company that clearly had a prior product and this update extends it → `new_feature`. if it's a new standalone thing → `new_product`.

### AI flag classification

**is_ai rules:**
- `true` if: the product uses ML/AI/LLM/neural networks as a core capability, OR is marketed as "AI-powered", OR involves agents, embeddings, fine-tuning, or inference
- `false` if: it's traditional software, hardware, or infrastructure with no AI component

### deduplication

group results by product name + company name (normalized lowercase). keep the highest-quality source:
1. company's own blog/press release > tech press (TechCrunch, VentureBeat) > aggregator > HN thread

### stage 2 output

```json
{
  "launches": [
    {
      "company_name": "Airbyte",
      "product_name": "Airbyte Agents",
      "launch_type": "new_product",
      "is_ai": true,
      "sources": [
        {"url": "https://airbyte.com/blog/airbyte-agents", "domain": "airbyte.com", "quality": "company_blog"},
        {"url": "https://news.ycombinator.com/item?id=...", "domain": "news.ycombinator.com", "quality": "hn"}
      ],
      "best_source_url": "https://airbyte.com/blog/airbyte-agents",
      "snippet_summary": "Context layer for AI agents. Context Store, Agent MCP, Agent SDK."
    }
  ],
  "filtered_out": [
    {"name": "...", "reason": "product review, not a launch"},
    {"name": "...", "reason": "funding announcement, not a product"}
  ]
}
```

---

## stage 3: enrich

### objective

for each classified launch, resolve the company domain if missing, scrape the best source URL for a clean description, check against PH process output, and find the LinkedIn URL.

### scraping strategy

**try WebFetch first** (free, fast). if 403 or blocked, fall back to **Spider Cloud**.

note from GT: VentureBeat returns 429, The Verge blocks scraping. Spider Cloud required for both.

spider cloud API:

```json
{
  "method": "POST",
  "url": "https://api.spider.cloud/crawl",
  "headers": {
    "Authorization": "Bearer {{spider_api_key}}",
    "Content-Type": "application/json"
  },
  "body": {
    "url": "TARGET_URL",
    "limit": 1,
    "return_format": "markdown"
  }
}
```

### enrichment steps (per launch)

**step 1: resolve company domain (if not already known)**

if domain is a GitHub repo URL (github.com/org/repo) → use that as domain, skip homepage scrape.

if domain is unknown, search:

```json
{
  "url": "https://google.serper.dev/search",
  "body": {"q": "{{company_name}} official website", "num": 5}
}
```

the company's own website is almost always result #1 or #2. extract domain from URL. if only third-party profiles found, flag `domain_uncertain: true`.

**step 2: scrape best source for description**

scrape the best source URL. extract a 1-2 sentence description of what the product does and what's new. do not pad — if the snippet already has enough, use it.

**step 3: check against PH output (if {{ph_output}} provided)**

look up `company_name` + `product_name` in the PH output array. if match found, set `on_product_hunt: true`. otherwise `false`.

**step 4: find LinkedIn URL**

search:

```json
{
  "url": "https://google.serper.dev/search",
  "body": {"q": "{{company_name}} site:linkedin.com/company", "num": 3}
}
```

extract the linkedin.com/company/[slug] URL from results. if not found, set `linkedin_url: null`.

---

## output schema

```json
{
  "date": "2026-05-04",
  "launch_count": 12,
  "launches": [
    {
      "company_name": "Airbyte",
      "company_domain": "airbyte.com",
      "product_name": "Airbyte Agents",
      "launch_type": "new_product",
      "is_ai": true,
      "source_url": "https://airbyte.com/blog/airbyte-agents",
      "source_name": "Hacker News",
      "announcement_date": "2026-05-04",
      "description": "Context layer for AI agents. Context Store, Agent MCP, Agent SDK, Automations builder. 40% fewer tool calls, 90% fewer tokens.",
      "linkedin_url": "https://linkedin.com/company/airbytehq",
      "on_product_hunt": true
    }
  ],
  "metadata": {
    "queries_run": 9,
    "raw_results": 112,
    "after_dedup": 28,
    "after_filter": 12,
    "scrape_success_rate": "10/12",
    "ph_overlap": 1
  }
}
```

---

## source tier reference

validated against GT 2026-05-02 to 2026-05-04 (12 products). anneal run pending.

### tier S: primary discovery sources

| source | type | GT products | coverage |
|--------|------|:-----------:|----------|
| TechCrunch | tech press | 6/12 | broad tech, hardware, enterprise |
| Hacker News Show HN | community posts | 5/12 | developer tools, OSS, early-stage |
| company blogs | first-party | 2/12 | most authoritative, often HN cross-posted |

### tier A: supplementary (add queries when recall is low)

| source | type | coverage |
|--------|------|----------|
| VentureBeat | tech press | AI/enterprise focus. 429 on scrape — use Spider |
| The Verge | consumer tech | hardware, consumer apps. blocks scraping — use Spider |
| press wires (BW, PRN) | press releases | enterprise launches, larger companies |
| GitHub | OSS repos | developer tools with no company blog |

### tier D: avoid

- Product Hunt — separate process (find-product-launches-ph) handles PH. this process deduplicates against PH but does not discover from it.
- App Store / Play Store listing pages — not launch announcements
- listicles ("best AI tools 2026") — no new launch signal
- Crunchbase / Pitchbook — funding data, not product launches

---

## source blocking reference

known scrape failures from GT run:

| source | failure | solution |
|--------|---------|----------|
| VentureBeat | 429 Too Many Requests | Spider Cloud |
| The Verge | 403 Blocked | Spider Cloud |
| Hacker News items | WebFetch succeeds | no Spider needed |
| company blogs | most succeed via WebFetch | Spider for Cloudflare-protected sites |

---

## SerperDev reference

### endpoints

| endpoint | URL | use for |
|----------|-----|---------|
| web search | `https://google.serper.dev/search` | general queries, domain lookup, HN posts |
| news search | `https://google.serper.dev/news` | NOT recommended — misses HN and company blogs |

### time controls (`tbs` parameter)

| value | meaning | use case |
|-------|---------|----------|
| `qdr:h` | past hour | not useful — too narrow for product launches |
| `qdr:d` | past 24 hours | **daily monitoring sweep** |
| `qdr:w` | past week | weekly catch-up / backfill |
| `qdr:m` | past month | not needed for this process |

**do NOT use** `after:YYYY-MM-DD` — broken in SerperDev, universally returns stale results.

### boolean operators (validated)

| operator | works? | example |
|----------|:------:|---------|
| `OR` | ✅ | `launches OR announces OR introduces` |
| `""` exact match | ✅ | `"Show HN"` |
| `-` exclusion | ✅ | `-review -opinion` |
| `site:` | ✅ | `site:techcrunch.com` |
| `site: OR site:` | ✅ | `site:a.com OR site:b.com` |
| `intitle:` | ❌ | too aggressive, strips results |
| `after:` | ❌ | broken in SerperDev |

---

## known failure modes

| failure | cause | mitigation |
|---------|-------|------------|
| review articles pass filter | "best X" or "hands on" without "launch" keyword | add review exclusion keywords to stage 2 filter prompt |
| HN links are comment threads, not company sites | Show HN links point to HN discussion, not product | in stage 3, scrape the URL field in the HN post, not the HN item URL |
| GitHub repos without company site | OSS tools launched by individuals | use github.com/org/repo as domain, skip LinkedIn step |
| two products from same company (e.g. Anthropic + OpenAI same article) | one article announces multiple launches | in stage 2, split into separate records per company |
| description inflated with PR fluff | press releases pad descriptions | extraction prompt: "1-2 sentences, functional description only, no marketing language" |
| VentureBeat/Verge blocked | 429 / 403 on scrape | Spider Cloud fallback (see blocking reference) |
| launch is actually a funding announcement | AI companies often announce product + funding together | stage 2 filter: if primary news is a funding round, discard — funding pipeline handles it |

---

## ground truth — 2026-05-02 to 2026-05-04

| company | product | launch_type | is_ai | source | on_ph |
|---------|---------|-------------|:-----:|--------|:-----:|
| DoorDash | Merchant AI Suite | new_feature | ✅ | TechCrunch | ❌ |
| Ouster | Rev8 | new_product | ❌ | TechCrunch | ❌ |
| Acorn / Blacksky | Acorn | new_product | ❌ | TechCrunch | ❌ |
| Amazon | Supply Chain Services | new_product | ❌ | TechCrunch | ❌ |
| Anthropic | Enterprise AI JV | new_product | ✅ | TechCrunch | ❌ |
| OpenAI | The Development Company | new_product | ✅ | TechCrunch | ❌ |
| Airbyte | Airbyte Agents | new_product | ✅ | Hacker News → company blog | ✅ |
| Retroguard | Retroguard | new_product | ✅ | Hacker News | ❌ |
| Xteink | Xteink X3 | new_product | ❌ | TechCrunch | ❌ |
| Bruin Data | DAC | new_product | ✅ | Hacker News | ❌ |
| Orch8 | Orch8 | new_product | ❌ | Hacker News | ❌ |
| Brainio | Brainio | new_product | ✅ | Hacker News | ❌ |

**GT breakdown:**
- 12 total, 11 new_product, 1 new_feature
- 7 AI-related, 5 non-AI
- TechCrunch: 6, Hacker News: 5, direct company blog (cross-posted via HN): 1
- VentureBeat returned 429, The Verge blocked — both sources excluded from GT run
- 1/12 also on Product Hunt (Airbyte Agents)

---

## iteration targets

- [ ] run first anneal: execute 9 queries against GT set, measure recall and precision
- [ ] test SerperDev News endpoint vs Search endpoint on same GT set — hypothesis: Search wins here too
- [ ] test The Verge via Spider Cloud — high product launch density, worth the cost
- [ ] measure false positive rate from q3/q5/q7 broad queries
- [ ] tune stage 2 filter prompt to catch "launch + funding" conflation
- [ ] add HN new Show HN page direct scrape as supplement (news.ycombinator.com/shownew)
- [ ] add weekly catch-up: `tbs: qdr:w` for missed launches
- [ ] build TriggerDev task definition
- [ ] add Slack notification on new launches (especially is_ai: true)
