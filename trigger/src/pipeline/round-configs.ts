import type { RoundConfig } from "./types.js";

/**
 * Clay webhook URLs and tokens are credentials, so they come from the environment
 * (Infisical -> Trigger.dev env vars), never from source. Read lazily so a missing
 * value fails the run that needs it, with the variable name in the error.
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

Rules:
- company_name = the company that RAISED money (NOT the investor/VC)
- company_domain = their official website domain (e.g. mosaic.pe, zenskar.com). Check the About section, learn-more links, contact email domains, and inline URLs. Do NOT return the PR wire domain. Return null if truly absent
- amount_raised = exact amount with currency symbol (e.g. "$15M", "EUR10M", "KRW 90B")
- round_type = the stated round, using an allowed taxonomy value, or null if absent
- lead_investors = who led the round, comma-separated, or null if absent
- round_reasoning = why they raised / what funds are for, 1-2 sentences, or null if absent
- industry = choose the closest allowed taxonomy value, or null if unclear
- location = company HQ city and country, or null if absent
- funding_date = date the funding was announced in YYYY-MM-DD format, or null if absent
- If this is NOT actually a ${roundLabel} funding announcement, set company_name to "${sentinel}"

---
Company hint: {{companyHint}}
Amount hint: {{amountHint}}

Article:
{{articleText}}`;
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
    { id: "q3", query: "site:thesaasnews.com Series A", num: 30, desc: "TheSaaSNews" },
    { id: "q4", query: "site:finsmes.com Series A", num: 30, desc: "FinSMEs" },
    { id: "q5", query: "site:alleywatch.com funding report", num: 10, desc: "AlleyWatch" },
    { id: "q9", query: "site:vcnewsdaily.com Series A", num: 10, desc: "VCNewsDaily" },
    { id: "q10", query: "site:infotechlead.com venture capital funding", num: 10, desc: "InfotechLead" },
    { id: "q1", query: '"Series A" raises OR raised OR funding OR round million', num: 30, desc: "broad sweep" },
    { id: "q2", query: '"Series A" announces OR secures OR closes OR completes funding', num: 20, desc: "announcement language" },
    { id: "q6", query: '"Series A" site:businesswire.com OR site:prnewswire.com OR site:einpresswire.com', num: 10, desc: "press wires" },
    { id: "q7", query: '"led the round" OR "led the Series A" OR "led a" Series A investment startup', num: 20, desc: "VC language" },
    { id: "q8", query: '"Series A" startup funding site:eu-startups.com OR site:tech.eu OR site:techround.co.uk', num: 10, desc: "European" },
  ],
  supabaseTable: "funding_discoveries",
  get webhookUrl() {
    return requireEnv("CLAY_SERIES_A_WEBHOOK_URL");
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
    { id: "bq1", query: '"Series B" raises OR raised OR funding OR round million', num: 30, desc: "broad sweep" },
    { id: "bq2", query: '"Series B" announces OR secures OR closes OR completes funding', num: 20, desc: "announcement language" },
    { id: "bq3", query: "site:thesaasnews.com Series B", num: 30, desc: "TheSaaSNews" },
    { id: "bq4", query: "site:finsmes.com Series B", num: 30, desc: "FinSMEs" },
    { id: "bq5", query: '"Series B" site:businesswire.com OR site:prnewswire.com OR site:einpresswire.com', num: 10, desc: "press wires" },
    { id: "bq6", query: '"Series B" growth round OR expansion capital startup', num: 20, desc: "growth language" },
    { id: "bq7", query: '"led the Series B" OR "led a Series B" investment', num: 20, desc: "VC language" },
    { id: "bq8", query: '"Series B" startup funding site:eu-startups.com OR site:tech.eu OR site:techround.co.uk', num: 10, desc: "European" },
  ],
  supabaseTable: "funding_discoveries",
  get webhookUrl() {
    return requireEnv("CLAY_SERIES_B_WEBHOOK_URL");
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
    { id: "cq1", query: '"Series C" raises OR raised OR funding OR round million', num: 30, desc: "broad sweep" },
    { id: "cq2", query: '"Series C" announces OR secures OR closes OR completes funding', num: 20, desc: "announcement language" },
    { id: "cq3", query: "site:thesaasnews.com Series C", num: 30, desc: "TheSaaSNews" },
    { id: "cq4", query: "site:finsmes.com Series C", num: 30, desc: "FinSMEs" },
    { id: "cq5", query: '"Series C" site:businesswire.com OR site:prnewswire.com OR site:einpresswire.com', num: 10, desc: "press wires" },
    { id: "cq6", query: '"Series C" scaling OR expansion OR "late stage" startup', num: 20, desc: "late-stage language" },
    { id: "cq7", query: '"led the Series C" OR "led a Series C" investment', num: 20, desc: "VC language" },
    { id: "cq8", query: '"Series C" startup funding site:eu-startups.com OR site:tech.eu OR site:techround.co.uk', num: 10, desc: "European" },
  ],
  supabaseTable: "funding_discoveries",
  get webhookUrl() {
    return requireEnv("CLAY_SERIES_C_WEBHOOK_URL");
  },
  get webhookAuthToken() {
    return requireEnv("CLAY_SERIES_C_WEBHOOK_TOKEN");
  },
  extractionPrompt: buildExtractionPrompt("Series C", "NOT_SERIES_C"),
};
