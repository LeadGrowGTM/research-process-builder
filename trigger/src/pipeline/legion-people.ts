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
import { aiArkSearchPeople, fetchProvider, MAX_FOUNDERS_PER_COMPANY } from "./founders.js";

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
export type CompanyPeopleProfile = {
  domain: string;
  hq: string | null;
  employees: string | null;
  founders: PublicFounder[];
  sources: string[];
  calls: ProviderCall[];
};

// "Founder", "Co-Founder", "Co-Founder & CEO"; not "Founding Engineer".
const FOUNDER_TITLE = /\b(co-?\s?)?founder\b/i;

function clean(value: unknown): string {
  if (typeof value !== "string") return "";
  const t = value.trim();
  return !t || /^(n\/a|none|null|unknown)$/i.test(t) ? "" : t;
}

function linkedinProfile(raw: unknown): string {
  try {
    const url = new URL(clean(raw));
    if (!/^(www\.)?linkedin\.com$/i.test(url.hostname) || !/^\/in\/[^/]+\/?$/.test(url.pathname)) return "";
    return `https://www.linkedin.com${url.pathname.replace(/\/$/, "")}`;
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
      const place = [clean(row.city), clean(row.region_code), clean(row.country_code)].filter(Boolean);
      if (clean(row.city)) hq = place.join(", ");
    }
    employees ??= clean(row.employee_count) || null;
    const title = clean(row.title);
    const linkedin = linkedinProfile(row.employee_linkedin);
    const name = [clean(row.first_name), clean(row.last_name)].filter(Boolean).join(" ");
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
  if (!res || !res.ok) return [];
  const body = (await res.json().catch(() => null)) as { data?: unknown } | null;
  return Array.isArray(body?.data) ? body.data.filter((r): r is Record<string, unknown> => !!r && typeof r === "object") : [];
}

async function quickEnrichPeople(domain: string, cfg: PeopleWaterfallConfig, calls: ProviderCall[]) {
  // "title" is a substring filter, so "Founder" matches Co-Founder but not Founding Engineer.
  const founderRows = await quickEnrichPage(domain, "Founder", cfg, calls);
  if (founderRows.length > 0) return parseQuickEnrichRows(founderRows);
  // No founder rows: one unfiltered page still yields the company's HQ and size.
  return parseQuickEnrichRows(await quickEnrichPage(domain, null, cfg, calls));
}

export async function findCompanyPeople(domain: string, cfg: PeopleWaterfallConfig): Promise<CompanyPeopleProfile> {
  const calls: ProviderCall[] = [];
  const sources: string[] = [];
  let hq: string | null = null;
  let employees: string | null = null;
  let founders: PublicFounder[] = [];

  if (cfg.quickEnrichKey) {
    const qe = await quickEnrichPeople(domain, cfg, calls);
    ({ hq, employees, founders } = qe);
    if (qe.founders.length || qe.hq) sources.push("quickenrich");
  }

  if (founders.length === 0 && cfg.aiArkKey) {
    const found = await aiArkSearchPeople(domain, undefined, cfg.aiArkKey);
    calls.push({ provider: "aiark", units: 1, costUsd: AI_ARK_COST_USD });
    founders = found
      .map((f) => ({ name: `${f.first_name} ${f.last_name}`.trim(), title: f.title, linkedin: linkedinProfile(f.linkedin_url) }))
      .filter((f) => f.name && f.linkedin);
    if (founders.length) sources.push("aiark");
  }

  return { domain, hq, employees, founders, sources, calls };
}
