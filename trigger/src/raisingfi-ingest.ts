import { schedules, logger } from "@trigger.dev/sdk";
import { isSupabaseConfigured, checkTable, pushRaisingFiRows } from "./pipeline/supabase.js";
import { normalizeAmount } from "./pipeline/normalize-amount.js";
import { logoUrlForDomain, normalizeIndustry, normalizeOptionalText, normalizeRoundType } from "./pipeline/taxonomy.js";

const X_BEARER_TOKEN = process.env.X_BEARER_TOKEN ?? "";
const X_API_BASE = "https://api.x.com/2";
const RAISINGFI_USERNAME = "raisingfi";
const SUPABASE_TABLE = "funding_discoveries";

const FIELD_PATTERNS: Record<string, RegExp> = {
  company_name: /🏛️\s*Company:\s*(.+)/,
  website: /🔗\s*Website:\s*(https?:\/\/\S+)/,
  amount_raised: /📊\s*Amount:\s*(.+)/,
  round_type: /🔄\s*Round:\s*(.+)/,
  industry: /⚙️\s*Industry:\s*(.+)/,
  location: /🌍\s*Location:\s*(.+)/,
  investors: /^(?:💰|🤝)?\s*(?:Lead investors?|Investors?|Backed by|Led by)\s*:\s*(.+)$/im,
};

const BAD_DOMAINS = new Set([
  "twitter.com", "x.com", "linkedin.com", "facebook.com", "instagram.com",
  "youtube.com", "tiktok.com", "github.com", "medium.com", "substack.com",
  "crunchbase.com", "techcrunch.com", "bloomberg.com", "reuters.com",
  "thesaasnews.com", "finsmes.com", "businesswire.com", "prnewswire.com",
  "pitchbook.com", "dealroom.co", "tracxn.com", "venturebeat.com",
  "t.co", "bit.ly", "goo.gl", "tinyurl.com", "ow.ly",
]);

const SHORTENER_HOSTS = new Set(["t.co", "bit.ly", "goo.gl", "tinyurl.com", "ow.ly"]);

export interface Tweet {
  id: string;
  text: string;
  created_at?: string;
  entities?: { urls?: Array<{ url: string; expanded_url?: string; unwound_url?: string }> };
}

export interface FundingRow {
  [key: string]: string | number | null;
  discovered_date: string;
  company_name: string;
  company_domain: string | null;
  amount_raised: string | null;
  round_type: string | null;
  source_url: string;
  lead_investors: string | null;
  round_reasoning: string | null;
  discovered_by_pipeline: string;
  industry: string | null;
  location: string | null;
  raw_text: string;
  website_url: string | null;
  logo_url: string | null;
  source_name: string;
  source_count: number;
  score: number;
  pipeline_version: string;
}

export function extractDomain(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "").toLowerCase();
  } catch {
    return "";
  }
}

export function isPlausibleDomain(domain: string): boolean {
  if (!domain || BAD_DOMAINS.has(domain)) return false;
  for (const bad of BAD_DOMAINS) {
    if (domain.endsWith("." + bad)) return false;
  }
  return domain.includes(".");
}

/** Follow redirects for a short link (HEAD, 5s timeout); null when unresolvable. */
export async function resolveTcoUrl(shortUrl: string): Promise<string | null> {
  try {
    const resp = await fetch(shortUrl, {
      method: "HEAD",
      redirect: "follow",
      signal: AbortSignal.timeout(5_000),
    });
    const finalUrl = resp.url && resp.url !== shortUrl ? resp.url : null;
    if (finalUrl) return finalUrl;
    const location = resp.headers?.get?.("location");
    return location ?? null;
  } catch {
    return null;
  }
}

