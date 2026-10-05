// Dry-run eval for the product launch tasks. Writes nothing to Supabase or webhooks.
//
//   news: discovery (direct fetches + search queries) -> GPT classify -> report raw / unique / kept per query.
//   ph:   Product Hunt leaderboard for --date -> extracted and score>=5 counts (no classify, no push).
//
// Run from C:\Users\mitch\Everything_CC\pipelines\gtm-orchestrator so secrets inject:
//   lg run --env prod npx -y tsx <abs path>\launches-eval.ts news --window 1 --set baseline   (baseline = the shipped query set)
//   lg run --env prod npx -y tsx <abs path>\launches-eval.ts news --window 2 --set candidates --no-direct
//   lg run --env prod npx -y tsx <abs path>\launches-eval.ts ph --date 2026-10-02
import { webSearch } from "../src/pipeline/rapid-search.js";
import {
  SERPER_QUERIES,
  classifySpend,
  runClassify,
  runDirectFetches,
  type SerperQuery,
} from "../src/pipeline/product-launches-news.js";
import { fetchLeaderboard } from "../src/pipeline/product-launches-ph.js";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import type { ProductLaunchRaw } from "../src/pipeline/product-launch-types.js";

const args = process.argv.slice(2);
const mode = args[0] ?? "news";
const flag = (name: string, dflt: string): string => {
  const i = args.indexOf(`--${name}`);
  return i >= 0 && args[i + 1] ? args[i + 1] : dflt;
};
const has = (name: string): boolean => args.includes(`--${name}`);

const q = (id: string, query: string, num = 30): SerperQuery => ({ id, desc: id, query, num });

