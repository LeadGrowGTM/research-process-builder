import { logger } from "@trigger.dev/sdk";
import type { ProductLaunchPipelineResult } from "./product-launch-types.js";
import { fetchUrl } from "./firecrawl.js";
import { day0BlitzEnrich } from "./enrich-company.js";
import { webSearch } from "./rapid-search.js";
import { lunaJson } from "./luna.js";
import { hasTime, LAUNCH_RUN_BUDGET_MS, LAUNCH_WRITE_BATCH_SIZE, LAUNCH_WRITE_TIMEOUT_MS, persistenceReserveMs } from "./launch-budget.js";

// fetchUrl has no deadline option: allow all three bounded provider attempts (30s + 60s + 15s).
const PAGE_FETCH_BUDGET_MS = 105_000;
const CLASSIFY_RESERVE_MS = 30_000;
const SEARCH_BUDGET_MS = 31_000;

/** Running Luna usage and cost, including leaderboard extraction. */
export const phSpend = { usd: 0, inputTokens: 0, cachedInputTokens: 0, outputTokens: 0 };

// ---------------------------------------------------------------------------
// Env
// ---------------------------------------------------------------------------

const RAPID_API_KEY = process.env.RAPID_API_KEY ?? "";
const SUPABASE_URL = (() => {
  const url = process.env.SUPABASE_PROJECT_URL ?? process.env.SUPABASE_URL ?? "";
  return url.startsWith("http") ? url : "";
})();
const SUPABASE_KEY =
  process.env.SUPABASE_KEY ??
  process.env.SUPABASE_SERVICE_ROLE_KEY ??
  process.env.SUPABASE_ANON_KEY ??
  "";

// ---------------------------------------------------------------------------
// Types (internal)
// ---------------------------------------------------------------------------

interface PhProduct {
  rank: number;
  product_name: string;
  company_name: string | null;
  tagline: string | null;
  score: number;
  ph_url: string;
  categories: string[];
  maker_website: string | null;
  linkedin_url: string | null;
}

interface ClassifiedProduct extends PhProduct {
  launch_type: "new_product" | "new_feature";
  is_ai: boolean;
  classification_reasoning: string;
  launch_count: number | null;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

export function buildPhUrl(dateStr: string): string {
  const d = new Date(dateStr + "T12:00:00Z");
  const year = d.getUTCFullYear();
  const month = d.getUTCMonth() + 1;
  const day = d.getUTCDate();
  return `https://www.producthunt.com/leaderboard/daily/${year}/${month}/${day}`;
}

function supabaseHeaders(prefer?: string): Record<string, string> {
  const h: Record<string, string> = {
    apikey: SUPABASE_KEY,
    Authorization: `Bearer ${SUPABASE_KEY}`,
    "Content-Type": "application/json",
  };
  if (prefer) h["Prefer"] = prefer;
  return h;
}

function domainToCompanyHint(makerWebsite: string | null): string | null {
  if (!makerWebsite) return null;
  try {
    const hostname = new URL(makerWebsite).hostname.toLowerCase();
    const parts = hostname.split(".");
    const filtered = parts.filter((p, i) => {
      if (i === parts.length - 1) return false; // TLD
      if (p === "www" || p === "app" || p === "get" || p === "try" || p === "use") return false;
      return true;
    });
    if (filtered.length === 0) return null;
    const slug = filtered[0];
    const generic = new Set(["github", "google", "apple", "microsoft", "openai", "anthropic", "solana", "notion", "vercel", "netlify"]);
    if (generic.has(slug)) return null;
    return slug
      .replace(/-/g, " ")
      .replace(/([a-z])([A-Z])/g, "$1 $2")
      .replace(/\b\w/g, (c) => c.toUpperCase());
  } catch {
    return null;
  }
}

/** Strict-schema Luna call. Throws on failure so the leaderboard retry loop and task retries still apply. */
async function lunaObject<T>(
  name: string,
  schema: Record<string, unknown>,
  system: string,
  user: string,
  maxTokens: number,
  timeoutMs: number,
  deadlineAt?: number
): Promise<T> {
  if (!hasTime(deadlineAt, 5_000)) throw new Error(`Luna ${name} deadline exhausted`);
  const result = await lunaJson<T>({ name, schema, systemPrompt: system, userPrompt: user, maxTokens, timeoutMs, deadlineAt });
  if (!result) throw new Error(`Luna ${name} call failed`);
  phSpend.usd += result.costUsd;
  phSpend.inputTokens += result.usage.inputTokens;
  phSpend.cachedInputTokens += result.usage.cachedInputTokens;
  phSpend.outputTokens += result.usage.outputTokens;
  return result.data;
}

const nullable = (type: string) => ({ type: [type, "null"] });

const EXTRACT_SCHEMA = {
  type: "object",
  additionalProperties: false,
  required: ["products", "error"],
  properties: {
    products: {
      type: "array",
      items: {
        type: "object",
        additionalProperties: false,
        required: ["rank", "product_name", "company_name", "tagline", "score", "ph_url", "categories", "maker_website"],
        properties: {
          rank: { type: "integer" },
          product_name: { type: "string" },
          company_name: nullable("string"),
          tagline: nullable("string"),
          score: { type: "integer" },
          ph_url: { type: "string" },
          categories: { type: "array", items: { type: "string" } },
          maker_website: nullable("string"),
        },
      },
    },
    error: nullable("string"),
  },
};

const CLASSIFY_SCHEMA = {
  type: "object",
  additionalProperties: false,
  required: ["classifications"],
  properties: {
    classifications: {
      type: "array",
      items: {
        type: "object",
        additionalProperties: false,
        required: ["rank", "product_name", "company_name", "launch_type", "is_ai", "classification_reasoning"],
        properties: {
          rank: { type: "integer" },
          product_name: { type: "string" },
          company_name: { type: "string" },
          launch_type: { type: "string", enum: ["new_product", "new_feature"] },
          is_ai: { type: "boolean" },
          classification_reasoning: { type: "string" },
        },
      },
    },
  },
};

/**
 * The leaderboard markdown is mostly image links, which used to push everything past the 15k fetch cap
 * (a day with 25 featured products lost the lower half). Drop them so the whole list fits and extraction is faster.
 */
export function cleanLeaderboard(markdown: string): string {
  return markdown
    .split("\n")
    .filter((line) => !/^\s*!\[[^\]]*\]\([^)]*\)\s*$/.test(line) && !/^\s*$/.test(line))
    .join("\n");
}

