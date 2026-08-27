/**
 * Signal Bank Daily Pipeline
 *
 * 7am ET: Process new unclassified funding_discoveries into signal_companies.
 *
 * Steps:
 *   1. Find funding_discoveries not yet in signal_companies
 *   2. For no-industry rows: scrape homepage via Spider.dev → extract industry
 *   3. Run icp-classification via OpenAI gpt-4.1-mini → write to signal_companies
 *   4. For new strong/moderate rows: run prospect-identification → write target_market
 *
 * Manual/weekly steps (too expensive for daily automation):
 *   - dm_pull (ai-ark-people, ~$0.02/company) → run 09_dm_pull.py locally
 *   - prospect_matcher (09_prospect_matcher.py) → run locally after dm_pull
 *   - apify_jobs_collector → run via .claude/skills/apify-linkedin-jobs
 *   - sheets_mirror → run 05_sheets_mirror.py locally
 *
 * Env vars required:
 *   SUPABASE_PROJECT_URL, SUPABASE_KEY (or SUPABASE_ANON_KEY)
 *   OPENAI_API_KEY
 *   FIRECRAWL_API_KEY (optional, graceful fallback)
 */

import { schedules, logger } from "@trigger.dev/sdk";

// ── Supabase helpers ──────────────────────────────────────────────────────────

const SUPABASE_URL = (() => {
  const u = process.env.SUPABASE_PROJECT_URL ?? process.env.SUPABASE_URL ?? "";
  return u.startsWith("http") ? u : "";
})();
const SUPABASE_KEY =
  process.env.SUPABASE_KEY ??
  process.env.SUPABASE_SERVICE_ROLE_KEY ??
  process.env.SUPABASE_ANON_KEY ??
  "";
const OPENAI_API_KEY = process.env.OPENAI_API_KEY ?? "";
const FIRECRAWL_API_KEY = process.env.FIRECRAWL_API_KEY ?? "";
const DEFAULT_SCHEMA = "leadgrow_knowledge";
// Fix-forward: only process rows discovered today or later (no backfill of 3.5-month stall)
const FIX_FORWARD_SINCE = "2026-08-27";

function sbHeaders(write = false, schema: string = DEFAULT_SCHEMA): Record<string, string> {
  const h: Record<string, string> = {
    apikey: SUPABASE_KEY,
    Authorization: `Bearer ${SUPABASE_KEY}`,
    "Content-Type": "application/json",
    "Accept-Profile": schema,
  };
  if (write) h["Content-Profile"] = schema;
  return h;
}

async function sbGet(path: string, params: Record<string, string> = {}, schema: string = DEFAULT_SCHEMA): Promise<unknown[]> {
  const qs = new URLSearchParams(params).toString();
  const url = `${SUPABASE_URL}/rest/v1/${path}${qs ? "?" + qs : ""}`;
  const resp = await fetch(url, { headers: sbHeaders(false, schema), signal: AbortSignal.timeout(20_000) });
  if (!resp.ok) {
    const snippet = await resp.text().then(t => t.slice(0, 200)).catch(() => "");
    logger.warn(`sbGet failed: ${resp.status} on ${path}`, { schema, snippet });
    return [];
  }
  const data = await resp.json();
  return Array.isArray(data) ? data : [];
}

async function sbUpsert(table: string, row: Record<string, unknown>, schema: string = DEFAULT_SCHEMA): Promise<boolean> {
  const resp = await fetch(`${SUPABASE_URL}/rest/v1/${table}?on_conflict=domain`, {
    method: "POST",
    headers: { ...sbHeaders(true, schema), Prefer: "resolution=merge-duplicates" },
    body: JSON.stringify([row]),
    signal: AbortSignal.timeout(15_000),
  });
  if (!resp.ok) {
    logger.warn(`sbUpsert failed: ${resp.status} on ${table}`, { schema });
  }
  return resp.ok;
}

// ── Firecrawl scrape ──────────────────────────────────────────────────────────

async function scrapeHomepage(domain: string): Promise<string | null> {
  if (!FIRECRAWL_API_KEY) return null;
  try {
    const resp = await fetch("https://api.firecrawl.dev/v1/scrape", {
      method: "POST",
      headers: { Authorization: `Bearer ${FIRECRAWL_API_KEY}`, "Content-Type": "application/json" },
      body: JSON.stringify({ url: `https://${domain}`, formats: ["markdown"], onlyMainContent: true }),
      signal: AbortSignal.timeout(25_000),
    });
    if (!resp.ok) return null;
    const data = await resp.json() as { success: boolean; data?: { markdown?: string } };
    const content = data.data?.markdown ?? "";
    return content.length > 150 ? content.slice(0, 8_000) : null;
  } catch {
    return null;
  }
}

// ── OpenAI helpers ────────────────────────────────────────────────────────────

