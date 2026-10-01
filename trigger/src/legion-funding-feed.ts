import { schedules, logger } from "@trigger.dev/sdk";
import { logoUrlForDomain, normalizeOptionalText, normalizeRoundType, sourceNameForUrl } from "./pipeline/taxonomy.js";
import { findCompanyPeople, type PeopleWaterfallConfig } from "./pipeline/legion-people.js";

const WINDOW_DAYS = 60;
const LIMIT = 500;
const CHUNK_SIZE = 50;
// New companies enriched per run; the 30-minute schedule clears a 447-company backlog in about 5 hours.
const ENRICH_PER_RUN = 40;
const PROFILE_SELECT = "domain,hq,employees,founders";
const CORE_SELECT = "company_name,company_domain,amount_raised,round_type,lead_investors,discovered_date,source_url,industry";
const FULL_SELECT = `${CORE_SELECT},amount_raised_usd,employee_count,employee_range,location,hq_location,company_description,products,founded_year,logo_url,source_name`;
const COMPANY_SELECT = "domain,industry_label";

type FundingDiscovery = Record<string, unknown>;
type CompanyProfile = Record<string, unknown>;

export type FundingFeedRow = {
  company: string;
  domain: string;
  logo: string | null;
  round: string;
  amount: string | null;
  amountUsd: number | null;
  investors: string | null;
  industry: string;
  description: string;
  employees: number | string | null;
  hq: string;
  founded: number | null;
  founders: Array<{ name: string; title: string; linkedin: string }>;
  date: string;
  source: string | null;
  sourceUrl: string;
};

export type FundingFeed = { updatedAt: string; count: number; rows: FundingFeedRow[] };

/**
 * One market signal on the Legion /signals page. Every signal type (funding today;
 * hiring activity, new locations later) fills the same fields so the page needs no
 * per-type code beyond a label. Type-specific extras go in `details`.
 */
export type Signal = {
  type: string;
  company: string;
  domain: string;
  logo: string | null;
  headline: string;
  metric: { label: string; value: string; sort: number | null } | null;
  summary: string;
  tags: string[];
  location: string;
  people: Array<{ name: string; title: string; linkedin: string }>;
  date: string;
  source: string | null;
  sourceUrl: string;
  details: Record<string, string | number | null>;
};

export type SignalsFeed = { updatedAt: string; count: number; signals: Signal[] };

/** $12M, $750K, $1.2B. */
export function compactUsd(usd: number): string {
  const units: Array<[number, string]> = [[1e9, "B"], [1e6, "M"], [1e3, "K"]];
  for (const [size, suffix] of units) {
    if (usd >= size) return `$${Number((usd / size).toFixed(usd >= size * 10 ? 0 : 1))}${suffix}`;
  }
  return `$${Math.round(usd)}`;
}

/** Maps a funding feed row to the shared signal shape. */
export function fundingSignal(row: FundingFeedRow): Signal {
  const value = row.amountUsd !== null ? compactUsd(row.amountUsd) : row.amount ?? "";
  const round = row.round === "Unknown" ? "" : row.round;
  return {
    type: "funding",
    company: row.company,
    domain: row.domain,
    logo: row.logo,
    headline: [round ? `${round} round` : "Funding round", value ? `of ${value}` : ""].filter(Boolean).join(" "),
    metric: value ? { label: "Raised", value, sort: row.amountUsd } : null,
    summary: row.description,
    tags: [round, row.industry].filter(Boolean),
    location: row.hq,
    people: row.founders,
    date: row.date,
    source: row.source,
    sourceUrl: row.sourceUrl,
    details: { investors: row.investors, employees: row.employees, founded: row.founded },
  };
}

const ENTITIES: Record<string, string> = { amp: "&", lt: "<", gt: ">", quot: '"', apos: "'", "#39": "'", nbsp: " " };

