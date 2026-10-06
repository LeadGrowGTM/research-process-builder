import { logger, schedules } from "@trigger.dev/sdk";
import { isDomainBlocked, lookupDomainMultiSignal, type DomainResult } from "./pipeline/domain-lookup.js";
import { logoUrlForDomain, normalizeIndustry } from "./pipeline/taxonomy.js";
import { workflowGate } from "./modules/workflow-gate.js";

const SEC_ARCHIVES = "https://www.sec.gov/Archives";
const SEC_USER_AGENT = "research-process-builder/1.0 Mitchell@leadgrow.ai";
export const DEFAULT_MAX_FILINGS = 12;
export const HARD_MAX_FILINGS = 25;
export const DEFAULT_MAX_DOMAIN_LOOKUPS = 6;
export const HARD_MAX_DOMAIN_LOOKUPS = 10;
export const SEC_MIN_REQUEST_GAP_MS = 110;

export interface SecIndexRow {
  cik: string;
  companyName: string;
  formType: string;
  filingDate: string;
  filename: string;
}

export interface SecFormDRecord {
  company_name: string;
  company_domain: string;
  amount_raised: string;
  amount_raised_usd: number;
  amount_raised_currency: "USD";
  round_type: "Unknown";
  source_url: string;
  source_name: "SEC Form D";
  discovered_date: string;
  funding_date: string;
  discovered_by_pipeline: "sec_form_d_daily";
  pipeline_version: "sec-form-d-1.0";
  source_count: number;
  score: number;
  industry: string | null;
  location: string | null;
  raw_text: string;
  article_text: string;
  website_url: string | null;
  logo_url: string | null;
  lead_investors: null;
  round_reasoning: null;
}

export interface SecFormDRunOptions {
  date?: string;
  asOfDate?: string;
  maxFilings?: number;
  maxDomainLookups?: number;
  fetchImpl?: typeof fetch;
  sleep?: (milliseconds: number) => Promise<void>;
  lookupDomain?: (companyName: string, clues: { industry?: string; location?: string }, sourceUrl: string, deadlineAt?: number) => Promise<DomainResult>;
  archivesBaseUrl?: string;
  supabaseUrl?: string;
  supabaseKey?: string;
  minRequestGapMs?: number;
}

function xmlText(xml: string, tag: string): string | null {
  const escaped = tag.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = xml.match(new RegExp(`<${escaped}(?:\\s[^>]*)?>([\\s\\S]*?)</${escaped}>`, "i"));
  return match ? decodeXml(match[1]).trim() || null : null;
}

function decodeXml(value: string): string {
  return value.replace(/<!\[CDATA\[([\s\S]*?)\]\]>/g, "$1").replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&#39;/g, "'");
}

export function parseDailyIndex(text: string, filingDate: string): SecIndexRow[] {
  return text.split(/\r?\n/).flatMap((line) => {
    const [cik, companyName, formType, filed, filename] = line.split("|");
    if (formType !== "D" || filed !== filingDate || !cik || !companyName || !filename) return [];
    return [{ cik, companyName: companyName.trim(), formType, filingDate: filed, filename: filename.trim() }];
  });
}

export function primaryDocumentUrl(row: SecIndexRow, archivesBaseUrl = SEC_ARCHIVES): string {
  const basename = row.filename.split("/").pop() ?? "";
  const accession = basename.replace(/\.txt$/i, "").replace(/-/g, "");
  const directory = row.filename.slice(0, row.filename.length - basename.length).replace(/\/$/, "");
  return `${archivesBaseUrl}/${directory}/${accession}/primary_doc.xml`;
}

export function parseDollarAmount(raw: string | null): number | null {
  if (!raw) return null;
  const normalized = raw.replace(/[$,\s]/g, "");
  if (!/^\d+(?:\.\d+)?$/.test(normalized)) return null;
  const value = Number(normalized);
  return Number.isFinite(value) ? value : null;
}

