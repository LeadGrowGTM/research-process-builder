/**
 * Read-only dry run: corroborating sources for published funding signals that have none.
 * Fetches the public Legion feed and calls Google Search via RapidAPI. Prints a table. Writes nothing.
 * Capped at 8 rounds because the search quota is small.
 *
 *   LG_PROJECT=master LG_CONFIRM=master lg run --env prod npx tsx trigger/scripts/source-dryrun.ts
 */
import { findGoogleSource } from "../src/pipeline/google-source.js";
import type { FundingRound } from "../src/pipeline/funding-rounds.js";

const FEED_URL = "https://www.joinlegion.io/data/signals.json";
const WANT = 8;
const ROUND_LABEL = /^(Pre-Seed|Seed|Series A|Series B|Series C|Series D\+|Growth|Debt|Grant)$/;

type PublicSignal = {
  type?: string;
  company?: string;
  domain?: string;
  headline?: string;
  metric?: { value?: string; sort?: number | null } | null;
  tags?: string[];
  date?: string;
  source?: string | null;
};

type FeedPage = { version?: string; pages?: number; signals?: PublicSignal[] };

function isBlankFunding(signal: PublicSignal): boolean {
  return signal.type === "funding" && (signal.source === null || signal.source === "") && !!signal.company;
}

function roundLabel(signal: PublicSignal): string {
  const tag = (signal.tags ?? []).find((item) => ROUND_LABEL.test(item));
  if (tag) return tag;
  const named = (signal.headline ?? "").match(/\b(Pre-Seed|Seed|Series [A-C]|Series D\+|Growth|Debt|Grant)\b/);
  return named?.[1] ?? "Unknown";
}

function asRound(signal: PublicSignal, index: number): FundingRound {
  const company = signal.company ?? "";
  const domain = signal.domain ?? "";
  const date = /^\d{4}-\d{2}-\d{2}/.test(signal.date ?? "") ? (signal.date ?? "").slice(0, 10) : "1970-01-01";
  const amount = signal.metric?.value?.trim() || null;
  const sort = signal.metric?.sort;
  return {
    key: `${domain || company}|${date}|${index}`,
    companyKey: (domain || company).toLowerCase(),
    company,
    domain,
    round: roundLabel(signal),
    amount,
    amountUsd: typeof sort === "number" && Number.isFinite(sort) ? sort : null,
    investors: null,
    date,
    lastReported: date,
    reports: 1,
    sources: [],
  };
}

async function readPage(url: string): Promise<FeedPage> {
  const res = await fetch(url, { signal: AbortSignal.timeout(30_000) });
  if (!res.ok) throw new Error(`feed GET failed with HTTP ${res.status}`);
  const body = (await res.json()) as FeedPage;
  if (!body || !Array.isArray(body.signals)) throw new Error("feed page has no signals array");
  return body;
}

async function loadBlankFunding(): Promise<PublicSignal[]> {
  const page1 = await readPage(FEED_URL);
  const collected = [...(page1.signals ?? [])];
  const pages = page1.pages ?? 1;
  for (let page = 2; page <= pages && collected.filter(isBlankFunding).length < WANT; page++) {
    if (!page1.version) break;
    const next = await readPage(`${FEED_URL}?version=${encodeURIComponent(page1.version)}&page=${page}`);
    collected.push(...(next.signals ?? []));
  }
  return collected.filter(isBlankFunding).slice(0, WANT);
}

function strategyOf(queries: string[], hit: boolean): "a" | "b" | "none" {
  if (!hit) return "none";
  return queries.length <= 1 ? "a" : "b";
}

function cell(value: string): string {
  return value.replace(/\|/g, "/").replace(/\s+/g, " ").trim();
}

async function main(): Promise<void> {
  const apiKey = process.env.RAPID_API_KEY ?? "";
  if (!apiKey) throw new Error("RAPID_API_KEY is not set");
  const signals = await loadBlankFunding();
  const counts = { a: 0, b: 0, none: 0, queries: 0 };
  console.log("company | strategy | url");
  for (const signal of signals) {
    const round = asRound(signal, counts.a + counts.b + counts.none);
    const result = await findGoogleSource(round, apiKey);
    counts.queries += result.queries.length;
    if (result.source === undefined) {
      console.log(`${cell(round.company)} | fail |`);
      console.log(`queries ${counts.queries}`);
      throw new Error(`Google search failed after ${counts.a + counts.b + counts.none} signals`);
    }
    const strategy = strategyOf(result.queries, !!result.source);
    counts[strategy]++;
    console.log(`${cell(round.company)} | ${strategy} | ${result.source?.url ?? ""}`);
  }
  const tried = counts.a + counts.b + counts.none;
  const hits = counts.a + counts.b;
  const rate = (count: number) => (tried === 0 ? "0.0" : ((count / tried) * 100).toFixed(1));
  console.log("---");
  console.log(`signals ${tried} (wanted ${WANT})`);
  console.log(`strategy a ${counts.a} (${rate(counts.a)}%)`);
  console.log(`strategy b ${counts.b} (${rate(counts.b)}%)`);
  console.log(`strategy none ${counts.none} (${rate(counts.none)}%)`);
  console.log(`overall ${hits}/${tried} (${rate(hits)}%)`);
  console.log(`queries ${counts.queries}`);
  if (tried < WANT) throw new Error(`only ${tried} blank funding signals`);
}

main().catch((error: unknown) => {
  const message = error instanceof Error ? error.message : "dry run failed";
  console.error(message);
  process.exitCode = 1;
});
