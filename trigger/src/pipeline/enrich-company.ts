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

/** Public profile fields these free providers can supply. `products` is never returned here. */
export const PROFILE_FIELDS = [
  "description",
  "products",
  "industry",
  "headcount",
  "headcount_range",
  "hq",
  "founded_year",
  "company_linkedin",
] as const;

export type ProfileField = (typeof PROFILE_FIELDS)[number];

export type FieldCoverage = {
  recordsAttempted: number;
  recordsWritten: number;
  present: Record<ProfileField, number>;
  omitted: Record<ProfileField, number>;
};

function emptyCounts(): Record<ProfileField, number> {
  return {
    description: 0,
    products: 0,
    industry: 0,
    headcount: 0,
    headcount_range: 0,
    hq: 0,
    founded_year: 0,
    company_linkedin: 0,
  };
}

export function emptyFieldCoverage(): FieldCoverage {
  return { recordsAttempted: 0, recordsWritten: 0, present: emptyCounts(), omitted: emptyCounts() };
}

function withoutContacts(value: string): string {
  return value
    .replace(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/gi, " ")
    .replace(/(?:\+|00)?\d[\d\s().-]{8,}\d/g, " ")
    .replace(/\s{2,}/g, " ")
    .trim();
}

function publicSentence(raw: unknown): string | null {
  const text = normalizeOptionalText(raw);
  if (!text) return null;
  return normalizeOptionalText(withoutContacts(text));
}

function foundedYear(raw: unknown): number | null {
  const year = normalizeOptionalInteger(raw);
  if (year == null) return null;
  const max = new Date().getUTCFullYear() + 1;
  if (year < 1800 || year > max) return null;
  return year;
}

function cleanHq(raw: string | null): string | null {
  if (!raw) return null;
  const parts = raw.split(",").map((part) => normalizeOptionalText(part)).filter((part): part is string => !!part);
  return parts.length ? parts.join(", ") : null;
}

/** https LinkedIn company, school, or showcase URL. Person profiles and junk are omitted. */
export function canonicalCompanyLinkedin(raw: unknown): string | null {
  const text = normalizeOptionalText(raw);
  if (!text) return null;
  try {
    const url = new URL(text);
    if ((url.protocol !== "http:" && url.protocol !== "https:") || url.username || url.password) return null;
    const host = url.hostname.toLowerCase();
    if (host !== "linkedin.com" && host !== "www.linkedin.com") return null;
    const parts = url.pathname.split("/").filter(Boolean);
    if (parts.length < 2) return null;
    const kind = parts[0].toLowerCase();
    if (kind !== "company" && kind !== "school" && kind !== "showcase") return null;
    const slug = parts[1].toLowerCase();
    if (!/^[a-z0-9][a-z0-9._-]{0,120}$/.test(slug)) return null;
    return `https://${host}/${kind}/${slug}`;
  } catch {
    return null;
  }
}

function definedPatch(fields: Record<string, unknown>): Record<string, unknown> {
  const patch: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(fields)) {
    if (value == null || value === "") continue;
    patch[key] = value;
  }
  return patch;
}

export function profileFieldCoverage(patch: Record<string, unknown>): { present: ProfileField[]; omitted: ProfileField[] } {
  const has = (key: string) => {
    const value = patch[key];
    return value != null && value !== "";
  };
  const flags: Record<ProfileField, boolean> = {
    description: has("company_description"),
    products: has("products"),
    industry: has("industry"),
    headcount: has("employee_count"),
    headcount_range: has("employee_range"),
    hq: has("hq_location") || has("company_location"),
    founded_year: has("founded_year"),
    company_linkedin: has("linkedin_url"),
  };
  return {
    present: PROFILE_FIELDS.filter((field) => flags[field]),
    omitted: PROFILE_FIELDS.filter((field) => !flags[field]),
  };
}

export function fundingPatchFromBlitz(linkedinUrl: string, c: BlitzCompany): Record<string, unknown> {
  const hqLocation = cleanHq(blitzHqString(c));
  return definedPatch({
    industry: normalizeIndustry(c.industry),
    location: hqLocation,
    hq_location: hqLocation,
    linkedin_url: canonicalCompanyLinkedin(linkedinUrl),
    employee_count: normalizeOptionalInteger(c.employees_on_linkedin),
    employee_range: normalizeOptionalText(c.size),
    linkedin_followers: normalizeOptionalInteger(c.followers),
    company_description: publicSentence(c.about),
    founded_year: foundedYear(c.founded_year),
    company_type: publicSentence(c.type),
  });
}

export function phPatchFromBlitz(linkedinUrl: string, c: BlitzCompany): Record<string, unknown> {
  return definedPatch({
    employee_count: normalizeOptionalInteger(c.employees_on_linkedin),
    industry: publicSentence(c.industry),
    company_location: cleanHq(blitzHqString(c)),
    company_description: publicSentence(c.about),
    linkedin_followers: normalizeOptionalInteger(c.followers),
    linkedin_url: canonicalCompanyLinkedin(linkedinUrl),
  });
}

