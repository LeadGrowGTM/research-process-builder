import { fetchProvider } from "./founders.js";

export type WebSearchResult = { url: string; title: string; snippet: string };
export type WebSearchResponse = { results: WebSearchResult[]; provider: "google" | "brave" };
export type WebSearchOptions = { limit: number; after?: Date | string; apiKey: string; fallback?: boolean };

const GOOGLE_URL = "https://google-search74.p.rapidapi.com/";
const GOOGLE_HOST = "google-search74.p.rapidapi.com";
const BRAVE_URL = "https://brave-web-search.p.rapidapi.com/search";
const BRAVE_HOST = "brave-web-search.p.rapidapi.com";

function mapResults(items: unknown[]): WebSearchResult[] {
  return items.filter((item): item is Record<string, unknown> => !!item && typeof item === "object")
    .map((item) => ({ url: String(item.url ?? ""), title: String(item.title ?? ""), snippet: String(item.description ?? "") }));
}

async function request(url: string, host: string, apiKey: string, signal?: AbortSignal): Promise<Response | undefined> {
  const { res } = await fetchProvider(url, { method: "GET", headers: { "x-rapidapi-key": apiKey, "x-rapidapi-host": host }, signal });
  return res?.ok ? res : undefined;
}

export async function webSearch(query: string, options: WebSearchOptions): Promise<WebSearchResponse | undefined> {
  const limit = Math.min(30, Math.max(1, Math.floor(options.limit)));
  const after = options.after instanceof Date ? options.after.toISOString().slice(0, 10) : options.after;
  const q = `${query}${after ? ` after:${after}` : ""}`;
  const googleParams = new URLSearchParams({ query: q, limit: String(limit), related_keywords: "false" });
  try {
    const response = await request(`${GOOGLE_URL}?${googleParams}`, GOOGLE_HOST, options.apiKey, AbortSignal.timeout(15_000));
    if (response) {
      const body = await response.json().catch(() => null) as { results?: unknown } | null;
      if (body && Array.isArray(body.results)) return { results: mapResults(body.results), provider: "google" };
    }
  } catch { /* Try the configured fallback. */ }
  if (options.fallback === false) return undefined;
  const braveParams = new URLSearchParams({ q, count: String(limit) });
  try {
    const response = await request(`${BRAVE_URL}?${braveParams}`, BRAVE_HOST, options.apiKey, AbortSignal.timeout(15_000));
    if (!response) return undefined;
    const body = await response.json().catch(() => null) as { results?: unknown } | null;
    if (!body || !Array.isArray(body.results)) return undefined;
    return { results: mapResults(body.results), provider: "brave" };
  } catch { return undefined; }
}
