import { schedules, logger } from "@trigger.dev/sdk";
import { decodeHtmlEntities, isPublicHttpsUrl, logoUrlForDomain, normalizeOptionalText, normalizeRoundType, sourceNameForUrl } from "./pipeline/taxonomy.js";
import { findCompanyPeople, type CompanyPeopleProfile, type PeopleWaterfallConfig } from "./pipeline/legion-people.js";
import { buildRounds, companyKeyOf, displaySource, needsSecondarySource, type CompanyRounds, type FundingRound, type RoundSource } from "./pipeline/funding-rounds.js";
import { findSecondarySource } from "./pipeline/brave-source.js";
import { findGoogleSource } from "./pipeline/google-source.js";
import { ADDITIONAL_SIGNAL_ORDER, ADDITIONAL_SIGNAL_SELECT, projectAdditionalSignals, type AdditionalFamilyCoverage } from "./pipeline/additional-signals.js";
import { createHash } from "node:crypto";

const READ_PAGE = 1000;
const MAX_READ_PAGES = 100_000;
const CHUNK_SIZE = 50;
// New companies enriched per run; the 30-minute schedule clears a ~3,400-company backlog in about 12 hours.
const ENRICH_PER_RUN = 150;
// Brave lookups per run for rounds only raisingfi reported (~1,900 at launch). Override with LEGION_BRAVE_PER_RUN.
const BRAVE_PER_RUN_DEFAULT = 60;
// Google Search queries per run. A round may spend two. Override with LEGION_SEARCH_PER_RUN.
const SEARCH_PER_RUN_DEFAULT = 60;
/** Secondary search generation. Misses checked before SECONDARY_NONE_RECHECK_BEFORE are searched once more. */
export const SEARCH_STRATEGY_VERSION = 2;
export const SECONDARY_NONE_RECHECK_BEFORE = "2026-10-05T16:00:00Z";
// Signals per KV page; the page loads page 1 and fetches more on "Show more".
export const SIGNALS_PAGE_SIZE = 500;
const PROFILE_SELECT = "domain,hq,employees,founders";
const CORE_SELECT = "id,company_name,company_domain,amount_raised,round_type,lead_investors,discovered_date,source_url,industry";
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
 * One market signal. Funding, product-launch, gaming, and hiring fill the same fields.
 * Type-specific extras go in `details`. Unknown fields stay absent.
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
  /** True when this company has an earlier round on record: it raised again and moved to the top. */
  raisedAgain: boolean;
  /** Earlier rounds, newest first. */
  earlier: Array<{ headline: string; value: string; round: string; date: string; source: string | null; sourceUrl: string }>;
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
    raisedAgain: false,
    earlier: [],
  };
}

function roundHeadline(round: string, value: string): string {
  const named = round && round !== "Unknown" ? round : "";
  return [named ? `${named} round` : "Funding round", value ? `of ${value}` : ""].filter(Boolean).join(" ");
}

function roundValue(round: FundingRound): string {
  return round.amountUsd !== null ? compactUsd(round.amountUsd) : round.amount ?? "";
}

/**
 * One signal per company: its latest round, with company fields from its newest report and
 * every earlier round listed under it. `secondary` holds looked-up sources by round key.
 */
export function companySignal(company: CompanyRounds, secondary: Map<string, RoundSource>): Signal {
  const { latest, earlier, profile } = company;
  const shown = displaySource(latest, secondary.get(latest.key));
  const signal = fundingSignal({
    ...profile,
    round: latest.round,
    amount: latest.amount,
    amountUsd: latest.amountUsd,
    investors: latest.investors ?? profile.investors,
    date: latest.date,
    source: shown?.name || null,
    sourceUrl: shown?.url ?? "",
  });
  signal.raisedAgain = earlier.length > 0;
  signal.earlier = earlier.map((round) => {
    const src = displaySource(round, secondary.get(round.key));
    const value = roundValue(round);
    return { headline: roundHeadline(round.round, value), value, round: round.round === "Unknown" ? "" : round.round, date: round.date, source: src?.name || null, sourceUrl: src?.url ?? "" };
  });
  return signal;
}

// Source text sometimes arrives HTML-escaped ("Software &amp; SaaS", or "&amp;amp;" twice). The page writes textContent, so publish the decoded characters.
function text(value: unknown): string {
  return decodeHtmlEntities(normalizeOptionalText(value) ?? "");
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
  return isPublicHttpsUrl(candidate) ? candidate : "";
}

function optionalNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function optionalAmount(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim() !== "" && Number.isFinite(Number(value))) return Number(value);
  return null;
}

