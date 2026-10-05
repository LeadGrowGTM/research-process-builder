import { logger } from "@trigger.dev/sdk";
import { searchSerper } from "./serper.js";
import { isLunaConfigured, lunaJson } from "./luna.js";
import type { ProductLaunchRaw, ProductLaunchPipelineResult } from "./product-launch-types.js";
import { hasTime, LAUNCH_RUN_BUDGET_MS, LAUNCH_WRITE_BATCH_SIZE, LAUNCH_WRITE_TIMEOUT_MS, persistenceReserveMs } from "./launch-budget.js";

const SEARCH_BUDGET_MS = 31_000;

const SUPABASE_URL = (() => {
  const url = process.env.SUPABASE_PROJECT_URL ?? process.env.SUPABASE_URL ?? "";
  return url.startsWith("http") ? url : "";
})();
const SUPABASE_KEY =
  process.env.SUPABASE_KEY ??
  process.env.SUPABASE_SERVICE_ROLE_KEY ??
  process.env.SUPABASE_ANON_KEY ??
  "";

const FETCH_HEADERS = { "User-Agent": "Mozilla/5.0 (compatible; LeadGrow/1.0)" };
const TABLE = "product_launches";
const HN_MIN_POINTS = 3;

// ---------------------------------------------------------------------------
// Serper supplement queries (Stage 1B)
// ---------------------------------------------------------------------------

export interface SerperQuery {
  id: string;
  desc: string;
  query: string;
  num: number;
}

// Tech terms keep the wire queries on software launches; without them the wires return food, pharma, and hardware.
const TECH = "(software OR platform OR AI OR SaaS OR API OR cloud OR cybersecurity)";
const WIRES = ["businesswire.com", "prnewswire.com", "globenewswire.com"];
const AGENT_TERMS = '(agentic OR "AI agents" OR MCP OR "open-source")';
const LAUNCH_VERBS = "(launches OR unveils OR introduces)";

function q(id: string, query: string, desc = id): SerperQuery {
  return { id, desc, query, num: 30 };
}

// Measured 2026-10-05 with scripts/launches-eval.ts: all 17 shipped queries added launches no other query found.
// Dropped as noise: generic "launches" queries (social posts, local news), betalist, the VentureBeat and Verge
// site queries (0-1 results a day), and the TechCrunch site query (the TC date page already covers it).
export const SERPER_QUERIES: SerperQuery[] = [
  ...WIRES.map((w) => q(`w_${w.split(".")[0]}_launch`, `site:${w} ${LAUNCH_VERBS} ${TECH}`)),
  ...WIRES.slice(0, 2).map((w) => q(`w_${w.split(".")[0]}_avail`, `site:${w} ("now available" OR "general availability" OR "announces availability") ${TECH}`)),
  ...WIRES.slice(0, 2).map((w) => q(`w_${w.split(".")[0]}_agent`, `site:${w} ${AGENT_TERMS} (launches OR unveils OR announces)`)),
  ...WIRES.slice(0, 2).map((w) => q(`w_${w.split(".")[0]}_debut`, `site:${w} (debuts OR "rolls out" OR releases OR "unveils new") ${TECH}`)),
  q("w_prn_announces", `site:prnewswire.com "announces" ${TECH} (new OR launch OR launches)`),
  q("w_other_launch", `(site:prweb.com OR site:einpresswire.com OR site:accessnewswire.com OR site:newswire.com) ${LAUNCH_VERBS} ${TECH}`),
  q("n_siliconangle", "site:siliconangle.com launches OR unveils OR introduces OR debuts"),
  q("n_helpnet", "site:helpnetsecurity.com launches OR unveils OR introduces"),
  q("n_fintech", "site:fintechfutures.com OR site:pymnts.com OR site:finextra.com launches OR unveils OR introduces"),
  q("n_martech", "site:martechseries.com launches OR unveils OR introduces"),
  q("n_dev", "site:marktechpost.com OR site:infoworld.com OR site:computerworld.com launches OR unveils OR introduces"),
  q("n_tech", "site:zdnet.com OR site:techradar.com OR site:engadget.com launches OR unveils OR introduces"),
];

