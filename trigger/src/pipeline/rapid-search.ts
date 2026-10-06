import { fetchProvider } from "./founders.js";

export type WebSearchResult = { url: string; title: string; snippet: string };
export type WebSearchResponse = { results: WebSearchResult[]; provider: "google" | "treg" };
/** tregToken defaults to TREG_TOKEN. Without one there is no fallback. */
export type WebSearchOptions = { limit: number; after?: Date | string; apiKey: string; tregToken?: string; fallback?: boolean; deadlineAt?: number };

const GOOGLE_URL = "https://google-search74.p.rapidapi.com/";
const GOOGLE_HOST = "google-search74.p.rapidapi.com";
// Fallback: treg's Google SERP route, ~$0.0009 a call. Brave on RapidAPI was dropped: it answers 200 with an empty list.
const TREG_URL = "https://treg.to/call/treg.google.serp.organic";
// In-process only: every HTTP attempt takes a slot; cross-repo coordination is out of scope.
export const GOOGLE_SPACING_MS = 250;
let googleQueue: Promise<void> = Promise.resolve();

function googleSlot(): Promise<void> {
  const ready = googleQueue;
  googleQueue = ready.then(() => new Promise((resolve) => setTimeout(resolve, GOOGLE_SPACING_MS)));
  return ready;
}

function mapResults(items: unknown[], url: string, snippet: string): WebSearchResult[] {
  return items.filter((item): item is Record<string, unknown> => !!item && typeof item === "object")
    .map((item) => ({ url: String(item[url] ?? ""), title: String(item.title ?? ""), snippet: String(item[snippet] ?? "") }));
}

async function searchTreg(q: string, limit: number, token: string, deadlineAt?: number): Promise<WebSearchResponse | undefined> {
  try {
    const { res } = await fetchProvider(TREG_URL, {
      method: "POST",
      headers: { "X-Treg-Token": token, "X-Treg-Route-Max-Cost": "0.01", "Content-Type": "application/json" },
      body: JSON.stringify({ q, limit }),
      signal: AbortSignal.timeout(15_000),
    }, { record: () => {}, deadlineAt });
    if (!res?.ok) return undefined;
    const body = await res.json().catch(() => null) as { output?: { results?: unknown } } | null;
    const rows = body?.output?.results;
    return Array.isArray(rows) ? { results: mapResults(rows, "link", "snippet"), provider: "treg" } : undefined;
  } catch { return undefined; }
}

/** Google first; treg only when Google fails (not when it finds nothing). Undefined means every provider failed. */
export async function webSearch(query: string, options: WebSearchOptions): Promise<WebSearchResponse | undefined> {
  if (options.deadlineAt !== undefined && Date.now() >= options.deadlineAt) return undefined;
  // google-search74 returns up to 100 results in one request; treg keeps its 30 cap.
  const limit = Math.min(100, Math.max(1, Math.floor(options.limit)));
  const after = options.after instanceof Date ? options.after.toISOString().slice(0, 10) : options.after;
  const q = `${query}${after ? ` after:${after}` : ""}`;
  const googleParams = new URLSearchParams({ query: q, limit: String(limit), related_keywords: "false" });
  try {
    const { res } = await fetchProvider(`${GOOGLE_URL}?${googleParams}`, {
      method: "GET",
      headers: { "x-rapidapi-key": options.apiKey, "x-rapidapi-host": GOOGLE_HOST },
      signal: AbortSignal.timeout(15_000),
    }, { record: () => {}, deadlineAt: options.deadlineAt }, googleSlot);
    if (res?.ok) {
      const body = await res.json().catch(() => null) as { results?: unknown } | null;
      if (body && Array.isArray(body.results)) return { results: mapResults(body.results, "url", "description"), provider: "google" };
    }
  } catch { /* Try the fallback. */ }
  if (options.fallback === false) return undefined;
  const token = options.tregToken ?? process.env.TREG_TOKEN ?? "";
  return token ? searchTreg(q, Math.min(30, limit), token, options.deadlineAt) : undefined;
}