const SETS: Record<string, SerperQuery[]> = {
  baseline: SERPER_QUERIES,
  v2: [
    q("t_bw_launch", 'site:businesswire.com (launches OR unveils OR introduces) TECH'),
    q("t_prn_launch", 'site:prnewswire.com (launches OR unveils OR introduces) TECH'),
    q("t_gn_launch", 'site:globenewswire.com (launches OR unveils OR introduces) TECH'),
    q("t_bw_avail", 'site:businesswire.com ("now available" OR "general availability" OR "announces availability") TECH'),
    q("t_prn_avail", 'site:prnewswire.com ("now available" OR "general availability" OR "announces availability") TECH'),
    q("t_gn_avail", 'site:globenewswire.com ("now available" OR "general availability" OR "announces availability") TECH'),
    q("t_other_launch", '(site:prweb.com OR site:einpresswire.com OR site:accessnewswire.com OR site:newswire.com) (launches OR unveils OR introduces) TECH'),
    q("t_bw_agent", 'site:businesswire.com (agentic OR "AI agents" OR MCP OR "open-source") (launches OR unveils OR announces)'),
    q("t_prn_agent", 'site:prnewswire.com (agentic OR "AI agents" OR MCP OR "open-source") (launches OR unveils OR announces)'),
    q("t_gn_agent", 'site:globenewswire.com (agentic OR "AI agents" OR MCP OR "open-source") (launches OR unveils OR announces)'),
    q("q_tc", 'site:techcrunch.com "launches" OR "announces" OR "introduces" OR "debuts"'),
    q("n_siliconangle", 'site:siliconangle.com launches OR unveils OR introduces OR debuts'),
    q("n_helpnet", "site:helpnetsecurity.com launches OR unveils OR introduces"),
    q("n_vb", "site:venturebeat.com launches OR announces OR unveils"),
    q("n_finextra", "site:finextra.com launches OR unveils"),
    q("n_techtarget", "site:techtarget.com OR site:infoq.com OR site:theregister.com launches OR unveils OR introduces"),
  ].map((x) => ({ ...x, query: x.query.replace("TECH", "(software OR platform OR AI OR SaaS OR API OR cloud OR cybersecurity)") })),
  v3: [
    q("u_bw_debut", 'site:businesswire.com (debuts OR "rolls out" OR releases OR "unveils new") TECH'),
    q("u_prn_debut", 'site:prnewswire.com (debuts OR "rolls out" OR releases OR "unveils new") TECH'),
    q("u_gn_debut", 'site:globenewswire.com (debuts OR "rolls out" OR releases OR "unveils new") TECH'),
    q("u_prn_announces", 'site:prnewswire.com "announces" TECH (new OR launch OR launches)'),
    q("u_bw_announces", 'site:businesswire.com "announces" TECH (new OR launch OR launches)'),
    q("u_acc", 'site:accessnewswire.com (launches OR unveils OR introduces) TECH'),
    q("n_devtools", "site:sdtimes.com OR site:devops.com OR site:thenewstack.io launches OR unveils OR introduces"),
    q("n_martech", "site:martechseries.com launches OR unveils OR introduces"),
    q("n_security", "site:securityweek.com OR site:darkreading.com OR site:scworld.com launches OR unveils OR introduces"),
    q("n_fintech", "site:fintechfutures.com OR site:pymnts.com OR site:finextra.com launches OR unveils OR introduces"),
    q("n_marktech", "site:marktechpost.com OR site:infoworld.com OR site:computerworld.com launches OR unveils OR introduces"),
    q("n_zdnet", "site:zdnet.com OR site:techradar.com OR site:engadget.com launches OR unveils OR introduces"),
    q("n_saas", "site:saasworthy.com OR site:saastr.com OR site:customerthink.com launches OR introduces"),
  ].map((x) => ({ ...x, query: x.query.replace("TECH", "(software OR platform OR AI OR SaaS OR API OR cloud OR cybersecurity)") })),
  // Every candidate at the Google max, so marginal yield per query is comparable.
  candidates: [
    q("q_tc", 'site:techcrunch.com "launches" OR "announces" OR "introduces" OR "debuts"'),
    q("q_vb", "site:venturebeat.com launches OR announces product"),
    q("q_verge", 'site:theverge.com "launches" OR "announces" product'),
    q("q_wire", '"now available" OR "product launch" site:businesswire.com OR site:prnewswire.com'),
    q("w_bw_launch", 'site:businesswire.com "launches" OR "unveils" OR "introduces"'),
    q("w_prn_launch", 'site:prnewswire.com "launches" OR "unveils" OR "introduces"'),
    q("w_gn_launch", 'site:globenewswire.com "launches" OR "unveils" OR "introduces"'),
    q("w_bw_avail", 'site:businesswire.com "now available" OR "announces availability" OR "general availability"'),
    q("w_prn_avail", 'site:prnewswire.com "now available" OR "announces availability" OR "general availability"'),
    q("w_gn_avail", 'site:globenewswire.com "now available" OR "announces availability" OR "general availability"'),
    q("w_prweb", 'site:prweb.com OR site:einpresswire.com OR site:accessnewswire.com "launches" OR "announces launch"'),
    q("w_ai", '"launches" "AI-powered" OR "AI agent" OR "agentic" site:businesswire.com OR site:prnewswire.com OR site:globenewswire.com'),
    q("w_saas", '"launches" platform OR software OR SaaS site:businesswire.com OR site:prnewswire.com OR site:globenewswire.com'),
    q("n_siliconangle", 'site:siliconangle.com launches OR unveils OR introduces OR debuts'),
    q("n_tnw", "site:thenextweb.com launches OR unveils OR introduces"),
    q("n_helpnet", "site:helpnetsecurity.com launches OR unveils OR introduces"),
    q("n_zdnet", "site:zdnet.com launches OR unveils OR introduces"),
    q("n_betalist", "site:betalist.com"),
    q("n_ph", "site:producthunt.com/posts launch"),
    q("g_launches", '"launches" new platform OR tool OR product -site:youtube.com'),
    q("g_introduces", '"introduces" OR "unveils" new AI platform OR tool'),
    q("g_launched", '"we just launched" OR "excited to announce" OR "officially launches" platform OR tool'),
  ],
};

function isoDaysAgo(n: number): string {
  return new Date(Date.now() - n * 86400000).toISOString().slice(0, 10);
}

