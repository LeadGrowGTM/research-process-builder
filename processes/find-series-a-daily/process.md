# find Series A announcements — daily monitoring sweep

model: gpt-6-luna
service_tier: flex

> **validated:** 2026-04-20. SerperDev Search + `tbs:qdr:d` = **7/8 (88%) GT hit rate** across 10 queries at $0.01/run. News endpoint 5/8 (62%). WebSearch (no time filter) ~4/8 (50%). Search endpoint is the winner.
> **type:** monitoring process (date-in → company list out). fundamentally different from lookup processes (company-in → data out).
> **target deployment:** TriggerDev cron (daily 7am ET)
> **cost:** $0.008-0.02/day ($0.24-0.60/month) for discovery queries. Enrichment/scraping additional.

surface all Series A funding announcements from the last 24 hours. extract company name, domain, amount raised, source URL, and stated reasoning for the round.

## inputs

- `{{date}}` — today's date in YYYY-MM-DD format
- `{{serper_api_key}}` — SerperDev API key
- `{{openai_api_key}}` — OpenAI API key (for GPT agent)
- `{{spider_api_key}}` — Spider Cloud API key (for scraping 403 sources)

## pipeline architecture

four-stage pipeline with parallel agents. each stage uses the cheapest model that can do the job.

```
┌──────────────────────────────────────────────────────────────────┐
│  STAGE 1: PARALLEL DISCOVERY (2 agents)                          │
│                                                                  │
│  ┌─────────────────────────┐  ┌─────────────────────────┐       │
│  │  AGENT A: Aggregators   │  │  AGENT B: Broad Sweep   │       │
│  │  q3 TheSaaSNews         │  │  q1 broad Series A      │       │
│  │  q4 FinSMEs             │  │  q2 announcement lang   │       │
│  │  q5 AlleyWatch          │  │  q6 press wires         │       │
│  │  q10 InfotechLead       │  │  q7 VC language         │       │
│  │  q9 VCNewsDaily         │  │  q8 European            │       │
│  │  num: 30-50 per query   │  │  num: 20-30 per query   │       │
│  │  + pagination (page 2)  │  │  + pagination (page 2)  │       │
│  └────────────┬────────────┘  └────────────┬────────────┘       │
│               └──────────┬─────────────────┘                     │
│                          ▼                                       │
│  model: gpt-4o-mini + SerperDev (search endpoint, tbs:qdr:d)    │
│  output: raw list of {company_name, source_url, snippet}         │
│  cost: ~$0.02-0.05 per run (20-40 Serper searches w/ pagination)│
├──────────────────────────────────────────────────────────────────┤
│  STAGE 2: CONSOLIDATE, DEDUP & FILTER                            │
│  model: gpt-4o-mini                                              │
│  input: raw list from stage 1                           │
│  output: scored list, deduped, Series A only            │
│  cost: ~$0.005 per run                                  │
├─────────────────────────────────────────────────────────┤
│  STAGE 3: ENRICH & EXTRACT                              │
│  model: claude-opus-4-6 (distillation)                  │
│  tools: Spider Cloud (scrape), SerperDev (domain lookup) │
│  input: scored companies from stage 2                   │
│  output: structured records with all 5 required fields  │
│  cost: ~$0.05-0.15 per company                          │
└─────────────────────────────────────────────────────────┘
```

if gpt-4o-mini produces poor tool-use results in stage 1, graduate to gpt-4.1-mini.

---

## stage 1: discovery sweep

### objective

cast a wide net across funding news aggregators and general news to find every Series A announcement from the last 24 hours. prioritize recall over precision — stage 2 filters.

### search tool

**primary:** SerperDev Web Search endpoint (NOT news — search outperforms news 88% vs 62% on GT):

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

**supplementary:** SerperDev News endpoint (same structure but `url: .../news`) — run in parallel for editorial coverage the search endpoint misses.

`tbs: "qdr:d"` = last 24 hours. this is the daily gate — no date parsing needed.

**important:** `after:YYYY-MM-DD` operator is BROKEN in SerperDev. do not use. `tbs` is the only reliable time filter.

### why search beats news

1. aggregator listing pages (TheSaaSNews, InfotechLead) rank in web search but NOT in news index
2. InfotechLead daily roundups found 3/8 GT companies via search, 0/8 via news
3. news endpoint filters too aggressively — demotes small aggregators and press wire reposts

### discovery queries (run all in parallel)

**query 1 — broad Series A sweep:**
```json
{"q": "\"Series A\" raises OR raised OR funding OR round million", "tbs": "qdr:d", "num": 20}
```

**query 2 — announcement language:**
```json
{"q": "\"Series A\" announces OR secures OR closes OR completes funding", "tbs": "qdr:d", "num": 20}
```