// Source text sometimes arrives HTML-escaped ("Software &amp; SaaS"); the page escapes on render.
function text(value: unknown): string {
  return (normalizeOptionalText(value) ?? "").replace(/&(amp|lt|gt|quot|apos|#39|nbsp);/g, (_, name: string) => ENTITIES[name]);
}

// Link shorteners and social hosts that ingestion sometimes stores as the company's domain.
const NON_COMPANY_DOMAINS = new Set(["t.co", "x.com", "twitter.com", "bit.ly", "lnkd.in", "linkedin.com", "buff.ly", "ow.ly", "tinyurl.com", "youtube.com", "youtu.be"]);

function companyDomain(value: unknown): string {
  const domain = text(value);
  return NON_COMPANY_DOMAINS.has(domain.toLowerCase().replace(/^www\./, "")) ? "" : domain;
}

function normalizedDomain(value: unknown): string {
  return text(value).toLowerCase();
}

function safeUrl(value: unknown): string {
  const candidate = text(value);
  try {
    const url = new URL(candidate);
    if (url.protocol !== "https:" || url.username || url.password) return "";
    for (const key of url.searchParams.keys()) {
      if (/token|api.?key|secret|password|auth/i.test(key)) return "";
    }
    return candidate;
  } catch {
    return "";
  }
}

function optionalNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function optionalAmount(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim() !== "" && Number.isFinite(Number(value))) return Number(value);
  return null;
}

/** Builds public feed rows without any I/O. */
export function buildFundingFeedRows(
  funding: Array<FundingDiscovery | null> | null | undefined,
  companies: Array<CompanyProfile | null> | null | undefined,
  people: Array<CompanyProfile | null> | null | undefined,
): FundingFeedRow[] {
  const companyByDomain = new Map<string, CompanyProfile>();
  for (const company of companies ?? []) {
    if (!company) continue;
    const domain = normalizedDomain(company.domain);
    if (domain && !companyByDomain.has(domain)) companyByDomain.set(domain, company);
  }

  const peopleByDomain = new Map<string, CompanyProfile>();
  for (const profile of people ?? []) {
    const domain = profile ? normalizedDomain(profile.domain) : "";
    if (profile && domain) peopleByDomain.set(domain, profile);
  }

  // Only name, title and https LinkedIn reach the public feed.
  function publicFounders(raw: unknown): FundingFeedRow["founders"] {
    if (!Array.isArray(raw)) return [];
    return raw
      .filter((f): f is Record<string, unknown> => !!f && typeof f === "object")
      .map((f) => ({ name: text(f.name), title: text(f.title), linkedin: safeUrl(f.linkedin) }))
      .filter((f) => f.name)
      .slice(0, 3);
  }

  const seen = new Set<string>();
  const rows: FundingFeedRow[] = [];
  for (const item of funding ?? []) {
    if (!item) continue;
    const company = text(item.company_name);
    if (!company) continue;
    const domain = companyDomain(item.company_domain);
    const key = normalizedDomain(domain) || company.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    const profile = companyByDomain.get(normalizedDomain(domain));
    const people = peopleByDomain.get(normalizedDomain(domain));
    const sourceUrl = safeUrl(item.source_url);
    const profileDescription = text(item.company_description) || text(item.products);
    rows.push({
      company,
      domain,
      // A stored logo for a link-shortener domain is that shortener's favicon, so it goes too.
      logo: domain ? safeUrl(item.logo_url) || logoUrlForDomain(domain) : null,
      round: normalizeRoundType(item.round_type) ?? "Unknown",
      amount: text(item.amount_raised) || null,
      amountUsd: optionalAmount(item.amount_raised_usd),
      investors: text(item.lead_investors) || null,
      industry: text(profile?.industry_label) || text(item.industry),
      description: profileDescription.slice(0, 280),
      employees: optionalNumber(item.employee_count) ?? (text(item.employee_range) || text(people?.employees) || null),
      hq: text(item.hq_location) || text(people?.hq) || text(item.location),
      founded: optionalNumber(item.founded_year),
      founders: publicFounders(people?.founders),
      date: text(item.discovered_date),
      source: text(item.source_name) || sourceNameForUrl(sourceUrl),
      sourceUrl,
    });
  }
  return rows;
}

type FetchLike = (input: string, init?: RequestInit) => Promise<Response>;
export type FundingFeedConfig = {
  url: string;
  key: string;
  legionKv?: { accountId: string; namespaceId: string; token: string };
  enrichment?: PeopleWaterfallConfig;
  fetchImpl?: FetchLike;
  now?: Date;
};

function headers(key: string, extra: HeadersInit = {}): HeadersInit {
  return { apikey: key, Authorization: `Bearer ${key}`, ...extra };
}

function domainsFrom(rows: FundingDiscovery[]): string[] {
  return [...new Set(rows.map((row) => companyDomain(row.company_domain).toLowerCase()).filter(Boolean))];
}

function inFilter(domains: string[]): string {
  return `in.(${domains.map(encodeURIComponent).join(",")})`;
}

async function fetchJson(fetchImpl: FetchLike, url: string, headersValue: HeadersInit): Promise<Response> {
  return fetchImpl(url, { headers: headersValue, signal: AbortSignal.timeout(15_000) });
}

async function fetchFunding(config: FundingFeedConfig): Promise<FundingDiscovery[]> {
  const fetchImpl = config.fetchImpl ?? fetch;
  const since = new Date((config.now ?? new Date()).getTime() - WINDOW_DAYS * 86_400_000).toISOString().slice(0, 10);
  const base = `${config.url.replace(/\/+$/, "")}/rest/v1/funding_discoveries?discovered_date=gte.${since}&order=discovered_date.desc&limit=${LIMIT}`;
  const readHeaders = headers(config.key, { "Accept-Profile": "public" });
  let response = await fetchJson(fetchImpl, `${base}&select=${FULL_SELECT}`, readHeaders);
  if (!response.ok && response.status === 400) {
    response = await fetchJson(fetchImpl, `${base}&select=${CORE_SELECT}`, readHeaders);
  }
  if (!response.ok) throw new Error(`funding_discoveries read failed with HTTP ${response.status}`);
  const rows: unknown = await response.json();
  if (!Array.isArray(rows)) throw new Error("funding_discoveries returned an invalid core response");
  return rows;
}

async function fetchOptional(
  config: FundingFeedConfig,
  domains: string[],
  table: "signal_companies",
): Promise<Record<string, unknown>[]> {
  const fetchImpl = config.fetchImpl ?? fetch;
  const select = COMPANY_SELECT;
  const rows: Record<string, unknown>[] = [];
  for (let start = 0; start < domains.length; start += CHUNK_SIZE) {
    const chunk = domains.slice(start, start + CHUNK_SIZE);
    const schemaHeaders: Record<string, string> = { "Accept-Profile": "leadgrow_knowledge" };
    try {
      const response = await fetchJson(
        fetchImpl,
        `${config.url.replace(/\/+$/, "")}/rest/v1/${table}?select=${select}&domain=${inFilter(chunk)}`,
        headers(config.key, schemaHeaders),
      );
      if (response.ok) {
        const data: unknown = await response.json();
        if (Array.isArray(data)) rows.push(...data.filter(row => row && typeof row === "object"));
      }
    } catch {
      // Profiles are optional. A core feed can still be published without them.
    }
  }
  return rows;
}

/** Writes the signals feed to Legion's FUNDING_FEED KV namespace, served by the Legion site at /data/signals.json. */
async function uploadFeed(config: FundingFeedConfig, feed: SignalsFeed): Promise<void> {
  const kv = config.legionKv;
  if (!kv?.accountId || !kv.namespaceId || !kv.token) throw new Error("Legion feed KV is not configured");
  const response = await (config.fetchImpl ?? fetch)(
    `https://api.cloudflare.com/client/v4/accounts/${kv.accountId}/storage/kv/namespaces/${kv.namespaceId}/values/signals.json`,
    {
      method: "PUT",
      headers: { Authorization: `Bearer ${kv.token}`, "Content-Type": "application/json" },
      body: JSON.stringify(feed),
      signal: AbortSignal.timeout(15_000),
    },
  );
  if (!response.ok) throw new Error(`Legion feed KV write failed with HTTP ${response.status}`);
}

async function fetchProfiles(config: FundingFeedConfig, domains: string[]): Promise<CompanyProfile[]> {
  const fetchImpl = config.fetchImpl ?? fetch;
  const rows: CompanyProfile[] = [];
  for (let start = 0; start < domains.length; start += CHUNK_SIZE) {
    const response = await fetchJson(
      fetchImpl,
      `${config.url.replace(/\/+$/, "")}/rest/v1/legion_company_profiles?select=${PROFILE_SELECT}&domain=${inFilter(domains.slice(start, start + CHUNK_SIZE))}`,
      headers(config.key, { "Accept-Profile": "leadgrow_knowledge" }),
    );
    if (!response.ok) throw new Error(`legion_company_profiles read failed with HTTP ${response.status}`);
    const data: unknown = await response.json();
    if (Array.isArray(data)) rows.push(...data);
  }
  return rows;
}

async function upsertProfile(config: FundingFeedConfig, row: CompanyProfile): Promise<void> {
  const response = await (config.fetchImpl ?? fetch)(`${config.url.replace(/\/+$/, "")}/rest/v1/legion_company_profiles`, {
    method: "POST",
    headers: headers(config.key, { "Content-Type": "application/json", "Content-Profile": "leadgrow_knowledge", Prefer: "resolution=merge-duplicates" }),
    body: JSON.stringify([row]),
    signal: AbortSignal.timeout(15_000),
  });
  if (!response.ok) throw new Error(`legion_company_profiles write failed with HTTP ${response.status}`);
}

/** Runs the find-people waterfall for domains with no cached profile, caps spend per run, stores spend per company. */
async function enrichMissing(config: FundingFeedConfig, domains: string[], profiles: CompanyProfile[]): Promise<{ enriched: number; costUsd: number }> {
  if (!config.enrichment) return { enriched: 0, costUsd: 0 };
  const done = new Set(profiles.map((p) => normalizedDomain(p.domain)));
  const todo = domains.filter((d) => !done.has(d)).slice(0, ENRICH_PER_RUN);
  let costUsd = 0;
  for (const domain of todo) {
    const found = await findCompanyPeople(domain, config.enrichment);
    const cost = found.calls.reduce((sum, call) => sum + call.costUsd, 0);
    costUsd += cost;
    const row = { domain, hq: found.hq, employees: found.employees, founders: found.founders, sources: found.sources, cost_usd: cost, calls: found.calls };
    await upsertProfile(config, row);
    profiles.push(row);
  }
  return { enriched: todo.length, costUsd };
}

export async function refreshFundingFeed(config: FundingFeedConfig): Promise<FundingFeed & { enriched: number; costUsd: number }> {
  if (!config.url || !config.key) throw new Error("Supabase is not configured for legion funding feed");
  const funding = await fetchFunding(config);
  const domains = domainsFrom(funding);
  const [companies, profiles] = await Promise.all([
    fetchOptional(config, domains, "signal_companies"),
    fetchProfiles(config, domains),
  ]);
  const { enriched, costUsd } = await enrichMissing(config, domains, profiles);
  const feed: FundingFeed = {
    updatedAt: (config.now ?? new Date()).toISOString(),
    count: 0,
    rows: buildFundingFeedRows(funding, companies, profiles),
  };
  feed.count = feed.rows.length;
  const signals = feed.rows.map(fundingSignal);
  await uploadFeed(config, { updatedAt: feed.updatedAt, count: signals.length, signals });
  return { ...feed, enriched, costUsd };
}

function runtimeConfig(): FundingFeedConfig {
  return {
    url: process.env.SUPABASE_PROJECT_URL ?? process.env.SUPABASE_URL ?? "",
    key: process.env.SUPABASE_SERVICE_ROLE_KEY ?? process.env.SUPABASE_KEY ?? "",
    legionKv: {
      accountId: process.env.LEGION_CF_ACCOUNT_ID ?? "",
      namespaceId: process.env.LEGION_FUNDING_KV_ID ?? "",
      token: process.env.LEGION_CF_API_TOKEN ?? "",
    },
    enrichment: {
      quickEnrichKey: process.env.QUICKENRICH_API_KEY ?? "",
      aiArkKey: process.env.AI_ARK_API_KEY ?? "",
      quickEnrichUsdPerCredit: Number(process.env.QUICKENRICH_USD_PER_CREDIT ?? 0) || 0,
    },
  };
}

export const legionFundingFeed = schedules.task({
  id: "legion-funding-feed",
  cron: { pattern: "*/30 * * * *", timezone: "America/New_York" },
  run: async () => {
    const feed = await refreshFundingFeed(runtimeConfig());
    logger.info("Legion funding feed published", { count: feed.count, enriched: feed.enriched, costUsd: feed.costUsd });
    return { count: feed.count, enriched: feed.enriched, costUsd: feed.costUsd, updatedAt: feed.updatedAt };
  },
});
