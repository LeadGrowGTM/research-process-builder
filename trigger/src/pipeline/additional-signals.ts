import type { Signal } from "../legion-funding-feed.js";
import { isDomainBlocked } from "./domain-lookup.js";
import { registrableDomain } from "./pipeline.js";
import { decodeHtmlEntities, isPublicHttpsUrl, logoUrlForDomain, normalizeOptionalInteger, normalizeOptionalText, sourceNameForUrl } from "./taxonomy.js";

/**
 * Pure map from stored product, game, and game-job rows onto the shared Signal
 * shape. No I/O. Dedup follows producer identity, not company name:
 * product_launches and game_signals conflict on source_url, game_job_signals
 * on job_id. A second product or job is its own signal. A duplicate identity
 * is replaced only when the later row has a later known source date. The same
 * date, or no known date, keeps the earlier row in that producer input.
 * `earlier` stays empty because that list is earlier funding rounds.
 *
 * Hiring rows are whatever game_job_signals stored from 80.lv, including roles
 * the classifier did not mark as animation. The public label is gaming and
 * animation because that is the only hiring source. Studio funding stays type
 * gaming with a reported amount. It is not a completed round.
 *
 * Publisher select (committed columns only). News writes source_domain, snippet,
 * and query_source, but those columns are not in migrations 003 or 004. Do not
 * add them to the PostgREST select until a migration adds them. This mapper
 * still reads source_domain and snippet when a row already has them.
 * Do not select classification_reasoning, query_source, pipeline_version,
 * discovered_by_pipeline, enriched_by, or enriched_at.
 *
 * Order is the writer conflict key, which is unique for a stable page cursor.
 * product_launches is unique on source_url (migration 003). game_signals and
 * game_job_signals have no migration in this repo; the writers conflict on
 * source_url and job_id. A live column the select does not name fails the read.
 */
export const ADDITIONAL_SIGNAL_SELECT = {
  product_launches:
    "discovered_date,company_name,company_domain,product_name,tagline,launch_type,is_ai,score,rank,maker_website,source,source_url,source_name,description,categories,company_location,company_description,employee_count,industry,linkedin_followers,linkedin_url,on_product_hunt",
  game_signals:
    "signal_type,developer,developer_domain,publisher,publisher_domain,game_title,funding_amount,genre,platform,article_date,source_url,summary,date_detected",
  game_job_signals:
    "job_id,job_title,company_name,company_website,company_domain,location_country,location_city,job_type,categories,tags,signal_keywords,signal_strength,job_url,date_posted,date_detected",
} as const;

export const ADDITIONAL_SIGNAL_ORDER = {
  product_launches: "source_url.asc",
  game_signals: "source_url.asc",
  game_job_signals: "job_id.asc",
} as const;

const TYPE_ORDER: Record<string, number> = { "product-launch": 0, gaming: 1, hiring: 2 };

// Hosts that show up as a company website but are the source, a shortener, or a store page.
const NON_COMPANY_HOSTS = new Set([
  "producthunt.com",
  "80.lv",
  "news.ycombinator.com",
  "t.co",
  "bit.ly",
  "lnkd.in",
  "buff.ly",
  "ow.ly",
  "tinyurl.com",
  "youtu.be",
]);

const HIRING_MARKET = "Gaming and animation";

type Row = Record<string, unknown>;

type Input = {
  productLaunches: Array<Record<string, unknown>>;
  gameSignals: Array<Record<string, unknown>>;
  jobSignals: Array<Record<string, unknown>>;
};

function decode(value: string): string {
  return decodeHtmlEntities(value);
}

function publicText(value: unknown): string {
  const raw = normalizeOptionalText(value);
  if (!raw) return "";
  return decode(raw)
    .replace(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/gi, "")
    .replace(/(?:\+?\d{1,3}[\s.-])?(?:\(?\d{3}\)?[\s.-])\d{3}[\s.-]\d{4}/g, "")
    .replace(/\s{2,}/g, " ")
    .replace(/\s+([.,;:])/g, "$1")
    .trim();
}

function named(value: unknown): string {
  const text = publicText(value);
  return text.toLowerCase() === "undisclosed" ? "" : text;
}

/** Producer key. Not passed through contact stripping, so a URL stays the stored identity. */
function identityText(value: unknown): string {
  const raw = normalizeOptionalText(value);
  return raw ? decode(raw).trim() : "";
}

