import { logger } from "@trigger.dev/sdk";
import { setTimeout as sleep } from "node:timers/promises";
import { requestSignal } from "./request-signal.js";

const BLITZ_BASE = "https://api.blitz-api.ai";
const BLITZ_KEY = process.env.BLITZ_API_KEY_MITCHELL ?? process.env.BLITZ_API_KEY ?? "";

// Blitz hard limit: 5 req/sec. Serialize calls with 250ms spacing.

export interface BlitzCompany {
  linkedin_url?: string | null;
  name?: string | null;
  about?: string | null;
  industry?: string | null;
  type?: string | null;
  size?: string | null; // e.g. "51-200"
  employees_on_linkedin?: number | null;
  followers?: number | null;
  founded_year?: number | null;
  hq?: { city?: string | null; state?: string | null; country_name?: string | null } | null;
}

export function blitzConfigured(): boolean {
  return Boolean(BLITZ_KEY);
}

async function blitzPost(path: string, body: unknown, retried = false, signal?: AbortSignal): Promise<any | null> {
  await sleep(250, undefined, { signal });
  const res = await fetch(`${BLITZ_BASE}${path}`, {
    method: "POST",
    headers: { "x-api-key": BLITZ_KEY, "content-type": "application/json" },
    body: JSON.stringify(body),
    signal: requestSignal(30_000, signal),
  });
  if (res.status === 429 && !retried) {
    logger.warn("Blitz 429 - waiting 60s once");
    await sleep(60_000, undefined, { signal });
    return blitzPost(path, body, true, signal);
  }
  if (!res.ok) {
    logger.warn(`Blitz ${path} -> HTTP ${res.status}`);
    return null;
  }
  return res.json();
}

/**
 * Wrong-match guard. Blitz's domain field echoes the input (its DB owns the
 * mapping), so domain comparison can't catch bad mappings like
 * mesoware.com -> "Meso America Inc". Compare names instead: any shared token
 * (len >= 3) or prefix containment counts as a match.
 */
export function nameMatches(ours: string, theirs: string | null | undefined): boolean {
  if (!theirs) return false;
  const norm = (s: string) =>
    s.toLowerCase().replace(/[^a-z0-9 ]/g, " ").split(/\s+/).filter((t) => t.length >= 3);
  const a = norm(ours);
  const b = norm(theirs);
  const aJoined = a.join("");
  const bJoined = b.join("");
  if (aJoined && bJoined && (aJoined.includes(bJoined) || bJoined.includes(aJoined))) return true;
  return a.some((t) => b.some((u) => u.includes(t) || t.includes(u)));
}

export function blitzHqString(c: BlitzCompany): string | null {
  const parts = [c.hq?.city, c.hq?.state, c.hq?.country_name].filter(Boolean);
  return parts.length ? parts.join(", ") : null;
}

/**
 * domain → company_linkedin_url → full company profile.
 * Pass knownLinkedin to skip the domain-to-linkedin hop (and its wrong-match risk).
 * Returns null on no match or API failure.
 */
export async function blitzEnrichDomain(
  domain: string,
  knownLinkedin?: string | null,
  signal?: AbortSignal
): Promise<{ linkedin_url: string; company: BlitzCompany } | null> {
  if (!BLITZ_KEY) return null;
  let linkedinUrl = knownLinkedin ?? null;
  if (!linkedinUrl) {
    const d2l = await blitzPost("/v2/enrichment/domain-to-linkedin", { domain }, false, signal);
    if (!d2l?.found || !d2l.company_linkedin_url) return null;
    linkedinUrl = d2l.company_linkedin_url;
  }
  const ce = await blitzPost("/v2/enrichment/company", { company_linkedin_url: linkedinUrl }, false, signal);
  if (!ce?.found || !ce.company) return null;
  return { linkedin_url: linkedinUrl!, company: ce.company as BlitzCompany };
}

/** LinkedIn company URL → full profile (used by retry pass to chain DiscoLike's linkedin_url). */
export async function blitzEnrichLinkedin(linkedinUrl: string, signal?: AbortSignal): Promise<BlitzCompany | null> {
  if (!BLITZ_KEY) return null;
  const ce = await blitzPost("/v2/enrichment/company", { company_linkedin_url: linkedinUrl }, false, signal);
  if (!ce?.found || !ce.company) return null;
  return ce.company as BlitzCompany;
}