/** Builds public feed rows without any I/O: newest report per company. */
export function buildFundingFeedRows(
  funding: Array<FundingDiscovery | null> | null | undefined,
  companies: Array<CompanyProfile | null> | null | undefined,
  people: Array<CompanyProfile | null> | null | undefined,
): FundingFeedRow[] {
  const seen = new Set<string>();
  return buildFundingReports(funding, companies, people).filter((row) => {
    const key = companyKeyOf(row);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

/** One public-safe row per funding report (no de-duplication), for merging into rounds. */
export function buildFundingReports(
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

  const rows: FundingFeedRow[] = [];
  for (const item of funding ?? []) {
    if (!item) continue;
    const company = text(item.company_name);
    if (!company) continue;
    const domain = companyDomain(item.company_domain);
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

/** One PostgREST project. `url` is the project origin. `key` is the apikey and bearer token. */
export type TableClient = { url: string; key: string };

type EnvSource = {
  SUPABASE_PROJECT_URL?: string;
  SUPABASE_URL?: string;
  SUPABASE_SERVICE_ROLE_KEY?: string;
  SUPABASE_KEY?: string;
  SUPABASE_ANON_KEY?: string;
};

/**
 * Funding discoveries, rounds, and company profiles.
 * URL: SUPABASE_PROJECT_URL, else SUPABASE_URL.
 * Key: SUPABASE_SERVICE_ROLE_KEY, else SUPABASE_KEY. The anon key is not used.
 */
export function fundingClientFromEnv(env: EnvSource): TableClient {
  return {
    url: env.SUPABASE_PROJECT_URL ?? env.SUPABASE_URL ?? "",
    key: env.SUPABASE_SERVICE_ROLE_KEY ?? env.SUPABASE_KEY ?? "",
  };
}

/**
 * Product, game, and job writers. The four files use this order, not the funding order.
 * product-launches-news.ts, product-launches-ph.ts, game-signals-pipeline.ts, jobs-pipeline.ts.
 * URL: SUPABASE_PROJECT_URL, else SUPABASE_URL, and only when the value starts with http.
 * Key: SUPABASE_KEY, else SUPABASE_SERVICE_ROLE_KEY, else SUPABASE_ANON_KEY.
 */
export function writerClientFromEnv(env: EnvSource): TableClient {
  const raw = env.SUPABASE_PROJECT_URL ?? env.SUPABASE_URL ?? "";
  return {
    url: raw.startsWith("http") ? raw : "",
    key: env.SUPABASE_KEY ?? env.SUPABASE_SERVICE_ROLE_KEY ?? env.SUPABASE_ANON_KEY ?? "",
  };
}

export type FundingFeedConfig = {
  url: string;
  key: string;
  /** Writer clients. Each family is authenticated the way its writer is, not with the funding key. */
  sources: {
    productLaunches: TableClient;
    gameSignals: TableClient;
    jobSignals: TableClient;
  };
  legionKv?: { accountId: string; namespaceId: string; token: string };
  enrichment?: PeopleWaterfallConfig;
  brave?: { apiKey: string; perRun: number; usdPerQuery: number };
  google?: { apiKey: string; perRun: number; usdPerQuery: number };
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

type RangeFrame = { start: number; end: number; total: number | null };

/** `null` means the header was absent or not a PostgREST range. `empty` is `* /0` or `* /*`. */
function parseContentRange(header: string | null): RangeFrame | "empty-zero" | "empty-unknown" | null {
  if (!header) return null;
  const trimmed = header.trim();
  if (trimmed === "*/0") return "empty-zero";
  if (trimmed === "*/*") return "empty-unknown";
  const match = /^(\d+)-(\d+)\/(\d+|\*)$/.exec(trimmed);
  if (!match) return null;
  return { start: Number(match[1]), end: Number(match[2]), total: match[3] === "*" ? null : Number(match[3]) };
}

/** Stable discovery identity. Public payloads never receive this id. */
function sourceIdentity(row: FundingDiscovery): string {
  const id = row.id;
  if (id !== undefined && id !== null && String(id).trim() !== "") return `id:${String(id)}`;
  return `row:${JSON.stringify([
    row.company_name ?? null,
    row.company_domain ?? null,
    row.round_type ?? null,
    row.discovered_date ?? null,
    row.source_url ?? null,
    row.amount_raised ?? null,
    row.amount_raised_usd ?? null,
  ])}`;
}

type PageRead = { rows: FundingDiscovery[]; duplicateRows: number };

/**
 * Reads every row. A server may enforce a smaller page than READ_PAGE. The next offset follows
 * the rows actually returned (or the Content-Range end), never a blind jump of READ_PAGE.
 * A short page is the end only when Content-Range names a total and the cursor has reached it.
 * A missing Content-Range is not that proof: keep reading at the returned offset until an empty
 * page, or fail before any publish. Required reads refuse an empty body that is not
 * Content-Range * /0, so a broken read cannot publish as zero rows.
 * Funding keeps the first row for an identity. Additional reads retain later copies because
 * their order is the unique key, not the source date. A page of only repeated identities still fails.
 */
async function readAllRows(
  label: string,
  invalidMessage: string,
  fetchPage: (offset: number) => Promise<Response>,
  identity: (row: FundingDiscovery) => string,
  requireProvenEmpty: boolean,
  retainDuplicates = false,
): Promise<PageRead> {
  const rows: FundingDiscovery[] = [];
  const seen = new Set<string>();
  let duplicateRows = 0;
  let offset = 0;
  let total: number | null = null;
  let raw = 0;
  for (let guard = 0; guard < MAX_READ_PAGES; guard++) {
    const response = await fetchPage(offset);
    if (!response.ok) throw new Error(`${label} read failed with HTTP ${response.status}`);
    const data: unknown = await response.json();
    if (!Array.isArray(data)) throw new Error(invalidMessage);
    const parsed = parseContentRange(response.headers.get("content-range"));
    let frame: RangeFrame | null = null;
    if (parsed === "empty-zero") total = total ?? 0;
    else if (parsed && parsed !== "empty-unknown") {
      if (parsed.end < parsed.start || parsed.end - parsed.start + 1 !== data.length) {
        throw new Error(`${label} page size mismatch at offset ${offset}`);
      }
      if (parsed.start !== offset) throw new Error(`${label} page boundary mismatch at offset ${offset}`);
      if (parsed.total !== null) {
        if (total !== null && parsed.total !== total) throw new Error(`${label} total changed during read`);
        total = parsed.total;
      }
      frame = parsed;
    }
    if (data.length === 0) {
      if (total !== null && raw !== total) throw new Error(`${label} ended before announced total of ${total}`);
      if (requireProvenEmpty && raw === 0 && total !== 0) throw new Error(`${label} returned an unproven empty read`);
      return { rows, duplicateRows };
    }
    let fresh = 0;
    for (const row of data) {
      if (!row || typeof row !== "object") throw new Error(invalidMessage);
      raw++;
      const key = identity(row as FundingDiscovery);
      if (seen.has(key)) {
        duplicateRows++;
        if (retainDuplicates) rows.push(row as FundingDiscovery);
        continue;
      }
      seen.add(key);
      rows.push(row as FundingDiscovery);
      fresh++;
    }
    if (fresh === 0) throw new Error(`${label} repeated source identities at offset ${offset}`);
    const next = frame ? frame.end + 1 : offset + data.length;
    if (next <= offset) throw new Error(`${label} page cursor stalled at offset ${offset}`);
    offset = next;
    if (total !== null && raw > total) throw new Error(`${label} exceeded announced total of ${total}`);
    if (total !== null && offset >= total) {
      if (raw !== total) throw new Error(`${label} row count ${raw} did not match announced total ${total}`);
      return { rows, duplicateRows };
    }
    continue;
  }
  throw new Error(`${label} read exceeded the page bound`);
}

/** Every funding report ever discovered, newest first. Incomplete reads throw before any publish. */
async function fetchFunding(config: FundingFeedConfig): Promise<PageRead> {
  const fetchImpl = config.fetchImpl ?? fetch;
  const readHeaders = headers(config.key, { "Accept-Profile": "public", Prefer: "count=exact" });
  const root = config.url.replace(/\/+$/, "");
  let select = FULL_SELECT;
  return readAllRows(
    "funding_discoveries",
    "funding_discoveries returned an invalid core response",
    async (offset) => {
      const urlFor = (columns: string) =>
        `${root}/rest/v1/funding_discoveries?order=discovered_date.desc,id.desc&limit=${READ_PAGE}&offset=${offset}&select=${columns}`;
      let response = await fetchJson(fetchImpl, urlFor(select), readHeaders);
      if (!response.ok && response.status === 400 && select === FULL_SELECT && offset === 0) {
        select = CORE_SELECT;
        response = await fetchJson(fetchImpl, urlFor(select), readHeaders);
      }
      return response;
    },
    sourceIdentity,
    true,
  );
}

type AdditionalTable = keyof typeof ADDITIONAL_SIGNAL_SELECT;

/** Stored unique key, not the contact-stripped public URL. Missing keys stay distinct. */
function sourceUrlIdentity(row: FundingDiscovery): string {
  const url = row.source_url;
  if (typeof url === "string" && url.trim() !== "") return `source_url:${url.trim()}`;
  return `source_url:${JSON.stringify(row)}`;
}

function jobIdentity(row: FundingDiscovery): string {
  const id = row.job_id;
  if (typeof id === "number" && Number.isInteger(id)) return `job_id:${id}`;
  if (typeof id === "string" && /^\d+$/.test(id.trim())) return `job_id:${Number(id.trim())}`;
  if (id !== undefined && id !== null && String(id).trim() !== "") return `job_id:${String(id).trim()}`;
  return `job_id:${JSON.stringify(row)}`;
}

/**
 * Required producer read. Select and order are the writer conflict key.
 * product_launches is unique on source_url. game_signals and game_job_signals have no
 * migration here; their writers conflict on source_url and job_id. A live column the
 * select does not name fails this read. There is no column fallback and no empty substitute.
 */
async function fetchAdditionalFamily(
  config: FundingFeedConfig,
  client: TableClient,
  table: AdditionalTable,
  identity: (row: FundingDiscovery) => string,
): Promise<PageRead> {
  const fetchImpl = config.fetchImpl ?? fetch;
  const readHeaders = headers(client.key, { "Accept-Profile": "public", Prefer: "count=exact" });
  const root = client.url.replace(/\/+$/, "");
  const select = ADDITIONAL_SIGNAL_SELECT[table];
  const order = ADDITIONAL_SIGNAL_ORDER[table];
  return readAllRows(
    table,
    `${table} returned an invalid response`,
    (offset) => fetchJson(
      fetchImpl,
      `${root}/rest/v1/${table}?order=${order}&limit=${READ_PAGE}&offset=${offset}&select=${select}`,
      readHeaders,
    ),
    identity,
    true,
    true,
  );
}

async function fetchOptional(
  config: FundingFeedConfig,
  domains: string[],
  table: "signal_companies",
): Promise<Record<string, unknown>[]> {
  const fetchImpl = config.fetchImpl ?? fetch;
  const select = COMPANY_SELECT;
  const root = config.url.replace(/\/+$/, "");
  const readHeaders = headers(config.key, { "Accept-Profile": "leadgrow_knowledge", Prefer: "count=exact" });
  const rows: Record<string, unknown>[] = [];
  for (let start = 0; start < domains.length; start += CHUNK_SIZE) {
    const chunk = domains.slice(start, start + CHUNK_SIZE);
    try {
      // Industry labels are optional. A failed or partial chunk does not block funding coverage.
      const page = await readAllRows(
        table,
        `${table} returned an invalid response`,
        (offset) => fetchJson(
          fetchImpl,
          `${root}/rest/v1/${table}?select=${select}&domain=${inFilter(chunk)}&order=domain.asc&limit=${READ_PAGE}&offset=${offset}`,
          readHeaders,
        ),
        (row) => `domain:${normalizedDomain(row.domain)}`,
        false,
      );
      rows.push(...page.rows);
    } catch {
      // Profiles are optional. A core feed can still be published without them.
    }
  }
  return rows;
}

// Immutable pages under signals/<version>/pN.json.
// signals/current.json is the only key this publisher moves. It is written after every page of
// that version exists. signals/meta.json is the legacy head and is not read, written, or deleted.
// Older version keys stay in place.
// The public Mercury preview still reads one flat GET /data/signals.json and does not fetch these keys.

const FEED_SCHEMA = {
  name: "legion-signals",
  pageSize: SIGNALS_PAGE_SIZE,
  manifest: ["version", "updatedAt", "count", "pageSize", "pages", "types"],
  page1: ["version", "updatedAt", "count", "pageSize", "pages", "types", "page", "signals"],
  pageN: ["version", "page", "pages", "signals"],
  signal: ["type", "company", "domain", "logo", "headline", "metric", "summary", "tags", "location", "people", "date", "source", "sourceUrl", "details", "raisedAgain", "earlier"],
  metric: ["label", "value", "sort"],
  person: ["name", "title", "linkedin"],
  earlier: ["headline", "value", "round", "date", "source", "sourceUrl"],
} as const;

const TYPE_RANK: Record<string, number> = { funding: 0, "product-launch": 1, gaming: 2, hiring: 3 };

function kvUrl(config: FundingFeedConfig, key: string): string {
  const kv = config.legionKv;
  if (!kv?.accountId || !kv.namespaceId || !kv.token) throw new Error("Legion feed KV is not configured");
  return `https://api.cloudflare.com/client/v4/accounts/${kv.accountId}/storage/kv/namespaces/${kv.namespaceId}/values/${encodeURIComponent(key)}`;
}

/** 404 is absence. Any other failed read stays unknown and must not be overwritten as a miss. */
async function kvGet(config: FundingFeedConfig, key: string): Promise<string | null> {
  const response = await (config.fetchImpl ?? fetch)(kvUrl(config, key), {
    headers: { Authorization: `Bearer ${config.legionKv!.token}` },
    signal: AbortSignal.timeout(15_000),
  });
  if (response.status === 404) return null;
  if (!response.ok) throw new Error(`Legion feed KV read failed with HTTP ${response.status}`);
  return response.text();
}

async function kvPut(config: FundingFeedConfig, key: string, body: string): Promise<void> {
  const response = await (config.fetchImpl ?? fetch)(kvUrl(config, key), {
    method: "PUT",
    headers: { Authorization: `Bearer ${config.legionKv!.token}`, "Content-Type": "application/json" },
    body,
    signal: AbortSignal.timeout(15_000),
  });
  if (!response.ok) throw new Error(`Legion feed KV write failed with HTTP ${response.status}`);
}

export function signalsPageKey(version: string, page: number): string {
  if (!/^[0-9a-f]{64}$/.test(version) || !Number.isInteger(page) || page < 1) throw new Error("invalid signals page key");
  return `signals/${version}/p${page}.json`;
}

const SIGNALS_CURRENT_KEY = "signals/current.json";

function canonicalJson(value: unknown): string {
  if (value === undefined) return "null";
  if (value === null || typeof value !== "object") return JSON.stringify(value) ?? "null";
  if (Array.isArray(value)) return `[${value.map((item) => canonicalJson(item)).join(",")}]`;
  const record = value as Record<string, unknown>;
  const keys = Object.keys(record).filter((key) => record[key] !== undefined).sort();
  return `{${keys.map((key) => `${JSON.stringify(key)}:${canonicalJson(record[key])}`).join(",")}}`;
}

function feedFacts(signals: Signal[]): { pages: number; types: Record<string, number> } {
  const pages = Math.max(1, Math.ceil(signals.length / SIGNALS_PAGE_SIZE));
  const types: Record<string, number> = {};
  for (const signal of signals) types[signal.type] = (types[signal.type] ?? 0) + 1;
  return { pages, types };
}

/** Canonical public content. The scan timestamp and the version itself are not inputs. */
export function canonicalFeedDocument(signals: Signal[]): string {
  const facts = feedFacts(signals);
  return canonicalJson({ schema: FEED_SCHEMA, count: signals.length, pages: facts.pages, types: facts.types, signals });
}

export function signalsVersion(signals: Signal[]): string {
  return createHash("sha256").update(canonicalFeedDocument(signals)).digest("hex");
}

function signalKey(signal: Signal): string {
  if (signal.type === "funding") return `funding|${(signal.domain || signal.company).trim().toLowerCase()}`;
  const job = signal.details.jobId ?? "";
  return `${signal.type}|${(signal.domain || signal.company).trim().toLowerCase()}|${signal.sourceUrl}|${signal.headline}|${job}|${signal.date}`;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

function numericTypes(value: unknown): Record<string, number> | null {
  if (!isRecord(value)) return null;
  const types: Record<string, number> = {};
  for (const [key, count] of Object.entries(value)) {
    if (typeof count !== "number" || !Number.isFinite(count)) return null;
    types[key] = count;
  }
  return types;
}

function sameTypes(left: Record<string, number>, right: Record<string, number>): boolean {
  const keys = new Set([...Object.keys(left), ...Object.keys(right)]);
  for (const key of keys) if (left[key] !== right[key]) return false;
  return true;
}

type FeedManifest = {
  version: string;
  updatedAt: string;
  count: number;
  pageSize: number;
  pages: number;
  types: Record<string, number>;
};

/** Legacy hash manifests do not parse. A 200 that is not this shape is not a current version. */
function parseManifest(raw: string | null): FeedManifest | null {
  if (!raw) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!isRecord(parsed)) return null;
  const types = numericTypes(parsed.types);
  if (typeof parsed.version !== "string" || !/^[0-9a-f]{64}$/.test(parsed.version)) return null;
  if (typeof parsed.updatedAt !== "string" || parsed.updatedAt.trim() === "") return null;
  if (typeof parsed.count !== "number" || typeof parsed.pageSize !== "number" || typeof parsed.pages !== "number") return null;
  if (!types) return null;
  return {
    version: parsed.version,
    updatedAt: parsed.updatedAt,
    count: parsed.count,
    pageSize: parsed.pageSize,
    pages: parsed.pages,
    types,
  };
}

type PageHeader = { updatedAt: string; count: number; pageSize: number; pages: number; types: Record<string, number> };

function pageDocument(version: string, page: number, pages: number, signals: Signal[], header: PageHeader | null): Record<string, unknown> {
  if (page === 1) {
    if (!header) throw new Error("signal page 1 is missing its creation header");
    return {
      version,
      updatedAt: header.updatedAt,
      count: header.count,
      pageSize: header.pageSize,
      pages,
      types: header.types,
      page,
      signals,
    };
  }
  return { version, page, pages, signals };
}

/** Matching bytes win, including the original page 1 creation time. A mismatch is not rewritten. */
function storedPageUpdatedAt(raw: string, version: string, page: number, pages: number, signals: Signal[], header: Omit<PageHeader, "updatedAt">): string | null {
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!isRecord(parsed)) return null;
  if (page === 1) {
    if (typeof parsed.updatedAt !== "string" || parsed.updatedAt.trim() === "") return null;
    const expected = pageDocument(version, page, pages, signals, { ...header, updatedAt: parsed.updatedAt });
    return canonicalJson(parsed) === canonicalJson(expected) ? parsed.updatedAt : null;
  }
  return canonicalJson(parsed) === canonicalJson(pageDocument(version, page, pages, signals, null)) ? "" : null;
}

/**
 * Writes signals/<version>/pN.json, then signals/current.json.
 * Version is the SHA-256 of the canonical content. An unchanged current version updates the
 * current head scan time only and does not read or rewrite pages, so the page 1 creation header stays.
 * A version that is not current is read first. Matching pages are left byte-for-byte, including
 * that creation header. A malformed page, a content mismatch, or a non-404 read failure throws
 * before any write. Pages are written from the last page down to page 1, and only the missing ones.
 * Nothing is deleted. The legacy signals/meta.json key is not touched. pagesWritten counts page puts, not the head.
 */
async function publishSignals(config: FundingFeedConfig, signals: Signal[], updatedAt: string): Promise<{ pages: number; written: number; version: string }> {
  const facts = feedFacts(signals);
  const { pages, types } = facts;
  const typeTotal = Object.values(types).reduce((sum, count) => sum + count, 0);
  if (typeTotal !== signals.length) throw new Error("signal type totals do not match the published signals");
  const seen = new Set<string>();
  const slices: Signal[][] = [];
  let covered = 0;
  for (let page = 1; page <= pages; page++) {
    const slice = signals.slice((page - 1) * SIGNALS_PAGE_SIZE, page * SIGNALS_PAGE_SIZE);
    if (slice.length === 0 && signals.length !== 0) throw new Error("signal page coverage mismatch");
    for (const signal of slice) {
      const key = signalKey(signal);
      if (seen.has(key)) throw new Error("duplicate company signal across pages");
      seen.add(key);
      covered++;
    }
    slices.push(slice);
  }
  if (covered !== signals.length) throw new Error("signal page coverage mismatch");
  const version = signalsVersion(signals);
  const header = { count: signals.length, pageSize: SIGNALS_PAGE_SIZE, pages, types };
  const manifest: FeedManifest = { version, updatedAt, ...header };
  const current = parseManifest(await kvGet(config, SIGNALS_CURRENT_KEY));
  if (current?.version === version) {
    if (current.count !== header.count || current.pageSize !== header.pageSize || current.pages !== header.pages || !sameTypes(current.types, header.types)) {
      throw new Error("signals manifest disagrees with its content version");
    }
    if (current.updatedAt !== updatedAt) await kvPut(config, SIGNALS_CURRENT_KEY, JSON.stringify({ ...manifest, updatedAt }));
    return { pages, written: 0, version };
  }
  const stored: Array<"missing" | "match"> = [];
  let page1UpdatedAt = updatedAt;
  for (let page = 1; page <= pages; page++) {
    const raw = await kvGet(config, signalsPageKey(version, page));
    if (raw === null) {
      stored.push("missing");
      continue;
    }
    const createdAt = storedPageUpdatedAt(raw, version, page, pages, slices[page - 1], header);
    if (createdAt === null) throw new Error(`signals/${version}/p${page}.json does not match its content`);
    if (page === 1) page1UpdatedAt = createdAt;
    stored.push("match");
  }
  let written = 0;
  for (let page = pages; page >= 1; page--) {
    if (stored[page - 1] === "match") continue;
    const body = pageDocument(version, page, pages, slices[page - 1], page === 1 ? { ...header, updatedAt: page1UpdatedAt } : null);
    await kvPut(config, signalsPageKey(version, page), JSON.stringify(body));
    written++;
  }
  await kvPut(config, SIGNALS_CURRENT_KEY, JSON.stringify(manifest));
  return { pages, written, version };
}

// ── Rounds table (leadgrow_knowledge.legion_funding_rounds) ──

type RoundCacheEntry = { status: string | null; source: RoundSource | null; checkedAt: string | null };
type RoundCache = Map<string, RoundCacheEntry>;

const RECHECK_CUTOFF: Record<number, string> = { [SEARCH_STRATEGY_VERSION]: SECONDARY_NONE_RECHECK_BEFORE };

/** Missing status is unchecked. A "none" from before the current strategy is unchecked once. "found" stays. */
function secondaryUnchecked(entry: RoundCacheEntry | undefined): boolean {
  if (!entry?.status) return true;
  if (entry.status !== "none") return false;
  const cutoff = Date.parse(RECHECK_CUTOFF[SEARCH_STRATEGY_VERSION] ?? "");
  if (!Number.isFinite(cutoff)) return false;
  const checked = Date.parse(entry.checkedAt ?? "");
  return !Number.isFinite(checked) || checked < cutoff;
}

async function readRoundCache(config: FundingFeedConfig): Promise<RoundCache> {
  const fetchImpl = config.fetchImpl ?? fetch;
  const root = config.url.replace(/\/+$/, "");
  const readHeaders = headers(config.key, { "Accept-Profile": "leadgrow_knowledge", Prefer: "count=exact" });
  const page = await readAllRows(
    "legion_funding_rounds",
    "legion_funding_rounds returned an invalid response",
    (offset) => fetchJson(
      fetchImpl,
      `${root}/rest/v1/legion_funding_rounds?select=round_key,secondary_status,secondary_source,secondary_checked_at&order=round_key&limit=${READ_PAGE}&offset=${offset}`,
      readHeaders,
    ),
    (row) => {
      const key = text(row.round_key);
      return key ? `round:${key}` : `round:${JSON.stringify(row)}`;
    },
    false,
  );
  const cache: RoundCache = new Map();
  for (const row of page.rows) {
    const key = text(row.round_key);
    if (!key || cache.has(key)) continue;
    cache.set(key, {
      status: typeof row.secondary_status === "string" ? row.secondary_status : null,
      source: row.secondary_source && typeof row.secondary_source === "object" ? row.secondary_source as RoundSource : null,
      checkedAt: typeof row.secondary_checked_at === "string" ? row.secondary_checked_at : null,
    });
  }
  return cache;
}

async function roundsRequest(config: FundingFeedConfig, path: string, init: RequestInit): Promise<void> {
  const response = await (config.fetchImpl ?? fetch)(`${config.url.replace(/\/+$/, "")}/rest/v1/${path}`, {
    ...init,
    headers: headers(config.key, { "Content-Type": "application/json", "Content-Profile": "leadgrow_knowledge", Prefer: "resolution=merge-duplicates,return=minimal" }),
    signal: AbortSignal.timeout(30_000),
  });
  if (!response.ok) throw new Error(`legion_funding_rounds ${init.method} failed with HTTP ${response.status}`);
}

/** Upserts every round (secondary_* columns untouched). Does not delete. */
async function upsertRounds(config: FundingFeedConfig, companies: CompanyRounds[], seenAt: string): Promise<number> {
  const rows = companies.flatMap((c) => [c.latest, ...c.earlier]).map((r) => ({
    round_key: r.key, company_key: r.companyKey, company: r.company, domain: r.domain || null, round: r.round === "Unknown" ? null : r.round,
    amount: r.amount, amount_usd: r.amountUsd, investors: r.investors, announced_date: r.date, last_reported: r.lastReported,
    reports: r.reports, sources: r.sources, seen_at: seenAt,
  }));
  for (let start = 0; start < rows.length; start += 500) {
    await roundsRequest(config, "legion_funding_rounds", { method: "POST", body: JSON.stringify(rows.slice(start, start + 500)) });
  }
  return rows.length;
}

/** Drops rounds this complete run no longer produced. Call only after the manifest write succeeds. */
async function pruneRounds(config: FundingFeedConfig, seenAt: string): Promise<void> {
  await roundsRequest(config, `legion_funding_rounds?seen_at=lt.${encodeURIComponent(seenAt)}`, { method: "DELETE" });
}

type SecondarySearchStats = {
  braveLookups: number;
  braveFound: number;
  searchLookups: number;
  searchFound: number;
  costUsd: number;
};

/** Google Search when configured, otherwise Brave. Budget counts queries. Newest raisingfi-only rounds first. */
async function findSecondarySources(config: FundingFeedConfig, companies: CompanyRounds[], cache: RoundCache, now: string): Promise<SecondarySearchStats> {
  const none: SecondarySearchStats = { braveLookups: 0, braveFound: 0, searchLookups: 0, searchFound: 0, costUsd: 0 };
  const googleKey = config.google?.apiKey ?? "";
  const braveKey = config.brave?.apiKey ?? "";
  const mode = googleKey ? "google" : braveKey ? "brave" : null;
  if (!mode) return none;
  const provider = mode === "google" ? config.google! : config.brave!;
  if (!(provider.perRun > 0)) return none;
  const apiKey = mode === "google" ? googleKey : braveKey;
  const todo = companies.flatMap((c) => [c.latest, ...c.earlier]).filter((r) => needsSecondarySource(r) && secondaryUnchecked(cache.get(r.key)));
  let found = 0;
  let lookups = 0;
  for (const round of todo) {
    if (lookups >= provider.perRun) break;
    let source: RoundSource | null | undefined;
    if (mode === "google") {
      const result = await findGoogleSource(round, apiKey);
      lookups += result.queries.length;
      source = result.source;
    } else {
      source = await findSecondarySource(round, apiKey);
      lookups += 1;
    }
    // Provider failure (bad key, quota, outage): stop for this run, leave the round unchecked, retry next run.
    if (source === undefined) break;
    if (source) found++;
    cache.set(round.key, { status: source ? "found" : "none", source, checkedAt: now });
    await roundsRequest(config, `legion_funding_rounds?round_key=eq.${encodeURIComponent(round.key)}`, {
      method: "PATCH",
      body: JSON.stringify({ secondary_source: source, secondary_status: source ? "found" : "none", secondary_checked_at: now }),
    });
  }
  const costUsd = lookups * provider.usdPerQuery;
  if (mode === "google") return { ...none, searchLookups: lookups, searchFound: found, costUsd };
  return { ...none, braveLookups: lookups, braveFound: found, costUsd };
}

async function fetchProfiles(config: FundingFeedConfig, domains: string[]): Promise<CompanyProfile[]> {
  const fetchImpl = config.fetchImpl ?? fetch;
  const root = config.url.replace(/\/+$/, "");
  const readHeaders = headers(config.key, { "Accept-Profile": "leadgrow_knowledge", Prefer: "count=exact" });
  const rows: CompanyProfile[] = [];
  for (let start = 0; start < domains.length; start += CHUNK_SIZE) {
    const chunk = domains.slice(start, start + CHUNK_SIZE);
    const page = await readAllRows(
      "legion_company_profiles",
      "legion_company_profiles returned an invalid response",
      (offset) => fetchJson(
        fetchImpl,
        `${root}/rest/v1/legion_company_profiles?select=${PROFILE_SELECT}&domain=${inFilter(chunk)}&order=domain.asc&limit=${READ_PAGE}&offset=${offset}`,
        readHeaders,
      ),
      (row) => {
        const domain = normalizedDomain(row.domain);
        return domain ? `domain:${domain}` : `profile:${JSON.stringify(row)}`;
      },
      false,
    );
    rows.push(...page.rows);
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

/** Cache only when every field was read. Confirmed empty (absent) may be stored. Unavailable is not a completed lookup. */
function profileReadyToCache(found: CompanyPeopleProfile): boolean {
  return [found.coverage.hq, found.coverage.employees, found.coverage.founders]
    .every((state) => state === "present" || state === "absent");
}

/** Runs the find-people waterfall for domains with no cached profile. Spend counts every call, including ones left uncached. */
async function enrichMissing(config: FundingFeedConfig, domains: string[], profiles: CompanyProfile[]): Promise<{ enriched: number; costUsd: number }> {
  if (!config.enrichment) return { enriched: 0, costUsd: 0 };
  const done = new Set(profiles.map((p) => normalizedDomain(p.domain)));
  const todo = domains.filter((d) => !done.has(d)).slice(0, ENRICH_PER_RUN);
  let costUsd = 0;
  let enriched = 0;
  for (const domain of todo) {
    const found = await findCompanyPeople(domain, config.enrichment);
    const cost = found.calls.reduce((sum, call) => sum + call.costUsd, 0);
    costUsd += cost;
    const row = { domain, hq: found.hq, employees: found.employees, founders: found.founders, sources: found.sources, cost_usd: cost, calls: found.calls };
    if (profileReadyToCache(found)) {
      await upsertProfile(config, row);
      enriched++;
    }
    profiles.push(row);
  }
  return { enriched, costUsd };
}

/** Separate coverage counts. A single record total is not coverage. */
export type FundingCoverage = {
  /** Unique discovery identities read. `id` when present, otherwise a row fingerprint. */
  sourceRows: number;
  /** Extra copies of an identity already read. Not merged a second time. */
  duplicateRows: number;
  /** Dropped because the company name was blank. */
  excludedNoCompany: number;
  /** Dropped because the date was not YYYY-MM-DD. Not turned into a round. */
  excludedUndated: number;
  /** Dated reports merged into a preserved round. */
  mergedReports: number;
  distinctRounds: number;
  /** One funding signal per company. */
  companySignals: number;
  /** Signals placed in the snapshot, including product, gaming, and hiring. */
  publishedSignals: number;
};

/** sourceRows equals excluded + merged + published. duplicateRows is an annotation, not a fourth bucket. */
export type FamilyCoverage = {
  sourceRows: number;
  duplicateRows: number;
  excluded: number;
  merged: number;
  published: number;
};

export type FeedFamilies = {
  funding: FamilyCoverage;
  productLaunches: FamilyCoverage;
  gameSignals: FamilyCoverage;
  jobSignals: FamilyCoverage;
};

export type RefreshResult = {
  updatedAt: string;
  version: string;
  count: number;
  rounds: number;
  raisedAgain: number;
  pages: number;
  pagesWritten: number;
  enriched: number;
  braveLookups: number;
  braveFound: number;
  searchLookups: number;
  searchFound: number;
  costUsd: number;
  coverage: FundingCoverage;
  families: FeedFamilies;
  signals: Signal[];
};

const DATED_REPORT = /^\d{4}-\d{2}-\d{2}/;

function fundingFamily(read: PageRead, excludedNoCompany: number, excludedUndated: number, published: number): FamilyCoverage {
  const excluded = excludedNoCompany + excludedUndated;
  const merged = read.rows.length - excluded - published;
  if (merged < 0) throw new Error("funding family coverage does not add up");
  return { sourceRows: read.rows.length, duplicateRows: read.duplicateRows, excluded, merged, published };
}

function withDuplicates(coverage: AdditionalFamilyCoverage, read: PageRead): FamilyCoverage {
  if (coverage.sourceRows !== read.rows.length) throw new Error("additional signal source rows do not match the read");
  return { ...coverage, duplicateRows: read.duplicateRows };
}

/** Newest known date first. Equal dates keep funding, then product, gaming, and hiring, then company. */
function byNewestDate(a: Signal, b: Signal): number {
  if (a.date !== b.date) {
    if (!a.date) return 1;
    if (!b.date) return -1;
    return a.date < b.date ? 1 : -1;
  }
  const type = (TYPE_RANK[a.type] ?? 9) - (TYPE_RANK[b.type] ?? 9);
  if (type !== 0) return type;
  return a.company.localeCompare(b.company) || a.sourceUrl.localeCompare(b.sourceUrl) || a.headline.localeCompare(b.headline);
}

function assertFamilyFeed(families: FeedFamilies, signals: Signal[]): void {
  for (const family of Object.values(families)) {
    if (family.sourceRows !== family.excluded + family.merged + family.published) {
      throw new Error("signal family coverage does not add up");
    }
  }
  const typeCounts: Record<string, number> = {};
  for (const signal of signals) typeCounts[signal.type] = (typeCounts[signal.type] ?? 0) + 1;
  const expected: Record<string, number> = {
    funding: families.funding.published,
    "product-launch": families.productLaunches.published,
    gaming: families.gameSignals.published,
    hiring: families.jobSignals.published,
  };
  for (const [type, published] of Object.entries(expected)) {
    if ((typeCounts[type] ?? 0) !== published) throw new Error("published family counts do not match the feed");
  }
  if (Object.keys(typeCounts).some((type) => !(type in expected))) throw new Error("published family counts do not match the feed");
}

function configuredSource(table: AdditionalTable, client: TableClient | undefined): TableClient {
  if (!client?.url || !client.key) throw new Error(`${table} source is not configured`);
  return client;
}

export async function refreshFundingFeed(config: FundingFeedConfig): Promise<RefreshResult> {
  if (!config.url || !config.key) throw new Error("Supabase is not configured for legion funding feed");
  if (!config.legionKv?.accountId || !config.legionKv.namespaceId || !config.legionKv.token) {
    throw new Error("Legion feed KV is not configured");
  }
  const productClient = configuredSource("product_launches", config.sources?.productLaunches);
  const gameClient = configuredSource("game_signals", config.sources?.gameSignals);
  const jobClient = configuredSource("game_job_signals", config.sources?.jobSignals);
  const updatedAt = (config.now ?? new Date()).toISOString();
  const read = await fetchFunding(config);
  const funding = read.rows;
  const [productRead, gameRead, jobRead] = await Promise.all([
    fetchAdditionalFamily(config, productClient, "product_launches", sourceUrlIdentity),
    fetchAdditionalFamily(config, gameClient, "game_signals", sourceUrlIdentity),
    fetchAdditionalFamily(config, jobClient, "game_job_signals", jobIdentity),
  ]);
  const domains = domainsFrom(funding);
  const [companies, profiles, cache] = await Promise.all([
    fetchOptional(config, domains, "signal_companies"),
    fetchProfiles(config, domains),
    readRoundCache(config),
  ]);
  const people = await enrichMissing(config, domains, profiles);
  const reports = buildFundingReports(funding, companies, profiles);
  const dated = reports.filter((row) => DATED_REPORT.test(row.date));
  const excludedNoCompany = funding.length - reports.length;
  const excludedUndated = reports.length - dated.length;
  if (funding.length !== excludedNoCompany + excludedUndated + dated.length) {
    throw new Error("funding source coverage does not add up");
  }
  const grouped = buildRounds(dated);
  const roundList = grouped.flatMap((company) => [company.latest, ...company.earlier]);
  const mergedReports = roundList.reduce((sum, round) => sum + round.reports, 0);
  if (mergedReports !== dated.length) throw new Error("funding round merge dropped dated reports");
  const projection = projectAdditionalSignals({
    productLaunches: productRead.rows,
    gameSignals: gameRead.rows,
    jobSignals: jobRead.rows,
  });
  const fundingCoverage = fundingFamily(read, excludedNoCompany, excludedUndated, grouped.length);
  const familiesBeforeWrite: FeedFamilies = {
    funding: fundingCoverage,
    productLaunches: withDuplicates(projection.families.productLaunches, productRead),
    gameSignals: withDuplicates(projection.families.gameSignals, gameRead),
    jobSignals: withDuplicates(projection.families.jobSignals, jobRead),
  };
  const rounds = await upsertRounds(config, grouped, updatedAt);
  const search = await findSecondarySources(config, grouped, cache, updatedAt);
  const secondary = new Map<string, RoundSource>();
  for (const [key, entry] of cache) if (entry.source?.url) secondary.set(key, entry.source);
  const fundingSignals = grouped.map((company) => companySignal(company, secondary));
  const signals = [...fundingSignals, ...projection.signals].sort(byNewestDate);
  assertFamilyFeed(familiesBeforeWrite, signals);
  const published = await publishSignals(config, signals, updatedAt);
  await pruneRounds(config, updatedAt);
  const coverage: FundingCoverage = {
    sourceRows: funding.length,
    duplicateRows: read.duplicateRows,
    excludedNoCompany,
    excludedUndated,
    mergedReports,
    distinctRounds: rounds,
    companySignals: fundingSignals.length,
    publishedSignals: signals.length,
  };
  return {
    updatedAt,
    version: published.version,
    count: signals.length,
    rounds,
    raisedAgain: signals.filter((s) => s.raisedAgain).length,
    pages: published.pages,
    pagesWritten: published.written,
    enriched: people.enriched,
    braveLookups: search.braveLookups,
    braveFound: search.braveFound,
    searchLookups: search.searchLookups,
    searchFound: search.searchFound,
    costUsd: people.costUsd + search.costUsd,
    coverage,
    families: familiesBeforeWrite,
    signals,
  };
}

function runtimeConfig(): FundingFeedConfig {
  const funding = fundingClientFromEnv(process.env);
  return {
    url: funding.url,
    key: funding.key,
    sources: {
      productLaunches: writerClientFromEnv(process.env),
      gameSignals: writerClientFromEnv(process.env),
      jobSignals: writerClientFromEnv(process.env),
    },
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
    brave: {
      apiKey: process.env.BRAVE_SEARCH_API_KEY ?? "",
      perRun: Number(process.env.LEGION_BRAVE_PER_RUN ?? BRAVE_PER_RUN_DEFAULT),
      usdPerQuery: Number(process.env.BRAVE_USD_PER_QUERY ?? 0) || 0,
    },
    google: {
      apiKey: process.env.RAPID_API_KEY ?? "",
      perRun: Number(process.env.LEGION_SEARCH_PER_RUN ?? SEARCH_PER_RUN_DEFAULT),
      usdPerQuery: Number(process.env.RAPID_API_USD_PER_QUERY ?? 0) || 0,
    },
  };
}

export const legionFundingFeed = schedules.task({
  id: "legion-funding-feed",
  cron: { pattern: "*/30 * * * *", timezone: "America/New_York" },
  maxDuration: 900,
  run: async () => {
    const { signals: _signals, ...summary } = await refreshFundingFeed(runtimeConfig());
    logger.info("Legion funding feed published", summary);
    return summary;
  },
});
