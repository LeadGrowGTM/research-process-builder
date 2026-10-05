import { logger } from "@trigger.dev/sdk";
import type { ProductLaunch, ProductLaunchPipelineResult } from "./product-launch-types.js";
import { fetchUrl } from "./firecrawl.js";
import { day0BlitzEnrich } from "./enrich-company.js";
import { webSearch } from "./rapid-search.js";

// ---------------------------------------------------------------------------
// Env
// ---------------------------------------------------------------------------

const RAPID_API_KEY = process.env.RAPID_API_KEY ?? "";
const OPENAI_API_KEY = process.env.OPENAI_API_KEY ?? "";
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

function buildPhUrl(dateStr: string): string {
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

function parseJsonResponse(raw: string): unknown {
  let text = raw.trim();
  if (text.startsWith("```")) {
    text = text.split("\n").slice(1).join("\n");
    text = text.split("```")[0];
  }
  return JSON.parse(text.trim());
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

async function openaiChat(messages: { role: "system" | "user"; content: string }[]): Promise<string> {
  const resp = await fetch("https://api.openai.com/v1/chat/completions", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${OPENAI_API_KEY}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      model: "gpt-4o-mini",
      temperature: 0,
      messages,
    }),
    signal: AbortSignal.timeout(60_000),
  });
  if (!resp.ok) {
    throw new Error(`OpenAI ${resp.status}: ${await resp.text()}`);
  }
  const data = (await resp.json()) as { choices: { message: { content: string } }[] };
  return data.choices[0]?.message?.content?.trim() ?? "";
}

// ---------------------------------------------------------------------------
// Stage 1: Fetch PH leaderboard
// ---------------------------------------------------------------------------

async function extractProductsFromContent(pageContent: string): Promise<{ products: PhProduct[]; error?: string }> {
  const raw = await openaiChat([
    {
      role: "system",
      content:
        "You are extracting structured product data from a Product Hunt leaderboard page. " +
        "Extract every ranked product. Return JSON only -- no commentary.",
    },
    {
      role: "user",
      content:
        `Page content:\n${pageContent}\n\n` +
        "Extract all ranked products. For each, return:\n" +
        '{"rank": <int>, "product_name": "<the specific product or feature launched today>", ' +
        '"company_name": "<the maker or organization behind it — same as product_name if unclear>", ' +
        '"tagline": "<string>", "score": <int>, "ph_url": "<full PH URL>", ' +
        '"categories": ["<cat>", ...], "maker_website": "<URL or null>"}\n\n' +
        'Return as JSON: {"products": [...]}\n' +
        'If the leaderboard has not posted yet, return {"products": [], "error": "leaderboard_not_posted"}.',
    },
  ]);

  const parsed = parseJsonResponse(raw) as { products?: (PhProduct & { company_name?: string })[]; error?: string };
  const products = (parsed.products ?? []).map((p) => ({
    ...p,
    company_name: p.company_name ?? null,
    linkedin_url: null,
  }));
  return { products, error: parsed.error };
}

async function serperSupplementFetch(dateStr: string): Promise<PhProduct[]> {
  if (!RAPID_API_KEY) return [];

  const d = new Date(dateStr + "T12:00:00Z");
  const month = d.toLocaleString("en-US", { month: "long", timeZone: "UTC" });
  const day = d.getUTCDate();
  const year = d.getUTCFullYear();
  const query = `site:producthunt.com/posts "${month} ${day}, ${year}"`;

  logger.info("Serper supplement: searching for PH posts", { query });

  try {
    const response = await webSearch(query, { limit: 30, after: dateStr, apiKey: RAPID_API_KEY });
    if (!response) {
      logger.warn("RapidAPI supplement failed");
      return [];
    }
    const results = response.results.map(({ title, url, snippet }) => ({ title, link: url, snippet }));
    logger.info("Serper supplement results", { count: results.length });

    const products: PhProduct[] = [];
    for (const r of results) {
      const link = r.link ?? "";
      if (!link.includes("producthunt.com/posts/")) continue;

      const name = (r.title ?? "").replace(/ - Product Hunt$/, "").replace(/ \| Product Hunt$/, "").trim();
      if (!name) continue;

      products.push({
        rank: 0,
        product_name: name,
        company_name: null,
        tagline: r.snippet ?? null,
        score: 0,
        ph_url: link,
        categories: [],
        maker_website: null,
        linkedin_url: null,
      });
    }

    return products;
  } catch (e) {
    logger.warn("Serper supplement error", { error: e instanceof Error ? e.message : String(e) });
    return [];
  }
}

function mergeProducts(primary: PhProduct[], supplement: PhProduct[]): PhProduct[] {
  const seen = new Set(primary.map((p) => p.product_name.toLowerCase()));
  const seenUrls = new Set(primary.map((p) => p.ph_url));
  let nextRank = Math.max(...primary.map((p) => p.rank), 0) + 1;

  for (const s of supplement) {
    if (seen.has(s.product_name.toLowerCase()) || seenUrls.has(s.ph_url)) continue;
    seen.add(s.product_name.toLowerCase());
    seenUrls.add(s.ph_url);
    s.rank = nextRank++;
    primary.push(s);
  }

  return primary;
}

