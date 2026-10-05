/**
 * Finds a secondary source (a news article or press release) for a funding round that
 * was only reported by raisingfi on X, using the Brave Search API.
 */
import { fetchProvider } from "./founders.js";
import { isPublicHttpsUrl, sourceNameForUrl } from "./taxonomy.js";
import type { FundingRound, RoundSource } from "./funding-rounds.js";

const BRAVE_URL = "https://api.search.brave.com/res/v1/web/search";

// Hosts that are not an independent report of the round.
const SKIP_HOST = /(^|\.)(x\.com|twitter\.com|t\.co|linkedin\.com|facebook\.com|instagram\.com|youtube\.com|reddit\.com|threads\.net|tiktok\.com|raisingfi\.[a-z]+)$/i;
const FUNDING_WORDS = /\b(rais(e|es|ed|ing)|funding|seed|series [a-h]|round|secures?|closes?|investment|backed)\b/i;

type BraveResult = { url?: string; title?: string; description?: string };
type RoundIdentity = Pick<FundingRound, "company" | "domain" | "round" | "amount" | "amountUsd">;

function normalizedText(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim().replace(/\s+/g, " ");
}

/** Phrases that must appear as whole words. Suffix-only stripping stays specific enough that "Scale AI" does not match "scale". */
function companyPhrases(company: string): string[] {
  const full = normalizedText(company);
  const stripped = full.replace(/\b(incorporated|inc|llc|ltd|limited|corp|corporation|co|company|labs?|technologies|technology|hq)\b/g, " ").replace(/\s+/g, " ").trim();
  const phrases: string[] = [];
  if (full.length >= 3) phrases.push(full);
  if (stripped.length >= 3 && stripped !== full && (stripped.includes(" ") || stripped.length >= 8)) phrases.push(stripped);
  return phrases;
}

function mentionsPhrase(text: string, phrase: string): boolean {
  const escaped = phrase.replace(/[.*+?^${}()|[\]\\]/g, "\\$&").replace(/ /g, "\\s+");
  return new RegExp(`(?:^|\\s)${escaped}(?:\\s|$)`).test(text);
}

function hostMatchesDomain(hostname: string, domain: string): boolean {
  const host = hostname.toLowerCase().replace(/^www\./, "");
  const root = domain.trim().toLowerCase().replace(/^www\./, "");
  if (!root.includes(".")) return false;
  return host === root || host.endsWith(`.${root}`);
}

function textMentionsDomain(text: string, domain: string): boolean {
  const root = domain.trim().toLowerCase().replace(/^www\./, "");
  if (!root.includes(".") || root.length < 4) return false;
  const escaped = root.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return new RegExp(`(?:^|[^a-z0-9])${escaped}(?:[^a-z0-9]|$)`, "i").test(text);
}

/** The name must be in the headline: a generic name like "Foundational" also turns up as a plain word in snippets. */
function mentionsCompany(round: RoundIdentity, url: URL, title: string, text: string): boolean {
  const normalized = normalizedText(title);
  if (companyPhrases(round.company).some((phrase) => mentionsPhrase(normalized, phrase))) return true;
  return hostMatchesDomain(url.hostname, round.domain) || textMentionsDomain(text, round.domain);
}