async function runNews(): Promise<void> {
  const windowDays = Number(flag("window", "1"));
  const setName = flag("set", "baseline");
  const queries = SETS[setName] ?? SETS.baseline;
  const only = flag("only", "");
  const selected = only ? queries.filter((x) => only.split(",").includes(x.id)) : queries;
  const date = isoDaysAgo(0);
  const after = isoDaysAgo(windowDays);

  const cachePath = flag("cache", "");
  let raw: ProductLaunchRaw[];
  let searchedIds: string[] = [];
  let requests = 0;
  if (cachePath && existsSync(cachePath)) {
    raw = JSON.parse(readFileSync(cachePath, "utf8")) as ProductLaunchRaw[];
  } else {
  const direct: ProductLaunchRaw[] = has("no-direct") ? [] : await runDirectFetches(date);

  const searched = [];
  for (const qd of selected) {
    requests++;
    await new Promise((r) => setTimeout(r, 400));
    const r = await webSearch(qd.query, { limit: qd.num, after, apiKey: process.env.RAPID_API_KEY ?? "", fallback: false });
    const items: ProductLaunchRaw[] = (r?.results ?? []).map((x) => {
      let host = "";
      try { host = new URL(x.url).hostname.replace(/^www\./, ""); } catch { /* keep empty */ }
      return { title: x.title, source_url: x.url, source_domain: host, snippet: x.snippet.slice(0, 300), query_source: qd.id };
    });
    searched.push({ id: qd.id, items, failed: !r });
  }

    raw = [...direct, ...searched.flatMap((s) => s.items)];
    if (cachePath) writeFileSync(cachePath, JSON.stringify(raw));
    searchedIds = searched.filter((x) => x.failed).map((x) => x.id);
  }
  const launches = await runClassify(raw);

  // Attribute each kept launch to every query that surfaced its URL, and count exclusive wins.
  const urlSources = new Map<string, Set<string>>();
  for (const r of raw) {
    const s = urlSources.get(r.source_url) ?? new Set<string>();
    s.add(r.query_source.startsWith("tc_direct") || r.query_source.startsWith("hn_") ? r.query_source.replace(/_\d{4}-\d{2}-\d{2}$/, "") : r.query_source);
    urlSources.set(r.source_url, s);
  }
  const keptUrls = new Set(launches.map((l) => l.source_url));
  const rows = [...new Set(raw.map((r) => r.query_source.replace(/_\d{4}-\d{2}-\d{2}$/, "")))].map((id) => {
    const urls = [...new Set(raw.filter((r) => r.query_source.replace(/_\d{4}-\d{2}-\d{2}$/, "") === id).map((r) => r.source_url))];
    const kept = urls.filter((u) => keptUrls.has(u));
    const exclusive = kept.filter((u) => (urlSources.get(u)?.size ?? 0) === 1);
    return { id, raw: raw.filter((r) => r.query_source.replace(/_\d{4}-\d{2}-\d{2}$/, "") === id).length, unique: urls.length, kept: kept.length, exclusiveKept: exclusive.length };
  });
  console.table(rows);
  const failed = searchedIds;
  console.log(JSON.stringify({ set: setName, windowDays, after, requests, raw: raw.length, uniqueUrls: urlSources.size, kept: launches.length, failedQueries: failed, classifyCostUsd: Number(classifySpend.usd.toFixed(5)) }));
  if (!has("quiet")) {
    for (const l of launches) console.log(`KEPT | ${l.company_name} | ${l.product_name} | ${l.source_domain} | ${l.query_source}`);
  }
}

async function runPh(): Promise<void> {
  const date = flag("date", isoDaysAgo(0));
  const products = await fetchLeaderboard(date);
  const kept = products.filter((p) => p.score >= 5);
  console.log(JSON.stringify({ date, extracted: products.length, scoreAtLeast5: kept.length }));
  for (const p of kept) console.log(`KEPT | ${p.rank} | ${p.product_name} | score ${p.score} | ${p.ph_url}`);
}

if (mode === "ph") await runPh();
else await runNews();