async function stage1Fetch(dateStr: string): Promise<PhProduct[]> {
  const url = buildPhUrl(dateStr);
  logger.info("Stage 1: fetching PH leaderboard via Spider (JS render)", { url });

  const pageContent = await fetchUrl(url, { renderJs: true, waitForSecs: 3 });

  let products: PhProduct[] = [];

  if (pageContent) {
    logger.info("Spider fetch succeeded", { chars: pageContent.length });
    const extracted = await extractProductsFromContent(pageContent);

    if (extracted.error === "leaderboard_not_posted") {
      logger.warn("PH leaderboard not yet posted for this date", { dateStr });
      return [];
    }
    products = extracted.products;
    logger.info("Spider extraction", { count: products.length });
  } else {
    logger.warn("Spider fetch failed — relying on Serper supplement only");
  }

  // Serper supplement: catch products Spider missed
  const supplement = await serperSupplementFetch(dateStr);
  if (supplement.length > 0) {
    const before = products.length;
    products = mergeProducts(products, supplement);
    logger.info("Serper supplement merged", { added: products.length - before, total: products.length });
  }

  if (products.length === 0) {
    logger.warn("No products from Spider + Serper", { dateStr });
    return [];
  }

  // Kill list: skip score < 5 (only applies to Spider products with real scores)
  const before = products.length;
  const filtered = products.filter((p) => p.rank === 0 || (p.score ?? 0) >= 5);
  if (filtered.length < before) {
    logger.info("Dropped low-score products", { dropped: before - filtered.length });
  }

  // Stage 1b: fetch individual product pages to extract maker_website
  // Leaderboard page doesn't include external links — only product pages have them
  const needsWebsite = filtered.filter((p) => !p.maker_website);
  if (needsWebsite.length > 0) {
    logger.info("Stage 1b: fetching product pages for maker_website", { count: needsWebsite.length });

    for (let i = 0; i < needsWebsite.length; i++) {
      const product = needsWebsite[i];
      const postUrl = product.ph_url;
      if (!postUrl) continue;

      try {
        const pageContent = await fetchUrl(postUrl, { renderJs: true, waitForSecs: 2 });
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

      if (i < needsWebsite.length - 1) {
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
      const product = needsLinkedin[i];
      try {
        const homepageContent = await fetchUrl(product.maker_website!, { renderJs: false, waitForSecs: 1 });
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
      if (i < needsLinkedin.length - 1) {
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

async function postsCountFromSlug(slug: string): Promise<number | null> {
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

async function serperFindProductSlug(productName: string): Promise<string | null> {
  if (!RAPID_API_KEY) return null;
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

async function fetchLaunchCount(product: PhProduct): Promise<number | null> {
  const phUrl = product.ph_url ?? "";

  let slug: string | null = null;
  if (phUrl.includes("/products/")) {
    slug = phUrl.replace(/\/$/, "").split("/products/").pop() ?? null;
  } else if (phUrl.includes("/posts/")) {
    slug = phUrl.replace(/\/$/, "").split("/posts/").pop() ?? null;
  }
  if (!slug) return null;

  // Try 1: exact slug
  let count = await postsCountFromSlug(slug);
  if (count !== null) return count;

  // Try 2: strip trailing -N (e.g. flowly-9 -> flowly)
  const stripped = slug.replace(/-\d+$/, "");
  if (stripped !== slug) {
    count = await postsCountFromSlug(stripped);
    if (count !== null) return count;
  }

  // Try 3: strip -for-X suffix (e.g. sleek-analytics-for-ios -> sleek-analytics)
  const forStripped = slug.replace(/-for-[a-z]+$/, "");
  if (forStripped !== slug && forStripped !== stripped) {
    count = await postsCountFromSlug(forStripped);
    if (count !== null) return count;
  }

  // Try 4: Serper search for canonical slug
  const canonicalSlug = await serperFindProductSlug(product.product_name);
  if (canonicalSlug && canonicalSlug !== slug && canonicalSlug !== stripped && canonicalSlug !== forStripped) {
    count = await postsCountFromSlug(canonicalSlug);
    if (count !== null) return count;
  }

  return null;
}

// ---------------------------------------------------------------------------
// Stage 2: Classify
// ---------------------------------------------------------------------------

async function stage2Classify(products: PhProduct[]): Promise<ClassifiedProduct[]> {
  if (products.length === 0) return [];

  logger.info("Stage 2: classifying products via GPT-4o-mini batch", { count: products.length });

  const productLines = products.map((p) => {
    const cats = (p.categories ?? []).join(", ");
    const domainHint = domainToCompanyHint(p.maker_website ?? null);
    const hintStr = domainHint ? ` | domain_hint=${domainHint}` : "";
    return `rank=${p.rank} | product_name=${p.product_name} | company_name_hint=${p.company_name ?? "unknown"}${hintStr} | tagline=${p.tagline ?? ""} | categories=${cats}`;
  });

  const raw = await openaiChat([
    {
      role: "system",
      content:
        "You classify Product Hunt launches. For each product, determine company_name, launch_type, and is_ai. " +
        "Return JSON only -- no commentary.",
    },
    {
      role: "user",
      content:
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
        '"Lingo.dev v1" + no hint → "Lingo.dev"\n\n' +
        'Return: {"classifications": [{"rank": <int>, "product_name": "<str>", "company_name": "<str>", ' +
        '"launch_type": "new_product|new_feature", "is_ai": true|false, ' +
        '"classification_reasoning": "<str>"}]}',
    },
  ]);

  const parsed = parseJsonResponse(raw) as {
    classifications?: { rank: number; product_name: string; company_name?: string; launch_type: string; is_ai: boolean; classification_reasoning: string }[];
  };
  const clsByRank = new Map((parsed.classifications ?? []).map((c) => [c.rank, c]));

  const classified: ClassifiedProduct[] = products.map((p) => {
    const cls = clsByRank.get(p.rank);
    return {
      ...p,
      company_name: cls?.company_name ?? p.company_name ?? null,
      launch_type: (cls?.launch_type as "new_product" | "new_feature") ?? "new_product",
      is_ai: cls?.is_ai ?? false,
      classification_reasoning: cls?.classification_reasoning ?? "",
      launch_count: null,
    };
  });

  logger.info("GPT classification done, fetching product page launch counts...");

  // Stage 2b: fetch launch counts with rate limiting (sequential, small delay)
  for (let i = 0; i < classified.length; i++) {
    const product = classified[i];
    if (i > 0) {
      // Small delay between PH page fetches to avoid rate limiting
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
    const count = await fetchLaunchCount(product);
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

async function stage3Push(products: ClassifiedProduct[], dateStr: string): Promise<number> {
  if (!SUPABASE_URL || !SUPABASE_KEY) {
    logger.warn("Supabase not configured -- skipping push");
    return 0;
  }
  if (products.length === 0) return 0;

  const TABLE = "product_launches";
  const rows = products.map((p) => toSupabaseRow(p, dateStr));

  logger.info("Stage 3: pushing to Supabase", { table: TABLE, count: rows.length });

  let upserted = 0;
  for (const row of rows) {
    try {
      const resp = await fetch(
        `${SUPABASE_URL}/rest/v1/${TABLE}?on_conflict=source_url`,
        {
          method: "POST",
          headers: supabaseHeaders("resolution=merge-duplicates"),
          body: JSON.stringify([row]),
          signal: AbortSignal.timeout(15_000),
        }
      );
      if (resp.ok) {
        upserted++;
      } else {
        const errText = await resp.text().catch(() => "");
        logger.error("Supabase upsert failed", {
          product: row["product_name"],
          status: resp.status,
          error: errText.slice(0, 200),
        });
      }
    } catch (e) {
      logger.error("Supabase upsert error", {
        product: row["product_name"],
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

async function stage4Enrich(products: ClassifiedProduct[]): Promise<number> {
  const targets = products
    .filter((p) => p.maker_website)
    .map((p) => ({
      companyName: p.company_name ?? p.product_name,
      domain: p.maker_website as string,
      sourceUrl: p.ph_url,
      knownLinkedin: p.linkedin_url,
    }));

  if (targets.length === 0) {
    logger.info("No products with maker_website — skipping enrichment");
    return 0;
  }

  logger.info("Stage 4: Blitz enrichment", { count: targets.length });
  const { enriched } = await day0BlitzEnrich("product_launches", targets);
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

  // Stage 1: fetch leaderboard
  const rawProducts = await stage1Fetch(dateStr);

  if (rawProducts.length === 0) {
    logger.warn("No products from Stage 1 -- aborting pipeline", { dateStr });
    return {
      date: dateStr,
      source: "product_hunt",
      launchCount: 0,
      stats: { rawResults: 0, afterClassify: 0, durationMs: Date.now() - start },
    };
  }

  // Stage 2: classify
  const classified = await stage2Classify(rawProducts);

  // Stage 3: push to Supabase
  const upserted = await stage3Push(classified, dateStr);

  // Stage 4: company enrichment (Blitz day-0)
  const enrichUpdated = await stage4Enrich(classified);

  const durationMs = Date.now() - start;
  logger.info("PH launch pipeline complete", {
    dateStr,
    rawProducts: rawProducts.length,
    classified: classified.length,
    upserted,
    enrichUpdated,
    durationMs,
  });

  return {
    date: dateStr,
    source: "product_hunt",
    launchCount: upserted,
    stats: {
      rawResults: rawProducts.length,
      afterClassify: classified.length,
      durationMs,
    },
  };
}