function mentionedRounds(text: string): Set<string> {
  const labels = new Set<string>();
  const lower = text.toLowerCase();
  if (/\bpre[\s-]*seed\b/.test(lower)) labels.add("Pre-Seed");
  if (/\bseed\b/.test(lower.replace(/\bpre[\s-]*seed\b/g, " "))) labels.add("Seed");
  for (const match of lower.matchAll(/\bseries\s*([a-z])\b/g)) {
    const letter = match[1];
    if (letter === "a") labels.add("Series A");
    else if (letter === "b") labels.add("Series B");
    else if (letter === "c") labels.add("Series C");
    else labels.add("Series D+");
  }
  if (/\bgrowth equity\b|\bgrowth round\b/.test(lower)) labels.add("Growth");
  if (/\bgrant\b/.test(lower)) labels.add("Grant");
  if (/\bventure debt\b|\bbridge loan\b|\bbridge round\b|\bdebt round\b/.test(lower)) labels.add("Debt");
  return labels;
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/** Phrase match that refuses a digit run sitting inside a longer number. */
function mentionsBoundedPhrase(text: string, phrase: string): boolean {
  const needle = phrase.trim();
  if (needle.length < 2) return false;
  const start = /^\d/.test(needle) ? "(?<![0-9])" : "";
  const end = /\d$/.test(needle) ? "(?![0-9])" : "";
  return new RegExp(`${start}${escapeRegExp(needle)}${end}`, "i").test(text);
}

/** Whole dollar amount. Commas are ignored. A trailing .00 still counts; other cents do not. */
function mentionsBoundedInteger(text: string, value: number): boolean {
  const digits = String(value);
  return new RegExp(`(?<![0-9.])${digits}(?:\\.0+)?(?![0-9.])`).test(text);
}

function mentionsAmount(round: RoundIdentity, text: string): boolean {
  const direct = round.amount?.trim() ?? "";
  if (mentionsBoundedPhrase(text, direct)) return true;
  const usd = round.amountUsd;
  if (usd === null || !Number.isFinite(usd) || usd < 1000) return false;
  const rounded = Math.round(usd);
  const body = text.replace(/,/g, "");
  if (mentionsBoundedInteger(body, rounded)) return true;
  const scales: Array<[number, string]> = [[1e9, "billion"], [1e6, "million"], [1e3, "thousand"]];
  for (const [size, word] of scales) {
    if (usd < size || usd % (size / 10) !== 0) continue;
    const number = escapeRegExp(String(usd / size).replace(/\.0$/, ""));
    // Unit must follow this amount, not a longer number that contains these digits.
    const pattern = new RegExp(`(?:\\$|usd\\s*)${number}(?![0-9.])\\s*(?:${word}|${word[0]})\\b|\\b${number}(?![0-9.])\\s+${word}\\b`, "i");
    if (pattern.test(body)) return true;
  }
  return false;
}

/** A known round needs its own label, or its amount with no conflicting round label. */
function confirmsRound(round: RoundIdentity, text: string): boolean {
  const named = mentionedRounds(text);
  if (round.round === "Unknown") return named.size > 0 || mentionsAmount(round, text) || FUNDING_WORDS.test(text);
  if (named.has(round.round)) return true;
  if ([...named].some((label) => label !== round.round)) return false;
  return mentionsAmount(round, text);
}

/** Picks the first public result about this company and this round. Pure, exported for tests. */
export function pickSecondarySource(round: RoundIdentity, results: BraveResult[]): RoundSource | null {
  for (const result of results) {
    let url: URL;
    try {
      url = new URL(result.url ?? "");
    } catch {
      continue;
    }
    if (!isPublicHttpsUrl(url.href) || SKIP_HOST.test(url.hostname)) continue;
    const title = (result.title ?? "").replace(/<[^>]+>/g, " ");
    const text = `${title} ${result.description ?? ""}`.replace(/<[^>]+>/g, " ");
    if (!mentionsCompany(round, url, title, text) || !FUNDING_WORDS.test(text) || !confirmsRound(round, text)) continue;
    return { name: sourceNameForUrl(url.href) ?? url.hostname.replace(/^www\./, ""), url: url.href };
  }
  return null;
}

export function secondaryQuery(round: Pick<FundingRound, "company" | "round" | "amount">): string {
  const parts = [`"${round.company}"`, "raises"];
  if (round.amount) parts.push(round.amount);
  if (round.round && round.round !== "Unknown") parts.push(round.round);
  parts.push("funding");
  return parts.join(" ");
}

function resultList(value: unknown): BraveResult[] | undefined {
  return Array.isArray(value) ? value as BraveResult[] : undefined;
}

/** One Brave query per call. Returns null when nothing qualifies (a definite miss) and undefined on provider failure (retry later). */
export async function findSecondarySource(round: FundingRound, apiKey: string): Promise<RoundSource | null | undefined> {
  const params = new URLSearchParams({ q: secondaryQuery(round), count: "10", search_lang: "en", safesearch: "off" });
  const { res } = await fetchProvider(`${BRAVE_URL}?${params}`, {
    headers: { Accept: "application/json", "X-Subscription-Token": apiKey },
  });
  if (!res || !res.ok) return undefined;
  const body = (await res.json().catch(() => null)) as { web?: { results?: unknown }; news?: { results?: unknown } } | null;
  if (!body || typeof body !== "object") return undefined;
  const news = resultList(body.news?.results);
  const web = resultList(body.web?.results);
  // A 200 without a result list is an unreadable payload, not proof that no article exists.
  if (!news && !web) return undefined;
  return pickSecondarySource(round, [...(news ?? []), ...(web ?? [])]);
}
