import { logger, task } from "@trigger.dev/sdk";
import { companyDomain } from "./pipeline/additional-signals.js";
import { companyDomainBudget, companyDomainCacheKey, fillCompanyDomains, type CompanyDomainRow } from "./pipeline/company-domains.js";
import { fetchUrl } from "./pipeline/scrape.js";
import { validateDomainSemantic } from "./pipeline/openai.js";
import { isPublicHttpsUrl, logoUrlForDomain, normalizeOptionalText } from "./pipeline/taxonomy.js";

export interface SignalDomainBackfillPayload {
  table: "funding_discoveries" | "product_launches";
  limit?: number;
  dryRun?: boolean;
}

const MISSING_DOMAINS = "(company_domain.is.null,company_domain.eq.,company_domain.in.(not_found,not_enriched,not_stated))";
const MAX_ROWS = 50;
const RUN_BUDGET_MS = 540_000;
const IO_TIMEOUT_MS = 10_000;

interface StoredRow extends CompanyDomainRow {
  id: number;
  raw_text?: string | null;
  tagline?: string | null;
  company_description?: string | null;
  company_location?: string | null;
  website_url?: string | null;
  logo_url?: string | null;
}

export async function runSignalDomainBackfill(payload: SignalDomainBackfillPayload) {
  if (!["funding_discoveries", "product_launches"].includes(payload.table)) throw new Error("Unsupported backfill table");
  if (payload.limit !== undefined && (!Number.isInteger(payload.limit) || payload.limit < 1)) throw new Error("limit must be a positive integer");
  if (payload.dryRun !== undefined && typeof payload.dryRun !== "boolean") throw new Error("dryRun must be a boolean");
  const limit = Math.min(MAX_ROWS, payload.limit ?? 25);
  const dryRun = payload.dryRun ?? true;
  const deadlineAt = Date.now() + RUN_BUDGET_MS;
  const root = (process.env.SUPABASE_PROJECT_URL ?? process.env.SUPABASE_URL ?? "").replace(/\/+$/, "");
  const key = process.env.SUPABASE_SERVICE_ROLE_KEY ?? process.env.SUPABASE_KEY ?? "";
  if (!root || !key) throw new Error("Supabase service credentials not configured");
  const headers = { apikey: key, Authorization: `Bearer ${key}`, "Content-Type": "application/json", "Accept-Profile": "public", "Content-Profile": "public" };
  const since = new Date(Date.now() - 90 * 86_400_000).toISOString().slice(0, 10);
  const today = new Date().toISOString().slice(0, 10);
  const query = new URLSearchParams({ select: "*", discovered_date: `gte.${since}`, and: `(discovered_date.lte.${today})`, or: MISSING_DOMAINS, order: "discovered_date.desc,id.asc", limit: String(limit) });
  const response = await fetch(`${root}/rest/v1/${payload.table}?${query}`, { headers, signal: AbortSignal.timeout(IO_TIMEOUT_MS) });
  if (!response.ok) throw new Error(`Backfill read failed: HTTP ${response.status}`);
  const rows: unknown = await response.json();
  if (!Array.isArray(rows)) throw new Error("Backfill read returned invalid rows");
  const budget = companyDomainBudget(deadlineAt - IO_TIMEOUT_MS, Math.min(20, limit));
  let processed = 0;
  let resolved = 0;
  let updated = 0;
  let failed = 0;
  const proposals: Array<{ id: number; company: string; domain: string | null }> = [];
  for (const raw of (rows as StoredRow[]).slice(0, limit)) {
    if (Date.now() + IO_TIMEOUT_MS + 5_000 > deadlineAt) break;
    if (!Number.isSafeInteger(raw?.id) || typeof raw.company_name !== "string" || typeof raw.source_url !== "string") {
      failed++;
      continue;
    }
    if (companyDomain(raw.company_domain)) continue;
    let row: StoredRow = {
      ...raw,
      maker_website: normalizeOptionalText(raw.maker_website),
      company_website: normalizeOptionalText(raw.company_website),
      article_text: normalizeOptionalText(raw.article_text) || normalizeOptionalText(raw.raw_text),
      description: normalizeOptionalText(raw.description) || normalizeOptionalText(raw.tagline) || normalizeOptionalText(raw.company_description),
      industry: normalizeOptionalText(raw.industry),
      location: normalizeOptionalText(raw.location) || normalizeOptionalText(raw.company_location),
    };
    // Stored Evidence first. A bounded Spider scrape is only needed when there is no article text or known website.
    if (!row.article_text && !companyDomain(row.maker_website) && !budget.cache.has(companyDomainCacheKey(row)) && isPublicHttpsUrl(row.source_url)) {
      try {
        row = { ...row, article_text: await fetchUrl(row.source_url, { maxChars: 20_000, deadlineAt: Math.min(budget.deadlineAt, Date.now() + 30_000) }) };
      } catch { /* Search can still resolve the company when the source is unavailable. */ }
    }
    const [filled] = await fillCompanyDomains([row], budget);
    let domain = filled.company_domain;
    if (payload.table === "funding_discoveries" && domain && row.article_text) {
      if (Date.now() >= budget.deadlineAt) break;
      const validation = await validateDomainSemantic(row.source_url, row.company_name, domain, row.article_text, budget.deadlineAt);
      if (Date.now() >= budget.deadlineAt) break;
      if (validation.status === "Wrong") domain = companyDomain(validation.correctDomain) || null;
      else if (validation.status !== "Correct") domain = null;
    }
    processed++;
    proposals.push({ id: row.id, company: row.company_name, domain });
    logger.info("Signal domain proposal", { table: payload.table, dryRun, ...proposals[proposals.length - 1] });
    if (!domain) continue;
    resolved++;
    if (dryRun || Date.now() + IO_TIMEOUT_MS > deadlineAt) continue;
    const patch: Record<string, unknown> = { company_domain: domain };
    if (payload.table === "funding_discoveries") {
      if (!normalizeOptionalText(raw.website_url)) patch.website_url = `https://${domain}`;
      if (!normalizeOptionalText(raw.logo_url)) patch.logo_url = logoUrlForDomain(domain);
    }
    try {
      const write = await fetch(`${root}/rest/v1/${payload.table}?id=eq.${row.id}&or=${MISSING_DOMAINS}`, {
        method: "PATCH", headers: { ...headers, Prefer: "return=representation" }, body: JSON.stringify(patch), signal: AbortSignal.timeout(IO_TIMEOUT_MS),
      });
      if (!write.ok) throw new Error(`HTTP ${write.status}`);
      const written: unknown = await write.json();
      if (!Array.isArray(written)) throw new Error("Invalid write response");
      updated += written.length;
    } catch (error) {
      failed++;
      logger.warn("Signal domain update failed", { id: row.id, error: error instanceof Error ? error.message : String(error) });
    }
  }
  const result = { table: payload.table, dryRun, processed, resolved, updated, failed, remainingLookups: budget.remainingLookups, stoppedEarly: processed + failed < rows.length, proposals };
  logger.info("Signal domain backfill complete", result);
  return result;
}

export const signalDomainBackfill = task({
  id: "signal-domain-backfill",
  maxDuration: 600,
  retry: { maxAttempts: 1 },
  run: runSignalDomainBackfill,
});