export function isOperatingCompanyFormD(xml: string): boolean {
  if (xml.length > 1_000_000 || xmlText(xml, "isAmendment") === "true") return false;
  const industry = xmlText(xml, "industryGroupType") ?? "";
  const issuer = xmlText(xml, "primaryIssuer") ?? "";
  const combined = `${industry} ${xmlText(xml, "investmentFundType") ?? ""} ${xmlText(issuer, "entityName") ?? ""}`.toLowerCase();
  if (/pooled investment fund|investment fund|real estate fund|venture capital fund|hedge fund/.test(combined)) return false;
  if (/\bfund\b/i.test(xmlText(issuer, "entityName") ?? "")) return false;
  const offering = parseDollarAmount(xmlText(xml, "totalOfferingAmount"));
  return offering !== null && offering >= 1_000_000;
}

function websiteDomain(raw: string | null): string {
  if (!raw) return "";
  try {
    const url = new URL(raw.includes("://") ? raw : `https://${raw}`);
    const domain = url.hostname.replace(/^www\./, "").toLowerCase();
    return /^(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}$/.test(domain) && !isDomainBlocked(domain) && domain !== "sec.gov" && !domain.endsWith(".sec.gov") ? domain : "";
  } catch {
    return "";
  }
}

export function parseFormDXml(xml: string, row: SecIndexRow, sourceUrl: string): SecFormDRecord | null {
  if (!isOperatingCompanyFormD(xml)) return null;
  const amount = parseDollarAmount(xmlText(xml, "totalAmountSold"));
  if (amount === null || amount <= 0) return null;
  const issuer = xmlText(xml, "primaryIssuer") ?? "";
  const companyName = xmlText(issuer, "entityName") ?? xmlText(issuer, "issuerName") ?? row.companyName;
  if (!companyName) return null;
  const industryRaw = xmlText(xml, "industryGroupType");
  const address = xmlText(issuer, "issuerAddress") ?? "";
  const city = xmlText(address, "city");
  const state = xmlText(address, "stateOrCountry");
  const location = [city, state].filter(Boolean).join(", ") || null;
  // Form D has no trustworthy company website field. Resolve it separately
  // through the bounded, high-confidence lookup below.
  const domain = "";
  return {
    company_name: companyName,
    company_domain: domain,
    amount_raised: `$${amount.toLocaleString("en-US")}`,
    amount_raised_usd: amount,
    amount_raised_currency: "USD",
    round_type: "Unknown",
    source_url: sourceUrl,
    source_name: "SEC Form D",
    discovered_date: row.filingDate,
    funding_date: xmlText(xmlText(xml, "dateOfFirstSale") ?? "", "value")?.match(/^\d{4}-\d{2}-\d{2}$/)?.[0] ?? row.filingDate,
    discovered_by_pipeline: "sec_form_d_daily",
    pipeline_version: "sec-form-d-1.0",
    source_count: 1,
    score: 0,
    industry: normalizeIndustry(industryRaw),
    location,
    raw_text: `SEC Form D: ${companyName}; sold $${amount.toLocaleString("en-US")}; filed ${row.filingDate}.`,
    article_text: `SEC Form D filing for ${companyName}, reporting $${amount.toLocaleString("en-US")} sold.`,
    website_url: domain ? `https://${domain}` : null,
    logo_url: logoUrlForDomain(domain),
    lead_investors: null,
    round_reasoning: null,
  };
}

function dateParts(date: string): { year: string; quarter: number; compact: string } {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || !Number.isFinite(Date.parse(`${date}T00:00:00Z`)) || new Date(`${date}T00:00:00Z`).toISOString().slice(0, 10) !== date) throw new Error("Invalid SEC run date");
  const [year, month] = date.split("-");
  return { year, quarter: Math.floor((Number(month) - 1) / 3) + 1, compact: date.replace(/-/g, "") };
}

