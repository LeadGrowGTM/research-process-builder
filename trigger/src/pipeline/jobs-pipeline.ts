import { logger } from "@trigger.dev/sdk";
import { companyDomain } from "./additional-signals.js";
import { companyDomainBudget, fillCompanyDomains } from "./company-domains.js";

const SUPABASE_URL = (() => {
  const url = process.env.SUPABASE_PROJECT_URL ?? process.env.SUPABASE_URL ?? "";
  return url.startsWith("http") ? url : "";
})();
const SUPABASE_KEY =
  process.env.SUPABASE_KEY ??
  process.env.SUPABASE_SERVICE_ROLE_KEY ??
  process.env.SUPABASE_ANON_KEY ??
  "";

const TABLE = "game_job_signals";
const JOBS_API = "https://80.lv/api/jobs";
const TOTAL_PAGES = 6;
const CLAY_WEBHOOK_URL = process.env.CLAY_GAME_JOB_SIGNALS_WEBHOOK ?? "";

// Category slugs from 80.lv API that indicate animation/mocap/rigging roles
const ANIMATION_CATEGORY_SLUGS = new Set([
  "animation-designers",
  "gameplay-animators",
  "technical-animators",
  "technical-Animators",
  "cinematic-animators",
  "cinematic-Animators",
  "motion-designers",
  "rigging-artists",
]);

// High-signal = explicitly mocap/rigging/locomotion operations
const HIGH_SIGNAL_KEYWORDS = [
  "motion capture",
  "mocap",
  "motion cap",
  "character rigging",
  "rigging artist",
  "rigger",
  "technical animator",
  "tech anim",
  "locomotion",
  "motion matching",
  "character td",
  "character technical director",
  "procedural animation",
  "inverse kinematics",
];

// Medium signal = animation leadership at game studios
const MEDIUM_SIGNAL_KEYWORDS = [
  "animation director",
  "lead animator",
  "gameplay animator",
  "cinematic animator",
  "senior animator",
  "animation lead",
  "gameplay animation",
];

// Kill list — exclude even if other keywords match
const KILL_KEYWORDS = [
  "motion graphics",
  "ui animator",
  "2d animation",
  "vfx artist",
  "special effects",
  "compositor",
];

export interface JobSignalRecord {
  job_id: number;
  job_title: string;
  company_name: string;
  company_website: string | null;
  company_domain: string | null;
  location_country: string | null;
  location_city: string | null;
  job_type: string | null;
  categories: string[];
  tags: string[];
  signal_keywords: string[];
  signal_strength: "high" | "medium";
  job_url: string;
  date_posted: string | null;
  date_detected: string;
}

interface RawJob {
  id: number;
  title: string;
  slug: string;
  date: string;
  description: string;
  job_type: string;
  country: string | null;
  city: string | null;
  company: {
    id: number;
    title: string;
    website: string | null;
  };
  tags: { name: string; slug: string }[];
  categories: { id: number; name: string; slug: string }[];
}

function classifyJob(job: RawJob): { matched: boolean; keywords: string[]; strength: "high" | "medium" } {
  const titleLower = job.title.toLowerCase();
  const descLower = (job.description ?? "").toLowerCase().slice(0, 1000);
  const combined = `${titleLower} ${descLower}`;

  // Kill check first
  for (const kill of KILL_KEYWORDS) {
    if (titleLower.includes(kill)) return { matched: false, keywords: [], strength: "medium" };
  }

  const catSlugs = job.categories.map((c) => c.slug.toLowerCase());
  const inAnimCat = catSlugs.some((s) => ANIMATION_CATEGORY_SLUGS.has(s));

  const foundKeywords: string[] = [];
  let strength: "high" | "medium" = "medium";

  for (const kw of HIGH_SIGNAL_KEYWORDS) {
    if (combined.includes(kw)) {
      foundKeywords.push(kw);
      strength = "high";
    }
  }

  for (const kw of MEDIUM_SIGNAL_KEYWORDS) {
    if (combined.includes(kw)) {
      foundKeywords.push(kw);
    }
  }

  const titleHasHigh = HIGH_SIGNAL_KEYWORDS.some((kw) => titleLower.includes(kw));
  const titleHasMedium = MEDIUM_SIGNAL_KEYWORDS.some((kw) => titleLower.includes(kw));

  if (titleHasHigh || (inAnimCat && foundKeywords.length > 0) || (inAnimCat && titleHasMedium)) {
    if (foundKeywords.length === 0) {
      foundKeywords.push(...catSlugs.filter((s) => ANIMATION_CATEGORY_SLUGS.has(s)));
    }
    return { matched: true, keywords: foundKeywords, strength: titleHasHigh ? "high" : "medium" };
  }

  return { matched: false, keywords: [], strength: "medium" };
}

