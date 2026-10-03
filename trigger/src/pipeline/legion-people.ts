/**
 * Find-people waterfall for the Legion funding feed.
 *
 * (1) QuickEnrich dataset search by company domain, title-filtered to founders
 *     (the API param is `title`; the gateway spec's `job_title` is ignored).
 *     The same rows carry company city/region/country and employee range (HQ).
 * (2) AI Ark people search (founder titles) when QuickEnrich found no founder.
 *
 * Output is public-safe only: founder name, title, LinkedIn. QuickEnrich also
 * returns emails and phone numbers; they are dropped here and never stored.
 */
import { aiArkSearchPeopleOutcome, fetchProvider, MAX_FOUNDERS_PER_COMPANY } from "./founders.js";

export const QUICKENRICH_DATASET_URL = "https://app.quickenrich.io/api/employees/dataset-search";
export const AI_ARK_COST_USD = 0.003;

export type PeopleWaterfallConfig = {
  quickEnrichKey: string;
  aiArkKey: string;
  /** USD per QuickEnrich credit; each dataset page is billed as one credit. */
  quickEnrichUsdPerCredit: number;
};

export type PublicFounder = { name: string; title: string; linkedin: string };
export type ProviderCall = { provider: "quickenrich" | "aiark"; units: number; costUsd: number };
/** present: a value was read. absent: a successful read had no value. unavailable: the read did not succeed. */
export type FieldState = "present" | "absent" | "unavailable";
export type CompanyPeopleProfile = {
  domain: string;
  hq: string | null;
  employees: string | null;
  founders: PublicFounder[];
  sources: string[];
  calls: ProviderCall[];
  /**
   * Callers must not cache a profile when every requested field is unavailable.
   * Confirmed empty is absent and may be cached. A failed read stays unavailable.
   */
  coverage: { hq: FieldState; employees: FieldState; founders: FieldState };
};

// "Founder", "Co-Founder", "Co-Founder & CEO"; not "Founding Engineer".
const FOUNDER_TITLE = /\b(co-?\s?)?founder\b/i;

function clean(value: unknown): string {
  if (typeof value !== "string") return "";
  const t = value.trim();
  return !t || /^(n\/a|none|null|unknown|undefined|not_stated|not_found|unclear)$/i.test(t) ? "" : t;
}

function withoutContacts(value: string): string {
  return value
    .replace(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/gi, " ")
    .replace(/(?:\+|00)?\d[\d\s().-]{8,}\d/g, " ")
    .replace(/\s{2,}/g, " ")
    .trim();
}

function publicLabel(raw: unknown): string {
  const cleaned = withoutContacts(clean(raw)).trim();
  return clean(cleaned);
}

function linkedinProfile(raw: unknown): string {
  try {
    const url = new URL(clean(raw));
    if (url.username || url.password) return "";
    if (!/^(www\.)?linkedin\.com$/i.test(url.hostname) || !/^\/in\/[^/]+\/?$/i.test(url.pathname)) return "";
    return `https://www.linkedin.com${url.pathname.replace(/\/$/, "").toLowerCase()}`;
  } catch {
    return "";
  }
}

/** Picks founders and HQ out of QuickEnrich dataset rows. Pure, exported for tests. */
export function parseQuickEnrichRows(rows: Array<Record<string, unknown>>): { founders: PublicFounder[]; hq: string | null; employees: string | null } {
  const founders: PublicFounder[] = [];
  const seen = new Set<string>();
  let hq: string | null = null;
  let employees: string | null = null;
  for (const row of rows) {
    if (!hq) {
      const city = publicLabel(row.city);
      const place = [city, publicLabel(row.region_code), publicLabel(row.country_code)].filter(Boolean);
      if (city) hq = place.join(", ");
    }
    employees ??= clean(row.employee_count) || null;
    const title = publicLabel(row.title);
    const linkedin = linkedinProfile(row.employee_linkedin);
    const name = [publicLabel(row.first_name), publicLabel(row.last_name)].filter(Boolean).join(" ");
    if (!FOUNDER_TITLE.test(title) || !name || !linkedin || seen.has(linkedin)) continue;
    if (founders.length >= MAX_FOUNDERS_PER_COMPANY) continue;
    seen.add(linkedin);
    founders.push({ name, title, linkedin });
  }
  return { founders, hq, employees };
}

