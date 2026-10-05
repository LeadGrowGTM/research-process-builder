/**
 * Corroborating public source for a raisingfi-only round, via RapidAPI Google Search
 * (google-search74). Quoted company query, then the company's own site when it has a domain.
 * Stops at the first qualifying hit. X and raisingfi links are rejected by pickSecondarySource.
 */
import { webSearch } from "./rapid-search.js";
import { pickSecondarySource, secondaryQuery } from "./brave-source.js";
import type { FundingRound, RoundSource } from "./funding-rounds.js";

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

/** One search: Google, then treg if Google fails. Undefined means both failed (retry next run). An empty array is a miss. */
async function googleResults(query: string, apiKey: string): Promise<MappedResult[] | undefined> {
  const response = await webSearch(query, { limit: 10, apiKey });
  return response ? response.results.map((item) => ({ url: item.url, title: item.title, description: item.snippet })) : undefined;
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