async function fetchJobsPage(page: number): Promise<RawJob[]> {
  const resp = await fetch(`${JOBS_API}?page=${page}`, {
    headers: { "User-Agent": "LeadGrow-Pipeline/1.0" },
    signal: AbortSignal.timeout(15_000),
  });
  if (!resp.ok) throw new Error(`80.lv API ${resp.status} on page ${page}`);
  const data = (await resp.json()) as { jobs: { items: RawJob[] } };
  return data.jobs?.items ?? [];
}

async function seenRecently(jobId: number): Promise<boolean> {
  if (!SUPABASE_URL || !SUPABASE_KEY) return false;
  try {
    const since = new Date();
    since.setDate(since.getDate() - 30);
    const url = `${SUPABASE_URL}/rest/v1/${TABLE}?job_id=eq.${jobId}&date_detected=gte.${since.toISOString().split("T")[0]}&select=id&limit=1`;
    const resp = await fetch(url, {
      headers: { apikey: SUPABASE_KEY, Authorization: `Bearer ${SUPABASE_KEY}` },
      signal: AbortSignal.timeout(8_000),
    });
    if (!resp.ok) return false;
    const rows = (await resp.json()) as unknown[];
    return rows.length > 0;
  } catch {
    return false;
  }
}

async function pushToClay(signal: JobSignalRecord): Promise<void> {
  if (!CLAY_WEBHOOK_URL) return;
  try {
    const resp = await fetch(CLAY_WEBHOOK_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        job_id: signal.job_id,
        job_title: signal.job_title,
        company_name: signal.company_name,
        company_website: signal.company_website ?? "",
        company_domain: signal.company_domain ?? "",
        signal_strength: signal.signal_strength,
        signal_keywords: signal.signal_keywords.join(", "),
        location_country: signal.location_country ?? "",
        location_city: signal.location_city ?? "",
        job_type: signal.job_type ?? "",
        job_url: signal.job_url,
        date_posted: signal.date_posted ?? "",
        date_detected: signal.date_detected,
      }),
      signal: AbortSignal.timeout(10_000),
    });
    if (!resp.ok) {
      logger.warn(`Clay push failed for job_id=${signal.job_id}: ${resp.status}`);
    }
  } catch (e) {
    logger.warn(`Clay push error for job_id=${signal.job_id}: ${e instanceof Error ? e.message : String(e)}`);
  }
}

async function pushToSupabase(signals: JobSignalRecord[]): Promise<number> {
  if (!SUPABASE_URL || !SUPABASE_KEY) return 0;
  const h = {
    apikey: SUPABASE_KEY,
    Authorization: `Bearer ${SUPABASE_KEY}`,
    "Content-Type": "application/json",
    Prefer: "resolution=merge-duplicates",
  };
  let upserted = 0;
  for (const row of signals) {
    const seen = await seenRecently(row.job_id);
    if (seen) {
      if (row.company_domain) {
        try {
          const response = await fetch(`${SUPABASE_URL}/rest/v1/${TABLE}?job_id=eq.${row.job_id}&or=(company_domain.is.null,company_domain.eq.,company_domain.in.(not_found,not_enriched,not_stated))`, {
            method: "PATCH", headers: h,
            body: JSON.stringify({ company_domain: row.company_domain }),
            signal: AbortSignal.timeout(8_000),
          });
          if (!response.ok) logger.warn("Job domain repair failed", { jobId: row.job_id, status: response.status });
        } catch (error) {
          logger.warn("Job domain repair failed", { jobId: row.job_id, error: error instanceof Error ? error.message : String(error) });
        }
      }
      logger.info(`Dedup skip: job_id=${row.job_id} "${row.job_title}"`);
      continue;
    }
    try {
      const resp = await fetch(`${SUPABASE_URL}/rest/v1/${TABLE}?on_conflict=job_id`, {
        method: "POST",
        headers: h,
        body: JSON.stringify([row]),
        signal: AbortSignal.timeout(15_000),
      });
      if (resp.ok) {
        upserted++;
        await pushToClay(row);
      } else {
        logger.error(`Supabase upsert failed: ${resp.status} ${(await resp.text().catch(() => "")).slice(0, 200)}`);
      }
    } catch (e) {
      logger.error(`Supabase error: ${e instanceof Error ? e.message : String(e)}`);
    }
  }
  return upserted;
}