async function callOpenAI(systemPrompt: string, userPrompt: string): Promise<string | null> {
  if (!OPENAI_API_KEY) return null;
  try {
    const resp = await fetch("https://api.openai.com/v1/chat/completions", {
      method: "POST",
      headers: { Authorization: `Bearer ${OPENAI_API_KEY}`, "Content-Type": "application/json" },
      body: JSON.stringify({
        model: "gpt-4.1-mini",
        temperature: 0.1,
        max_tokens: 512,
        messages: [
          { role: "system", content: systemPrompt },
          { role: "user", content: userPrompt },
        ],
      }),
      signal: AbortSignal.timeout(30_000),
    });
    if (!resp.ok) return null;
    const data = await resp.json() as { choices: { message: { content: string } }[] };
    let content = data.choices[0]?.message?.content?.trim() ?? "";
    if (content.startsWith("```")) {
      content = content.replace(/^```(?:json)?\s*/, "").replace(/\s*```$/, "");
    }
    return content;
  } catch {
    return null;
  }
}

// Inlined from auto-prompt-creator/library/icp-classification.md
const ICP_SYSTEM = `Classify this company for B2B outbound lead gen fit. Return ONLY a JSON object, no other text:
{"industry":"casual label","company_size":"startup|SMB|mid-market|enterprise","decision_makers":["title1","title2"],"pain_points":["pain1","pain2","pain3"],"icp_fit":"strong|moderate|weak","reasoning":"one sentence"}
Rules: startup <20 employees, SMB 20-200, mid-market 200-2000, enterprise 2000+. Strong = B2B service/manufacturer, clear sales pain, commercial buyers, mid-market or SMB $5M+. Moderate = has potential but structural limits. Weak = consumer, government, or no clear sales gap.`;

async function classifyICP(company: {
  company_name?: string;
  company_domain?: string;
  industry?: string;
  round_type?: string;
  amount_raised?: string;
  location?: string;
  homepage_content?: string;
}): Promise<Record<string, unknown> | null> {
  const parts: string[] = [];
  if (company.industry) parts.push(company.industry);
  if (company.round_type) parts.push(`${company.round_type} funded`);
  if (company.amount_raised) parts.push(`raised ${company.amount_raised}`);
  if (company.location) parts.push(`based in ${company.location}`);
  if (company.homepage_content) parts.push(`\nHomepage: ${company.homepage_content.slice(0, 1000)}`);
  const description = parts.join(". ") || `Recently funded company (${company.round_type ?? "unknown round"})`;

  const userPrompt = `Company: ${company.company_name ?? company.company_domain ?? "Unknown"}\nDomain: ${company.company_domain ?? ""}\nDescription: ${description}`;
  const raw = await callOpenAI(ICP_SYSTEM, userPrompt);
  if (!raw) return null;
  try {
    const match = raw.match(/\{[\s\S]*\}/);
    return match ? JSON.parse(match[0]) as Record<string, unknown> : null;
  } catch {
    return null;
  }
}

// Inlined from auto-prompt-creator/library/prospect-identification.md
const PROSPECT_SYSTEM = `Identify the target market for this B2B company. Return ONLY JSON:
{"target_market":"what type of companies buy from them (e.g. 'mid-market B2B SaaS companies needing HR tools')","buyer_titles":"typical job titles of buyers (e.g. 'Head of HR, People Ops Manager')"}
Be specific: name the buyer type + company type + what they're buying for. Not generic.`;

async function identifyTargetMarket(company: {
  company_name?: string;
  industry_label?: string;
  icp_fit_reason?: string;
  pain_points?: unknown[];
}): Promise<Record<string, string> | null> {
  const desc = [
    company.industry_label,
    company.icp_fit_reason,
    Array.isArray(company.pain_points) ? company.pain_points.join(", ") : "",
  ]
    .filter(Boolean)
    .join(". ");
  const userPrompt = `Company: ${company.company_name ?? "Unknown"}\nWhat they do: ${desc}`;
  const raw = await callOpenAI(PROSPECT_SYSTEM, userPrompt);
  if (!raw) return null;
  try {
    const match = raw.match(/\{[\s\S]*\}/);
    return match ? JSON.parse(match[0]) as Record<string, string> : null;
  } catch {
    return null;
  }
}

// ── Main task ─────────────────────────────────────────────────────────────────

