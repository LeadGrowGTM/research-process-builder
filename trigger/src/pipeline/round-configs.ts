import type { QueryDef, RoundConfig } from "./types.js";

/**
 * Clay webhook URLs and tokens are credentials, so they come from the environment
 * (Infisical -> Trigger.dev env vars), never from source. Read lazily so a missing
 * token fails a run with a configured URL, with the variable name in the error.
 * An absent URL disables Clay delivery for that round.
 */
export function requireEnv(name: string): string {
  const value = process.env[name]?.trim();
  if (!value) {
    throw new Error(`Missing required env var ${name}: set it on the Trigger.dev environment (source of truth: Infisical project research-process-builder).`);
  }
  return value;
}

function buildExtractionPrompt(roundLabel: string, sentinel: string): string {
  return `Extract ${roundLabel} funding data from this article.

Return the requested structured fields. Use JSON null for any absent value. Never emit "not_stated" or an empty string.

Decide first whether this page announces a ${roundLabel}:
- It counts only when the page reports, as news, that a named company raised a ${roundLabel}. Extensions and lettered sub-rounds count (e.g. "${roundLabel} extension", "${roundLabel}-2").
- A round mentioned only as background is NOT an announcement: company history ("following its ${roundLabel}", "previously raised"), a company or investor profile, a portfolio or track-record list, an investor presentation, a job posting, a directory, or a "top startups" style list. Set company_name to "${sentinel}".
- These are not a ${roundLabel}: pre-${roundLabel}, any other round letter, seed, debt, convertible notes, SAFEs, grants, crowdfunding, fund closes, acquisitions, secondaries, and preferred stock sold by a public company.
- The company hint may be a page title, a category, or a company that did not raise. On a news listing, roundup, or newsletter, if exactly one company is reported raising a ${roundLabel} as news, extract that company even if it is not the hint. If two or more different companies are reported raising a ${roundLabel}, set company_name to "${sentinel}".

Rules:
- company_name = the company that RAISED money (NOT the investor/VC)
- company_domain = their official website domain (e.g. mosaic.pe, zenskar.com). Check the About section, learn-more links, contact email domains, and inline URLs. Do NOT return the PR wire domain. Return null if truly absent
- amount_raised = exact amount with currency symbol (e.g. "$15M", "EUR10M", "KRW 90B")
- round_type = the stated round, using an allowed taxonomy value, or null if absent
- lead_investors = who led the round, comma-separated, or null if absent
- round_reasoning = why they raised / what funds are for, 1-2 sentences, or null if absent
- industry = choose the closest allowed taxonomy value, or null if unclear
- location = company HQ city and country, or null if absent
- funding_date = date the funding was announced in YYYY-MM-DD format. On a listing or roundup, use the date shown on that item, not the page date. On a single-story article, use the article's publication date if the round itself has no date. Null if no date applies
- If this is NOT actually a ${roundLabel} funding announcement, set company_name to "${sentinel}"

---
Company hint: {{companyHint}}
Amount hint: {{amountHint}}

Article:
{{articleText}}`;
}

/**
 * Phrasing and source queries that found rounds the original set missed (discovery-eval,
 * 2026-10-05). Google returns up to 100 results per request at the same cost. Series A rounds
 * kept turning up at depth 50-100; for B and C the results past 50 were mostly old rounds.
 */
function extraQueries(idPrefix: string, label: string, num: number): QueryDef[] {
  return [
    { id: `${idPrefix}1`, query: `"million ${label}"`, num, desc: "million + round" },
    { id: `${idPrefix}2`, query: `"${label} funding round"`, num, desc: "funding round phrase" },
    { id: `${idPrefix}3`, query: `site:citybiz.co "${label}"`, num, desc: "citybiz" },
    { id: `${idPrefix}4`, query: `site:pulse2.com "${label}"`, num, desc: "Pulse 2.0" },
    { id: `${idPrefix}5`, query: `site:techfundingnews.com "${label}"`, num, desc: "Tech Funding News" },
    { id: `${idPrefix}6`, query: `site:techinasia.com "${label}"`, num, desc: "Tech in Asia" },
    { id: `${idPrefix}7`, query: `site:e27.co "${label}"`, num, desc: "e27" },
    { id: `${idPrefix}8`, query: `site:axios.com "${label}"`, num, desc: "Axios Pro" },
  ];
}

export const SERIES_A_CONFIG: RoundConfig = {
  roundType: "series_a",
  roundLabel: "Series A",
  roundPattern: /\bSeries\s+A\b/i,
  nonRoundPattern:
    /\b(Series\s+[B-Z]|Pre-Seed|pre-seed|Pre-IPO|IPO|Debt|Grant|acquisition|acquires|acquired|merger|SPAC|refinanc)\b/i,
  softNonPattern: /\b(Seed|Growth)\b/i,
  noisePatterns:
    /(?:Series A activity|weekly recap|funding recap|venture market|job search|quarterly.*dividend|financial results|earnings|stock|preferred stock|broadband|announces common|\bTag\b\s*[-|]|\bTag\s*$)/i,
  notRoundSentinel: "NOT_SERIES_A",
  queries: [
    { id: "q3", query: "site:thesaasnews.com Series A", num: 100, desc: "TheSaaSNews" },
    { id: "q4", query: "site:finsmes.com Series A", num: 100, desc: "FinSMEs" },
    { id: "q5", query: "site:alleywatch.com funding report", num: 100, desc: "AlleyWatch" },
    { id: "q1", query: '"Series A" raises OR raised OR funding OR round million', num: 100, desc: "broad sweep" },
    { id: "q2", query: '"Series A" announces OR secures OR closes OR completes funding', num: 100, desc: "announcement language" },
    { id: "q6", query: '"Series A" site:businesswire.com OR site:prnewswire.com OR site:einpresswire.com', num: 100, desc: "press wires" },
    { id: "q7", query: '"led the round" OR "led the Series A" OR "led a" Series A investment startup', num: 100, desc: "VC language" },
    { id: "q8", query: '"Series A" startup funding site:eu-startups.com OR site:tech.eu OR site:techround.co.uk', num: 100, desc: "European" },
    ...extraQueries("qx", "Series A", 100),
  ],
  supabaseTable: "funding_discoveries",
  get webhookUrl() {
    return process.env.CLAY_SERIES_A_WEBHOOK_URL?.trim() ?? "";
  },
  get webhookAuthToken() {
    return requireEnv("CLAY_SERIES_A_WEBHOOK_TOKEN");
  },
  extractionPrompt: buildExtractionPrompt("Series A", "NOT_SERIES_A"),
};