// ---------------------------------------------------------------------------
// HTML parsing utilities (regex-based, no cheerio)
// ---------------------------------------------------------------------------

// TC article URLs match: https://techcrunch.com/YYYY/MM/DD/slug/
const TC_ARTICLE_RE = /https:\/\/techcrunch\.com\/\d{4}\/\d{2}\/\d{2}\/[^/"]+\/?/g;

function parseTCArticles(html: string): Array<{ title: string; url: string }> {
  const results: Array<{ title: string; url: string }> = [];
  const seen = new Set<string>();

  // Find all <a href="https://techcrunch.com/YYYY/MM/DD/slug/">TITLE</a>
  // We scan for anchor tags and check the href pattern
  const anchorRe = /<a\s+[^>]*href="(https:\/\/techcrunch\.com\/\d{4}\/\d{2}\/\d{2}\/[^"]+)"[^>]*>([^<]{20,})<\/a>/gi;
  let match: RegExpExecArray | null;
  while ((match = anchorRe.exec(html)) !== null) {
    const url = match[1].split("?")[0].replace(/\/$/, "") + "/";
    const title = match[2].replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">").trim();
    if (!seen.has(url) && title.length > 20) {
      seen.add(url);
      results.push({ url, title });
    }
  }

  return results;
}

function domainFromUrl(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return "";
  }
}

// ---------------------------------------------------------------------------
// Stage 1A: Direct source fetches
// ---------------------------------------------------------------------------

async function fetchTCDatePage(dateStr: string, deadlineAt?: number): Promise<ProductLaunchRaw[]> {
  if (!hasTime(deadlineAt, 20_000)) return [];
  const [year, month, day] = dateStr.split("-");
  const url = `https://techcrunch.com/${year}/${month}/${day}/`;
  try {
    const resp = await fetch(url, {
      headers: FETCH_HEADERS,
      signal: AbortSignal.timeout(20_000),
    });
    if (!resp.ok) {
      logger.warn(`TC ${dateStr}: HTTP ${resp.status}`);
      return [];
    }
    const html = await resp.text();
    const articles = parseTCArticles(html);
    logger.info(`TC ${dateStr}: ${articles.length} articles`);
    return articles.map((a) => ({
      title: a.title,
      source_url: a.url,
      source_domain: "techcrunch.com",
      snippet: "",
      query_source: `tc_direct_${dateStr}`,
    }));
  } catch (err) {
    logger.warn(`TC ${dateStr}: ${err instanceof Error ? err.message : String(err)}`);
    return [];
  }
}

interface AlgoliaHit {
  objectID: string;
  title?: string;
  url?: string | null;
  points?: number;
}

/**
 * HN via the Algolia API. The hn.algolia.com JSON is stable; the news.ycombinator.com HTML answers
 * 419 "Sorry." to cloud IPs, which is why the scraped Show HN and front pages produced nothing.
 */
export async function fetchHNAlgolia(tag: "show_hn" | "front_page", queryId: string, minPoints: number, windowHours: number, deadlineAt?: number): Promise<ProductLaunchRaw[]> {
  if (!hasTime(deadlineAt, 20_000)) return [];
  const since = Math.floor(Date.now() / 1000) - windowHours * 3600;
  const url = `https://hn.algolia.com/api/v1/search_by_date?tags=${tag}&numericFilters=${encodeURIComponent(`created_at_i>${since},points>=${minPoints}`)}&hitsPerPage=100`;
  try {
    const resp = await fetch(url, { headers: FETCH_HEADERS, signal: AbortSignal.timeout(20_000) });
    if (!resp.ok) {
      logger.warn(`HN ${queryId}: HTTP ${resp.status}`);
      return [];
    }
    const { hits = [] } = (await resp.json()) as { hits?: AlgoliaHit[] };
    logger.info(`HN ${queryId}: ${hits.length} items`);
    return hits.filter((h) => h.title).map((h) => {
      const link = h.url || `https://news.ycombinator.com/item?id=${h.objectID}`;
      return {
        title: h.title as string,
        source_url: link,
        source_domain: domainFromUrl(link) || "news.ycombinator.com",
        snippet: "",
        query_source: queryId,
      };
    });
  } catch (err) {
    logger.warn(`HN ${queryId}: ${err instanceof Error ? err.message : String(err)}`);
    return [];
  }
}