async function quickEnrichPage(domain: string, title: string | null, cfg: PeopleWaterfallConfig, calls: ProviderCall[]) {
  const params = new URLSearchParams({ company_url: domain, page: "1" });
  if (title) params.set("title", title);
  const { res } = await fetchProvider(`${QUICKENRICH_DATASET_URL}?${params}`, {
    headers: { Authorization: `Bearer ${cfg.quickEnrichKey}`, Accept: "application/json" },
  });
  calls.push({ provider: "quickenrich", units: 1, costUsd: cfg.quickEnrichUsdPerCredit });
  if (!res || !res.ok) return { rows: [] as Array<Record<string, unknown>>, known: false };
  const body = (await res.json().catch(() => null)) as { data?: unknown } | null;
  const rows = Array.isArray(body?.data) ? body.data.filter((r): r is Record<string, unknown> => !!r && typeof r === "object") : [];
  return { rows, known: true };
}

async function quickEnrichPeople(domain: string, cfg: PeopleWaterfallConfig, calls: ProviderCall[]) {
  // "title" is a substring filter, so "Founder" matches Co-Founder but not Founding Engineer.
  const founderPage = await quickEnrichPage(domain, "Founder", cfg, calls);
  if (founderPage.rows.length > 0) return { ...parseQuickEnrichRows(founderPage.rows), companyKnown: true };
  // An empty founder filter is not confirmation that the company has no founders.
  const open = await quickEnrichPage(domain, null, cfg, calls);
  return { ...parseQuickEnrichRows(open.rows), companyKnown: open.known };
}

function fieldState(hasValue: boolean, known: boolean): FieldState {
  if (hasValue) return "present";
  return known ? "absent" : "unavailable";
}

export async function findCompanyPeople(domain: string, cfg: PeopleWaterfallConfig): Promise<CompanyPeopleProfile> {
  const calls: ProviderCall[] = [];
  const sources: string[] = [];
  let hq: string | null = null;
  let employees: string | null = null;
  let founders: PublicFounder[] = [];
  let companyKnown = false;
  let foundersKnown = false;

  if (cfg.quickEnrichKey) {
    const qe = await quickEnrichPeople(domain, cfg, calls);
    ({ hq, employees, founders } = qe);
    companyKnown = qe.companyKnown;
    if (qe.founders.length || qe.hq || qe.employees) sources.push("quickenrich");
    if (founders.length > 0) foundersKnown = true;
  }

  if (founders.length === 0 && cfg.aiArkKey) {
    const outcome = await aiArkSearchPeopleOutcome(domain, undefined, cfg.aiArkKey);
    if (outcome.called) calls.push({ provider: "aiark", units: 1, costUsd: AI_ARK_COST_USD });
    if (outcome.called && !outcome.failure) {
      founders = outcome.people
        .map((f) => ({
          name: publicLabel(`${f.first_name} ${f.last_name}`),
          title: publicLabel(f.title),
          linkedin: linkedinProfile(f.linkedin_url),
        }))
        .filter((f) => f.name && f.title && f.linkedin);
      foundersKnown = true;
      if (founders.length) sources.push("aiark");
    }
  }

  return {
    domain,
    hq,
    employees,
    founders,
    sources,
    calls,
    coverage: {
      hq: fieldState(Boolean(hq), companyKnown),
      employees: fieldState(Boolean(employees), companyKnown),
      founders: fieldState(founders.length > 0, foundersKnown),
    },
  };
}
