# Product Launch Monitor

Daily automated pipeline that discovers new product launches from two sources — Product Hunt and tech news — classifies them with GPT-4o-mini, and stores everything in Supabase. Runs on Trigger.dev (free tier). Total cost: ~$0.10/day (~$3/month).

This was built entirely with Claude Code. The whole system — scraping, classification, database, deployment — was designed and shipped in conversation.

---

## Trigger Scheduled Tasks + Signal Control Plane

This project shares Trigger.dev `proj_vvsvdbeeoiaausrkdiqp` with 13 active signal-workflow schedules that run independently of the product-launch pipelines:

**Company signals (funding/series stages, 07:00-08:00 ET on weekdays):**
- `signal-bank-daily` - Funding discoveries classification
- `series-a-daily`, `series-b-daily`, `series-c-daily` - Series stage signals
- `series-a-weekly`, `series-b-weekly`, `series-c-weekly` - Weekly aggregations (Monday 08:00 ET)
- `enrichment-retry-weekly` - Retry failed enrichments

**Other signals (varying schedules):**
- `jobs-weekly` (Mon/Thu 07:00 ET) - Job posting signals
- `game-signals-weekly` (Monday 08:00 ET) - Game signal aggregation
- `product-launches-ph-daily` (07:00 ET) - Product Hunt launches
- `product-launches-news-daily` (07:00 ET) - News product launches
- `raisingfi-ingest` (07:30 ET) - RaisingFi ingest

Each signal task checks the control plane at run start via `signals/modules/workflow-gate.ts` (fail-open on registry outage) and can be paused/resumed without deploying:

```bash
cd ../trigger-workflows  # note: toggle command runs from gtm-orchestrator
lg run --env prod node scripts/trigger/signals_toggle.mjs status
lg run --env prod node scripts/trigger/signals_toggle.mjs off leadgrow funding-series-a "reason"
lg run --env prod node scripts/trigger/signals_toggle.mjs on leadgrow funding-series-a
```

Registry: `lg_market_data.signal_workflows` (Supabase, shared with signals project `proj_qncjiykwdcrcsjnglyzz`). Schema: `client_slug`, `workflow`, `status`, `pause_reason`, cost model, catalog. Toggle takes effect at the next cron fire (no deploy needed).

**Deploy protocol:** Always verify live sha is in HEAD before deploying — shared project means multiple branches may deploy. See DEPLOYING.md (in gtm-orchestrator/trigger-workflows) for the full protocol.

---

## What It Does

Every morning, two scheduled tasks fire:

1. **Product Hunt pipeline** (9 AM ET) — scrapes the PH leaderboard, fetches each product page for metadata, classifies every launch
2. **News pipeline** (10 AM ET) — scrapes TechCrunch, Hacker News, VentureBeat, The Verge, and press wires via Google search, classifies every article as launch or not

Both pipelines write to the same Supabase table (`product_launches`). Each row includes: company name, product name, whether it's AI, whether it's a new product or new feature, source URL, and optionally Clay enrichment data (employee count, industry, location, LinkedIn followers).

Results are served via a website API endpoint and displayed on a public signals page.

---

## Architecture

```
                    ┌──────────────────────────────────────┐
                    │         Trigger.dev (cloud)           │
                    │                                      │
                    │  ┌────────────────┐  ┌────────────┐  │
                    │  │  PH Pipeline   │  │   News      │  │
  9 AM ET daily ────┤  │  (4 stages)    │  │  Pipeline   │──┤── 10 AM ET daily
                    │  │                │  │  (3 stages) │  │
                    │  └───────┬────────┘  └──────┬──────┘  │
                    │          │                   │         │
                    └──────────┼───────────────────┼─────────┘
                               │                   │
                    ┌──────────▼───────────────────▼─────────┐
                    │            Supabase                     │
                    │         product_launches                │
                    │  (deduped on source_url, RLS enabled)   │
                    └──────────────────┬─────────────────────┘
                                       │
                              ┌────────▼────────┐
                              │   Website API    │
                              │  /api/signals/   │
                              │ product-launches │
                              └─────────────────┘
```

### External APIs Used

