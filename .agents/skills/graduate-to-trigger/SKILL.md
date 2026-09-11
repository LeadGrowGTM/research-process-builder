---
name: graduate-to-trigger
description: Graduate a research process (processes/find-*.md) into a production trigger.dev scheduled task. Use when user decides a monitoring process is ready for automated daily/weekly execution. Handles config generation, task scaffolding, deploy, and validation.
---

# Graduate to Trigger.dev

Take a validated research process from `processes/find-*.md` and deploy it as a trigger.dev scheduled task.

## When to Use

- User explicitly says "graduate this" or "make this run automatically" or "deploy to trigger.dev"
- Process is a **monitoring** type (date-in → results-out, runs on schedule)
- Process has validated queries with known hit rates

## When NOT to Use

- **Lookup** processes (company-in → data-out) — these are L3a agents, not trigger.dev tasks
- Process has < 5 ground truth results — stay at L1, keep testing
- Process runs < 1x/week — not worth the infra
- User hasn't decided to graduate yet — **graduation decision belongs to user**

## Prerequisites

Before graduating, the process spec should have:
- [ ] Validated search queries with GT hit rates
- [ ] Defined output schema (what fields the webhook/Supabase needs)
- [ ] Cost per run estimated
- [ ] Schedule defined (daily, weekly, etc.)

If any are missing, help the user fill them in first.

## Required Skills

Load these trigger.dev skills as needed during graduation:
- `trigger-tasks` — task definition patterns, cron, retries, queues
- `trigger-config` — trigger.config.ts setup, env vars, build config
- `trigger-cost-savings` — optimize for cost after initial deploy
- `trigger-setup` — if this is first trigger.dev task in a new repo

## Phase 1: Read Process Spec

Read `processes/find-{name}.md` and extract:

```
QUERIES       → SerperDev query definitions (id, query string, num results)
FILTER_RULES  → Round/noise patterns, exclusion regexes
OUTPUT_SCHEMA → Fields the webhook/Supabase expects
SCHEDULE      → Cron pattern (default: "0 7 * * *" = daily 7am ET)
ROUND_LABEL   → Display name (e.g. "Series A", "Competitor News")
TABLE_NAME    → Supabase table (default: "funding_discoveries")
```

## Phase 2: Generate Config

Create a round config in `trigger/src/pipeline/round-configs.ts` or a new config file if the process doesn't fit the funding round pattern.

**For funding rounds** (Series A/B/C pattern):
- Add to existing `round-configs.ts`
- Follow SERIES_A_CONFIG structure exactly

**For non-funding processes** (news, competitors, growth signals):
- Create `trigger/src/configs/{process-name}.ts`
- Define process-specific types if output schema differs from EnrichedRecord

## Phase 3: Generate Task Files

Scaffold from existing templates:

```
trigger/src/{process-name}-daily.ts   → schedules.task, cron pattern, daily
trigger/src/{process-name}-weekly.ts  → schedules.task, qdr:w catch-up
```

Template pattern (copy from series-a-daily.ts):
```ts
import { schedules, logger } from "@trigger.dev/sdk";
import { runFundingPipeline } from "./pipeline/pipeline.js";
import { PROCESS_CONFIG } from "./pipeline/round-configs.js";

export const processDaily = schedules.task({
  id: "{process-name}-daily",
  cron: { pattern: "CRON_HERE", timezone: "America/New_York" },
  retry: { maxAttempts: 3, factor: 2, minTimeoutInMs: 10_000, maxTimeoutInMs: 120_000, randomize: true },
  run: async (payload) => {
    const date = payload.timestamp.toISOString().split("T")[0];
    const result = await runFundingPipeline({
      roundConfig: PROCESS_CONFIG,
      pipelineId: "{process_name}_daily",
      tbs: "qdr:d",
      date,
      skipEnrich: false,
      maxEnrich: 20,
      dryRun: false,
    });
    return { date: result.date, companyCount: result.companyCount, durationMs: result.stats.durationMs, stats: result.stats };
  },
});
```

## Phase 4: Validate & Deploy

1. `npx tsc --noEmit` — typecheck
2. `npx trigger.dev@latest deploy` — deploy
3. Trigger test run from dashboard (empty payload)
4. Check logs for expected output
5. If validation script exists, run against GT

## Shared Pipeline Infrastructure

These modules are shared across ALL graduated processes — don't duplicate:

| Module | What it does | Modify? |
|--------|-------------|---------|
| `pipeline.ts` | 4-stage orchestrator (discover→filter→enrich→output) | No |
| `filters.ts` | Score, filter, fuzzy dedup | Add process-specific patterns only |
| `spider.ts` | Scrape with retry (20s→45s→direct) | No |
| `openai.ts` | GPT-4o-mini extraction | No |
| `domain-lookup.ts` | 3-tier domain resolution | No |
| `serper.ts` | SerperDev search | No |
| `supabase.ts` | Upsert with score-wins | No |
| `webhook.ts` | Clay webhook push | No |

## Process-Specific Parts (the only custom code)

1. **Queries** — different search strings per process
2. **Filter patterns** — different round/noise regexes
3. **Extraction prompt** — different fields to pull from articles
4. **Output schema** — different columns (if not funding)

## Env Vars

Ensure these are set in trigger.dev cloud (`npx trigger.dev env`):
- `SERPER_API_KEY` — search
- `SPIDER_API_KEY` — scraping
- `OPENAI_API_KEY` — extraction
- `SUPABASE_PROJECT_URL` — storage
- `SUPABASE_ANON_KEY` or `SUPABASE_KEY` — auth
- Process-specific webhook URLs: `CLAY_WEBHOOK_URL_{PROCESS}`, `CLAY_WEBHOOK_AUTH_{PROCESS}`

## Cost Reference

| Service | Unit Cost |
|---------|-----------|
| Serper | $0.0075/search |
| Spider | ~$0.001/page |
| OpenAI gpt-4o-mini | ~$0.15/1M in (cached prompts cheaper) |
| Trigger.dev | Free tier: 500 runs/mo |
| Supabase | Free tier |

Typical daily monitoring run: ~$0.30/run × 30 days = ~$9/mo per process.

## Graduation Checklist (for handoff)

After deploying, confirm:
- [ ] Task appears in trigger.dev dashboard
- [ ] Cron schedule is correct
- [ ] Test run completes without errors
- [ ] Data appears in Supabase
- [ ] Webhook fires to Clay (if configured)
- [ ] dryRun flag works (no output when true)
- [ ] Weekly catch-up task also deployed
