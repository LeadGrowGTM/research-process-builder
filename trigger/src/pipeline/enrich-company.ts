import { logger } from "@trigger.dev/sdk";
import { blitzEnrichDomain, blitzEnrichLinkedin, blitzHqString, nameMatches, blitzConfigured } from "./blitz.js";
import type { BlitzCompany } from "./blitz.js";
import { lgenrichDomain, lgHqString, lgenrichConfigured } from "./lgenrich.js";
import type { LgFirmographics } from "./lgenrich.js";
import { isDomainBlocked } from "./domain-lookup.js";
import { patchRowBySourceUrl } from "./supabase.js";
import { normalizeIndustry, normalizeOptionalInteger, normalizeOptionalText } from "./taxonomy.js";

export function normalizeDomain(raw: string): string {
  return raw
    .replace(/^https?:\/\//, "")
    .replace(/^www\./, "")
    .split(/[/?#]/)[0]
    .toLowerCase();
}

export function fundingPatchFromBlitz(linkedinUrl: string, c: BlitzCompany): Record<string, unknown> {
  const hqLocation = blitzHqString(c);
  return {
    industry: normalizeIndustry(c.industry),
    location: hqLocation,
    hq_location: hqLocation,
    linkedin_url: linkedinUrl,
    employee_count: normalizeOptionalInteger(c.employees_on_linkedin),
    employee_range: normalizeOptionalText(c.size),
    linkedin_followers: c.followers ?? null,
    company_description: c.about ?? null,
    founded_year: normalizeOptionalInteger(c.founded_year),
    company_type: c.type ?? null,
  };
}

export function phPatchFromBlitz(linkedinUrl: string, c: BlitzCompany): Record<string, unknown> {
  return {
    employee_count: c.employees_on_linkedin ?? null,
    industry: c.industry ?? null,
    company_location: blitzHqString(c),
    company_description: c.about ?? null,
    linkedin_followers: c.followers ?? null,
    linkedin_url: linkedinUrl,
  };
}

export function fundingPatchFromLg(linkedinUrl: string, f: LgFirmographics): Record<string, unknown> {
  const hqLocation = lgHqString(f);
  return {
    industry: normalizeIndustry(f.industry),
    location: hqLocation,
    hq_location: hqLocation,
    linkedin_url: linkedinUrl,
    employee_count: normalizeOptionalInteger(f.employee_count),
    employee_range: normalizeOptionalText(f.employee_count_range),
    linkedin_followers: f.follower_count ?? null,
    company_description: f.description ?? null,
    founded_year: normalizeOptionalInteger(f.founded_year),
    company_type: f.company_type ?? null,
  };
}

export function phPatchFromLg(linkedinUrl: string, f: LgFirmographics): Record<string, unknown> {
  return {
    employee_count: f.employee_count ?? null,
    industry: f.industry ?? null,
    company_location: lgHqString(f),
    company_description: f.description ?? null,
    linkedin_followers: f.follower_count ?? null,
    linkedin_url: linkedinUrl,
  };
}

export interface Day0Target {
  companyName: string;
  domain: string; // raw - normalized internally
  sourceUrl: string; // row key for PATCH
  knownLinkedin?: string | null;
}

export interface WaterfallHit {
  patch: Record<string, unknown>;
  provider: string;
}

/**
 * Provider waterfall for one domain:
 * 1. lg-free-enrichments (free, internal, live-scrape - no index lag,
 *    domain_verified trust signal kills the wrong-match problem)
 * 2. Blitz domain path (free, but name-match guard required)
 * lgenrich hits with a trusted linkedin_url but thin firmographics chain
 * into Blitz's company endpoint for the full profile.
 */
export async function enrichDomainWaterfall(
  table: "funding_discoveries" | "product_launches",
  t: Day0Target,
  domain: string
): Promise<WaterfallHit | null> {
  const isFunding = table === "funding_discoveries";

  if (lgenrichConfigured()) {
    const lg = await lgenrichDomain(domain);
    if (lg) {
      const f = lg.firmographics;
      if (f && (f.employee_count != null || f.description || (isFunding && (f.employee_count_range || f.founded_year != null || lgHqString(f))))) {
        return {
          patch: isFunding ? fundingPatchFromLg(lg.linkedin_url, f) : phPatchFromLg(lg.linkedin_url, f),
          provider: "lgenrich",
        };
      }
      // Trusted LinkedIn URL but thin scrape - let Blitz fill the profile
      const blitz = await blitzEnrichLinkedin(lg.linkedin_url);
      if (blitz) {
        return {
          patch: isFunding ? fundingPatchFromBlitz(lg.linkedin_url, blitz) : phPatchFromBlitz(lg.linkedin_url, blitz),
          provider: "lgenrich+blitz",
        };
      }
      return isFunding ? { patch: { linkedin_url: lg.linkedin_url }, provider: "lgenrich" } : null;
    }
  }

  if (!blitzConfigured()) return null;
  const hit = await blitzEnrichDomain(domain, t.knownLinkedin);
  if (!hit) return null;
  if (!nameMatches(t.companyName, hit.company.name)) {
    logger.warn("Blitz name mismatch - skipping", {
      ours: t.companyName,
      theirs: hit.company.name,
      domain,
    });
    return null;
  }
  return {
    patch: isFunding ? fundingPatchFromBlitz(hit.linkedin_url, hit.company) : phPatchFromBlitz(hit.linkedin_url, hit.company),
    provider: "blitz",
  };
}

/**
 * Day-0 enrichment pass over freshly upserted rows. Skips blocked/junk
 * domains. Rows that miss every provider stay NULL and get picked up by
 * the delayed DiscoLike retry pass (enrichment-retry-weekly).
 */
export async function day0BlitzEnrich(
  table: "funding_discoveries" | "product_launches",
  targets: Day0Target[]
): Promise<{ attempted: number; enriched: number }> {
  if (!lgenrichConfigured() && !blitzConfigured()) {
    logger.warn("No enrichment provider configured - skipping day-0 enrichment");
    return { attempted: 0, enriched: 0 };
  }

  let attempted = 0;
  let enriched = 0;

  for (const t of targets) {
    const domain = normalizeDomain(t.domain);
    // "not_enriched" placeholder and other non-domains have no dot
    if (!domain || !domain.includes(".") || isDomainBlocked(domain)) continue;
    attempted++;

    const hit = await enrichDomainWaterfall(table, t, domain);
    if (!hit) continue;

    const ok = await patchRowBySourceUrl(table, t.sourceUrl, {
      ...hit.patch,
      enriched_by: hit.provider,
      enriched_at: new Date().toISOString(),
    });
    if (ok) enriched++;
  }

  logger.info(`Day-0 enrichment complete`, { table, attempted, enriched });
  return { attempted, enriched };
}