| Service | What For | Cost |
|---|---|---|
| [Spider.cloud](https://spider.cloud) | JS-rendered page fetching (PH needs JavaScript) | ~$0.002/page |
| [Serper.dev](https://serper.dev) | Google search (news discovery + PH supplement) | ~$0.001/query |
| [OpenAI](https://platform.openai.com) | GPT-4o-mini for extraction + classification | ~$0.002/call |
| [Clay](https://clay.com) (optional) | Company enrichment via webhook | Clay credits |

### Tech Stack

- **Runtime:** [Trigger.dev](https://trigger.dev) v3 (serverless TypeScript tasks with cron scheduling)
- **Language:** TypeScript (ES2022, NodeNext modules)
- **Database:** [Supabase](https://supabase.com) (Postgres + REST API + RLS)
- **Enrichment callback:** Cloudflare Worker (routes Clay responses back to Trigger.dev)

---

## Pipeline 1: Product Hunt

Runs daily at 9 AM ET. Discovers every product launched on Product Hunt that day.

```
[Stage 1] Spider API fetches the PH leaderboard page (JS-rendered)
     │  GPT-4o-mini extracts structured data from the page content:
     │  rank, product_name, company_name, tagline, score, categories, maker_website
     │
     ▼
[Stage 1b] For each product missing maker_website:
     │  Spider fetches the individual PH product page
     │  Extracts the ?ref=producthunt external link → maker_website
     │  Also extracts linkedin.com/company/ URLs from the page
     │
     ▼
[Stage 1c] For each product with maker_website but no LinkedIn:
     │  Spider fetches the company homepage
     │  Extracts linkedin.com/company/ URL from footer/header links
     │
     ▼
[Domain hint] Company name normalization (no GPT, free):
     │  Parses maker_website domain → strips TLD, www, app, get, try, use
     │  Converts slug to title case → pre-fills company_name
     │  Skips generic domains (github, google, openai, anthropic, etc.)
     │
     ▼
[Serper supplement] Google search for PH posts from this date:
     │  Query: site:producthunt.com/posts "May 7, 2026"
     │  Catches products Spider missed (PH sometimes lazy-loads items)
     │  Merged with Spider results, deduped by product_name + ph_url
     │
     ▼
[Stage 2] GPT-4o-mini batch classification:
     │  launch_type: new_product | new_feature
     │  is_ai: true | false (checks categories, tagline keywords)
     │  company_name: strips version noise ("v7", "2.0", "for VS Code")
     │    Uses domain_hint as strong signal for company identity
     │  classification_reasoning: 1 sentence
     │
     ▼
[Stage 2b] Launch count verification:
     │  Fetches producthunt.com/products/{slug} for postsCount
     │  If count > 1 → override to new_feature (returning product)
     │  If count = 1 → override to new_product (first launch)
     │  Tries 4 slug variants: exact, strip -N, strip -for-X, Serper canonical
     │
     ▼
[Stage 3] Upsert to Supabase:
     │  ON CONFLICT source_url → merge-duplicates (idempotent)
     │  Re-running same date safely updates existing rows
     │
     ▼
[Stage 4] Clay enrichment (optional, async):
     Fires webhook per product with maker_website
     Waits up to 5 min per product for Clay callback via Cloudflare Worker
     Patches back: employee_count, industry, company_location,
       company_description, linkedin_followers
```

**Why Spider + Serper?** Product Hunt's leaderboard page is a React SPA — you need JS rendering to get content. Spider handles that. But Spider occasionally misses products that load lazily, so Serper catches the stragglers via Google's index.

**Why the domain hint?** On Product Hunt, `company_name` often equals `product_name` — you get "Kilo Code v7 for VS Code" instead of "Kilo Code". The domain hint strips version noise cheaply (the domain `kilocode.ai` → "Kilo Code"), then GPT confirms or improves it.

---

## Pipeline 2: News

Runs daily at 10 AM ET (1 hour after PH). Discovers product launches from tech press and Hacker News.

```
[Stage 1A] Direct source fetches (parallel, no API cost):
     │  TechCrunch: fetches today's + yesterday's date pages
     │    Parses article links via regex (no cheerio dependency)
     │  Hacker News: fetches /show page (Show HN posts)
     │  Hacker News: fetches /front?day=YYYY-MM-DD
     │
     ▼
[Stage 1B] Serper supplement (4 parallel Google queries):
     │  TechCrunch: "launches" OR "announces" OR "introduces" OR "debuts"
     │  VentureBeat: launches OR announces product
     │  The Verge: "launches" OR "announces" product
     │  Press wires: "now available" OR "product launch"
     │    (businesswire.com + prnewswire.com)
     │
     ▼
[Stage 2] GPT-4o-mini classification (batches of 30):
     │  Dedup by URL first, skip pagination/tag/author pages
     │  For each article, classify:
     │    is_launch: true/false (filters reviews, funding-only, listicles, lawsuits)
     │    launch_type: new_product | new_feature
     │    is_ai: true/false (extensive keyword matching)
     │    company_name: extracted from title patterns
     │    product_name: the specific thing launched
     │  Dedup by normalized company_name + product_name
     │  Source priority: own domain > TC > VB/Verge > press wire > HN
     │
     ▼
[Stage 3] Upsert to Supabase:
     Same table as PH pipeline, source = "news"
     ON CONFLICT source_url → merge-duplicates
```

**Source priority matters.** If TechCrunch and a press wire both cover the same launch, we keep the TechCrunch article (better signal, more detail). Own-domain sources (company blog) rank highest.

**Show HN is gold.** Many AI tools launch on Hacker News before (or instead of) Product Hunt. The "Show HN:" prefix is a strong launch signal — the pipeline treats it accordingly.

---

## Setup Guide

### 1. Create a Supabase project

Go to [supabase.com](https://supabase.com), create a new project. Note your project URL and service role key.

### 2. Create the database table

Open SQL Editor in Supabase Dashboard and run each block:

**Table:**
```sql
CREATE TABLE IF NOT EXISTS product_launches (
  id bigint generated always as identity primary key,
  discovered_date date not null,
  company_name text not null,
  company_domain text,
  product_name text not null,
  tagline text,
  launch_type text not null check (launch_type in ('new_product', 'new_feature')),
  is_ai boolean not null default false,
  score integer,
  rank integer,
  launch_count integer,
  maker_website text,
  ph_url text,
  source text not null check (source in ('product_hunt', 'news')),
  source_url text not null,
  source_name text,
  description text,
  categories text[],
  classification_reasoning text,
  linkedin_url text,
  on_product_hunt boolean default false,
  employee_count integer,
  industry text,
  company_location text,
  company_description text,
  linkedin_followers integer,
  discovered_by_pipeline text,
  pipeline_version text default '1.0-ts',
  created_at timestamptz default now(),
  updated_at timestamptz default now(),
  unique(source_url)
);
```

**Indexes:**
```sql
CREATE INDEX IF NOT EXISTS idx_pl_date    ON product_launches(discovered_date DESC);
CREATE INDEX IF NOT EXISTS idx_pl_company ON product_launches(company_name);
CREATE INDEX IF NOT EXISTS idx_pl_source  ON product_launches(source);
CREATE INDEX IF NOT EXISTS idx_pl_is_ai   ON product_launches(is_ai) WHERE is_ai = true;
```

**Row Level Security:**
```sql
ALTER TABLE product_launches ENABLE ROW LEVEL SECURITY;

-- Public read (your website API uses the anon key)
CREATE POLICY "anon_read" ON product_launches
  FOR SELECT TO anon USING (true);

-- Service role write (pipeline uses service_role key)
CREATE POLICY "service_write" ON product_launches
  FOR ALL TO service_role USING (true);
```

**Auto-update timestamp trigger:**
```sql
-- Create the function (skip if you already have it from another table)
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Attach to the table
CREATE OR REPLACE TRIGGER set_updated_at
  BEFORE UPDATE ON product_launches
  FOR EACH ROW EXECUTE FUNCTION update_updated_at();
```

### 3. Get your API keys

| Service | Where to get it |
|---|---|
| Supabase project URL | Project Settings → API → Project URL |
| Supabase service role key | Project Settings → API → service_role (secret) |
| Spider API key | [spider.cloud](https://spider.cloud) → Dashboard → API Key |
| Serper API key | [serper.dev](https://serper.dev) → Dashboard → API Key |
| OpenAI API key | [platform.openai.com](https://platform.openai.com) → API Keys |
| Trigger.dev secret key | [cloud.trigger.dev](https://cloud.trigger.dev) → Project → API Keys |

### 4. Create your .env file

```env
# Trigger.dev
TRIGGER_SECRET_KEY=tr_secret_...

# Supabase
SUPABASE_PROJECT_URL=https://xxxxxxxxxxxx.supabase.co
SUPABASE_SERVICE_ROLE_KEY=eyJ...

# Web fetching
SPIDER_API_KEY=spider_...

# Google search
SERPER_API_KEY=...

# Classification
OPENAI_API_KEY=sk-...

# Clay enrichment (optional — skip if you don't use Clay)
CLAY_COMPANY_ENRICH_WEBHOOK=https://...
CLAY_COMPANY_ENRICH_CALLBACK_URL=https://your-worker.workers.dev
```

### 5. Install and deploy

```bash
cd trigger
npm install

# Authenticate with Trigger.dev
npx trigger.dev@latest login

# Push env vars to Trigger.dev cloud
node push-env.mjs

# Deploy and test with a specific date
node deploy-and-run.mjs 2026-05-07
```

You'll get a dashboard URL to watch the run in real time. The first run takes 2-3 minutes (Spider fetches ~15-20 product pages).

### 6. Verify results

```bash
# Check your Supabase table directly
curl -s "https://YOUR-PROJECT.supabase.co/rest/v1/product_launches?select=discovered_date,company_name,product_name,is_ai,launch_type&order=discovered_date.desc&limit=10" \
  -H "apikey: YOUR_ANON_KEY" | python -m json.tool
```

Or in Supabase SQL Editor:
```sql
SELECT discovered_date, company_name, product_name, launch_type, is_ai, source, score
FROM product_launches
ORDER BY discovered_date DESC, score DESC
LIMIT 20;
```

---

## Running and Operating

### Manual run (any date)

```bash
node deploy-and-run.mjs 2026-05-04
```

Deploys latest code + triggers one run. Safe to re-run — upserts on source_url.

### Backfill multiple dates

```bash
node backfill.mjs 2026-05-01 2026-05-02 2026-05-03 2026-05-04 2026-05-05
```

Each date triggers a separate run. No deploy needed if code is already deployed.

### Scheduled runs (automatic)

Once deployed, two cron tasks fire daily:

| Task | Schedule | What |
|---|---|---|
| `product-launches-ph-daily` | 9 AM ET | Product Hunt leaderboard |
| `product-launches-news-daily` | 10 AM ET | TechCrunch, HN, VentureBeat, The Verge, press wires |

PH runs first because the leaderboard settles by ~8 AM ET. News runs an hour later.

### Check a run

```bash
node poll-run.mjs run_xxxxxxxxxxxx
node get-run-logs.mjs run_xxxxxxxxxxxx
```

---

## Useful SQL Queries

```sql
-- What launched today?
SELECT company_name, product_name, launch_type, is_ai, source, score
FROM product_launches
WHERE discovered_date = CURRENT_DATE
ORDER BY score DESC NULLS LAST;

-- AI launches this week
SELECT discovered_date, company_name, product_name, tagline, source
FROM product_launches
WHERE discovered_date >= CURRENT_DATE - interval '7 days'
  AND is_ai = true
ORDER BY discovered_date DESC, score DESC NULLS LAST;

-- New products only (not features/updates)
SELECT discovered_date, company_name, product_name, tagline
FROM product_launches
WHERE launch_type = 'new_product'
  AND discovered_date >= CURRENT_DATE - interval '7 days'
ORDER BY discovered_date DESC, score DESC NULLS LAST;

-- Data quality: company_name still equals product_name (needs cleanup)
SELECT product_name, company_name, maker_website, source
FROM product_launches
WHERE company_name = product_name
  AND discovered_date >= CURRENT_DATE - interval '7 days'
ORDER BY discovered_date DESC;

-- Source breakdown
SELECT source, count(*) as total,
       count(*) FILTER (WHERE is_ai) as ai_count,
       count(*) FILTER (WHERE launch_type = 'new_product') as new_products
FROM product_launches
WHERE discovered_date >= CURRENT_DATE - interval '30 days'
GROUP BY source;

-- Companies with Clay enrichment data
SELECT company_name, product_name, employee_count, industry, company_location
FROM product_launches
WHERE employee_count IS NOT NULL
ORDER BY discovered_date DESC
LIMIT 20;
```

---

## Project Structure

```
trigger/
├── src/
│   ├── product-launches-ph-daily.ts      # Cron: 9 AM ET — PH pipeline
│   ├── product-launches-news-daily.ts    # Cron: 10 AM ET — news pipeline
│   ├── series-a-daily.ts                 # (separate pipeline — funding discovery)
│   └── pipeline/
│       ├── product-launches-ph.ts        # PH pipeline: 4 stages + domain hints
│       ├── product-launches-news.ts      # News pipeline: 3 stages
│       ├── product-launch-types.ts       # Shared TypeScript types
│       ├── spider.ts                     # Web fetching (Spider API + plain fetch fallback)
│       ├── serper.ts                     # Google Serper wrapper
│       └── ...
├── supabase/
│   └── migrations/
│       └── 003_product_launches.sql      # Canonical table DDL
├── deploy-and-run.mjs                    # Deploy + trigger one PH run
├── backfill.mjs                          # Trigger runs for multiple dates
├── push-env.mjs                          # Push .env → Trigger.dev cloud
├── poll-run.mjs                          # Poll run status
├── get-run-logs.mjs                      # Fetch full logs for a run
├── trigger.config.ts                     # Trigger.dev project config
├── package.json                          # Dependencies (@trigger.dev/sdk 4.4.5)
└── tsconfig.json                         # TypeScript config (ES2022, NodeNext)
```

---

## Cost Breakdown

### Per day (both pipelines combined)

| Step | Cost |
|---|---|
| Spider: PH leaderboard (1 page, JS render) | ~$0.002 |
| Spider: PH product pages (~15 pages) | ~$0.030 |
| Spider: homepage LinkedIn lookups (~10 pages) | ~$0.020 |
| Serper: PH supplement (1 query) | ~$0.001 |
| Serper: PH slug lookups (~15 queries) | ~$0.015 |
| Serper: news queries (4 queries) | ~$0.004 |
| GPT-4o-mini: PH extraction + classification (2 calls) | ~$0.002 |
| GPT-4o-mini: news classification (1-2 calls) | ~$0.002 |
| **Total per day** | **~$0.08** |
| **Total per month** | **~$2.50** |

Trigger.dev and Supabase both fit within free tiers.

---

## How This Was Built

This entire system was built in Claude Code sessions over ~2 weeks. The process:

1. **Started with the PH pipeline.** Prompt: "build a daily pipeline that scrapes Product Hunt and stores results in Supabase." Claude Code wrote the Spider integration, GPT classification prompt, Supabase schema, and Trigger.dev task definition.

2. **Added the news pipeline.** Same session type — "now build one that does the same thing from tech news sites." Claude Code designed the source hierarchy (TC > VB > press wire > HN), the classification prompt (extensive rules for what counts as a "launch"), and the dedup logic.

3. **Iterated on data quality.** The biggest issue was `company_name` — Product Hunt's leaderboard shows product names, not company names. "Kilo Code v7 for VS Code" is a product name, not a company. Claude Code designed the domain-hint system: parse the maker's website URL to extract a clean company name, pass it to GPT as a signal.

4. **Deployed via Trigger.dev.** Claude Code wrote the deploy script, the env push script, the backfill script, and the polling utilities. The cron schedules were set to 9 AM and 10 AM ET based on when Product Hunt finalizes its leaderboard.

Every file in this repo was written by Claude Code. The SQL, the TypeScript, the deploy scripts, this README.

---

## Known Limitations

- **OpenAI products** — `openai.com` is in the generic domain blocklist, so no domain hint fires. GPT usually catches it from context but not always (e.g. "Codex Pets" may not resolve to "OpenAI").
- **PH leaderboard timing** — runs before 8 AM ET often return empty (leaderboard not posted). The 9 AM cron handles this.
- **Clay enrichment** — optional. Requires a Cloudflare Worker to route callbacks. Skipped silently if not configured.
- **Spider rate limits** — heavy days (30+ products) may hit limits. Pipeline uses 500ms delays between fetches.
- **News dedup** — same launch covered by TC + VB + HN creates 3 raw items. The dedup picks the best source, but if company/product names differ across articles, duplicates can slip through.
- **HN front page** — requires login for `/front?day=` historical access. Current-day `/show` works without auth.
