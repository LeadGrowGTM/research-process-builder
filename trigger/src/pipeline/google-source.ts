/**
 * Corroborating public source for a raisingfi-only round, via RapidAPI Google Search
 * (google-search74). Quoted company query, then the company's own site when it has a domain.
 * Stops at the first qualifying hit. X and raisingfi links are rejected by pickSecondarySource.
 */
import { fetchProvider } from "./founders.js";
import { pickSecondarySource, secondaryQuery } from "./brave-source.js";
import type { FundingRound, RoundSource } from "./funding-rounds.js";

const GOOGLE_SEARCH_URL = "https://google-search74.p.rapidapi.com/";
const GOOGLE_SEARCH_HOST = "google-search74.p.rapidapi.com";

type GoogleItem = { url?: string; title?: string; description?: string };
type MappedResult = { url?: string; title?: string; description?: string };

export type GoogleSourceLookup = {
  source: RoundSource | null | undefined;
  queries: string[];
};

/** site: needs a dotted domain. Blank and single-label values skip the second query. */
function siteQuery(domain: string): string | null {
  const root = domain.trim().toLowerCase().replace(/^www\./, "");
  if (!root.includes(".")) return null;
  return `site:${root} (raises OR funding OR announces)`;
}

function mappedResults(items: unknown[]): MappedResult[] {
  const results: MappedResult[] = [];
  for (const item of items) {
    if (!item || typeof item !== "object") continue;
    const row = item as GoogleItem;
    results.push({ url: row.url, title: row.title, description: row.description });
  }
  return results;
}

/** One Google Search call. Undefined means non-2xx, 429, or a payload with no results array. An empty array is a miss. */
async function googleResults(query: string, apiKey: string): Promise<MappedResult[] | undefined> {
  const params = new URLSearchParams({ query, limit: "10", related_keywords: "false" });
  const { res } = await fetchProvider(`${GOOGLE_SEARCH_URL}?${params}`, {
    method: "GET",
    headers: { "x-rapidapi-key": apiKey, "x-rapidapi-host": GOOGLE_SEARCH_HOST },
  });
  if (!res || !res.ok) return undefined;
  const body = (await res.json().catch(() => null)) as { results?: unknown } | null;
  if (!body || typeof body !== "object" || Array.isArray(body)) return undefined;
  if (!Array.isArray(body.results)) return undefined;
  return mappedResults(body.results);
}

/**
 * Tries the quoted round query, then the company site. source is a hit, null when every query missed,
 * or undefined when Google Search failed (caller retries the round later). queries lists what ran.
 */
export async function findGoogleSource(round: FundingRound, apiKey: string): Promise<GoogleSourceLookup> {
  const plans = [secondaryQuery(round)];
  const site = siteQuery(round.domain);
  if (site) plans.push(site);

  const queries: string[] = [];
  for (const query of plans) {
    queries.push(query);
    const results = await googleResults(query, apiKey);
    if (!results) return { source: undefined, queries };
    const source = pickSecondarySource(round, results);
    if (source) return { source, queries };
  }
  return { source: null, queries };
}