function knownDate(value: unknown): string {
  const text = publicText(value);
  const match = /^(\d{4}-\d{2}-\d{2})(?:[T\s]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?$/.exec(text);
  if (!match) return "";
  const [year, month, day] = match[1].split("-").map(Number);
  if (year < 1990 || year > 2100) return "";
  const parsed = new Date(Date.UTC(year, month - 1, day));
  if (parsed.getUTCFullYear() !== year || parsed.getUTCMonth() !== month - 1 || parsed.getUTCDate() !== day) return "";
  return match[1];
}

function hostname(value: unknown): string {
  const text = publicText(value).toLowerCase();
  if (!text) return "";
  try {
    return new URL(text.includes("://") ? text : `https://${text}`).hostname.replace(/^www\./, "");
  } catch {
    return "";
  }
}

export function companyDomain(value: unknown): string {
  const host = hostname(value);
  if (!host || NON_COMPANY_HOSTS.has(host) || isDomainBlocked(host)) return "";
  if (!/^(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}$/.test(host)) return "";
  const domain = registrableDomain(host);
  return NON_COMPANY_HOSTS.has(domain) || isDomainBlocked(domain) ? "" : domain;
}

/** Public https link, or empty. Credential checks use the raw URL. Contact stripping can turn userinfo into a different URL that still looks public. */
function safeUrl(value: unknown): string {
  const candidate = identityText(value);
  return isPublicHttpsUrl(candidate) ? candidate : "";
}

function companyLinkedin(value: unknown): string {
  const url = safeUrl(value);
  if (!url) return "";
  try {
    const parsed = new URL(url);
    const host = parsed.hostname.toLowerCase().replace(/^www\./, "");
    if (host !== "linkedin.com") return "";
    return parsed.pathname.toLowerCase().startsWith("/company/") || parsed.pathname.toLowerCase().startsWith("/school/") ? url : "";
  } catch {
    return "";
  }
}

function count(value: unknown): number | null {
  return normalizeOptionalInteger(value);
}

function texts(value: unknown): string[] {
  const items = Array.isArray(value) ? value : value == null || value === "" ? [] : [value];
  const seen = new Set<string>();
  const out: string[] = [];
  for (const item of items) {
    const text = named(item);
    if (!text) continue;
    const key = text.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(text);
  }
  return out;
}

function splitList(value: unknown): string[] {
  const text = named(value);
  if (!text) return [];
  return texts(text.split(","));
}

function tagsOf(...groups: string[][]): string[] {
  return texts(groups.flat());
}

function detailsOf(entries: Array<[string, string | number | null | undefined]>): Signal["details"] {
  const details: Signal["details"] = {};
  for (const [key, value] of entries) {
    if (value === undefined || value === null || value === "") continue;
    details[key] = value;
  }
  return details;
}

function makeSignal(fields: Omit<Signal, "people" | "raisedAgain" | "earlier">): Signal {
  return {
    type: fields.type,
    company: fields.company,
    domain: fields.domain,
    logo: fields.logo,
    headline: fields.headline,
    metric: fields.metric,
    summary: fields.summary,
    tags: fields.tags,
    location: fields.location,
    people: [],
    date: fields.date,
    source: fields.source,
    sourceUrl: fields.sourceUrl,
    details: fields.details,
    raisedAgain: false,
    earlier: [],
  };
}

function rowOf(value: unknown): Row | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  return value as Row;
}

function rows(value: unknown): Row[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item) => {
    const row = rowOf(item);
    return row ? [row] : [];
  });
}

function sourceLabel(row: Row, rawUrl: unknown, sourceUrl: string): string | null {
  const supplied = named(row.source_name);
  if (supplied) return supplied;
  const source = named(row.source).toLowerCase().replace(/[\s-]+/g, "_");
  const host = hostname(sourceUrl) || hostname(rawUrl) || hostname(row.source_domain);
  if (source === "product_hunt" || host === "producthunt.com") return "Product Hunt";
  if (sourceUrl) return sourceNameForUrl(sourceUrl);
  if (host) return sourceNameForUrl(`https://${host}`);
  return null;
}

function productDomain(row: Row): string {
  return companyDomain(row.company_domain) || companyDomain(row.maker_website);
}