export async function runDirectFetches(dateStr: string, deadlineAt?: number): Promise<ProductLaunchRaw[]> {
  const yesterday = new Date(dateStr);
  yesterday.setDate(yesterday.getDate() - 1);
  const yesterdayStr = yesterday.toISOString().split("T")[0];

  const [tc1, tc2, hnShow, hnFront] = await Promise.all([
    fetchTCDatePage(dateStr, deadlineAt),
    fetchTCDatePage(yesterdayStr, deadlineAt),
    fetchHNAlgolia("show_hn", "hn_show", HN_MIN_POINTS, 36, deadlineAt),
    fetchHNAlgolia("front_page", `hn_front_${dateStr}`, 0, 36, deadlineAt),
  ]);

  const all = [...tc1, ...tc2, ...hnShow, ...hnFront];
  logger.info(`Stage 1A direct fetches: ${all.length} raw items`);
  return all;
}

// ---------------------------------------------------------------------------
// Stage 1B: Serper supplement
// ---------------------------------------------------------------------------

export async function runSerperQueries(tbs: string, queries: SerperQuery[] = SERPER_QUERIES, deadlineAt?: number): Promise<ProductLaunchRaw[]> {
  const all: ProductLaunchRaw[] = [];
  // Sequential: a burst of ~20 parallel calls trips the RapidAPI per-second limit and spills to the paid fallback.
  for (let index = 0; index < queries.length; index++) {
    const query = queries[index];
    const workDeadlineAt = deadlineAt === undefined ? undefined : deadlineAt - persistenceReserveMs(all.length);
    if (!hasTime(workDeadlineAt, SEARCH_BUDGET_MS + (index > 0 ? 250 : 0))) {
      logger.warn("News search deadline exhausted", { remaining: queries.length - index });
      break;
    }
    if (index > 0) await new Promise((resolve) => setTimeout(resolve, 250));
    try {
      const items = await searchSerper(query.query, query.num, tbs);
      logger.info(`Search [${query.id}] ${query.desc}: ${items.length} results`);
      for (const item of items) {
        const url = item.link ?? "";
        all.push({
          title: item.title ?? "",
          source_url: url,
          source_domain: domainFromUrl(url),
          snippet: (item.snippet ?? "").slice(0, 300),
          query_source: query.id,
        });
      }
    } catch (err) {
      logger.warn(`Search [${query.id}] failed: ${err instanceof Error ? err.message : String(err)}`);
    }
  }
  logger.info(`Stage 1B search: ${all.length} raw items`);
  return all;
}

// ---------------------------------------------------------------------------
// Stage 2: Classify & filter via Luna
// ---------------------------------------------------------------------------

const CLASSIFY_SYSTEM = "You classify news articles as product launches. Output strict JSON only.";