export const signalBankDaily = schedules.task({
  id: "signal-bank-daily",
  cron: {
    pattern: "0 7 * * *",
    timezone: "America/New_York",
  },
  maxDuration: 600,
  retry: {
    maxAttempts: 2,
    factor: 2,
    minTimeoutInMs: 30_000,
    maxTimeoutInMs: 120_000,
  },

  run: async (payload) => {
    const date = payload.timestamp.toISOString().split("T")[0];
    logger.info("signal-bank-daily starting", { date });

    if (!SUPABASE_URL || !SUPABASE_KEY) {
      logger.error("Supabase not configured — skipping");
      return { error: "missing_supabase_config" };
    }
    if (!OPENAI_API_KEY) {
      logger.error("OPENAI_API_KEY not set — skipping");
      return { error: "missing_openai_key" };
    }

    // Check signal_companies table exists
    const tableCheck = await sbGet("signal_companies", { limit: "1" });
    if (!Array.isArray(tableCheck)) {
      logger.warn("signal_companies table not found — run signal_companies.sql in Supabase first");
      return { error: "signal_companies_table_missing" };
    }

    // ── Step 1: Find funding_discoveries not yet in signal_companies ──────────
    const MAX_PER_RUN = 50; // cost gate: ~$0.015 for 50 rows
    // funding_discoveries is in schema "public", not leadgrow_knowledge
    const allFunding = await sbGet("funding_discoveries", {
      select: "company_name,company_domain,industry,round_type,amount_raised,location,discovered_date",
      company_domain: "not.is.null",
      order: "discovered_date.desc",
      limit: "500",
    }, "public") as Array<Record<string, string>>;

    const existingDomains = new Set(
      (await sbGet("signal_companies", { select: "domain", limit: "2000" }) as Array<{ domain: string }>)
        .map((r) => r.domain)
    );

    const toProcess = allFunding
      .filter(
        (r) =>
          r.company_domain &&
          !r.company_domain.includes("not_found") &&
          !existingDomains.has(r.company_domain) &&
          r.discovered_date >= FIX_FORWARD_SINCE  // No backfill of 3.5-month stall; operator directive
      )
      .slice(0, MAX_PER_RUN);

    logger.info("signal_companies gap", {
      fundingTotal: allFunding.length,
      alreadyIn: existingDomains.size,
      toProcess: toProcess.length,
    });

    let classified = 0;
    let scraped = 0;
    let targetMarketsSet = 0;

    // ── Step 2+3: Scrape + classify ──────────────────────────────────────────
    for (const row of toProcess) {
      const domain = row.company_domain;
      let homepageContent: string | null = null;

      // Spider scrape for no-industry companies
      if (!row.industry && FIRECRAWL_API_KEY) {
        homepageContent = await scrapeHomepage(domain);
        if (homepageContent) scraped++;
      }

      const result = await classifyICP({
        company_name: row.company_name,
        company_domain: domain,
        industry: row.industry,
        round_type: row.round_type,
        amount_raised: row.amount_raised,
        location: row.location,
        homepage_content: homepageContent ?? undefined,
      });

      if (!result) continue;

      const fit = String(result.icp_fit ?? "weak");
      let signal_type = "funded";
      const rt = (row.round_type ?? "").toLowerCase();
      if (rt.includes("series a")) signal_type = "series_a";
      else if (rt.includes("series b")) signal_type = "series_b";
      else if (rt.includes("series c")) signal_type = "series_c";
      else if (rt.includes("seed")) signal_type = "seed";

      const ok = await sbUpsert("signal_companies", {
        domain,
        company_name: row.company_name,
        signal_type,
        signal_date: row.discovered_date,
        round_type: row.round_type,
        amount_raised: row.amount_raised,
        icp_fit: fit,
        icp_fit_reason: String(result.reasoning ?? ""),
        industry_label: String(result.industry ?? ""),
        company_size: String(result.company_size ?? ""),
        decision_makers: result.decision_makers ?? [],
        pain_points: result.pain_points ?? [],
        homepage_analysis: homepageContent
          ? { homepage_summary: homepageContent.slice(0, 500) }
          : null,
        homepage_scraped: homepageContent !== null,
        source: "funding_discoveries",
      });

      if (ok) classified++;
    }

    // ── Step 4: Target markets for new strong/moderate rows ──────────────────
    const needsTargetMarket = await sbGet("signal_companies", {
      select: "domain,company_name,industry_label,icp_fit_reason,pain_points",
      icp_fit: "in.(strong,moderate)",
      target_market: "is.null",
      limit: "50",
    }) as Array<Record<string, unknown>>;

    for (const row of needsTargetMarket) {
      const tm = await identifyTargetMarket({
        company_name: String(row.company_name ?? ""),
        industry_label: String(row.industry_label ?? ""),
        icp_fit_reason: String(row.icp_fit_reason ?? ""),
        pain_points: Array.isArray(row.pain_points) ? row.pain_points as unknown[] : [],
      });

      if (!tm) continue;

      await fetch(
        `${SUPABASE_URL}/rest/v1/signal_companies?domain=eq.${encodeURIComponent(String(row.domain))}`,
        {
          method: "PATCH",
          headers: sbHeaders(true, DEFAULT_SCHEMA),
          body: JSON.stringify({
            target_market: tm.target_market,
            buyer_titles: tm.buyer_titles,
          }),
          signal: AbortSignal.timeout(10_000),
        }
      );
      targetMarketsSet++;
    }

    const summary = {
      date,
      toProcess: toProcess.length,
      classified,
      scraped,
      targetMarketsSet,
    };

    logger.info("signal-bank-daily complete", summary);
    return summary;
  },
});
