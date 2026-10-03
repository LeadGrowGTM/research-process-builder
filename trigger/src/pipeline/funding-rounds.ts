/**
 * Funding reports -> rounds -> companies, for the Legion /signals feed. Pure, no I/O.
 *
 * funding_discoveries holds one row per *report* of a round; the same round is often
 * reported several times (raisingfi tweet, TechCrunch, a press release, SEC Form D).
 * A named report joins an existing round of the same name when it lands within
 * MERGE_WINDOW_DAYS of that round's first report, so a later article stays on the
 * round it describes. Otherwise a report joins the company's current round when it
 * is inside that window and either leaves the round unknown or reports the same
 * amount. Anything else starts a new round, which is how a company "raises again".
 */

import { isPublicHttpsUrl } from "./taxonomy.js";

export const MERGE_WINDOW_DAYS = 60;

export type FundingReport = {
  company: string;
  domain: string;
  logo: string | null;
  round: string; // "Unknown" when not stated
  amount: string | null;
  amountUsd: number | null;
  investors: string | null;
  industry: string;
  description: string;
  employees: number | string | null;
  hq: string;
  founded: number | null;
  founders: Array<{ name: string; title: string; linkedin: string }>;
  date: string; // YYYY-MM-DD
  source: string | null;
  sourceUrl: string;
};

export type RoundSource = { name: string; url: string };

export type FundingRound = {
  key: string;
  companyKey: string;
  company: string;
  domain: string;
  round: string;
  amount: string | null;
  amountUsd: number | null;
  investors: string | null;
  date: string; // first report
  lastReported: string;
  reports: number;
  sources: RoundSource[];
};

export type CompanyRounds = {
  companyKey: string;
  latest: FundingRound;
  earlier: FundingRound[]; // newest first
  profile: FundingReport; // newest report, for company-level fields
};

const DAY = 86_400_000;

export function companyKeyOf(report: Pick<FundingReport, "company" | "domain">): string {
  return (report.domain || report.company).trim().toLowerCase();
}

/** True for raisingfi's X posts (and X/Twitter links generally), which should not be the shown source. */
export function isRaisingfiSource(source: Partial<RoundSource> | null | undefined): boolean {
  if (!source) return false;
  if (/raisingfi/i.test(source.name ?? "") || /raisingfi/i.test(source.url ?? "")) return true;
  try {
    return /^(www\.)?(x|twitter)\.com$/i.test(new URL(source.url ?? "").hostname);
  } catch {
    return false;
  }
}

function withinWindow(round: FundingRound, report: FundingReport): boolean {
  const days = (Date.parse(report.date) - Date.parse(round.date)) / DAY;
  return days >= 0 && days <= MERGE_WINDOW_DAYS;
}

/** Named reports prefer the round they name. Unknown reports and same-amount reports stay on the current round. */
function findMergeTarget(rounds: FundingRound[], report: FundingReport): FundingRound | undefined {
  const open = rounds.filter((round) => withinWindow(round, report));
  const sameName = open.filter((round) => round.round !== "Unknown" && round.round === report.round);
  if (sameName.length > 0) return sameName[sameName.length - 1];
  const latest = rounds[rounds.length - 1];
  if (!latest || !withinWindow(latest, report)) return undefined;
  if (latest.round === "Unknown" || report.round === "Unknown") return latest;
  if (latest.amountUsd !== null && latest.amountUsd === report.amountUsd) return latest;
  return undefined;
}

function nextRoundKey(companyKey: string, date: string, taken: Set<string>): string {
  const base = `${companyKey}|${date}`;
  if (!taken.has(base)) return base;
  let n = 2;
  while (taken.has(`${base}|${n}`)) n += 1;
  return `${base}|${n}`;
}

function addSource(round: FundingRound, report: FundingReport) {
  const url = report.sourceUrl?.trim() ?? "";
  if (!isPublicHttpsUrl(url)) return;
  const name = (report.source ?? "").trim();
  const existing = round.sources.find((source) => source.url === url);
  if (existing) {
    if (!existing.name && name) existing.name = name;
    return;
  }
  round.sources.push({ name, url });
}

function absorb(round: FundingRound, report: FundingReport) {
  round.reports += 1;
  round.lastReported = report.date;
  if (round.round === "Unknown" && report.round !== "Unknown") round.round = report.round;
  if (report.amountUsd !== null && (round.amountUsd === null || report.amountUsd > round.amountUsd)) {
    round.amountUsd = report.amountUsd;
    round.amount = report.amount;
  } else if (round.amountUsd === null && !round.amount && report.amount) {
    round.amount = report.amount;
  }
  round.investors ??= report.investors;
  addSource(round, report);
}

/** Groups reports into rounds per company. Reports may arrive in any order. */
export function buildRounds(reports: FundingReport[]): CompanyRounds[] {
  const byCompany = new Map<string, FundingReport[]>();
  for (const report of reports) {
    if (!report.company || !/^\d{4}-\d{2}-\d{2}/.test(report.date)) continue;
    const key = companyKeyOf(report);
    const list = byCompany.get(key) ?? [];
    list.push(report);
    byCompany.set(key, list);
  }

  const companies: CompanyRounds[] = [];
  for (const [companyKey, list] of byCompany) {
    list.sort((a, b) => a.date.localeCompare(b.date));
    const rounds: FundingRound[] = [];
    const takenKeys = new Set<string>();
    for (const report of list) {
      const current = findMergeTarget(rounds, report);
      if (current) {
        absorb(current, report);
        continue;
      }
      const key = nextRoundKey(companyKey, report.date, takenKeys);
      takenKeys.add(key);
      const round: FundingRound = {
        key,
        companyKey,
        company: report.company,
        domain: report.domain,
        round: report.round,
        amount: report.amount,
        amountUsd: report.amountUsd,
        investors: report.investors,
        date: report.date,
        lastReported: report.date,
        reports: 1,
        sources: [],
      };
      addSource(round, report);
      rounds.push(round);
    }
    const newestFirst = rounds.reverse();
    companies.push({ companyKey, latest: newestFirst[0], earlier: newestFirst.slice(1), profile: list[list.length - 1] });
  }
  // Newest activity first: a company that raises again moves back to the top.
  return companies.sort((a, b) => b.latest.date.localeCompare(a.latest.date) || a.companyKey.localeCompare(b.companyKey));
}

function publishable(source: RoundSource | null | undefined): source is RoundSource {
  return !!source && isPublicHttpsUrl(source.url);
}

/** The source to show for a round: a public non-raisingfi report, then a public secondary source, then any public source. */
export function displaySource(round: FundingRound, secondary?: RoundSource | null): RoundSource | null {
  return round.sources.find((s) => publishable(s) && !isRaisingfiSource(s))
    ?? (publishable(secondary) ? secondary : null)
    ?? round.sources.find((s) => publishable(s))
    ?? null;
}

/** Rounds whose only sources are raisingfi/X, so a secondary source is worth looking up. */
export function needsSecondarySource(round: FundingRound): boolean {
  return round.sources.length > 0 && round.sources.every(isRaisingfiSource);
}
