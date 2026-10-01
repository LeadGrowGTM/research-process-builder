# Handoff: Clay Enrichment Integration for Product Launches

**Date:** 2026-05-06
**Status:** Clay CLI tested + validated, ready for Trigger.dev integration
**Depends on:** HANDOFF-product-launches.md (pipelines deployed, Supabase migration pending)

## What Was Validated

### Clay Table: Missing Company Info Data (Tier 1)
- **Table:** `t_0tcxgijYsmrT5seFVrr`
- **Workbook:** `wb_0tcxgeiA3FpxSDubNtw`
- **Clay URL:** https://app.clay.com/workspaces/206846/workbooks/wb_0tcxgeiA3FpxSDubNtw/tables/t_0tcxgijYsmrT5seFVrr/views/gv_0tcxgijrpk2qFbWgWsk
- **Webhook URL:** env `CLAY_COMPANY_ENRICH_WEBHOOK_URL` (Infisical, research-process-builder prod; never commit the URL)
- **CLI slug:** `missing-company-info` (registered in local webhooks)
- **Registry:** promoted to **tier 1** in `api-registry.ts`

### Minimum Viable Input
```json
{
  "Company Name": "Vercel",
  "Company LinkedIn": "https://www.linkedin.com/company/vercel",
  "Company Website": "https://vercel.com"
}
```

### Output Fields (validated on Vercel test)
| Field | Example |
|-------|---------|
| CompanyName | Vercel |
| EmployeeCount | 893 |
| Description | Full company description from LinkedIn |
| CompanyIndustry | Software Development |
| CompanyLocation | HQ location (fixed post-session) |
| CompanyFollowers | 228224 |
| FoundedDate | varies |
| ProductsAndServices | product summary |
| Confidence | high |

### Cost
- ~$0.0003/company
- ~10-20 launches/day = pennies

## Callback Architecture (from N8N baseline)

The N8N "Sun Tzu" workflow (`Sun Tzu — LeadGrow Intelligence Bot (Slack ↔ Clay).json`) demonstrates the callback pattern:

### How It Works
1. Build payload with `_callback_id` (UUID) and `_callback_url` (publicly reachable endpoint)
2. POST payload to Clay webhook URL (no auth needed — URL is the secret)
3. Clay creates row → enrichment columns run (Claygent scrapes LinkedIn, etc.)
4. Clay's HTTP API column POSTs enriched data back to `_callback_url`
5. Callback receiver gets the data, continues workflow

### Key Detail: Callback URL carries context
N8N encodes Slack context as query params on `_callback_url`:
```
https://lgn8nwebhookv2.up.railway.app/hook/clay-enrich-callback?channel=C123&thread_ts=1776683982.200869&ack_ts=1776683985.552859
```
Clay echoes back the full URL including query string. This is how the callback handler knows which request the response belongs to.

### N8N Reference File
`C:\Users\mitch\Downloads\Telegram Desktop\Sun Tzu — LeadGrow Intelligence Bot (Slack ↔ Clay).json`

## Trigger.dev Integration Plan

### Option A: `wait.forRequest()` (recommended)
Trigger.dev provides a publicly reachable URL that suspends the task until it receives a POST. Same pattern as N8N's Railway webhook, but built into the task runtime.

```typescript
// Pseudocode
const callbackId = crypto.randomUUID();
const { url, response } = await wait.forRequest<ClayEnrichmentResponse>({
  id: `clay-enrich-${domain}-${callbackId}`,
  timeout: "3m",
});

// POST to Clay with callback URL
await fetch(CLAY_WEBHOOK_URL, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    _callback_id: callbackId,
    _callback_url: url,
    "Company Name": launch.company_name,
    "Company LinkedIn": launch.company_linkedin,
    "Company Website": launch.company_domain,
  }),
});

// Task suspends here until Clay POSTs back
const enrichedData = await response;
```

### Option B: Two-task pattern
1. Task A: fires Clay webhook, stores callback ID in Supabase
2. Webhook trigger task B: receives Clay callback, matches by callback ID, writes enriched data

### Required Env Vars for Trigger.dev
| Var | Value | Why |
|-----|-------|-----|
| `CLAY_COMPANY_ENRICH_WEBHOOK` | env `CLAY_COMPANY_ENRICH_WEBHOOK_URL` (Infisical, research-process-builder prod; never commit the URL) | POST company data here |

No Clay login/session needed — webhook URL is the only credential.

### Pipeline Flow
```
Product Launch Pipeline (existing)
  → discovers company_name, company_domain, company_linkedin
  → POST to Clay webhook with _callback_url
  → Clay enriches (~7 seconds)
  → Clay POSTs back to _callback_url
  → Task resumes with enriched data
  → Write to Supabase product_launches table
```

### Payload Format (match Clay table columns exactly)
```json
{
  "_callback_id": "<uuid>",
  "_callback_url": "<trigger-dev-wait-url>",
  "Company Name": "Linear",
  "Company LinkedIn": "https://www.linkedin.com/company/linearapp",
  "Company Website": "https://linear.app"
}
```

## Clay CLI Changes Made
1. **Rebuilt CLI** — `C:\Users\mitch\Everything_CC\cli\leadgrow-clay-cli\dist\` regenerated
2. **Re-authenticated** — session refreshed for workspace 206846
3. **Tier promotion** — `missing-company-info-data` changed from tier 2 → tier 1 in `src/lib/api-registry.ts`
4. **Webhook registered** — `missing-company-info` slug registered with new webhook URL

## Clay MCP Status
Clay MCP tools (`mcp__claude_ai_Clay__*`) are cloud-side Anthropic integrations, not local MCP servers. Cannot uninstall from CLI — disconnect from claude.ai account settings. **Do not use for pipeline work** — use Clay CLI fire or direct HTTP POST to webhook.

## Available Clay APIs (25 total)
Full list via: `node "C:/Users/mitch/Everything_CC/cli/leadgrow-clay-cli/dist/index.js" api list`

**Tier 1 (foundational):**
- `company-data-enrich` — person employment verification (needs LinkedIn URL)
- `missing-company-info-data` — **company enrichment from domain** (this one)

**Tier 2 (deep, for future use):**
- `find-company-profiles` — tier/category/funding/summary
- `checking-funding` — funding state + outreach angle
- `company-techstack` — free, formula-only
- `company-growth-signals` — growth investments
- `company-recent-news` — trajectory + events
- `general-hiring-activity` — hiring status
- `business-type-summary` — SaaS vs Service classification
- +16 more (people finding, competitors, reviews, etc.)

## Next Steps
1. Run `003_product_launches.sql` in Supabase (still pending from previous handoff)
2. Verify `wait.forRequest()` exists in Trigger.dev v4.4.5
3. Add enrichment stage to product launch pipeline tasks
4. Add `CLAY_COMPANY_ENRICH_WEBHOOK` to Trigger.dev env vars
5. Test end-to-end: launch discovery → Clay enrichment → Supabase write