const CLASSIFY_USER_TEMPLATE = `You are given a list of news article titles (and optional snippets) from tech press and Hacker News.

For each item, classify whether it is a PRODUCT LAUNCH or feature announcement.

KEEP (is_launch=true) if:
- Article is about a new product, new feature, new version, or open-source project released for the first time
- Company is real and identifiable
- "Show HN: ..." posts that introduce something new
- Company launches a new service, platform, or initiative (e.g. "Amazon opens logistics network" = new_product)
- Company launches a joint venture or new business unit focused on a product/service
- Company adds new AI tools or features to an existing product

DISCARD (is_launch=false) if:
- Product review (product existed before, this tests/reviews it)
- Funding announcement ONLY (Series A/B/C) -- funding pipeline handles those. But if an article is PRIMARILY about a new product/service and mentions funding secondarily, KEEP it.
- Job posting, acquisition (unless acquiring to launch new product), market analysis, opinion, roundup
- Listicle ("best AI tools 2026")
- Court cases, lawsuits, regulatory actions
- Earnings reports, stock news, market commentary
- Consumer goods, food and beverage, toys, pharma, biotech, medical devices, physical hardware, restaurants, franchise opportunities, real estate, travel, local news, and anything aimed at consumers rather than businesses or developers. Keep only software, AI, data, developer, cybersecurity, fintech, and B2B service launches.
- Government, police, municipal, school, or non-profit programs and initiatives
- Social media posts, videos, community-forum posts, and pre-launch directory listings with no real company behind them
- Press releases that are really a partnership, a webinar, an award, a customer win, or a "free trial" offer with no new product
- Personal, hobby, or research projects (a GitHub repo or model checkpoint by an individual) with no company or commercial product behind them
- Anything where you cannot name a specific company and a specific product or feature

COMPANY NAME EXTRACTION RULES:
- For "Show HN: ProductName" titles: the company name is the product name or the maker. NEVER return "Show HN" as company_name.
- For "Show HN: ProductName -- description" titles: extract ProductName as both company_name and product_name.
- If title mentions a well-known company (Amazon, DoorDash, Anthropic, OpenAI, etc.), use that as company_name.
- If title says "X launches Y" or "X announces Y", X is the company, Y is the product.
- Never return "Unknown" as company_name -- extract the best guess from the title.

For each KEPT item, also classify:
- launch_type: "new_product" (brand new thing, new service, new platform, new JV) or "new_feature" (extends existing product)
- is_ai: true if the product is AI-powered or AI-related. Flag true if title or snippet contains ANY of:
  "AI", "artificial intelligence", "LLM", "GPT", "Claude", "neural", "embeddings", "fine-tun",
  "machine learning", "ML", "agentic", "MCP" (Model Context Protocol), "RAG", "vector",
  "diffusion", "generative", "copilot", "AI-powered", "AI-native", "AI-driven",
  "context for agents", "context layer for agents", "AI agents", "software agents",
  "dashboard for agents", "tool for agents", "built for agents".
  CRITICAL RULE: If the product NAME contains "Agents" or "Agent" (e.g. "Airbyte Agents",
  "AI Agents", "Agent SDK") it is ALWAYS is_ai=true -- software agents are AI systems.
  Also flag true if the product description implies AI automation (e.g. "turns notes into
  visual mind maps" = auto-generation by AI, "context for agents across data sources" = AI agent
  infrastructure). Flag false only if there is no AI signal at all.
- company_name: the company behind the product (see extraction rules above)
- product_name: the product, service, or feature being launched

Return STRICT JSON:
{
  "results": [
    {
      "idx": 1,
      "is_launch": true,
      "launch_type": "new_product",
      "is_ai": false,
      "company_name": "Acme Corp",
      "product_name": "Acme Widget"
    },
    {
      "idx": 2,
      "is_launch": false,
      "reason": "product review"
    }
  ]
}

Items:
{items}`;

const SKIP_URL_RE = /techcrunch\.com\/tag\/|techcrunch\.com\/author\/|techcrunch\.com\/category\/|\/page\/\d+/;

/** Social, video, and community hosts: search surfaces them for generic launch queries, and none is a company's launch. */
export const SOCIAL_DOMAIN_RE = /(^|\.)(facebook|instagram|youtube|linkedin|threads|tiktok|twitter|reddit|skool|pinterest)\.(com|net)$|(^|\.)x\.com$/;

