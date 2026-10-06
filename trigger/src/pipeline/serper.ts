import type { QueryDef, RawResult } from "./types.js";
import { webSearch } from "./rapid-search.js";
import { fetchProvider } from "./founders.js";

interface SerperOrganic {
  title?: string;
  link?: string;
  snippet?: string;
}

export async function searchSerper(
  query: string,
  num: number,
  tbs: string,
  deadlineAt?: number
): Promise<SerperOrganic[]> {
  const response = await webSearch(query, { limit: num, after: freshnessDate(tbs), apiKey: process.env.RAPID_API_KEY ?? "", deadlineAt });
  if (!response) throw new Error("RapidAPI Google and Brave searches failed");
  return response.results.map(({ url, title, snippet }) => ({ link: url, title, snippet }));
}

function freshnessDate(tbs: string, now = new Date()): string | undefined {
  const match = tbs.match(/qdr:(d|w|m|y)/i);
  if (!match) return undefined;
  const days = ({ d: 1, w: 7, m: 30, y: 365 } as const)[match[1].toLowerCase() as "d" | "w" | "m" | "y"];
  const date = new Date(now);
  date.setUTCDate(date.getUTCDate() - days);
  return date.toISOString().slice(0, 10);
}

export async function runSingleQuery(
  qdef: QueryDef,
  tbs: string
): Promise<{ queryId: string; desc: string; results: RawResult[]; error?: string }> {
  try {
    const items = await searchSerper(qdef.query, qdef.num, tbs);

    const results: RawResult[] = items.map((item) => {
      const link = item.link ?? "";
      const domain = link.includes("://") ? new URL(link).hostname : "";
      return {
        company_name_raw: "",
        amount_raw: "",
        round_type_raw: "",
        source_url: link,
        source_domain: domain,
        snippet: (item.snippet ?? "").slice(0, 300),
        title: item.title ?? "",
        query_source: qdef.id,
      };
    });

    return { queryId: qdef.id, desc: qdef.desc, results };
  } catch (e) {
    return {
      queryId: qdef.id,
      desc: qdef.desc,
      results: [],
      error: e instanceof Error ? e.message : String(e),
    };
  }
}

export async function runDiscovery(
  queries: QueryDef[],
  tbs: string
): Promise<RawResult[]> {
  const results = await Promise.all(
    queries.map((q) => runSingleQuery(q, tbs))
  );

  const allResults: RawResult[] = [];
  for (const r of results) {
    allResults.push(...r.results);
  }

  return allResults;
}
