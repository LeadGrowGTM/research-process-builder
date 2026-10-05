import type { EnrichedRecord } from "./types.js";
import { normalizeCompanyName } from "./filters.js";
import { requestSignal } from "./request-signal.js";
import {
  logoUrlForDomain,
  normalizeIndustry,
  normalizeRoundType,
  sourceNameForUrl,
} from "./taxonomy.js";

const SUPABASE_URL = (() => {
  const url =
    process.env.SUPABASE_PROJECT_URL ?? process.env.SUPABASE_URL ?? "";
  return url.startsWith("http") ? url : "";
})();

const SUPABASE_KEY =
  process.env.SUPABASE_SERVICE_ROLE_KEY ??
  process.env.SUPABASE_KEY ??
  process.env.SUPABASE_ANON_KEY ??
  "";

function headers(prefer?: string): Record<string, string> {
  const h: Record<string, string> = {
    apikey: SUPABASE_KEY,
    Authorization: `Bearer ${SUPABASE_KEY}`,
    "Content-Type": "application/json",
    "Accept-Profile": "public",
    "Content-Profile": "public",
  };
  if (prefer) h["Prefer"] = prefer;
  return h;
}

export function isSupabaseConfigured(): boolean {
  return Boolean(SUPABASE_URL && SUPABASE_KEY);
}

export async function checkTable(tableName: string, signal?: AbortSignal): Promise<boolean> {
  if (!SUPABASE_URL) return false;
  try {
    const resp = await fetch(
      `${SUPABASE_URL}/rest/v1/${tableName}?limit=1`,
      { headers: headers(), signal: requestSignal(10_000, signal) }
    );
    return resp.status === 200;
  } catch {
    return false;
  }
}

const LEAD_SENTINELS = new Set([
  "",
  "not_stated",
  "not_enriched",
  "not_found",
  "n/a",
  "na",
  "none",
  "null",
  "unknown",
]);

function nullIfSentinel(raw: unknown): string | null {
  if (typeof raw !== "string") return null;
  const t = raw.trim();
  if (!t || LEAD_SENTINELS.has(t.toLowerCase())) return null;
  return t;
}

function websiteUrlForDomain(domain: string | null | undefined): string | null {
  if (!domain) return null;
  const d = domain.trim().toLowerCase();
  if (!d || d === "not_found" || d === "not_stated" || d === "not_enriched" || !d.includes(".")) {
    return null;
  }
  return `https://${d}`;
}

export function fundingRowFromRecord(record: EnrichedRecord, dateStr: string) {
  const round = normalizeRoundType(record.round_type);
  return {
    discovered_date: dateStr,
    company_name: record.company_name,
    company_domain: nullIfSentinel(record.company_domain),
    amount_raised: nullIfSentinel(record.amount_raised),
    round_type: round,
    source_url: record.source_url,
    source_name: sourceNameForUrl(record.source_url),
    lead_investors: nullIfSentinel(record.lead_investors),
    round_reasoning: nullIfSentinel(record.round_reasoning),
    article_text: record.article_text,
    raw_text: record.article_text,
    website_url: websiteUrlForDomain(record.company_domain),
    logo_url: logoUrlForDomain(record.company_domain),
    industry: normalizeIndustry(record.industry),
    location: nullIfSentinel(record.location),
    discovered_by_pipeline: record.discovered_by_pipeline,
    amount_raised_usd: record.amount_raised_usd ?? null,
    amount_raised_currency: record.amount_raised_currency ?? null,
    funding_date: record.funding_date ?? null,
    source_count: record.source_count,
    score: record.score,
    pipeline_version: "1.0-ts",
  };
}

