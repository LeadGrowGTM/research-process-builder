const STEPS = [
  { provider: "direct", timeoutMs: 10_000 },
  { provider: "spider-smart", timeoutMs: 45_000 },
  { provider: "spider-chrome", timeoutMs: 60_000 },
  { provider: "spider-unblocker", timeoutMs: 60_000 },
] as const;

interface ScrapeOptions {
  maxChars?: number;
  deadlineAt?: number;
  /** Skip earlier steps. JS-rendered pages (the Product Hunt leaderboard) start at spider-chrome. */
  startAt?: typeof STEPS[number]["provider"];
}

interface ScrapeResult {
  content: string;
  provider: typeof STEPS[number]["provider"];
  costUsd: number;
}

export function htmlToText(html: string): string {
  const entities: Record<string, string> = { amp: "&", lt: "<", gt: ">", quot: '"', apos: "'", nbsp: " " };
  return html
    .replace(/<!--[\s\S]*?-->/g, "")
    .replace(/<(script|style|noscript|svg|template)\b[^>]*>[\s\S]*?<\/\1\s*>/gi, "\n")
    .replace(/<a\b[^>]*\shref\s*=\s*(?:"([^"]*)"|'([^']*)')[^>]*>([\s\S]*?)<\/a\s*>/gi, (_tag, double: string | undefined, single: string | undefined, label: string) => {
      const href = double ?? single;
      return href && /^(?:https?:\/\/|\/)/i.test(href) ? `${label} (${href})` : label;
    })
    .replace(/<\/?(?:title|p|div|main|article|section|nav|header|footer|aside|h[1-6]|li|ul|ol|br|hr|table|tr|td|th|dl|dt|dd|pre|blockquote)\b[^>]*>/gi, "\n")
    .replace(/<[^>]*>/g, "")
    .replace(/&(#x[0-9a-f]+|#\d+|amp|lt|gt|quot|apos|nbsp);/gi, (entity, name: string) => {
      if (!name.startsWith("#")) return entities[name.toLowerCase()];
      const code = name[1].toLowerCase() === "x" ? parseInt(name.slice(2), 16) : Number(name.slice(1));
      return code > 0 && code <= 0x10ffff && !(code >= 0xd800 && code <= 0xdfff) ? String.fromCodePoint(code) : entity;
    })
    .replace(/[^\S\n]+/g, " ")
    .replace(/ *\n */g, "\n")
    .replace(/\n{2,}/g, "\n")
    .trim();
}

export function looksBlocked(text: string): boolean {
  return text.split(/\s+/).filter(Boolean).length < 300
    && /just a moment|attention required|checking your browser|verify you are (a )?human|are you a robot|press and hold|access denied|enable javascript|javascript is required|please enable cookies|cf-browser-verification|captcha|datadome|perimeterx/i.test(text.replace(/\s+/g, " "));
}

export function isUsableContent(text: string): boolean {
  return text.split(/\s+/).filter(Boolean).length >= 50 && !looksBlocked(text);
}

function pickNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

export async function scrapePage(url: string, options: ScrapeOptions = {}): Promise<ScrapeResult | null> {
  let costUsd = 0;
  const first = Math.max(0, STEPS.findIndex((s) => s.provider === (options.startAt ?? "direct")));
  for (const { provider, timeoutMs } of STEPS.slice(first)) {
    const remainingMs = options.deadlineAt === undefined ? timeoutMs : options.deadlineAt - Date.now();
    if (remainingMs <= 0) return null;
    const apiKey = provider === "direct" ? "" : process.env.SPIDER_API_KEY;
    if (provider !== "direct" && !apiKey) continue;

    try {
      const signal = AbortSignal.timeout(Math.min(timeoutMs, remainingMs));
      let content: string;
      if (provider === "direct") {
        const resp = await fetch(url, {
          method: "GET",
          headers: {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            Accept: "text/html",
          },
          redirect: "follow",
          signal,
        });
        // A real 404/410 is a missing page, not bot protection: stop instead of paying Spider for it.
        if (resp.status === 404 || resp.status === 410) return null;
        if (!resp.ok || resp.headers.get("content-type")?.split(";")[0].trim().toLowerCase() !== "text/html") continue;
        content = htmlToText(await resp.text());
      } else {
        const resp = await fetch(provider === "spider-unblocker" ? "https://api.spider.cloud/unblocker" : "https://api.spider.cloud/scrape", {
          method: "POST",
          headers: { Authorization: `Bearer ${apiKey}`, "Content-Type": "application/json" },
          body: JSON.stringify({ url, return_format: "markdown", request: provider === "spider-chrome" ? "chrome" : "smart", filter_output_main_only: false }),
          signal,
        });
        if (!resp.ok) return null;
        const data: unknown = await resp.json();
        const first = Array.isArray(data) ? data[0] : undefined;
        if (!first || typeof first !== "object" || Array.isArray(first)) return null;
        const page = first as Record<string, unknown>;
        const costs = page.costs && typeof page.costs === "object" ? page.costs as Record<string, unknown> : undefined;
        costUsd += pickNumber(costs?.total_cost) ?? 0;
        const status = pickNumber(page.status);
        if (page.error || (status !== null && status >= 400)) return null;
        content = typeof page.content === "string" ? page.content : "";
      }
      content = content.trim();
      if (isUsableContent(content)) {
        return { content: content.slice(0, options.maxChars), provider, costUsd };
      }
    } catch {
      if (provider !== "direct") return null;
    }
  }
  return null;
}

const DEFAULT_MAX_CHARS = 15_000;

interface FetchOptions {
  renderJs?: boolean;   // kept for call-site compat - ignored
  waitForSecs?: number; // kept for compat - ignored
  maxChars?: number; // cap on returned text
  startAt?: ScrapeOptions["startAt"];
}

export async function fetchUrl(url: string, options?: FetchOptions): Promise<string | null> {
  const result = await scrapePage(url, { maxChars: options?.maxChars ?? DEFAULT_MAX_CHARS, startAt: options?.startAt });
  return result?.content ?? null;
}