// ---------------------------------------------------------------------------
// Stage 1: Fetch PH leaderboard
// ---------------------------------------------------------------------------

export async function extractProductsFromContent(pageContent: string, deadlineAt?: number): Promise<{ products: PhProduct[]; error?: string }> {
  const parsed = await lunaObject<{ products: (PhProduct & { company_name: string | null })[]; error: string | null }>("ph_leaderboard", EXTRACT_SCHEMA,
        "You are extracting structured product data from a Product Hunt leaderboard page. " +
        "Extract every ranked product. Return JSON only -- no commentary.",
    `Page content:
${pageContent}

` +
        "Extract all ranked products. For each, return rank, product_name (the specific product or feature launched today), " +
        "company_name (the maker or organization behind it, same as product_name if unclear), tagline, score, " +
        "ph_url (the full PH URL), categories, and maker_website (URL or null). " +
        'If the leaderboard has not posted yet, return no products and error "leaderboard_not_posted".',
    16_000,
    120_000,
    deadlineAt
  );

  const products = (parsed.products ?? []).map((p) => ({
    ...p,
    company_name: p.company_name ?? null,
    linkedin_url: null,
  }));
  return { products, error: parsed.error ?? undefined };
}

const LEADERBOARD_ATTEMPTS = 3;

/**
 * Fetch and extract one day's leaderboard. A single transient fetch or extraction failure used to end the
 * run with zero launches (~2s runtime), so retry with backoff before giving up.
 */