function previousBusinessDay(date: string): string {
  const value = new Date(`${date}T00:00:00Z`);
  do { value.setUTCDate(value.getUTCDate() - 1); } while (value.getUTCDay() === 0 || value.getUTCDay() === 6);
  return value.toISOString().slice(0, 10);
}

function headers(key?: string): Record<string, string> {
  return { "User-Agent": SEC_USER_AGENT, Accept: "application/json,text/plain,*/*", ...(key ? { apikey: key, Authorization: `Bearer ${key}`, "Content-Type": "application/json", "Accept-Profile": "public" } : {}) };
}

function isRecentDuplicate(rows: Array<{ round_type?: string | null }>, round: string): boolean {
  return rows.some((row) => {
    const oldRound = (row.round_type ?? "Unknown").trim() || "Unknown";
    return oldRound === "Unknown" || round === "Unknown" || oldRound === round;
  });
}

class SecReadError extends Error {
  constructor(readonly status: number) { super(`SEC core read failed with HTTP ${status}`); }
}

export async function runSecFormDDaily(options: SecFormDRunOptions = {}) {
  const deadlineAt = Date.now() + 8 * 60_000;
  const requestedDate = options.date ?? options.asOfDate ?? new Date().toISOString().slice(0, 10);
  dateParts(requestedDate);
  let date = options.date ?? previousBusinessDay(requestedDate);
  const maxFilings = Math.min(Math.max(Number.isFinite(options.maxFilings) ? Math.floor(options.maxFilings!) : DEFAULT_MAX_FILINGS, 0), HARD_MAX_FILINGS);
  const maxDomainLookups = Math.min(Math.max(Number.isFinite(options.maxDomainLookups) ? Math.floor(options.maxDomainLookups!) : DEFAULT_MAX_DOMAIN_LOOKUPS, 0), HARD_MAX_DOMAIN_LOOKUPS);
  const fetchImpl = options.fetchImpl ?? fetch;
  const sleep = options.sleep ?? ((ms) => new Promise<void>((resolve) => setTimeout(resolve, ms)));
  const lookupDomain = options.lookupDomain ?? lookupDomainMultiSignal;
  const archivesBaseUrl = options.archivesBaseUrl ?? SEC_ARCHIVES;
  const supabaseUrl = options.supabaseUrl ?? (process.env.SUPABASE_PROJECT_URL ?? process.env.SUPABASE_URL ?? "");
  const supabaseKey = options.supabaseKey ?? (process.env.SUPABASE_SERVICE_ROLE_KEY ?? process.env.SUPABASE_KEY ?? "");
  if (!supabaseUrl || !supabaseKey) throw new Error("Supabase is not configured for SEC Form D ingest");

  let lastSecRequest = 0;
  const secFetch = async (url: string): Promise<Response> => {
    if (Date.now() >= deadlineAt) throw new Error("SEC run request budget reached");
    const elapsed = Date.now() - lastSecRequest;
    const gap = Math.max(SEC_MIN_REQUEST_GAP_MS, Number.isFinite(options.minRequestGapMs) ? options.minRequestGapMs! : SEC_MIN_REQUEST_GAP_MS);
    if (lastSecRequest && elapsed < gap) await sleep(gap - elapsed);
    lastSecRequest = Date.now();
    let response: Response | undefined;
    for (let attempt = 0; attempt < 2; attempt++) {
      response = await fetchImpl(url, { headers: headers(), signal: AbortSignal.timeout(Math.max(1, Math.min(20_000, deadlineAt - Date.now()))) });
      if (response.ok || response.status < 500) break;
      await sleep(gap);
      lastSecRequest = Date.now();
    }
    if (!response?.ok) throw new SecReadError(response?.status ?? 0);
    return response;
  };

  let indexResponse: Response | undefined;
  for (let remaining = 0; remaining < 7; remaining++) {
    const parts = dateParts(date);
    const indexUrl = `${archivesBaseUrl}/edgar/daily-index/${parts.year}/QTR${parts.quarter}/master.${parts.compact}.idx`;
    try { indexResponse = await secFetch(indexUrl); break; }
    catch (error) { if (!(error instanceof SecReadError) || error.status !== 404 || options.date) throw error; date = previousBusinessDay(date); }
  }
  if (!indexResponse) throw new Error("SEC core read failed: no completed daily index in 7 days");
  const filings = parseDailyIndex(await indexResponse.text(), date).slice(0, maxFilings);
  const records: SecFormDRecord[] = [];
  let excluded = 0;
  let domainLookups = 0;
  const seenDomains = new Set<string>();
  for (const filing of filings) {
    if (domainLookups >= maxDomainLookups || Date.now() >= deadlineAt) break;
    const sourceUrl = primaryDocumentUrl(filing, archivesBaseUrl);
    const xmlResponse = await secFetch(sourceUrl);
    const xml = await xmlResponse.text();
    const record = parseFormDXml(xml, filing, sourceUrl);
    if (!record) { excluded++; continue; }
    if (!record.company_domain && domainLookups < maxDomainLookups) {
      domainLookups++;
      const industry = xmlText(xml, "industryGroupType");
      const resolved = await lookupDomain(record.company_name, { ...(industry ? { industry } : {}), ...(record.location ? { location: record.location } : {}) }, sourceUrl, deadlineAt);
      const domain = websiteDomain(resolved.domain);
      if (resolved.confidence === "high" && domain) {
        record.company_domain = domain;
        record.website_url = `https://${domain}`;
        record.logo_url = logoUrlForDomain(domain);
      }
    }
    if (!record.company_domain) { excluded++; continue; }
    if (seenDomains.has(record.company_domain)) { excluded++; continue; }
    seenDomains.add(record.company_domain);
    if (record.company_domain) {
      const since = new Date(`${date}T00:00:00Z`);
      since.setUTCDate(since.getUTCDate() - 60);
      const dedupUrl = `${supabaseUrl}/rest/v1/funding_discoveries?company_domain=eq.${encodeURIComponent(record.company_domain)}&discovered_date=gte.${since.toISOString().slice(0, 10)}&select=round_type`;
      const dedup = await fetchImpl(dedupUrl, { headers: headers(supabaseKey), signal: AbortSignal.timeout(15_000) });
      if (!dedup.ok) throw new Error(`SEC Form D dedup read failed: ${dedup.status}`);
      if (isRecentDuplicate(await dedup.json() as Array<{ round_type?: string | null }>, record.round_type)) { excluded++; continue; }
    }
    records.push(record);
  }
  if (records.length > 0) {
    const write = await fetchImpl(`${supabaseUrl}/rest/v1/funding_discoveries?on_conflict=source_url`, {
      method: "POST", headers: { ...headers(supabaseKey), "Content-Profile": "public", Prefer: "resolution=merge-duplicates" }, body: JSON.stringify(records), signal: AbortSignal.timeout(20_000),
    });
    if (!write.ok) throw new Error(`SEC Form D write failed: ${write.status}`);
  }
  return { date, indexed: filings.length, parsed: records.length, written: records.length, excluded, domainLookups };
}

export const secFormDDaily = schedules.task({
  id: "sec-form-d-daily",
  // Schedule moved to go-to-market-trigger-jobs (Smart Enrich) on 2026-10-06.
  // cron: { pattern: "0 8 * * *", timezone: "America/New_York" },
  retry: { maxAttempts: 3, factor: 2, minTimeoutInMs: 10_000, maxTimeoutInMs: 120_000, randomize: true },
  run: async (payload) => {
    const gate = await workflowGate("leadgrow", "funding-sec-form-d");
    if (!gate.active) return { skipped: true, reason: gate.reason };
    const date = payload.timestamp.toISOString().slice(0, 10);
    logger.info("Starting SEC Form D daily ingest", { date, scheduleId: payload.scheduleId });
    return runSecFormDDaily({ asOfDate: date });
  },
});