export function fundingPatchFromLg(linkedinUrl: string, f: LgFirmographics): Record<string, unknown> {
  const hqLocation = cleanHq(lgHqString(f));
  return definedPatch({
    industry: normalizeIndustry(f.industry),
    location: hqLocation,
    hq_location: hqLocation,
    linkedin_url: canonicalCompanyLinkedin(linkedinUrl),
    employee_count: normalizeOptionalInteger(f.employee_count),
    employee_range: normalizeOptionalText(f.employee_count_range),
    linkedin_followers: normalizeOptionalInteger(f.follower_count),
    company_description: publicSentence(f.description),
    founded_year: foundedYear(f.founded_year),
    company_type: publicSentence(f.company_type),
  });
}

export function phPatchFromLg(linkedinUrl: string, f: LgFirmographics): Record<string, unknown> {
  return definedPatch({
    employee_count: normalizeOptionalInteger(f.employee_count),
    industry: publicSentence(f.industry),
    company_location: cleanHq(lgHqString(f)),
    company_description: publicSentence(f.description),
    linkedin_followers: normalizeOptionalInteger(f.follower_count),
    linkedin_url: canonicalCompanyLinkedin(linkedinUrl),
  });
}

function lgHasFirmographics(f: LgFirmographics, isFunding: boolean): boolean {
  if (normalizeOptionalInteger(f.employee_count) != null || publicSentence(f.description)) return true;
  if (!isFunding) return false;
  return Boolean(normalizeOptionalText(f.employee_count_range) || foundedYear(f.founded_year) != null || cleanHq(lgHqString(f)));
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
  /** Fields actually copied. Omitted fields were unknown and are not written. */
  present: ProfileField[];
  omitted: ProfileField[];
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

  const asHit = (patch: Record<string, unknown>, provider: string): WaterfallHit | null => {
    if (Object.keys(patch).length === 0) return null;
    const coverage = profileFieldCoverage(patch);
    return { patch, provider, present: coverage.present, omitted: coverage.omitted };
  };

  if (lgenrichConfigured()) {
    const lg = await lgenrichDomain(domain);
    if (lg) {
      const linkedin = canonicalCompanyLinkedin(lg.linkedin_url);
      const f = lg.firmographics;
      if (f && lgHasFirmographics(f, isFunding)) {
        return asHit(isFunding ? fundingPatchFromLg(lg.linkedin_url, f) : phPatchFromLg(lg.linkedin_url, f), "lgenrich");
      }
      // Trusted LinkedIn URL but no usable firmographics. Blitz may fill the profile.
      const blitz = linkedin ? await blitzEnrichLinkedin(linkedin) : null;
      if (blitz && linkedin) {
        const patch = isFunding ? fundingPatchFromBlitz(linkedin, blitz) : phPatchFromBlitz(linkedin, blitz);
        const hit = asHit(patch, "lgenrich+blitz");
        if (hit) return hit;
      }
      // Funding keeps a verified company LinkedIn. Product launches stay unstamped so the retry pass can continue.
      if (isFunding && linkedin) return asHit({ linkedin_url: linkedin }, "lgenrich");
      return null;
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
  return asHit(
    isFunding ? fundingPatchFromBlitz(hit.linkedin_url, hit.company) : phPatchFromBlitz(hit.linkedin_url, hit.company),
    "blitz"
  );
}

/**
 * Day-0 enrichment pass over freshly upserted rows. Skips blocked/junk
 * domains. Rows that miss every provider stay NULL and get picked up by
 * the delayed DiscoLike retry pass (enrichment-retry-weekly).
 */
export async function day0BlitzEnrich(
  table: "funding_discoveries" | "product_launches",
  targets: Day0Target[],
  concurrency = 1
): Promise<{ attempted: number; enriched: number; providers: "available" | "unavailable"; coverage: FieldCoverage }> {
  const coverage = emptyFieldCoverage();
  if (!lgenrichConfigured() && !blitzConfigured()) {
    logger.warn("No enrichment provider configured - skipping day-0 enrichment");
    return { attempted: 0, enriched: 0, providers: "unavailable", coverage };
  }

  let attempted = 0;
  let enriched = 0;

  const enrichOne = async (t: Day0Target) => {
    const domain = normalizeDomain(t.domain);
    // "not_enriched" placeholder and other non-domains have no dot
    if (!domain || !domain.includes(".") || isDomainBlocked(domain)) return;
    attempted++;
    coverage.recordsAttempted++;

    const hit = await enrichDomainWaterfall(table, t, domain);
    const present = new Set(hit?.present ?? []);
    for (const field of PROFILE_FIELDS) {
      if (present.has(field)) coverage.present[field]++;
      else coverage.omitted[field]++;
    }
    if (!hit) return;

    const ok = await patchRowBySourceUrl(table, t.sourceUrl, {
      ...hit.patch,
      enriched_by: hit.provider,
      enriched_at: new Date().toISOString(),
    });
    if (ok) {
      enriched++;
      coverage.recordsWritten++;
    }
  };

  for (let i = 0; i < targets.length; i += concurrency) {
    await Promise.all(targets.slice(i, i + concurrency).map(enrichOne));
  }

  logger.info(`Day-0 enrichment complete`, { table, attempted, enriched, coverage });
  return { attempted, enriched, providers: "available", coverage };
}