export async function fetchLeaderboard(dateStr: string, deadlineAt?: number): Promise<PhProduct[]> {
  const url = buildPhUrl(dateStr);
  for (let attempt = 1; attempt <= LEADERBOARD_ATTEMPTS; attempt++) {
    if (!hasTime(deadlineAt, PAGE_FETCH_BUDGET_MS + 5_000)) break;
    try {
      const pageContent = await fetchUrl(url, { maxChars: 200_000 });
      if (pageContent) {
        const extracted = await extractProductsFromContent(cleanLeaderboard(pageContent).slice(0, 60_000), deadlineAt);
        logger.info("Leaderboard extraction", { dateStr, attempt, count: extracted.products.length, error: extracted.error });
        if (extracted.products.length > 0) return extracted.products;
      } else {
        logger.warn("Leaderboard fetch failed", { dateStr, attempt });
      }
    } catch (e) {
      logger.warn("Leaderboard attempt error", { dateStr, attempt, error: e instanceof Error ? e.message : String(e) });
    }
    if (attempt < LEADERBOARD_ATTEMPTS) {
      const delayMs = 5_000 * attempt;
      if (!hasTime(deadlineAt, delayMs + PAGE_FETCH_BUDGET_MS + 5_000)) break;
      await new Promise((resolve) => setTimeout(resolve, delayMs));
    }
  }
  return [];
}

/** Products whose ph_url is already in product_launches; the previous-day pass only needs the rest. */
async function knownPhUrls(urls: string[], deadlineAt: number): Promise<Set<string> | null> {
  if (urls.length === 0) return new Set();
  if (!SUPABASE_URL || !SUPABASE_KEY || !hasTime(deadlineAt, 15_000)) return null;
  try {
    const list = urls.map((u) => `"${u}"`).join(",");
    const resp = await fetch(
      `${SUPABASE_URL}/rest/v1/product_launches?select=source_url&source_url=in.(${encodeURIComponent(list)})`,
      { headers: supabaseHeaders(), signal: AbortSignal.timeout(15_000) }
    );
    if (!resp.ok) return null;
    return new Set(((await resp.json()) as { source_url: string }[]).map((r) => r.source_url));
  } catch {
    return null;
  }
}