export async function getRecentCompanyNames(
  tableName: string,
  days: number
): Promise<Set<string>> {
  if (!SUPABASE_URL || !SUPABASE_KEY) return new Set();

  try {
    const since = new Date();
    since.setDate(since.getDate() - days);
    const sinceStr = since.toISOString().split("T")[0];

    const url =
      `${SUPABASE_URL}/rest/v1/${tableName}` +
      `?discovered_date=gte.${sinceStr}` +
      `&select=company_name`;

    const resp = await fetch(url, {
      headers: headers(),
      signal: AbortSignal.timeout(15_000),
    });

    if (!resp.ok) return new Set();

    const rows: { company_name: string }[] = await resp.json();
    const names = new Set<string>();
    for (const row of rows) {
      if (row.company_name) {
        names.add(normalizeCompanyName(row.company_name));
      }
    }
    return names;
  } catch {
    return new Set();
  }
}

const UNKNOWN_DOMAINS = new Set(["not_found", "not_stated", "not_enriched", ""]);
const UNKNOWN_ROUNDS = new Set(["Unknown", "not_stated", "not_enriched", ""]);

async function isDomainSeenRecently(
  domain: string,
  roundType: string | null,
  tableName: string,
  lookbackDays = 90,
  signal?: AbortSignal
): Promise<boolean> {
  if (!domain || UNKNOWN_DOMAINS.has(domain)) return false;
  if (!SUPABASE_URL || !SUPABASE_KEY) return false;

  const since = new Date();
  since.setDate(since.getDate() - lookbackDays);
  const sinceStr = since.toISOString().split("T")[0];

  try {
    const resp = await fetch(
      `${SUPABASE_URL}/rest/v1/${tableName}?company_domain=eq.${encodeURIComponent(domain)}&discovered_date=gte.${sinceStr}&select=round_type,discovered_date&limit=10`,
      { headers: headers(), signal: requestSignal(10_000, signal) }
    );
    if (!resp.ok) throw new Error(`Funding dedup read failed with HTTP ${resp.status}`);
    const rows: { round_type: string }[] = await resp.json();
    if (rows.length === 0) return false;

    const newRound = normalizeRoundType(roundType) ?? "Unknown";
    for (const row of rows) {
      const existingRound = normalizeRoundType(row.round_type) ?? "Unknown";
      // Both known and different: new raise event, not a dup
      if (!UNKNOWN_ROUNDS.has(existingRound) && !UNKNOWN_ROUNDS.has(newRound) && existingRound !== newRound) {
        continue;
      }
      return true; // Same or unknown round within window: dup
    }
    return false; // All existing records have different known rounds: allow
  } catch {
    throw new Error("Funding dedup read failed");
  }
}

export class FundingWriteError extends Error {
  constructor(public readonly upserted: number) {
    super("Supabase funding write failed");
  }
}