/** Classifier fallbacks for when it cannot name the company; these are not leads. */
export const PLACEHOLDER_COMPANY_RE = /^(unknown|show hn|n\/a|none)?$/i;

export interface RawItemWithIdx extends ProductLaunchRaw {
  idx: number;
}

export interface ClassifiedLaunch extends RawItemWithIdx {
  is_launch: true;
  launch_type: "new_product" | "new_feature";
  is_ai: boolean;
  company_name: string;
  product_name: string;
  idx: number;
}

interface ClassifyBody {
  results: Array<{
    idx: number;
    is_launch: boolean;
    launch_type: "new_product" | "new_feature" | null;
    is_ai: boolean | null;
    company_name: string | null;
    product_name: string | null;
    reason: string | null;
  }>;
}

const nullable = (type: string, extra: Record<string, unknown> = {}) => ({ type: [type, "null"], ...extra });

const CLASSIFY_SCHEMA = {
  type: "object",
  additionalProperties: false,
  required: ["results"],
  properties: {
    results: {
      type: "array",
      items: {
        type: "object",
        additionalProperties: false,
        required: ["idx", "is_launch", "launch_type", "is_ai", "company_name", "product_name", "reason"],
        properties: {
          idx: { type: "integer" },
          is_launch: { type: "boolean" },
          launch_type: nullable("string", { enum: ["new_product", "new_feature", null] }),
          is_ai: nullable("boolean"),
          company_name: nullable("string"),
          product_name: nullable("string"),
          reason: nullable("string"),
        },
      },
    },
  },
};

/** Running Luna cost of classification in this process; the eval script reads it. */
export const classifySpend = { usd: 0 };

async function classifyBatch(items: RawItemWithIdx[], deadlineAt?: number): Promise<ClassifiedLaunch[]> {
  if (!isLunaConfigured()) {
    logger.warn("OPENAI_API_KEY missing -- classify skipped");
    return [];
  }

  const BATCH_SIZE = 30;
  const resultsMap = new Map<number, ClassifiedLaunch>();

  for (let start = 0; start < items.length; start += BATCH_SIZE) {
    if (!hasTime(deadlineAt, 5_000)) {
      logger.warn("News classification deadline exhausted", { remaining: items.length - start });
      break;
    }
    const batch = items.slice(start, start + BATCH_SIZE);
    const lines = batch.map((it, localI) => {
      const title = (it.title ?? "").replace(/\n/g, " ").trim();
      const snippet = (it.snippet ?? "").replace(/\n/g, " ").trim().slice(0, 200);
      let line = `[${localI + 1}] TITLE: ${title}`;
      if (snippet) line += ` | SNIPPET: ${snippet}`;
      return line;
    });

    const userMsg = CLASSIFY_USER_TEMPLATE.replace("{items}", lines.join("\n"));

    try {
      const result = await lunaJson<ClassifyBody>({
        name: "launch_classification",
        schema: CLASSIFY_SCHEMA,
        systemPrompt: CLASSIFY_SYSTEM,
        userPrompt: userMsg,
        maxTokens: 8000,
        timeoutMs: 90_000,
        deadlineAt,
      });
      if (!result) {
        logger.warn("Luna classify batch failed");
        continue;
      }
      classifySpend.usd += result.costUsd;
      const body = result.data;

      for (const r of body.results ?? []) {
        const localIdx = r.idx;
        if (!localIdx || localIdx < 1 || localIdx > batch.length) continue;
        if (!r.is_launch) continue;
        const companyName = r.company_name?.trim();
        if (!companyName || PLACEHOLDER_COMPANY_RE.test(companyName)) continue;
        const globalIdx = batch[localIdx - 1].idx;
        const raw = batch[localIdx - 1];
        resultsMap.set(globalIdx, {
          ...raw,
          is_launch: true,
          launch_type: r.launch_type ?? "new_product",
          is_ai: r.is_ai ?? false,
          company_name: companyName,
          product_name: r.product_name?.trim() || raw.title.slice(0, 60),
          idx: globalIdx,
        });
      }
    } catch (err) {
      logger.warn(`Luna classify batch error: ${err instanceof Error ? err.message : String(err)}`);
    }
  }

  return [...resultsMap.values()];
}