async function stage1Fetch(dateStr: string, onlyNew: boolean, deadlineAt: number, todayUrls: Set<string>): Promise<PhProduct[]> {
  logger.info("Stage 1: fetching PH leaderboard", { url: buildPhUrl(dateStr), onlyNew });

  const products = await fetchLeaderboard(dateStr, deadlineAt - persistenceReserveMs(0) - CLASSIFY_RESERVE_MS);
  if (!onlyNew) for (const product of products) todayUrls.add(product.ph_url);
  if (products.length === 0) {
    logger.warn("No products from leaderboard", { dateStr });
    return [];
  }

  // Kill list: skip score < 5
  const before = products.length;
  let filtered = products.filter((p) => (p.score ?? 0) >= 5);
  if (filtered.length < before) {
    logger.info("Dropped low-score products", { dropped: before - filtered.length });
  }

  if (onlyNew) {
    filtered = filtered.filter((p) => !todayUrls.has(p.ph_url));
    const known = await knownPhUrls(filtered.map((p) => p.ph_url), deadlineAt - persistenceReserveMs(filtered.length) - CLASSIFY_RESERVE_MS);
    if (known === null) throw new Error("Stored PH URL lookup failed; previous-day pass skipped");
    filtered = filtered.filter((p) => !known.has(p.ph_url));
    logger.info("Previous-day leaderboard: products not captured yesterday", { dateStr, new: filtered.length, known: known.size });
  }

  // Stage 1b: fetch individual product pages to extract maker_website
  // Leaderboard page doesn't include external links — only product pages have them
  const needsWebsite = filtered.filter((p) => !p.maker_website);
  const pageDeadlineAt = deadlineAt - persistenceReserveMs(filtered.length) - CLASSIFY_RESERVE_MS;
  if (needsWebsite.length > 0) {
    logger.info("Stage 1b: fetching product pages for maker_website", { count: needsWebsite.length });

    for (let i = 0; i < needsWebsite.length; i++) {
      if (!hasTime(pageDeadlineAt, PAGE_FETCH_BUDGET_MS)) break;
      const product = needsWebsite[i];
      const postUrl = product.ph_url;
      if (!postUrl) continue;

      try {
        const pageContent = await fetchUrl(postUrl, { maxChars: 15_000 });
        if (pageContent) {
          // Extract maker_website from ?ref=producthunt link
          const urlMatch = pageContent.match(/https?:\/\/[^\s\)\]"']+\?ref=producthunt/);
          if (urlMatch) {
            const rawUrl = urlMatch[0].replace(/\?ref=producthunt.*$/, "");
            if (!rawUrl.includes("producthunt.com")) {
              product.maker_website = rawUrl;
              logger.info("Found maker_website", { product: product.product_name, website: rawUrl });
            }
          }

          // Extract LinkedIn company URL from PH product page
          const linkedinMatch = pageContent.match(/https?:\/\/(?:www\.)?linkedin\.com\/company\/([a-zA-Z0-9_-]+)/);
          if (linkedinMatch) {
            product.linkedin_url = `https://www.linkedin.com/company/${linkedinMatch[1]}`;
            logger.info("Found linkedin_url on PH page", { product: product.product_name, linkedin: product.linkedin_url });
          }
        }
      } catch {
        logger.warn("Failed to fetch product page", { product: product.product_name });
      }

      if (i < needsWebsite.length - 1 && hasTime(pageDeadlineAt, 500 + PAGE_FETCH_BUDGET_MS)) {
        await new Promise((resolve) => setTimeout(resolve, 500));
      }
    }

    const found = needsWebsite.filter((p) => p.maker_website).length;
    logger.info("Stage 1b complete", { found, total: needsWebsite.length });
  }

  // Stage 1c: homepage LinkedIn fallback for products with maker_website but no linkedin_url found on PH page
  const needsLinkedin = filtered.filter((p) => p.maker_website && !p.linkedin_url);
  if (needsLinkedin.length > 0) {
    logger.info("Stage 1c: fetching maker homepages for LinkedIn URLs", { count: needsLinkedin.length });
    for (let i = 0; i < needsLinkedin.length; i++) {
      if (!hasTime(pageDeadlineAt, PAGE_FETCH_BUDGET_MS)) break;
      const product = needsLinkedin[i];
      try {
        const homepageContent = await fetchUrl(product.maker_website!, { maxChars: 15_000 });
        if (homepageContent) {
          const linkedinMatch = homepageContent.match(/https?:\/\/(?:www\.)?linkedin\.com\/company\/([a-zA-Z0-9_-]+)/);
          if (linkedinMatch) {
            product.linkedin_url = `https://www.linkedin.com/company/${linkedinMatch[1]}`;
            logger.info("Found linkedin_url on homepage", { product: product.product_name, linkedin: product.linkedin_url });
          }
        }
      } catch {
        logger.warn("Failed to fetch homepage for LinkedIn", { product: product.product_name });
      }
      if (i < needsLinkedin.length - 1 && hasTime(pageDeadlineAt, 300 + PAGE_FETCH_BUDGET_MS)) {
        await new Promise((resolve) => setTimeout(resolve, 300));
      }
    }
    const linkedinFound = needsLinkedin.filter((p) => p.linkedin_url).length;
    logger.info("Stage 1c complete", { found: linkedinFound, total: needsLinkedin.length });
  }

  // Apply domain-based company name hint for products without one (cheap, no GPT)
  for (const product of filtered) {
    if (!product.company_name && product.maker_website) {
      const hint = domainToCompanyHint(product.maker_website);
      if (hint) product.company_name = hint;
    }
  }

  logger.info("Stage 1 complete", { productCount: filtered.length });
  return filtered;
}

// ---------------------------------------------------------------------------
// Stage 2b: Fetch launch count from PH product page
// ---------------------------------------------------------------------------

const PH_PAGE_HEADERS = {
  "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
  "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
  "Accept-Language": "en-US,en;q=0.5",
};