function productSummary(row: Row): string {
  return named(row.description) || named(row.company_description) || named(row.tagline) || named(row.snippet);
}

function mapProduct(row: Row): Signal | null {
  const sourceUrlRaw = identityText(row.source_url);
  const company = named(row.company_name);
  if (!sourceUrlRaw || !company) return null;
  const launchType = named(row.launch_type).toLowerCase().replace(/[\s-]+/g, "_");
  const sourceKind = named(row.source).toLowerCase().replace(/[\s-]+/g, "_");
  const fromProductHunt = sourceKind === "product_hunt" || hostname(sourceUrlRaw) === "producthunt.com";
  const kind = launchType === "new_feature"
    ? "New feature"
    : launchType === "new_product" || (!launchType && fromProductHunt)
      ? "New product"
      : "Product launch";
  const productName = named(row.product_name);
  const domain = productDomain(row);
  const sourceUrl = safeUrl(sourceUrlRaw);
  const score = sourceKind === "product_hunt" ? count(row.score) : null;
  const employees = count(row.employee_count);
  const followers = count(row.linkedin_followers);
  const rank = count(row.rank);
  const industry = named(row.industry);
  return makeSignal({
    type: "product-launch",
    company,
    domain,
    logo: domain ? logoUrlForDomain(domain) : null,
    headline: productName ? `${kind}: ${productName}` : kind,
    metric: score !== null ? { label: "Product Hunt score", value: String(score), sort: score } : null,
    summary: productSummary(row),
    tags: tagsOf([kind === "Product launch" ? "" : kind, row.is_ai === true ? "AI" : ""], texts(row.categories)),
    location: named(row.company_location),
    date: knownDate(row.discovered_date),
    source: sourceLabel(row, sourceUrlRaw, sourceUrl),
    sourceUrl,
    details: detailsOf([
      ["launchType", launchType === "new_product" || launchType === "new_feature" ? launchType : ""],
      ["productName", productName],
      ["rank", rank],
      ["employees", employees],
      ["industry", industry],
      ["linkedinFollowers", followers],
      ["linkedin", companyLinkedin(row.linkedin_url)],
      ["onProductHunt", row.on_product_hunt === true ? "true" : ""],
    ]),
  });
}

function mapGame(row: Row): Signal | null {
  const sourceUrlRaw = identityText(row.source_url);
  const kind = named(row.signal_type).toLowerCase().replace(/[\s-]+/g, "_");
  if (!sourceUrlRaw || (kind !== "game_announcement" && kind !== "studio_funding")) return null;
  const developer = named(row.developer);
  const publisher = named(row.publisher);
  const company = developer || publisher;
  if (!company) return null;
  const domain = developer ? companyDomain(row.developer_domain) : companyDomain(row.publisher_domain);
  const title = named(row.game_title);
  const amount = named(row.funding_amount);
  const genre = named(row.genre);
  const platform = named(row.platform);
  const sourceUrl = safeUrl(sourceUrlRaw);
  const reported = kind === "studio_funding";
  return makeSignal({
    type: "gaming",
    company,
    domain,
    logo: domain ? logoUrlForDomain(domain) : null,
    headline: reported
      ? amount ? `Reported studio funding of ${amount}` : "Reported studio funding"
      : title ? `Game announcement: ${title}` : "Game announcement",
    metric: reported && amount ? { label: "Reported", value: amount, sort: null } : null,
    summary: named(row.summary),
    tags: tagsOf([reported ? "Studio funding" : "Game announcement", genre], splitList(platform)),
    location: "",
    date: knownDate(row.article_date) || knownDate(row.date_detected),
    source: sourceLabel(row, sourceUrlRaw, sourceUrl),
    sourceUrl,
    details: detailsOf([
      ["kind", kind],
      ["gameTitle", title],
      ["publisher", publisher && publisher.toLowerCase() !== company.toLowerCase() ? publisher : ""],
      ["companyRole", developer ? "" : "publisher"],
      ["genre", genre],
      ["platform", platform],
    ]),
  });
}