const SOURCE_RANK: Record<string, number> = {
  "techcrunch.com": 2,
  "venturebeat.com": 3,
  "theverge.com": 3,
  "businesswire.com": 4,
  "prnewswire.com": 4,
  "news.ycombinator.com": 5,
};

function normalizeKey(s: string): string {
  return s.toLowerCase().replace(/[^a-z0-9]/g, "");
}

function sourceRank(item: ClassifiedLaunch): number {
  const domain = item.source_domain ?? "";
  if (domain in SOURCE_RANK) return SOURCE_RANK[domain];
  if (domain && !domain.includes("ycombinator")) return 1; // own domain = best
  return 6;
}

function dedupLaunches(launches: ClassifiedLaunch[]): ClassifiedLaunch[] {
  const groups = new Map<string, ClassifiedLaunch[]>();
  for (const item of launches) {
    const key = `${normalizeKey(item.company_name)}|${normalizeKey(item.product_name)}`;
    const group = groups.get(key) ?? [];
    group.push(item);
    groups.set(key, group);
  }

  const out: ClassifiedLaunch[] = [];
  for (const group of groups.values()) {
    const best = group.reduce((a, b) => (sourceRank(a) <= sourceRank(b) ? a : b));
    out.push(best);
  }
  return out;
}

export async function runClassify(rawResults: ProductLaunchRaw[], deadlineAt?: number): Promise<ClassifiedLaunch[]> {
  // Dedup by URL and strip pagination/tag pages
  const seenUrls = new Set<string>();
  const filtered: RawItemWithIdx[] = [];
  let skippedPages = 0;

  for (let i = 0; i < rawResults.length; i++) {
    const r = rawResults[i];
    const url = r.source_url ?? "";
    if (seenUrls.has(url)) continue;
    seenUrls.add(url);
    if ((url && SKIP_URL_RE.test(url)) || SOCIAL_DOMAIN_RE.test(r.source_domain ?? "")) {
      skippedPages++;
      continue;
    }
    filtered.push({ ...r, idx: i });
  }

  logger.info(`Stage 2 input: ${rawResults.length} raw -> ${filtered.length} after dedup+filter (${skippedPages} pagination skipped)`);

  const workDeadlineAt = deadlineAt === undefined ? undefined : deadlineAt - persistenceReserveMs(filtered.length);
  const launches = await classifyBatch(filtered, workDeadlineAt);
  logger.info(`Luna classified ${launches.length} launches from ${filtered.length} items`);

  const named = launches.filter((l) => !PLACEHOLDER_COMPANY_RE.test(l.company_name.trim()));
  if (named.length < launches.length) logger.info(`Dropped ${launches.length - named.length} launches with no identifiable company`);

  const deduped = dedupLaunches(named);
  logger.info(`After company dedup: ${deduped.length} launches`);

  return deduped;
}

// ---------------------------------------------------------------------------
// Stage 3: Push to Supabase
// ---------------------------------------------------------------------------

interface ProductLaunchRow {
  discovered_date: string;
  company_name: string;
  product_name: string;
  launch_type: string;
  is_ai: boolean;
  source: "news";
  source_url: string;
  description: string | null;
  pipeline_version: string;
}

function toRow(launch: ClassifiedLaunch, dateStr: string): ProductLaunchRow {
  return {
    discovered_date: dateStr,
    company_name: launch.company_name,
    product_name: launch.product_name,
    launch_type: launch.launch_type,
    is_ai: launch.is_ai,
    source: "news",
    source_url: launch.source_url,
    description: launch.snippet || null,
    pipeline_version: "1.0-ts",
  };
}