export function resolveCompanyDomainFromEntities(
  websiteTco: string | null,
  entities: Tweet["entities"],
  companyName: string
): string {
  const urlEntities = entities?.urls ?? [];
  const tcoMap = new Map<string, string>();
  for (const ue of urlEntities) {
    const short = ue.url ?? "";
    const expanded = ue.unwound_url || ue.expanded_url || "";
    if (short && expanded) tcoMap.set(short, expanded);
  }

  if (websiteTco && tcoMap.has(websiteTco)) {
    const domain = extractDomain(tcoMap.get(websiteTco)!);
    if (isPlausibleDomain(domain)) return domain;
  }

  const plausible: string[] = [];
  for (const expanded of tcoMap.values()) {
    const domain = extractDomain(expanded);
    if (isPlausibleDomain(domain)) plausible.push(domain);
  }

  if (plausible.length === 1) return plausible[0];

  if (plausible.length > 1 && companyName) {
    const nameWords = companyName.toLowerCase().split(/[\s&\-]+/).filter(w => w.length > 2);
    for (const domain of plausible) {
      const base = domain.split(".")[0];
      for (const word of nameWords) {
        if (base.includes(word) || word.includes(base)) return domain;
      }
    }
  }

  return "";
}

export async function resolveCompanyDomain(
  websiteTco: string | null,
  entities: Tweet["entities"],
  companyName: string
): Promise<string> {
  return (await resolveCompanyWebsite(websiteTco, entities, companyName)).domain;
}

export interface ResolvedCompanyWebsite {
  domain: string;
  websiteUrl: string | null;
}

export async function resolveCompanyWebsite(
  websiteTco: string | null,
  entities: Tweet["entities"],
  companyName: string
): Promise<ResolvedCompanyWebsite> {
  const viaEntities = resolveCompanyDomainFromEntities(websiteTco, entities, companyName);
  if (viaEntities) {
    const match = (entities?.urls ?? []).find((item) => {
      const expanded = item.unwound_url || item.expanded_url || "";
      return extractDomain(expanded) === viaEntities;
    });
    return {
      domain: viaEntities,
      websiteUrl: match?.unwound_url || match?.expanded_url || `https://${viaEntities}`,
    };
  }

  // No entity expansion: follow the short link before domain extraction.
  if (websiteTco) {
    let host = "";
    try {
      host = new URL(websiteTco).hostname.replace(/^www\./, "").toLowerCase();
    } catch {
      return { domain: "", websiteUrl: null };
    }
    if (SHORTENER_HOSTS.has(host) || host === "t.co") {
      const resolved = await resolveTcoUrl(websiteTco);
      if (resolved) {
        const domain = extractDomain(resolved);
        if (isPlausibleDomain(domain)) return { domain, websiteUrl: resolved };
      }
      return { domain: "", websiteUrl: null };
    }
    const domain = extractDomain(websiteTco);
    if (isPlausibleDomain(domain)) return { domain, websiteUrl: websiteTco };
  }
  return { domain: "", websiteUrl: null };
}

export async function parseTweet(tweet: Tweet): Promise<FundingRow | null> {
  const text = tweet.text ?? "";
  if (!text.includes("🏛️") || !text.includes("📊")) return null;

  const parsed: Record<string, string> = {};
  for (const [field, pattern] of Object.entries(FIELD_PATTERNS)) {
    const match = text.match(pattern);
    if (match) parsed[field] = match[1].trim();
  }

  if (!parsed.company_name) return null;

  const name = parsed.company_name.replace(/https?:\/\/\S+/g, "").trim();
  if (!name) return null;

  const websiteTco = parsed.website ?? null;
  const website = await resolveCompanyWebsite(websiteTco, tweet.entities, name);
  const domain = website.domain;

  const discoveredDate = tweet.created_at ? tweet.created_at.slice(0, 10) : new Date().toISOString().slice(0, 10);
  const amountRaw = parsed.amount_raised ?? "";
  const norm = normalizeAmount(amountRaw);

  return {
    discovered_date: discoveredDate,
    company_name: name,
    company_domain: domain || null,
    amount_raised: normalizeOptionalText(amountRaw),
    amount_raised_usd: norm?.value_usd ?? null,
    amount_raised_currency: norm?.currency ?? null,
    round_type: normalizeRoundType(parsed.round_type ?? ""),
    source_url: `https://x.com/${RAISINGFI_USERNAME}/status/${tweet.id}`,
    lead_investors: normalizeOptionalText(parsed.investors),
    round_reasoning: null,
    discovered_by_pipeline: "raisingfi",
    industry: normalizeIndustry(parsed.industry ?? ""),
    location: normalizeOptionalText(parsed.location),
    raw_text: text,
    website_url: website.websiteUrl,
    logo_url: logoUrlForDomain(domain),
    source_name: "@raisingfi on X",
    source_count: 1,
    score: 0,
    pipeline_version: "raisingfi-1.0-ts",
  };
}