export interface JobsPipelineResult {
  date: string;
  totalFetched: number;
  matched: number;
  highSignal: number;
  upserted: number;
  durationMs: number;
}

export async function runJobsPipeline(opts: { dryRun: boolean; date: string }): Promise<JobsPipelineResult> {
  const start = Date.now();
  logger.info("80.lv jobs pipeline starting", opts);

  // Stage 1: fetch all pages
  const allJobs: RawJob[] = [];
  for (let page = 0; page < TOTAL_PAGES; page++) {
    try {
      const jobs = await fetchJobsPage(page);
      allJobs.push(...jobs);
      logger.info(`Page ${page}: ${jobs.length} jobs fetched`);
    } catch (e) {
      logger.warn(`Page ${page} failed: ${e instanceof Error ? e.message : String(e)}`);
    }
  }
  logger.info(`Stage 1 complete: ${allJobs.length} total jobs`);

  // Stage 2: map all jobs — no filtering, Clay handles downstream
  let signals: JobSignalRecord[] = allJobs.map((job) => {
    const { keywords, strength } = classifyJob(job);
    return {
      job_id: job.id,
      job_title: job.title,
      company_name: job.company.title,
      company_website: job.company.website ?? null,
      company_domain: companyDomain(job.company.website) || null,
      location_country: job.country ?? null,
      location_city: job.city ?? null,
      job_type: job.job_type ?? null,
      categories: job.categories.map((c) => c.name),
      tags: job.tags.map((t) => t.name),
      signal_keywords: keywords,
      signal_strength: strength,
      job_url: `https://80.lv/jobs/${job.slug}`,
      date_posted: job.date ?? null,
      date_detected: opts.date,
    };
  });

  const budget = companyDomainBudget(Math.min(start + 240_000, Date.now() + 90_000));
  signals = (await fillCompanyDomains(signals.map((row, i) => ({
    ...row, source_url: row.job_url,
    company_identity: Number.isSafeInteger(allJobs[i].company.id) ? `80lv:${allJobs[i].company.id}` : undefined,
    description: allJobs[i].description?.slice(0, 2_000), location: row.location_country,
  })), budget))
    .map(({ source_url: _sourceUrl, company_identity: _companyIdentity, description: _description, location: _location, ...row }) => row);

  const highSignal = signals.filter((s) => s.signal_strength === "high").length;
  logger.info(`Stage 2 complete: ${signals.length} total jobs (${highSignal} high-signal, ${signals.length - highSignal} other)`);

  if (opts.dryRun) {
    logger.info("Dry run — skipping Supabase");
    return { date: opts.date, totalFetched: allJobs.length, matched: signals.length, highSignal, upserted: 0, durationMs: Date.now() - start };
  }

  // Stage 3: Supabase upsert — Clay pulls from game_job_signals via API
  const upserted = await pushToSupabase(signals);
  logger.info(`Stage 3 complete: ${upserted}/${signals.length} upserted to ${TABLE}`);

  return {
    date: opts.date,
    totalFetched: allJobs.length,
    matched: signals.length,
    highSignal,
    upserted,
    durationMs: Date.now() - start,
  };
}