async function postsCountFromSlug(slug: string, deadlineAt: number): Promise<number | null> {
  if (!hasTime(deadlineAt, 15_000)) return null;
  const url = `https://www.producthunt.com/products/${slug}`;
  try {
    const resp = await fetch(url, {
      headers: PH_PAGE_HEADERS,
      signal: AbortSignal.timeout(15_000),
    });
    if (resp.status !== 200) return null;
    const html = await resp.text();
    if (html.length < 10000) return null;
    const m = html.match(/postsCount[":\s]+(\d+)/);
    if (!m) return null;
    const count = parseInt(m[1], 10);
    return count > 0 ? count : null;
  } catch {
    return null;
  }
}

async function serperFindProductSlug(productName: string, deadlineAt: number): Promise<string | null> {
  if (!RAPID_API_KEY || !hasTime(deadlineAt, SEARCH_BUDGET_MS)) return null;
  const query = `site:producthunt.com/products "${productName}"`;
  try {
    const response = await webSearch(query, { limit: 3, apiKey: RAPID_API_KEY });
    if (!response) return null;
    for (const r of response.results) {
      const link = r.url ?? "";
      const m = link.match(/https:\/\/www\.producthunt\.com\/products\/([^/?#]+)/);
      if (m) return m[1];
    }
    return null;
  } catch {
    return null;
  }
}

async function fetchLaunchCount(product: PhProduct, deadlineAt: number): Promise<number | null> {
  const phUrl = product.ph_url ?? "";

  let slug: string | null = null;
  if (phUrl.includes("/products/")) {
    slug = phUrl.replace(/\/$/, "").split("/products/").pop() ?? null;
  } else if (phUrl.includes("/posts/")) {
    slug = phUrl.replace(/\/$/, "").split("/posts/").pop() ?? null;
  }
  if (!slug) return null;

  // Try 1: exact slug
  let count = await postsCountFromSlug(slug, deadlineAt);
  if (count !== null) return count;

  // Try 2: strip trailing -N (e.g. flowly-9 -> flowly)
  const stripped = slug.replace(/-\d+$/, "");
  if (stripped !== slug) {
    count = await postsCountFromSlug(stripped, deadlineAt);
    if (count !== null) return count;
  }

  // Try 3: strip -for-X suffix (e.g. sleek-analytics-for-ios -> sleek-analytics)
  const forStripped = slug.replace(/-for-[a-z]+$/, "");
  if (forStripped !== slug && forStripped !== stripped) {
    count = await postsCountFromSlug(forStripped, deadlineAt);
    if (count !== null) return count;
  }

  // Try 4: Serper search for canonical slug
  const canonicalSlug = await serperFindProductSlug(product.product_name, deadlineAt);
  if (canonicalSlug && canonicalSlug !== slug && canonicalSlug !== stripped && canonicalSlug !== forStripped) {
    count = await postsCountFromSlug(canonicalSlug, deadlineAt);
    if (count !== null) return count;
  }

  return null;
}

// ---------------------------------------------------------------------------
// Stage 2: Classify
// ---------------------------------------------------------------------------

async function stage2Classify(products: PhProduct[], deadlineAt: number): Promise<ClassifiedProduct[]> {
  if (products.length === 0) return [];

  logger.info("Stage 2: classifying products via Luna batch", { count: products.length });

  const productLines = products.map((p) => {
    const cats = (p.categories ?? []).join(", ");
    const domainHint = domainToCompanyHint(p.maker_website ?? null);
    const hintStr = domainHint ? ` | domain_hint=${domainHint}` : "";
    return `rank=${p.rank} | product_name=${p.product_name} | company_name_hint=${p.company_name ?? "unknown"}${hintStr} | tagline=${p.tagline ?? ""} | categories=${cats}`;
  });

  const parsed = await lunaObject<{ classifications: { rank: number; product_name: string; company_name: string; launch_type: string; is_ai: boolean; classification_reasoning: string }[] }>("ph_classification", CLASSIFY_SCHEMA,
        "You classify Product Hunt launches. For each product, determine company_name, launch_type, and is_ai. " +
        "Return JSON only -- no commentary.",
        `Products (from leaderboard):\n${productLines.join("\n")}\n\n` +
        "For each product, classify:\n" +
        "1. launch_type: 'new_product' if this appears to be a first-time PH launch based on product name/tagline/categories. " +
        "'new_feature' if the product name or tagline strongly implies it is an addition/update to an existing product " +
        "(e.g. 'v2', 'for X product', OpenAI products, products with version suffixes). " +
        "When uncertain from leaderboard data alone, default to 'new_product'.\n" +
        "2. is_ai: true if ANY apply: categories contain 'AI' prefix or 'Artificial Intelligence', " +
        "tagline contains: AI, agent, LLM, GPT, Claude, automated, intelligent, generative. " +
        "false if no explicit AI signal.\n" +
        "3. classification_reasoning: 1 sentence.\n" +
        "4. company_name: the organization behind this product. Use domain_hint as a strong signal. " +
        "Strip version suffixes (v1, v2, 2.0, v7), descriptors (for VS Code, - Incorporation MCP), " +
        "and dates from product_name to get company name. " +
        "If domain_hint is provided and plausible, prefer it over raw product_name. " +
        'Examples: "Kilo Code v7 for VS Code" + domain_hint=Kilo Code → "Kilo Code"; ' +
        '"Shadow 2.0" + domain_hint=Shadow Labs → "Shadow Labs"; ' +
        '"Lingo.dev v1" + no hint → "Lingo.dev"\n\n',
    8_000,
    90_000,
    deadlineAt
  );

  const clsByRank = new Map((parsed.classifications ?? []).map((c) => [c.rank, c]));

  const classified: ClassifiedProduct[] = products.flatMap((p) => {
    const cls = clsByRank.get(p.rank);
    if (!cls) return [];
    return [{
      ...p,
      company_name: cls.company_name,
      launch_type: cls.launch_type as "new_product" | "new_feature",
      is_ai: cls.is_ai,
      classification_reasoning: cls.classification_reasoning,
      launch_count: null,
    }];
  });

  logger.info("Luna classification done, fetching product page launch counts...");

  // Stage 2b: fetch launch counts with rate limiting (sequential, small delay)
  for (let i = 0; i < classified.length; i++) {
    if (!hasTime(deadlineAt, 15_000 + (i > 0 ? 500 : 0))) break;
    const product = classified[i];
    if (i > 0) {
      // Small delay between PH page fetches to avoid rate limiting
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
    const count = await fetchLaunchCount(product, deadlineAt);
    product.launch_count = count;

    if (count !== null) {
      if (count > 1 && product.launch_type !== "new_feature") {
        product.launch_type = "new_feature";
        product.classification_reasoning += ` [2b: ${count} launches on PH -> new_feature]`;
      } else if (count === 1 && product.launch_type !== "new_product") {
        product.launch_type = "new_product";
        product.classification_reasoning += ` [2b: 1 launch on PH -> new_product]`;
      }
    }

    logger.info("Product classified", {
      rank: product.rank,
      name: product.product_name,
      launch_type: product.launch_type,
      is_ai: product.is_ai,
      launch_count: count,
    });
  }

  return classified;
}

// ---------------------------------------------------------------------------
// Stage 3: Push to Supabase
// ---------------------------------------------------------------------------

function toSupabaseRow(product: ClassifiedProduct, dateStr: string): Record<string, unknown> {
  return {
    discovered_date: dateStr,
    company_name: product.company_name ?? product.product_name,
    product_name: product.product_name,
    tagline: product.tagline ?? null,
    rank: product.rank,
    score: product.score,
    ph_url: product.ph_url,
    categories: product.categories,
    maker_website: product.maker_website ?? null,
    linkedin_url: product.linkedin_url ?? null,
    launch_type: product.launch_type,
    is_ai: product.is_ai,
    launch_count: product.launch_count ?? null,
    classification_reasoning: product.classification_reasoning,
    source: "product_hunt",
    source_url: product.ph_url,
  };
}

async function stage3Push(products: ClassifiedProduct[], dateStr: string, deadlineAt: number): Promise<number> {
  if (!SUPABASE_URL || !SUPABASE_KEY) {
    logger.warn("Supabase not configured -- skipping push");
    return 0;
  }
  if (products.length === 0) return 0;

  const TABLE = "product_launches";
  const rows = products.map((p) => toSupabaseRow(p, dateStr));

  logger.info("Stage 3: pushing to Supabase", { table: TABLE, count: rows.length });

  let upserted = 0;
  for (let start = 0; start < rows.length; start += LAUNCH_WRITE_BATCH_SIZE) {
    if (!hasTime(deadlineAt, LAUNCH_WRITE_TIMEOUT_MS)) {
      logger.warn("PH persistence deadline exhausted", { remaining: rows.length - start });
      break;
    }
    const batch = rows.slice(start, start + LAUNCH_WRITE_BATCH_SIZE);
    try {
      const resp = await fetch(
        `${SUPABASE_URL}/rest/v1/${TABLE}?on_conflict=source_url`,
        {
          method: "POST",
          headers: supabaseHeaders("resolution=merge-duplicates"),
          body: JSON.stringify(batch),
          signal: AbortSignal.timeout(LAUNCH_WRITE_TIMEOUT_MS),
        }
      );
      if (resp.ok) {
        upserted += batch.length;
      } else {
        const errText = await resp.text().catch(() => "");
        logger.error("Supabase upsert failed", {
          count: batch.length,
          status: resp.status,
          error: errText.slice(0, 200),
        });
      }
    } catch (e) {
      logger.error("Supabase upsert error", {
        count: batch.length,
        error: e instanceof Error ? e.message : String(e),
      });
    }
  }

  logger.info("Stage 3 complete", { upserted });
  return upserted;
}

// ---------------------------------------------------------------------------
// Stage 4: Company enrichment (Blitz day-0 — misses retried later by DiscoLike)
// ---------------------------------------------------------------------------

async function stage4Enrich(products: ClassifiedProduct[], deadlineAt: number): Promise<number> {
  const targets = products
    .filter((p) => p.maker_website)
    .map((p) => ({
      companyName: p.company_name ?? p.product_name,
      domain: p.maker_website as string,
      sourceUrl: p.ph_url,
      knownLinkedin: p.linkedin_url,
    }));

  if (targets.length === 0 || !hasTime(deadlineAt, 15_000)) {
    logger.info("Skipping PH enrichment", { targets: targets.length, deadlineAt });
    return 0;
  }

  logger.info("Stage 4: Blitz enrichment", { count: targets.length });
  const { enriched } = await day0BlitzEnrich("product_launches", targets, deadlineAt);
  return enriched;
}

// ---------------------------------------------------------------------------
// Public entry point
// ---------------------------------------------------------------------------

export async function runPhLaunchPipeline(options: {
  date: string;
  dryRun?: boolean;
}): Promise<ProductLaunchPipelineResult> {
  const start = Date.now();
  const deadlineAt = start + LAUNCH_RUN_BUDGET_MS;
  const { date: dateStr, dryRun = false } = options;

  logger.info("PH launch pipeline starting", { dateStr, dryRun });

  if (dryRun) {
    const url = buildPhUrl(dateStr);
    logger.info("Dry run -- preview only", { url });
    return {
      date: dateStr,
      source: "product_hunt",
      launchCount: 0,
      stats: { rawResults: 0, afterClassify: 0, durationMs: Date.now() - start },
    };
  }

  // The 9 AM ET run sees the new PT day only ~6 hours in (17 products at most, 40% under the score floor),
  // so the previous day's final leaderboard is where most launches are. Its products are dated by their own
  // leaderboard day and skipped if yesterday's run already stored them.
  const previousDate = new Date(`${dateStr}T12:00:00Z`);
  previousDate.setUTCDate(previousDate.getUTCDate() - 1);

  const todayUrls = new Set<string>();
  const today = await processLeaderboard(dateStr, false, deadlineAt, todayUrls);
  const previous = hasTime(deadlineAt, PREVIOUS_DAY_MIN_REMAINING_MS)
    ? await processLeaderboard(previousDate.toISOString().slice(0, 10), true, deadlineAt, todayUrls)
    : { raw: 0, classified: 0, upserted: 0, enriched: 0 };

  const durationMs = Date.now() - start;
  logger.info("PH launch pipeline complete", { dateStr, today, previous, durationMs });

  return {
    date: dateStr,
    source: "product_hunt",
    launchCount: today.upserted + previous.upserted,
    stats: {
      rawResults: today.raw + previous.raw,
      afterClassify: today.classified + previous.classified,
      durationMs,
    },
  };
}

// Task maxDuration is 600s; skip the previous-day pass if today's took too long to finish it safely.
const PREVIOUS_DAY_MIN_REMAINING_MS = 300_000;

async function processLeaderboard(dateStr: string, onlyNew: boolean, deadlineAt: number, todayUrls: Set<string>) {
  let raw = 0;
  let classified = 0;
  let upserted = 0;
  let enriched = 0;
  try {
    const rawProducts = await stage1Fetch(dateStr, onlyNew, deadlineAt, todayUrls);
    raw = rawProducts.length;
    if (raw > 0) {
      const products = await stage2Classify(rawProducts, deadlineAt - persistenceReserveMs(raw));
      classified = products.length;
      upserted = await stage3Push(products, dateStr, deadlineAt);
      enriched = await stage4Enrich(products, deadlineAt);
    }
    logger.info("PH leaderboard day complete", { dateStr, status: "complete", raw, classified, upserted, enriched });
  } catch (e) {
    logger.warn("PH leaderboard day failed", { dateStr, status: "failed", error: e instanceof Error ? e.message : String(e) });
  }
  return { raw, classified, upserted, enriched };
}