export async function pushToSupabase(
  enriched: EnrichedRecord[],
  dateStr: string,
  tableName: string,
  signal?: AbortSignal,
  onUpsert?: (upserted: number) => void
): Promise<number> {
  if (!SUPABASE_URL || !SUPABASE_KEY) throw new Error("Supabase is not configured for funding writes");

  // Dedup within batch by company_domain (keep first = highest scored)
  const seenDomains = new Set<string>();
  const rows = enriched
    .map((r) => fundingRowFromRecord(r, dateStr))
    .filter((row) => {
      const domain = row.company_domain ?? "";
      if (UNKNOWN_DOMAINS.has(domain)) return true;
      if (seenDomains.has(domain)) return false;
      seenDomains.add(domain);
      return true;
    });

  // Cross-run dedup: skip domains seen within 90 days (unless different round)
  const filteredRows: typeof rows = [];
  for (const row of rows) {
    const seen = await isDomainSeenRecently(row.company_domain ?? "", row.round_type, tableName, 90, signal);
    if (seen) {
      console.log(`SKIP (seen <90d): ${row.company_name} (${row.company_domain})`);
    } else {
      filteredRows.push(row);
    }
  }

  let upserted = 0;
  for (const row of filteredRows) {
    try {
      const existing = await fetch(
        `${SUPABASE_URL}/rest/v1/${tableName}?source_url=eq.${encodeURIComponent(row.source_url)}&select=score,discovered_by_pipeline`,
        { headers: headers(), signal: requestSignal(10_000, signal) }
      );

      if (!existing.ok) throw new Error(`Funding source read failed with HTTP ${existing.status}`);
      if (existing.ok) {
        const data = await existing.json();
        if (Array.isArray(data) && data.length > 0) {
          const prev = data[0];
          if (row.score <= (prev.score ?? 0)) {
            const pipelines = new Set(
              (prev.discovered_by_pipeline ?? "").split(",").filter(Boolean)
            );
            pipelines.add(row.discovered_by_pipeline);
            const patched = await fetch(
              `${SUPABASE_URL}/rest/v1/${tableName}?source_url=eq.${encodeURIComponent(row.source_url)}`,
              {
                method: "PATCH",
                headers: headers(),
                body: JSON.stringify({ discovered_by_pipeline: [...pipelines].join(",") }),
                signal: requestSignal(10_000, signal),
              }
            );
            if (!patched.ok) throw new Error(`Funding pipeline patch failed with HTTP ${patched.status}`);
            upserted++;
            onUpsert?.(upserted);
            continue;
          }
          row.discovered_by_pipeline = [
            ...new Set(
              [...(prev.discovered_by_pipeline ?? "").split(",").filter(Boolean), row.discovered_by_pipeline]
            ),
          ].join(",");
        }
      }

      const resp = await fetch(
        `${SUPABASE_URL}/rest/v1/${tableName}?on_conflict=source_url`,
        {
          method: "POST",
          headers: headers("resolution=merge-duplicates"),
          body: JSON.stringify([row]),
          signal: requestSignal(15_000, signal),
        }
      );
      if (resp.ok) {
        upserted++;
        onUpsert?.(upserted);
      } else {
        throw new Error(`Funding upsert failed with HTTP ${resp.status}`);
      }
    } catch {
      throw new FundingWriteError(upserted);
    }
  }

  return upserted;
}

export async function patchRowBySourceUrl(
  tableName: string,
  sourceUrl: string,
  patch: Record<string, unknown>,
  signal?: AbortSignal
): Promise<boolean> {
  if (!SUPABASE_URL || !SUPABASE_KEY) return false;
  try {
    const resp = await fetch(
      `${SUPABASE_URL}/rest/v1/${tableName}?source_url=eq.${encodeURIComponent(sourceUrl)}`,
      {
        method: "PATCH",
        headers: headers(),
        body: JSON.stringify(patch),
        signal: requestSignal(15_000, signal),
      }
    );
    if (!resp.ok) {
      console.error(`Supabase patch failed with HTTP ${resp.status}`);
    }
    return resp.ok;
  } catch {
    console.error("Supabase patch transport failed");
    return false;
  }
}

export interface RaisingFiPushResult {
  attempted: number;
  upserted: number;
  errors: string[];
}

export async function pushRaisingFiRows<T extends Record<string, unknown>>(
  rows: T[],
  tableName: string
): Promise<RaisingFiPushResult> {
  if (!SUPABASE_URL || !SUPABASE_KEY) {
    throw new Error("Supabase is not configured (SUPABASE_PROJECT_URL / SUPABASE_KEY missing)");
  }

  const seen = new Set<string>();
  const deduped = rows.filter((row) => {
    const key = `${String(row.company_name).toLowerCase()}|${row.discovered_date}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });

  let upserted = 0;
  const errors: string[] = [];
  for (const row of deduped) {
    try {
      const resp = await fetch(
        `${SUPABASE_URL}/rest/v1/${tableName}?on_conflict=source_url`,
        {
          method: "POST",
          headers: headers("resolution=merge-duplicates"),
          body: JSON.stringify([row]),
          signal: AbortSignal.timeout(15_000),
        }
      );
      if (resp.ok) {
        upserted++;
      } else {
        const msg = `RaisingFi upsert failed with HTTP ${resp.status}`;
        console.error(msg);
        errors.push(msg);
      }
    } catch {
      const msg = "RaisingFi upsert transport failed";
      console.error(msg);
      errors.push(msg);
    }
  }

  return { attempted: deduped.length, upserted, errors };
}