async function pushToSupabase(launches: ClassifiedLaunch[], dateStr: string, deadlineAt: number): Promise<number> {
  if (!SUPABASE_URL || !SUPABASE_KEY) {
    logger.warn("Supabase not configured -- skipping push");
    return 0;
  }

  const h = {
    apikey: SUPABASE_KEY,
    Authorization: `Bearer ${SUPABASE_KEY}`,
    "Content-Type": "application/json",
    Prefer: "resolution=merge-duplicates",
  };

  let upserted = 0;
  for (let start = 0; start < launches.length; start += LAUNCH_WRITE_BATCH_SIZE) {
    if (!hasTime(deadlineAt, LAUNCH_WRITE_TIMEOUT_MS)) {
      logger.warn("News persistence deadline exhausted", { remaining: launches.length - start });
      break;
    }
    const rows = launches.slice(start, start + LAUNCH_WRITE_BATCH_SIZE).map((launch) => toRow(launch, dateStr));
    try {
      const resp = await fetch(`${SUPABASE_URL}/rest/v1/${TABLE}?on_conflict=source_url`, {
        method: "POST",
        headers: h,
        body: JSON.stringify(rows),
        signal: AbortSignal.timeout(LAUNCH_WRITE_TIMEOUT_MS),
      });
      if (resp.ok) {
        upserted += rows.length;
      } else {
        const err = await resp.text().catch(() => "");
        logger.error(`Supabase upsert failed: ${resp.status} ${err.slice(0, 200)}`);
      }
    } catch (err) {
      logger.error(`Supabase upsert error: ${err instanceof Error ? err.message : String(err)}`);
    }
  }
  return upserted;
}

// ---------------------------------------------------------------------------
// Public entrypoint
// ---------------------------------------------------------------------------

export async function runNewsLaunchPipeline(options: {
  date: string;
  tbs?: string;
  skipSerper?: boolean;
  dryRun?: boolean;
}): Promise<ProductLaunchPipelineResult> {
  const { date, tbs = "qdr:d", skipSerper = false, dryRun = false } = options;
  const startMs = Date.now();
  const deadlineAt = startMs + LAUNCH_RUN_BUDGET_MS;

  logger.info("News launch pipeline starting", { date, tbs, skipSerper, dryRun });

  if (dryRun) {
    logger.info("[DRY] Stage 1A: TC today, TC yesterday, HN Show, HN front");
    if (!skipSerper) {
      for (const q of SERPER_QUERIES) {
        logger.info(`[DRY] Serper [${q.id}] ${q.desc} (num=${q.num})`);
      }
    }
    return {
      date,
      source: "news",
      launchCount: 0,
      stats: { rawResults: 0, afterClassify: 0, durationMs: Date.now() - startMs },
    };
  }

  // Stage 1A: Direct fetches
  const direct = await runDirectFetches(date, deadlineAt - persistenceReserveMs(0) - 30_000);

  // Stage 1B: Serper supplement
  const serper = skipSerper ? [] : await runSerperQueries(tbs, SERPER_QUERIES, deadlineAt - 30_000 - persistenceReserveMs(direct.length));

  const rawResults = [...direct, ...serper];
  logger.info(`Stage 1 total: ${rawResults.length} raw items (${direct.length} direct + ${serper.length} Serper)`);

  // Stage 2: Classify
  const launches = await runClassify(rawResults, deadlineAt);

  // Stage 3: Push to Supabase
  const pushed = await pushToSupabase(launches, date, deadlineAt);
  logger.info(`Stage 3: pushed ${pushed} rows to ${TABLE}`);

  const durationMs = Date.now() - startMs;
  logger.info("News launch pipeline complete", { launchCount: pushed, pushed, durationMs });

  return {
    date,
    source: "news",
    launchCount: pushed,
    stats: {
      rawResults: rawResults.length,
      afterClassify: launches.length,
      durationMs,
    },
  };
}