function mapJob(row: Row): Signal | null {
  const jobId = count(row.job_id);
  const company = named(row.company_name);
  const title = named(row.job_title);
  if (jobId === null || jobId <= 0 || !company || !title) return null;
  const domain = companyDomain(row.company_domain) || companyDomain(row.company_website);
  const sourceUrl = safeUrl(row.job_url);
  const keywords = texts(row.signal_keywords);
  const strength = named(row.signal_strength).toLowerCase();
  const animationMatch = keywords.length > 0 && (strength === "high" || strength === "medium") ? strength : "";
  return makeSignal({
    type: "hiring",
    company,
    domain,
    logo: domain ? logoUrlForDomain(domain) : null,
    headline: `Hiring: ${title}`,
    metric: null,
    summary: "",
    tags: tagsOf([HIRING_MARKET], texts(row.categories), texts(row.tags), keywords, [named(row.job_type)]),
    location: [named(row.location_city), named(row.location_country)].filter(Boolean).join(", "),
    date: knownDate(row.date_posted) || knownDate(row.date_detected),
    source: sourceLabel(row, row.job_url, sourceUrl),
    sourceUrl,
    details: detailsOf([
      ["market", HIRING_MARKET],
      ["jobType", named(row.job_type)],
      ["animationMatch", animationMatch],
      ["jobId", jobId],
    ]),
  });
}

function identity(prefix: string, value: string): string {
  return `${prefix}:${value}`;
}

/** Later known source date wins. A tie keeps the earlier producer row. */
function better(next: Signal, prev: Signal): boolean {
  if (!next.date || next.date === prev.date) return false;
  if (!prev.date) return true;
  return next.date > prev.date;
}

/** Newest known date first. Then family, company, source URL, and headline. Equal keys keep producer encounter order. */
function compareSignals(a: Signal, b: Signal): number {
  if (a.date !== b.date) {
    if (!a.date) return 1;
    if (!b.date) return -1;
    return a.date < b.date ? 1 : -1;
  }
  const type = (TYPE_ORDER[a.type] ?? 9) - (TYPE_ORDER[b.type] ?? 9);
  if (type !== 0) return type;
  return a.company.localeCompare(b.company)
    || a.sourceUrl.localeCompare(b.sourceUrl)
    || a.headline.localeCompare(b.headline);
}

/** Rows seen by the mapper. Repeated producer identities are merged, not dropped here. */
export type AdditionalFamilyCoverage = {
  sourceRows: number;
  excluded: number;
  merged: number;
  published: number;
};

export type AdditionalProjection = {
  signals: Signal[];
  families: {
    productLaunches: AdditionalFamilyCoverage;
    gameSignals: AdditionalFamilyCoverage;
    jobSignals: AdditionalFamilyCoverage;
  };
};

function cover(
  list: Row[],
  identityOf: (row: Row) => string,
  mapRow: (row: Row) => Signal | null,
): { best: Map<string, Signal>; coverage: AdditionalFamilyCoverage } {
  const best = new Map<string, Signal>();
  let sourceRows = 0;
  let excluded = 0;
  let merged = 0;
  for (const row of list) {
    sourceRows++;
    const id = identityOf(row);
    const signal = id ? mapRow(row) : null;
    if (!id || !signal) {
      excluded++;
      continue;
    }
    const prev = best.get(id);
    if (!prev) {
      best.set(id, signal);
      continue;
    }
    merged++;
    if (better(signal, prev)) best.set(id, signal);
  }
  const published = best.size;
  if (sourceRows !== excluded + merged + published) throw new Error("additional signal coverage does not add up");
  return { best, coverage: { sourceRows, excluded, merged, published } };
}

/** Maps producer rows and counts every row as published, excluded, or merged. */
export function projectAdditionalSignals(input: Input | null | undefined): AdditionalProjection {
  const product = cover(rows(input?.productLaunches), (row) => {
    const key = identityText(row.source_url);
    return key ? identity("product", key) : "";
  }, mapProduct);
  const game = cover(rows(input?.gameSignals), (row) => {
    const key = identityText(row.source_url);
    return key ? identity("game", key) : "";
  }, mapGame);
  const job = cover(rows(input?.jobSignals), (row) => {
    const jobId = count(row.job_id);
    return jobId !== null ? identity("job", String(jobId)) : "";
  }, mapJob);
  return {
    signals: [...product.best.values(), ...game.best.values(), ...job.best.values()].sort(compareSignals),
    families: {
      productLaunches: product.coverage,
      gameSignals: game.coverage,
      jobSignals: job.coverage,
    },
  };
}

export function mapAdditionalSignals(input: Input): Signal[] {
  return projectAdditionalSignals(input).signals;
}