export const SERIES_B_CONFIG: RoundConfig = {
  roundType: "series_b",
  roundLabel: "Series B",
  roundPattern: /\bSeries\s+B\b/i,
  nonRoundPattern:
    /\b(Series\s+[CDEFG-Z]|Series\s+A(?!\s*-?\s*B)|Pre-Seed|pre-seed|Seed\s+round|IPO|Debt|Grant|acquisition|acquires|acquired|merger|SPAC|refinanc)\b/i,
  softNonPattern: /\b(Pre-Series|Bridge)\b/i,
  noisePatterns:
    /(?:Series B activity|weekly recap|funding recap|venture market|job search|quarterly.*dividend|financial results|earnings|stock|preferred stock|broadband|announces common|\bTag\b\s*[-|]|\bTag\s*$)/i,
  notRoundSentinel: "NOT_SERIES_B",
  queries: [
    { id: "bq1", query: '"Series B" raises OR raised OR funding OR round million', num: 50, desc: "broad sweep" },
    { id: "bq2", query: '"Series B" announces OR secures OR closes OR completes funding', num: 50, desc: "announcement language" },
    { id: "bq3", query: "site:thesaasnews.com Series B", num: 50, desc: "TheSaaSNews" },
    { id: "bq4", query: "site:finsmes.com Series B", num: 50, desc: "FinSMEs" },
    { id: "bq5", query: '"Series B" site:businesswire.com OR site:prnewswire.com OR site:einpresswire.com', num: 50, desc: "press wires" },
    { id: "bq6", query: '"Series B" growth round OR expansion capital startup', num: 50, desc: "growth language" },
    { id: "bq7", query: '"led the Series B" OR "led a Series B" investment', num: 50, desc: "VC language" },
    { id: "bq8", query: '"Series B" startup funding site:eu-startups.com OR site:tech.eu OR site:techround.co.uk', num: 50, desc: "European" },
    ...extraQueries("bqx", "Series B", 50),
  ],
  supabaseTable: "funding_discoveries",
  get webhookUrl() {
    return process.env.CLAY_SERIES_B_WEBHOOK_URL?.trim() ?? "";
  },
  get webhookAuthToken() {
    return requireEnv("CLAY_SERIES_B_WEBHOOK_TOKEN");
  },
  extractionPrompt: buildExtractionPrompt("Series B", "NOT_SERIES_B"),
};

export const SERIES_C_CONFIG: RoundConfig = {
  roundType: "series_c",
  roundLabel: "Series C",
  roundPattern: /\bSeries\s+C\b/i,
  nonRoundPattern:
    /\b(Series\s+[DEFG-Z]|Series\s+[AB](?!\s*-?\s*C)|Pre-Seed|pre-seed|Seed\s+round|IPO|Debt|Grant|acquisition|acquires|acquired|merger|SPAC|refinanc)\b/i,
  softNonPattern: /\b(Pre-Series|Bridge)\b/i,
  noisePatterns:
    /(?:Series C activity|weekly recap|funding recap|venture market|job search|quarterly.*dividend|financial results|earnings|stock|preferred stock|broadband|announces common|\bTag\b\s*[-|]|\bTag\s*$)/i,
  notRoundSentinel: "NOT_SERIES_C",
  queries: [
    { id: "cq1", query: '"Series C" raises OR raised OR funding OR round million', num: 50, desc: "broad sweep" },
    { id: "cq2", query: '"Series C" announces OR secures OR closes OR completes funding', num: 50, desc: "announcement language" },
    { id: "cq3", query: "site:thesaasnews.com Series C", num: 50, desc: "TheSaaSNews" },
    { id: "cq4", query: "site:finsmes.com Series C", num: 50, desc: "FinSMEs" },
    { id: "cq5", query: '"Series C" site:businesswire.com OR site:prnewswire.com OR site:einpresswire.com', num: 50, desc: "press wires" },
    { id: "cq6", query: '"Series C" scaling OR expansion OR "late stage" startup', num: 50, desc: "late-stage language" },
    { id: "cq7", query: '"led the Series C" OR "led a Series C" investment', num: 50, desc: "VC language" },
    { id: "cq8", query: '"Series C" startup funding site:eu-startups.com OR site:tech.eu OR site:techround.co.uk', num: 50, desc: "European" },
    ...extraQueries("cqx", "Series C", 50),
  ],
  supabaseTable: "funding_discoveries",
  get webhookUrl() {
    return process.env.CLAY_SERIES_C_WEBHOOK_URL?.trim() ?? "";
  },
  get webhookAuthToken() {
    return requireEnv("CLAY_SERIES_C_WEBHOOK_TOKEN");
  },
  extractionPrompt: buildExtractionPrompt("Series C", "NOT_SERIES_C"),
};
