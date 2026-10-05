import { logger } from "@trigger.dev/sdk";

// lg-free-enrichments - internal Cloud Run service (free). Live homepage-scrape
// → LinkedIn extract, so no index lag on brand-new companies.
// Spec: https://gist.github.com/charlesdr13/1f5f7c70c9d757685957a880941e77a2
const LGENRICH_BASE = "https://lg-linkedin-enrich-l6qeugwwca-uc.a.run.app";
const LGENRICH_KEY = process.env.LG_FREE_ENRICHMENTS_API_KEY ?? "";

export interface LgFirmographics {
  linkedin_url: string | null;
  name: string | null;
  description: string | null;
  employee_count: number | null;
  employee_count_range: string | null;
  follower_count: number | null;
  hq_city: string | null;
  hq_region: string | null;
  hq_country: string | null;
  industry: string | null;
  company_type: string | null;
  founded_year: number | null;
}

export interface LgEnrichResult {
  linkedin_url: string;
  trusted: boolean; // domain_verified or resolved via homepage link
  firmographics: LgFirmographics | null;
}

export function lgenrichConfigured(): boolean {
  return Boolean(LGENRICH_KEY);
}

export function lgHqString(f: LgFirmographics): string | null {
  const parts = [f.hq_city, f.hq_region, f.hq_country].filter(Boolean);
  return parts.length ? parts.join(", ") : null;
}

/**
 * domain → LinkedIn URL + firmographics. `trusted` is the service's own
 * verification (LinkedIn link found on the company's homepage, or the
 * LinkedIn page links back to the domain) - only trust data when true.
 * Returns null on miss, untrusted resolution, or API failure.
 */
export async function lgenrichDomain(domain: string): Promise<LgEnrichResult | null> {
  if (!LGENRICH_KEY) return null;
  try {
    const res = await fetch(`${LGENRICH_BASE}/enrich/linkedin`, {
      method: "POST",
      headers: { "x-api-key": LGENRICH_KEY, "content-type": "application/json" },
      body: JSON.stringify({ domain }),
      signal: AbortSignal.timeout(90_000), // live scrape - can be slow
    });
    if (!res.ok) {
      logger.warn(`lgenrich → HTTP ${res.status}`, { domain });
      return null;
    }
    const d = (await res.json()) as {
      linkedin_url?: string | null;
      domain_verified?: boolean;
      resolution_method?: string | null;
      firmographics?: LgFirmographics | null;
      error?: string | null;
    };
    if (d.error || !d.linkedin_url) return null;
    const trusted = d.domain_verified === true || d.resolution_method === "homepage_link";
    if (!trusted) {
      logger.warn("lgenrich resolution untrusted - skipping", {
        domain,
        resolution_method: d.resolution_method,
      });
      return null;
    }
    return {
      linkedin_url: d.linkedin_url,
      trusted,
      firmographics: d.firmographics ?? null,
    };
  } catch {
    logger.warn("lgenrich transport or response failure", { domain });
    return null;
  }
}