export async function requireRaisingFiStorage(
  configured: () => boolean = isSupabaseConfigured,
  tableExists: () => Promise<boolean> = () => checkTable(SUPABASE_TABLE)
): Promise<void> {
  if (!configured()) {
    throw new Error("Supabase is not configured (SUPABASE_PROJECT_URL / SUPABASE_KEY missing)");
  }
  if (!(await tableExists())) {
    throw new Error(`Table ${SUPABASE_TABLE} not found`);
  }
}

async function getUserId(username: string): Promise<string> {
  const resp = await fetch(`${X_API_BASE}/users/by/username/${username}`, {
    headers: { Authorization: `Bearer ${X_BEARER_TOKEN}` },
    signal: AbortSignal.timeout(15_000),
  });
  if (!resp.ok) throw new Error(`X API user lookup failed: ${resp.status}`);
  const data = await resp.json();
  if (!data.data) throw new Error(`User @${username} not found`);
  return data.data.id;
}

async function fetchTweets(userId: string, startTime: string): Promise<Tweet[]> {
  const all: Tweet[] = [];
  let nextToken: string | undefined;

  while (true) {
    const params = new URLSearchParams({
      max_results: "100",
      exclude: "retweets,replies",
      "tweet.fields": "created_at,text,id,entities",
      start_time: startTime,
    });
    if (nextToken) params.set("pagination_token", nextToken);

    const resp = await fetch(`${X_API_BASE}/users/${userId}/tweets?${params}`, {
      headers: { Authorization: `Bearer ${X_BEARER_TOKEN}` },
      signal: AbortSignal.timeout(15_000),
    });
    if (!resp.ok) throw new Error(`X API tweets fetch failed: ${resp.status}`);
    const body = await resp.json();

    const tweets: Tweet[] = body.data ?? [];
    all.push(...tweets);

    nextToken = body.meta?.next_token;
    if (!nextToken || tweets.length === 0) break;
  }

  return all;
}

export const raisingfiIngest = schedules.task({
  id: "raisingfi-ingest",
  cron: {
    pattern: "30 7 * * *",
    timezone: "America/New_York",
  },
  retry: {
    maxAttempts: 3,
    factor: 2,
    minTimeoutInMs: 10_000,
    maxTimeoutInMs: 120_000,
    randomize: true,
  },
  run: async (payload) => {
    if (!X_BEARER_TOKEN) throw new Error("X_BEARER_TOKEN not set");
    await requireRaisingFiStorage();

    const scheduledDate = payload.timestamp.toISOString().split("T")[0];
    const startTime = new Date(Date.now() - 25 * 60 * 60 * 1000).toISOString();

    logger.info("Starting RaisingFi ingest", { scheduledDate, startTime });

    const userId = await getUserId(RAISINGFI_USERNAME);
    logger.info(`User ID: ${userId}`);

    const tweets = await fetchTweets(userId, startTime);
    logger.info(`Fetched ${tweets.length} tweets`);

    if (tweets.length === 0) {
      return { date: scheduledDate, parsed: 0, upserted: 0, skipped: 0 };
    }

    const rows: FundingRow[] = [];
    let skipped = 0;
    for (const tweet of tweets) {
      const row = await parseTweet(tweet);
      if (row) {
        rows.push(row);
      } else {
        skipped++;
      }
    }

    logger.info(`Parsed ${rows.length} funding tweets, skipped ${skipped} non-funding`);

    if (rows.length === 0) {
      return { date: scheduledDate, parsed: 0, upserted: 0, skipped };
    }

    const { upserted, attempted, errors } = await pushRaisingFiRows(rows, SUPABASE_TABLE);
    logger.info(`Supabase: ${upserted}/${rows.length} upserted`);
    if (errors.length > 0 || upserted !== attempted) {
      throw new Error(
        `RaisingFi ingest failed: ${upserted}/${attempted} attempted rows upserted, ${errors.length} Supabase errors: ${errors[0] ?? "unknown write mismatch"}`
      );
    }

    return { date: scheduledDate, parsed: rows.length, upserted, skipped };
  },
});