**query 3 — aggregator sweep (TheSaaSNews):**
```json
{"q": "site:thesaasnews.com Series A", "tbs": "qdr:d", "num": 10}
```

**query 4 — aggregator sweep (FinSMEs):**
```json
{"q": "site:finsmes.com Series A", "tbs": "qdr:d", "num": 10}
```

**query 5 — aggregator sweep (AlleyWatch):**
```json
{"q": "site:alleywatch.com funding report", "tbs": "qdr:d", "num": 10}
```

**query 6 — press wire sweep:**
```json
{"q": "\"Series A\" site:businesswire.com OR site:prnewswire.com OR site:einpresswire.com", "tbs": "qdr:d", "num": 10}
```

**query 7 — VC/investor announcement language:**
```json
{"q": "\"led the round\" OR \"led the Series A\" OR \"led a\" Series A investment startup", "tbs": "qdr:d", "num": 20}
```

**query 8 — European coverage:**
```json
{"q": "\"Series A\" startup funding site:eu-startups.com OR site:tech.eu OR site:techround.co.uk", "tbs": "qdr:d", "num": 10}
```

**query 9 — VCNewsDaily (untested, add after validation):**
```json
{"q": "site:vcnewsdaily.com Series A", "tbs": "qdr:d", "num": 10}
```

**query 10 — InfotechLead daily VC roundup:**
```json
{"q": "site:infotechlead.com venture capital funding", "tbs": "qdr:d", "num": 10}
```

**total: 10 queries, ~130 result slots.** at $0.001/search = $0.01 per daily run.

### validated query performance (2026-04-20, search endpoint, qdr:d)

| query | GT hits | companies found | verdict |
|-------|:-------:|-----------------|---------|
| q3 TheSaaSNews | **4** | Ethermed, Zenskar, Creao AI, Capsule Security | **BEST — run first** |
| q10 InfotechLead | **3** | Zenskar, Spektr, Creao AI | **SECOND BEST** |
| q1 broad sweep | 2 | Hata, Zenskar | KEEP |
| q2 announcement language | 2 | Hata, Archangel Lightworks | KEEP — catches niche |
| q6 press wires | 1 | Hata | KEEP — only APAC source |
| q4 FinSMEs | 1 | Zenskar | KEEP — overlap coverage |
| q7 VC language | 1 | Zenskar | MARGINAL |
| q8 European | 0 (daily) / 2 (weekly) | Wamo via qdr:w | KEEP for weekly catch-up |
| q5 AlleyWatch | 0 (daily) / 1 (weekly) | Zenskar via qdr:w | WEAK daily |
| q9 tech press | 0 | — | REPLACED with VCNewsDaily |

### stage 1 output

for each result, extract:

```json
{
  "company_name_raw": "string — may be VC name, need disambiguation",
  "amount_raw": "string — e.g. '$15M', '€10M', '$20 million'",
  "round_type_raw": "string — e.g. 'Series A', 'seed', 'growth'",
  "source_url": "string — the article URL",
  "source_domain": "string — e.g. 'thesaasnews.com', 'businesswire.com'",
  "snippet": "string — the search result snippet for context",
  "query_source": "string — which query found this (q1-q10)"
}
```

---

## stage 2: score and filter

### objective

take raw discovery results, deduplicate, filter to actual Series A rounds, and score each for scraping priority.

### model

gpt-4o-mini (no tools needed, pure text analysis).

### filtering rules

**KEEP only if ALL of these are true:**
1. round type is Series A (not Seed, Series B/C/D, growth equity, debt, grant, IPO)
2. announcement is from a real company (not a VC fund raise, not a market report, not a "how to raise Series A" article)
3. not a duplicate — same company from different sources counts as one entry

**SCORE each on 1-5:**
- **source_quality:** 5 = company blog/press wire, 4 = first-party VC post, 3 = quality aggregator (TheSaaSNews, FinSMEs), 2 = secondary coverage, 1 = listicle/paid lead magnet
- **data_completeness:** 5 = has company name + amount + investors in snippet, 3 = has name + amount, 1 = name only
- **scrape_priority:** `source_quality * data_completeness` — higher = scrape first

### VC name vs company name disambiguation

this is the biggest failure mode. announcements often lead with investor name:

> "Sequoia leads $30M round in..." → company name is NOT Sequoia
> "Bessemer backs AI billing startup..." → need to find the actual company name

**disambiguation rules:**
1. if `company_name_raw` matches a known VC/investor name → flag as `needs_disambiguation: true`
2. known VC patterns: contains "Capital", "Ventures", "Partners", "Fund", "Investment"
3. if flagged: the actual company name is usually in the snippet or article title after "in" or "into" or "backs" or "for"
4. if still ambiguous after snippet analysis: add to scrape queue — the article body will have the company name

