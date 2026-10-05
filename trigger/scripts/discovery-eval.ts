/**
 * Dry-run eval for funding discovery (Stage 1 search + Stage 2 scoreAndFilter). Writes nothing to
 * Supabase or Clay: it only reads recent company names for the 7-day dedup. Search responses are
 * cached on disk so re-scoring a query set costs no requests.
 *
 *   lg run --env prod -- npx tsx trigger/scripts/discovery-eval.ts --round a [--days 1] [--pool]
 *     [--num 50] [--enrich 999] [--max-enrich 100] [--pre-drop-low] [--cache <dir>] [--out <file.json>]
 *
 * Search and LLM keys come from gtm-orchestrator, Supabase from this project: run an outer
 * `lg run --env prod` in pipelines/gtm-orchestrator around an inner one in this repo.
 *
 * --pool adds the candidate queries below to the round's configured queries and reports each
 * query's marginal yield. --enrich N fetches + extracts the top N new candidates (Firecrawl + LLM)
 * to measure how many the extraction step rejects as not this round.
 */
import { createHash } from "node:crypto";
import { mkdirSync, readFileSync, writeFileSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { webSearch } from "../src/pipeline/rapid-search.js";
import { normalizeCompanyName, scoreAndFilter } from "../src/pipeline/filters.js";
import { getRecentCompanyNames } from "../src/pipeline/supabase.js";
import { fetchUrl } from "../src/pipeline/firecrawl.js";
import { extractWithOpenAI } from "../src/pipeline/openai.js";
import { applyOutputGates, resolveFundingDate } from "../src/pipeline/pipeline.js";
import { SERIES_A_CONFIG, SERIES_B_CONFIG, SERIES_C_CONFIG } from "../src/pipeline/round-configs.js";
import type { Candidate, QueryDef, RawResult, RoundConfig } from "../src/pipeline/types.js";

const JUNK_SOURCE = /\/jobs?\/|jobs\.|bebee\.com|remotesource\.com|preqin\.com\/data\/profile|yespress\.io|securities\.io|eventbrite\.|cbinsights\.com\/company|pitchbook\.com\/profiles|crunchbase\.com\/organization/i;

const ROUNDS: Record<string, RoundConfig> = { a: SERIES_A_CONFIG, b: SERIES_B_CONFIG, c: SERIES_C_CONFIG };

/** Candidate queries to test against the configured set. L is the round label, e.g. "Series A". */
function poolQueries(letter: string, label: string): QueryDef[] {
  const q = (id: string, query: string, desc: string): QueryDef => ({ id: `x${letter}-${id}`, query, num: 30, desc });
  const sites: [string, string][] = [
    ["techcrunch", "techcrunch.com"], ["siliconangle", "siliconangle.com"], ["axios", "axios.com"],
    ["fortune", "fortune.com"], ["cbnews", "news.crunchbase.com"], ["prweb", "prweb.com"],
    ["globenewswire", "globenewswire.com"], ["accesswire", "accesswire.com"], ["tfn", "techfundingnews.com"],
    ["startupnews", "startupnews.fyi"], ["ventureburn", "ventureburn.com"], ["inc42", "inc42.com"],
    ["e27", "e27.co"], ["techeu", "tech.eu"], ["sifted", "sifted.eu"], ["eustartups", "eu-startups.com"],
    ["businesswire", "businesswire.com"], ["prnewswire", "prnewswire.com"], ["einpresswire", "einpresswire.com"],
    ["pulse2", "pulse2.com"], ["citybiz", "citybiz.co"], ["yahoo", "finance.yahoo.com"],
    ["venturebeat", "venturebeat.com"], ["geekwire", "geekwire.com"], ["fintechglobal", "fintech.global"],
    ["fiercebiotech", "fiercebiotech.com"], ["techinasia", "techinasia.com"], ["entrackr", "entrackr.com"],
    ["betakit", "betakit.com"], ["ctech", "calcalistech.com"], ["dealstreetasia", "dealstreetasia.com"],
    ["techround", "techround.co.uk"], ["uktech", "uktech.news"], ["finextra", "finextra.com"],
    ["bizjournals", "bizjournals.com"],["vcnews", "vcnewsdaily.com"],
  ];
  return [
    ...sites.map(([id, site]) => q(id, `site:${site} "${label}"`, site)),
    q("raises", `"raises $" "${label}"`, "raises $"),
    q("ledby", `"${label} round led by"`, "round led by"),
    q("fundinground", `"${label} funding round"`, "funding round"),
    q("infunding", `"in ${label} funding"`, "in funding"),
    q("financing", `"${label} financing"`, "financing (biotech)"),
    q("million", `"million ${label}"`, "million round"),
    q("closes", `closes "${label}" million`, "closes"),
    q("de", `"${label}" Finanzierungsrunde`, "German"),
    q("fr", `"${label}" levée de fonds`, "French"),
    q("es", `"${label}" ronda inversión`, "Spanish"),
  ];
}

function arg(name: string): string | undefined {
  const i = process.argv.indexOf(`--${name}`);
  return i >= 0 ? process.argv[i + 1] : undefined;
}

function afterDate(days: number): string {
  const d = new Date();
  d.setUTCDate(d.getUTCDate() - days);
  return d.toISOString().slice(0, 10);
}

/** Responses are fetched once at FETCH results; --num N re-scores every query as if it asked for N. */
const FETCH = 100;
const NUM_OVERRIDE = arg("num") ? Number(arg("num")) : undefined;

type Fetched = { queryId: string; results: RawResult[]; error?: string };

async function search(qdef: QueryDef, after: string, cacheDir: string): Promise<Fetched> {
  const key = createHash("sha1").update(`${qdef.query}|${after}|${FETCH}`).digest("hex");
  const file = join(cacheDir, `${key}.json`);
  let rows: { url: string; title: string; snippet: string }[] | undefined;
  if (existsSync(file)) rows = JSON.parse(readFileSync(file, "utf8"));
  else {
    let res;
    for (let attempt = 0; attempt < 4 && !res; attempt++) {
      if (attempt) await new Promise((r) => setTimeout(r, 3000 * attempt));
      res = await webSearch(qdef.query, { limit: FETCH, after, apiKey: process.env.RAPID_API_KEY ?? "", fallback: false });
    }
    if (!res) return { queryId: qdef.id, results: [], error: "google failed" };
    rows = res.results;
    writeFileSync(file, JSON.stringify(rows));
  }
  // Results are ranked, so a smaller num is the prefix of the full response.
  const results = rows!.slice(0, NUM_OVERRIDE ?? qdef.num).map((r): RawResult => {
    let domain = "";
    try { domain = new URL(r.url).hostname; } catch { /* keep blank */ }
    return { company_name_raw: "", amount_raw: "", round_type_raw: "", source_url: r.url, source_domain: domain, snippet: r.snippet.slice(0, 300), title: r.title, query_source: qdef.id };
  });
  return { queryId: qdef.id, results };
}

async function pmap<T, R>(items: T[], n: number, fn: (t: T) => Promise<R>): Promise<R[]> {
  const out: R[] = new Array(items.length);
  let next = 0;
  await Promise.all(Array.from({ length: n }, async () => {
    while (next < items.length) { const i = next++; out[i] = await fn(items[i]); }
  }));
  return out;
}

type Summary = { requests: number; raw: number; uniqueUrls: number; candidates: number; known: number; fresh: number; freshNonLow: number; names: string[] };
export type EvaluatedCandidate = { name: string; outcome: string; extractedName?: string; round?: string | null; amount?: string | null; date?: string | null; url: string; candidateUrl?: string; by: string[]; genuine?: boolean };

// The daily tasks do not export their cap; all three currently use 100.
export const DEFAULT_MAX_ENRICH = 100;

export async function evaluateCandidate(c: Candidate, rc: RoundConfig): Promise<EvaluatedCandidate> {
  const by = [...new Set(c.sources.map((s) => s.query_source))];
  let url = c.best_source_url;
  let text = url ? await fetchUrl(url) : null;
  if (!text && url) {
    for (const source of c.sources) {
      if (source.url === url) continue;
      text = await fetchUrl(source.url);
      if (text) { url = source.url; break; }
    }
  }
  const ex = text ? await extractWithOpenAI(text, c.company_name, c.amount ?? "", rc) : null;
  const outcome = !text ? "fetch_failed" : !ex ? "extract_failed" : ex.company_name === rc.notRoundSentinel ? "not_this_round" : "kept";
  return { name: c.company_name, outcome, extractedName: ex?.company_name, round: ex?.round_type, amount: ex?.amount_raised,
    date: ex?.funding_date, url, candidateUrl: c.best_source_url, by };
}

export function simulateProduction(candidates: Candidate[], enriched: EvaluatedCandidate[], runDate: string, maxEnrich = DEFAULT_MAX_ENRICH, preDropLow = false) {
  const byKey = new Map(enriched.map((e) => [`${e.candidateUrl ?? e.url}|${e.name}`, e]));
  const pool = preDropLow ? candidates.filter((c) => c.confidence !== "low") : candidates;
  return pool.map((c, i) => {
    const e = byKey.get(`${c.best_source_url}|${c.company_name}`);
    let stage = i >= maxEnrich ? "cap" : c.confidence === "low" ? "low_gate" : e?.outcome ?? "not_enriched";
    // Production retains fetch/extract misses as fallback records; only the sentinel rejects.
    if (stage === "fetch_failed" || stage === "extract_failed") stage = "kept";
    if (stage === "kept" && e) {
      const gated = applyOutputGates([{ confidence: c.confidence, funding_date: resolveFundingDate(e.date, e.url) }], runDate);
      if (gated.stale.length) stage = "stale";
      else if (gated.dropped.length) stage = "low_gate";
    }
    return { name: c.company_name, stage, enrichmentOutcome: e?.outcome, genuine: !!e?.genuine && stage === "kept",
      key: normalizeCompanyName(e?.extractedName ?? c.company_name), reasons: c.confidenceReasons };
  });
}

function summarize(rc: RoundConfig, fetched: Fetched[], ids: Set<string>, known: Set<string>): { s: Summary; fresh: Candidate[] } {
  const raw = fetched.filter((f) => ids.has(f.queryId)).flatMap((f) => f.results);
  const scored = scoreAndFilter(raw, rc);
  const fresh = scored.companies.filter((c) => !known.has(c.company_name_normalized));
  const nonLow = fresh.filter((c) => c.confidence !== "low");
  return {
    s: {
      requests: ids.size, raw: raw.length, uniqueUrls: new Set(raw.map((r) => r.source_url)).size,
      candidates: scored.stats.company_count, known: scored.stats.company_count - fresh.length,
      fresh: fresh.length, freshNonLow: nonLow.length, names: nonLow.map((c) => c.company_name),
    },
    fresh,
  };
}

async function main(): Promise<void> {
  const letter = (arg("round") ?? "a").toLowerCase();
  const rc = ROUNDS[letter];
  if (!rc) throw new Error("--round must be a, b or c");
  const days = Number(arg("days") ?? "1");
  const after = afterDate(days);
  const cacheDir = arg("cache") ?? join(tmpdir(), "discovery-eval-cache");
  mkdirSync(cacheDir, { recursive: true });

  const configured = rc.queries;
  const pool = process.argv.includes("--pool") ? poolQueries(letter.toUpperCase(), rc.roundLabel) : [];
  const all = [...configured, ...pool];
  const fetched = await pmap(all, 3, (q) => search(q, after, cacheDir));
  const errors = fetched.filter((f) => f.error);
  const known = await getRecentCompanyNames(rc.supabaseTable, 7);

  const configIds = new Set(configured.map((q) => q.id));
  const base = summarize(rc, fetched, configIds, known);
  console.log(`${rc.roundLabel} after:${after} known(7d)=${known.size} errors=${errors.length}`);
  console.log(`configured: ${JSON.stringify({ ...base.s, names: undefined })}`);

  // Per-query: own yield, and fresh non-low companies no configured query found.
  const baseNames = new Set(base.fresh.filter((c) => c.confidence !== "low").map((c) => c.company_name_normalized));
  const perQuery = all.map((q) => {
    const one = summarize(rc, fetched, new Set([q.id]), known);
    const nonLow = one.fresh.filter((c) => c.confidence !== "low");
    const added = nonLow.filter((c) => ![...baseNames].some((n) => n === c.company_name_normalized));
    return { id: q.id, desc: q.desc, raw: one.s.raw, cands: one.s.candidates, freshNonLow: nonLow.length, newVsConfig: added.map((c) => c.company_name) };
  });
  console.log("id | desc | raw | cands | freshNonLow | new vs configured");
  for (const p of perQuery) console.log(`${p.id} | ${p.desc} | ${p.raw} | ${p.cands} | ${p.freshNonLow} | ${p.newVsConfig.length} ${p.newVsConfig.slice(0, 6).join("; ")}`);

  let union = base;
  if (pool.length) {
    union = summarize(rc, fetched, new Set(all.map((q) => q.id)), known);
    console.log(`configured+pool: ${JSON.stringify({ ...union.s, names: undefined })}`);
  }

  const report: Record<string, unknown> = {
    round: rc.roundLabel, after, configured: base.s, union: union.s, perQuery,
    freshCandidates: union.fresh.map((c) => ({ name: c.company_name, conf: c.confidence, reasons: c.confidenceReasons, score: c.best_score, url: c.best_source_url, by: [...new Set(c.sources.map((s) => s.query_source))], title: c.sources[0]?.title })),
    filtered: scoreAndFilter(fetched.flatMap((f) => f.results), rc).filtered_out,
    errors,
  };

  const enrichN = Number(arg("enrich") ?? "0");
  if (enrichN > 0) {
    const seen = new Set<string>();
    const targets = [...union.fresh, ...base.fresh].filter((c) => c.confidence !== "low" && !seen.has(`${c.best_source_url}|${c.company_name}`) && !!seen.add(`${c.best_source_url}|${c.company_name}`)).slice(0, enrichN);
    const enriched: EvaluatedCandidate[] = await pmap(targets, 5, async (c) => {
      const by = [...new Set(c.sources.map((s) => s.query_source))];
      const file = join(cacheDir, `enrich-${createHash("sha1").update(`${rc.roundType}|${c.best_source_url}|${c.company_name}`).digest("hex")}.json`);
      if (existsSync(file)) return { ...JSON.parse(readFileSync(file, "utf8")), by };
      const row = await evaluateCandidate(c, rc);
      writeFileSync(file, JSON.stringify(row));
      return row;
    });
    // Genuine = extraction kept it, labeled it this round, not dated older than a week before the
    // window, and not from a job board / company-profile / event page (those carry stale rounds).
    const oldest = afterDate(days + 7);
    for (const e of enriched) e.genuine = e.outcome === "kept" && e.round === rc.roundLabel && (!e.date || e.date >= oldest) && !JUNK_SOURCE.test(e.url);
    report.enrich = enriched;
    console.log("enrich:");
    for (const e of enriched) console.log(`  ${e.genuine ? "Y" : "n"} ${e.name} -> ${e.outcome} | ${e.extractedName ?? ""} | ${e.round ?? ""} | ${e.amount ?? ""} | ${e.date ?? ""} | ${e.by.join(",")}`);

    // Distinct genuine companies per query, then greedy query selection by marginal genuine yield.
    const genuineBy = new Map<string, Set<string>>();
    for (const e of enriched.filter((x) => x.genuine)) {
      const key = normalizeCompanyName(e.extractedName ?? e.name);
      for (const id of e.by) genuineBy.set(id, (genuineBy.get(id) ?? new Set()).add(key));
    }
    const distinct = (ids: Iterable<string>) => new Set([...ids].flatMap((id) => [...(genuineBy.get(id) ?? [])])).size;
    console.log(`genuine distinct: configured=${distinct(configIds)} all=${distinct(all.map((q) => q.id))} (of ${enriched.length} enriched, low-confidence excluded)`);
    const chosen: string[] = [];
    const left = new Set(all.map((q) => q.id));
    for (;;) {
      let best = "", gain = 0;
      for (const id of left) { const g = distinct([...chosen, id]) - distinct(chosen); if (g > gain) { gain = g; best = id; } }
      if (!gain) break;
      chosen.push(best); left.delete(best);
      console.log(`  greedy +${best} -> ${distinct(chosen)}`);
    }
    report.greedy = chosen;

    // What the scheduled task would ship from the configured queries: top maxEnrich fresh
    // candidates by score, low-confidence dropped after enrichment, extraction rejects dropped,
    // then the production confidence and freshness gates. Semantic domain validation is not run,
    // so a candidate it would demote to LOW still counts as shipped here.
    const runDate = new Date().toISOString().slice(0, 10);
    const maxEnrich = Number(arg("max-enrich") ?? DEFAULT_MAX_ENRICH);
    // --pre-drop-low models the pipeline skipping LOW candidates before the cap (current code).
    const sim = simulateProduction(base.fresh, enriched, runDate, maxEnrich, process.argv.includes("--pre-drop-low"));
    const count = (st: string) => sim.filter((x) => x.stage === st).length;
    const misses = sim.filter((x) => x.enrichmentOutcome === "fetch_failed" || x.enrichmentOutcome === "extract_failed").length;
    console.log(`production sim (configured, maxEnrich ${maxEnrich}): candidates ${base.s.candidates} known ${base.s.known} fresh ${base.fresh.length} cap ${count("cap")} low_gate ${count("low_gate")} not_this_round ${count("not_this_round")} stale ${count("stale")} fetch/extract_failed ${misses} shipped ${count("kept")} (distinct ${new Set(sim.filter((x) => x.stage === "kept").map((x) => x.key)).size}) genuine distinct ${new Set(sim.filter((x) => x.genuine).map((x) => x.key)).size}`);
    for (const x of sim) console.log(`    ${x.stage} ${x.genuine ? "Y" : "-"} ${x.name}${x.stage === "low_gate" ? ` (${x.reasons?.join("; ")})` : ""}`);
    report.sim = sim;
  }

  const out = arg("out");
  if (out) writeFileSync(out, JSON.stringify(report, null, 2));
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) main().catch((error: unknown) => {
  console.error(error instanceof Error ? error.message : error);
  process.exitCode = 1;
});
