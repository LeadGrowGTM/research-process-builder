import { logger } from "@trigger.dev/sdk";
import { discolikeConfigured, discolikeProfile, discolikeUsageOk, type DiscoProfile } from "./discolike.js";
import { blitzEnrichLinkedin, blitzHqString } from "./blitz.js";
import { isDomainBlocked } from "./domain-lookup.js";
import { normalizeDomain, enrichDomainWaterfall } from "./enrich-company.js";
import { patchRowBySourceUrl } from "./supabase.js";

const SUPABASE_URL = (() => {
  const url = process.env.SUPABASE_PROJECT_URL ?? process.env.SUPABASE_URL ?? "";
  return url.startsWith("http") ? url : "";
})();
const SUPABASE_KEY =
  process.env.SUPABASE_KEY ??
  process.env.SUPABASE_SERVICE_ROLE_KEY ??
  process.env.SUPABASE_ANON_KEY ??
  "";

// DiscoLike is ~$0.18/query — 50 rows/table = ~$9 worst case per table.
// Usage gate re-checked before each table, so total run overshoot is bounded.
const MAX_ROWS_PER_TABLE = 50;

interface RetryRow {
  company_name: string;
  source_url: string;
  domain: string;
}

function daysAgoIso(days: number): string {
  return new Date(Date.now() - days * 86_400_000).toISOString().split("T")[0];
}

async function fetchRetryRows(
  table: "funding_discoveries" | "product_launches",
  domainCol: string,
  minAgeDays: number,
  maxAgeDays: number
): Promise<RetryRow[]> {
  const params =
    `select=company_name,source_url,${domainCol}` +
    `&${domainCol}=not.is.null` +
    `&enriched_at=is.null` +
    `&discovered_date=lte.${daysAgoIso(minAgeDays)}` +
    `&discovered_date=gte.${daysAgoIso(maxAgeDays)}` +
    `&order=discovered_date.asc` +
    `&limit=${MAX_ROWS_PER_TABLE}`;
  const res = await fetch(`${SUPABASE_URL}/rest/v1/${table}?${params}`, {
    headers: { apikey: SUPABASE_KEY, Authorization: `Bearer ${SUPABASE_KEY}` },
    signal: AbortSignal.timeout(15_000),
  });
  if (!res.ok) {
    logger.error(`Retry-pass fetch failed: ${table} → ${res.status}`, {
      body: (await res.text()).slice(0, 200),
    });
    return [];
  }
  const rows = (await res.json()) as Record<string, string>[];
  return rows
    .map((r) => ({
      company_name: r.company_name,
      source_url: r.source_url,
      domain: normalizeDomain(r[domainCol] ?? ""),
    }))
    .filter((r) => r.domain && r.domain.includes(".") && !isDomainBlocked(r.domain));
}

/**
 * Delayed enrichment pass. Day-0 Blitz misses sit with enriched_at NULL until
 * the row is old enough for DiscoLike's index (~30 days for tiny startups,
 * less for funded companies). Every row gets exactly ONE DiscoLike attempt —
 * hit or miss, enriched_at is stamped so the next run skips it.
 * When DiscoLike surfaces a linkedin_url, chain into Blitz (free) for
 * followers / employee range / founded year.
 */
export async function runEnrichmentRetryPass(): Promise<{
  funding: { scanned: number; enriched: number };
  ph: { scanned: number; enriched: number };
}> {
  const result = { funding: { scanned: 0, enriched: 0 }, ph: { scanned: 0, enriched: 0 } };

  if (!discolikeConfigured()) {
    logger.warn("DiscoLike not configured — skipping retry pass");
    return result;
  }
  const tables = [
    { key: "funding" as const, table: "funding_discoveries" as const, domainCol: "company_domain", minAge: 7, maxAge: 60 },
    { key: "ph" as const, table: "product_launches" as const, domainCol: "maker_website", minAge: 25, maxAge: 90 },
  ];

  for (const t of tables) {
    // Re-check spend before every table — one table can burn ~$9 on its own
    if (!(await discolikeUsageOk())) {
      logger.warn(`DiscoLike spend near max — skipping ${t.table} retry pass`);
      continue;
    }

    const rows = await fetchRetryRows(t.table, t.domainCol, t.minAge, t.maxAge);
    result[t.key].scanned = rows.length;
    logger.info(`Retry pass: ${t.table}`, { rows: rows.length });

    for (const row of rows) {
      // Free waterfall first (lg-free + Blitz) — day-0 may have missed on a
      // transient failure, or the company's LinkedIn page appeared since.
      const free = await enrichDomainWaterfall(t.table, {
        companyName: row.company_name,
        domain: row.domain,
        sourceUrl: row.source_url,
      }, row.domain);
      if (free) {
        const ok = await patchRowBySourceUrl(t.table, row.source_url, {
          ...free.patch,
          enriched_by: `retry:${free.provider}`,
          enriched_at: new Date().toISOString(),
        });
        if (ok) result[t.key].enriched++;
        continue;
      }

      let profile: DiscoProfile | null;
      try {
        profile = await discolikeProfile(row.domain);
      } catch (e) {
        // Transient network/timeout error — NOT a DiscoLike miss. Leave the row
        // unstamped so next week retries it, and don't kill the rest of the run.
        logger.warn(`DiscoLike error for ${row.domain}: ${e instanceof Error ? e.message : String(e)}`);
        continue;
      }

      if (!profile) {
        // Stamp the miss so this row is never re-queried (cost control)
        await patchRowBySourceUrl(t.table, row.source_url, {
          enriched_by: "miss:discolike",
          enriched_at: new Date().toISOString(),
        });
        continue;
      }

      const blitz = profile.linkedin_url ? await blitzEnrichLinkedin(profile.linkedin_url) : null;

      const patch: Record<string, unknown> =
        t.table === "funding_discoveries"
          ? {
              industry: blitz?.industry ?? profile.industry,
              location: (blitz && blitzHqString(blitz)) ?? profile.location,
              linkedin_url: profile.linkedin_url,
              employee_count: blitz?.employees_on_linkedin ?? profile.employees,
              employee_range: blitz?.size ?? null,
              linkedin_followers: blitz?.followers ?? null,
              company_description: blitz?.about ?? profile.description,
              founded_year: blitz?.founded_year ?? null,
              company_type: blitz?.type ?? null,
            }
          : {
              industry: blitz?.industry ?? profile.industry,
              company_location: (blitz && blitzHqString(blitz)) ?? profile.location,
              linkedin_url: profile.linkedin_url,
              employee_count: blitz?.employees_on_linkedin ?? profile.employees,
              linkedin_followers: blitz?.followers ?? null,
              company_description: blitz?.about ?? profile.description,
            };
      patch["enriched_by"] = blitz ? "discolike+blitz" : "discolike";
      patch["enriched_at"] = new Date().toISOString();

      const ok = await patchRowBySourceUrl(t.table, row.source_url, patch);
      if (ok) result[t.key].enriched++;
    }
  }

  logger.info("Retry pass complete", result);
  return result;
}