### deduplication

group results by company name (normalized lowercase, stripped of Inc/Ltd/Corp). keep the highest-scored source for each company.

### stage 2 output

```json
{
  "companies": [
    {
      "company_name": "Zenskar",
      "amount": "$15M",
      "round_type": "Series A",
      "sources": [
        {"url": "https://...", "domain": "businesswire.com", "score": 25},
        {"url": "https://...", "domain": "thesaasnews.com", "score": 15}
      ],
      "best_source_url": "https://... (highest scored)",
      "needs_disambiguation": false,
      "scrape_priority": 25
    }
  ],
  "filtered_out": [
    {"name": "...", "reason": "Series B, not Series A"},
    {"name": "...", "reason": "VC fund raise, not startup"}
  ]
}
```

---

## stage 3: enrich and extract

### objective

for each scored company, scrape the best source URL and extract the 5 required output fields. if company domain isn't in the source, do a follow-up search.

### scraping strategy

**try WebFetch first** (free, fast). if 403 or blocked, fall back to **Spider Cloud**.

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

### extraction steps (per company)

**step 1: scrape best source URL**

scrape the highest-scored source. extract:
- company name (confirmed)
- amount raised (confirmed with currency)
- lead investor(s)
- participating investors
- stated use of funds / reasoning for the round
- company website/domain (if mentioned in the article)

**step 2: find company domain (if not in source)**

if domain wasn't in the article, search:

```json
{
  "url": "https://google.serper.dev/search",
  "body": {"q": "{{company_name}} official website", "num": 5}
}
```

the company's own website is almost always result #1 or #2. extract domain from URL.

**step 3: validate domain**

quick sanity check: does the domain resolve? is it actually the company's site (not a Crunchbase/LinkedIn profile)?

if the search returns only third-party profiles and no company website: flag as `domain_uncertain: true` and use the most likely candidate.

### extraction model

claude-opus-4-6 for distillation. system prompt:

```
you are extracting structured funding data from a scraped article. extract exactly these fields:

1. company_name — the company that raised money (NOT the investor)
2. company_domain — their website domain (e.g. zenskar.com)
3. amount_raised — exact amount with currency (e.g. "$15M", "€10M")
4. source_url — the URL of this article
5. round_reasoning — why they raised / what they'll use the money for, in 1-2 sentences

if a field is not available in the article, return "not_stated" — never fabricate.
```

---

## output schema

```json
{
  "date": "2026-04-20",
  "series_a_count": 5,
  "companies": [
    {
      "company_name": "Zenskar",
      "company_domain": "zenskar.com",
      "amount_raised": "$15M",
      "source_url": "https://www.businesswire.com/...",
      "round_reasoning": "Expand agentic capabilities for B2B revenue automation platform, scale AI-native billing and collections."
    }
  ],
  "metadata": {
    "queries_run": 10,
    "raw_results": 87,
    "after_dedup": 12,
    "after_filter": 5,
    "scrape_success_rate": "5/5"
  }
}
```

---

## source tier reference

validated against April 2026 ground truth (8 companies).

### tier S: primary discovery sources (scrape daily)

| source | type | URL pattern | GT hits | coverage |
|--------|------|-------------|:-------:|----------|
| TheSaaSNews | aggregator listing | thesaasnews.com/news/series-a | 3/8 | SaaS, AI, B2B |
| AlleyWatch | daily digest | alleywatch.com/YYYY/MM/the-alleywatch-startup-daily-funding-report-M-D-YYYY/ | 2/8 | US-focused, all sectors |
| FinSMEs | per-deal articles | finsmes.com/YYYY/MM/[slug].html | 2/8 | global, all sectors |
| InfotechLead | daily VC roundup | infotechlead.com/tech/venture-capital-funding-[names]-[id] | 2/8 | global tech |

### tier A: reliable supplementary

| source | type | GT hits | coverage |
|--------|------|:-------:|----------|
| VentureBurn | per-deal articles | 2/8 | global |
| Pulse2 | per-deal articles | 1/8 | global |
| Tech.eu | per-deal articles | 1/8 | European |
| EU-Startups | per-deal articles | 1/8 | European (some paywalled) |
| FinTech Global | per-deal articles | 1/8 | fintech vertical |

### tier B: niche/regional

| source | type | GT hits | coverage |
|--------|------|:-------:|----------|
| SatNews | per-deal | 1/8 | space/defense |
| HelpNetSecurity | per-deal | 1/8 | cybersecurity |
| SiliconANGLE | per-deal | 1/8 | enterprise tech |
| EQS News | press wire | 1/8 | APAC, crypto |
| Calcalist (Ctech) | per-deal | 0/8 | Israeli startups |

### tier D: avoid (paid lead magnets / gated)

- fundraiseinsider.com — gated list, requires email
- vcbacked.co — gated directory
- growthlist.co — paid list ($)
- cyberleads.com — paid list ($)
- topstartups.io — aggregator with limited free data
- crunchbase.com — paywalled detail pages (Crunchbase News articles are OK)
- pitchbook.com — fully paywalled

---

## SerperDev reference

### endpoints

| endpoint | URL | use for |
|----------|-----|---------|
| web search | `https://google.serper.dev/search` | general queries, domain lookup |
| news search | `https://google.serper.dev/news` | time-gated funding announcements |

### time controls (`tbs` parameter)

| value | meaning | use case |
|-------|---------|----------|
| `qdr:h` | past hour | not useful for funding news |
| `qdr:d` | past 24 hours | **daily monitoring sweep** |
| `qdr:w` | past week | weekly catch-up / backfill |
| `qdr:m` | past month | monthly review |
| `qdr:y` | past year | annual analysis |

**do NOT use** `after:YYYY-MM-DD` — broken in SerperDev, universally returns Q1 results.

### boolean operators (validated)

| operator | works? | example |
|----------|:------:|---------|
| `OR` | ✅ | `raises OR raised OR funding` |
| `""` exact match | ✅ | `"Series A"` |
| `-` exclusion | ✅ | `-jobs -careers` |
| `site:` | ✅ | `site:thesaasnews.com` |
| `site: OR site:` | ✅ | `site:a.com OR site:b.com` |
| `intitle:` | ❌ | too aggressive, strips results |
| `after:` | ❌ | broken in SerperDev |
| `AROUND(n)` | ❌ | untested, likely broken |
| `inurl:` | ⚠️ | inconsistent, avoid |

---

## known failure modes

| failure | cause | mitigation |
|---------|-------|------------|
| VC name surfaces instead of company name | announcements led by investor | disambiguation rules in stage 2 |
| non-US deals missed | `gl: "us"` biases results | add EU-specific queries (q8) |
| 403 on scrape | site blocks WebFetch | fall back to Spider Cloud |
| crypto/APAC deals missed | low coverage in US aggregators | add EQS News, regional queries |
| company domain not in article | press releases rarely link company | domain lookup search in stage 3 |
| "Series A extension" miscounted | semantically similar but different | filter rule: extension = keep, report as "Series A Extension" |
| listicle/roundup double-counts | one article mentions 10 companies | dedup by normalized company name |

---

## ground truth — April 2026

| company | amount | type | best first-party source | discovered via |
|---------|--------|------|-------------------------|----------------|
| Zenskar | $15M | Series A | BusinessWire press release | AlleyWatch daily, TheSaaSNews, InfotechLead |
| Spektr | $20M | Series A | spektr.com/blog (company blog) | FinSMEs, Pulse2, Crunchbase News |
| Ethermed | $8.5M | Series A | EIN Presswire | AlleyWatch weekly, TheSaaSNews |
| Hata | $8M | Series A | EQS News (press wire) | direct name search only (APAC gap) |
| Archangel Lightworks | £10M (~$14M) | Series A | DataCenterDynamics | direct name search (niche sector) |
| Capsule Security | $7M | Seed | Forgepoint Capital blog (VC) | HelpNetSecurity, SiliconANGLE |
| Wamo | €10M | Series A | EU-Startups | Tech.eu, FinTech Global, VentureBurn |
| Creao AI | $10M | Growth | TheSaaSNews | FinSMEs, VentureBeat, InfotechLead |

**notes:**
- Capsule Security is Seed, not Series A — should be filtered out in stage 2 but included in GT for testing recall
- Creao AI is "Growth" round, not explicitly Series A — borderline, may filter depending on strictness
- Hata is hardest to discover — APAC crypto, no US aggregator coverage
- coverage via aggregator queries alone: **5/8 (62.5%)**
- coverage adding direct name search: **8/8 (100%)**

---

## iteration targets

- [ ] test SerperDev News endpoint (`/news`) with `tbs: "qdr:d"` for all 10 queries against GT
- [ ] test Spider Cloud on 403 sources (FinSMEs)
- [ ] add LinkedIn VC post detection (VC announces portfolio investment)
- [ ] measure daily false positive rate (non-Series-A results that pass filter)
- [ ] add Substack newsletter sources (PostRound digest)
- [ ] test `qdr:w` for weekly catch-up backfill
- [ ] build TriggerDev task definition
- [ ] add Slack/Telegram notification on new companies found
