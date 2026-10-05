import { logger } from "@trigger.dev/sdk";
import type {
  Candidate,
  ExtractedData,
  EnrichedRecord,
  PipelineConfig,
  PipelineResult,
  RoundConfig,
} from "./types.js";
import { runDiscovery } from "./serper.js";
import { fetchUrl } from "./scrape.js";
import { extractWithOpenAI, validateDomainSemantic } from "./openai.js";
import { scoreAndFilter } from "./filters.js";
import { isSupabaseConfigured, checkTable, pushToSupabase, getRecentCompanyNames, FundingWriteError } from "./supabase.js";
import { pushToWebhook } from "./webhook.js";
import { lookupDomainMultiSignal, isDomainBlocked, type ContextClues } from "./domain-lookup.js";
import { normalizeAmount } from "./normalize-amount.js";
import { day0BlitzEnrich } from "./enrich-company.js";

const SUSPECT_DOMAIN_PATTERNS = [
  /newswire|businesswire|prnewswire|einpresswire|globenewswire/i,
  /techcrunch|thesaasnews|finsmes|alleywatch|vcnewsdaily/i,
  /yahoo|reuters|bloomberg|forbes|fortune|cnbc|wsj/i,
  /linkedin|crunchbase|pitchbook|wikipedia|facebook/i,
  /eu-startups|tech\.eu|venturebeat|siliconangle/i,
  /finanzwire|therecursive|netinfluencer|biospace/i,
  /kitsapsun|cincinnati|bandt\.com/i,
  /googletagmanager|googleapis|gstatic|cloudfront|cloudflare/i,
  /wistia|cision|adobedtm|doubleclick|googlesyndication/i,
  /cdn\.|analytics\.|tracker\.|pixel\.|tag\./i,
  /fonts\.|static\.|assets\.|media\.|images\./i,
  /licdn|fbcdn|twimg|ytimg|akamai/i,
  /gravatar|wordpress\.com|wp\.com|disqus/i,
  /newrelic|segment\.io|mixpanel|hotjar|intercom/i,
  /yoast|schema\.org|w3\.org/i,
];

function isExtractedDomainSuspect(domain: string, sourceUrl: string): boolean {
  if (SUSPECT_DOMAIN_PATTERNS.some(p => p.test(domain))) return true;
  if (isDomainBlocked(domain)) return true;
  try {
    const sourceHost = new URL(sourceUrl).hostname.replace(/\.$/, "");
    // Common country-code suffixes keep the publisher label (e.g. publisher.co.uk).
    const suffixLabels = /\.(?:ac|co|com|edu|gov|net|org)\.[a-z]{2}$/.test(sourceHost) ? 3 : 2;
    const sourceDomain = sourceHost.split(".").slice(-suffixLabels).join(".");
    if (domain === sourceDomain || domain.endsWith(`.${sourceDomain}`)) return true;
  } catch { /* ignore */ }
  return false;
}

export function extractDateFromUrl(url: string): string | null {
  const match = url.match(/\/(\d{4})\/(\d{2})\/(\d{2})\//);
  if (!match) return null;
  const [, y, m, d] = match;
  const year = parseInt(y), month = parseInt(m), day = parseInt(d);
  if (year < 2020 || year > 2030 || month < 1 || month > 12 || day < 1 || day > 31) return null;
  return `${y}-${m}-${d}`;
}

/**
 * The extracted round date wins over the article URL date: a fresh article can report an old
 * round, and the freshness gate must see the round's date.
 */
export function resolveFundingDate(extractedDate: string | null | undefined, sourceUrl: string): string | null {
  const valid = !!extractedDate && /^\d{4}-\d{2}-\d{2}$/.test(extractedDate) && !Number.isNaN(Date.parse(extractedDate));
  return valid ? extractedDate : extractDateFromUrl(sourceUrl);
}

export const MAX_ROUND_AGE_DAYS = 45;

/** Stage 4 gates: confidence (drop LOW), then freshness (drop dated rounds past MAX_ROUND_AGE_DAYS). */
export function applyOutputGates<T extends Pick<EnrichedRecord, "confidence" | "funding_date">>(
  records: T[],
  runDate: string
): { highMedium: T[]; dropped: T[]; stale: T[] } {
  const confident = records.filter((r) => r.confidence !== "low");
  return {
    highMedium: confident.filter((r) => !isStaleRound(r.funding_date, runDate)),
    dropped: records.filter((r) => r.confidence === "low"),
    stale: confident.filter((r) => isStaleRound(r.funding_date, runDate)),
  };
}

/** True when a dated round is more than maxAgeDays before the run date. Undated rounds pass. */
export function isStaleRound(fundingDate: string | null | undefined, runDate: string, maxAgeDays = MAX_ROUND_AGE_DAYS): boolean {
  if (!fundingDate || !/^\d{4}-\d{2}-\d{2}/.test(fundingDate)) return false;
  const ageDays = (Date.parse(runDate) - Date.parse(fundingDate.slice(0, 10))) / 86_400_000;
  return ageDays > maxAgeDays;
}

function sanitizeDomain(domain: string): string {
  return domain
    .replace(/^https?:\/\//, "")
    .replace(/^www\./, "")
    .replace(/\/+$/, "");
}

export function extractDomainFromArticle(articleText: string, companyName: string, sourceUrl: string): string | null {
  const companyNames: string[] = [];
  const dbaMatch = companyName.match(/\bdba\s+([^)]+)/i);
  if (dbaMatch) companyNames.push(dbaMatch[1].replace(/[™®©]/g, "").trim().toLowerCase().replace(/[^a-z0-9]/g, ""));
  companyNames.push(companyName.replace(/\s*\(.*?\)\s*/g, "").trim().toLowerCase().replace(/[^a-z0-9]/g, ""));
  companyNames.push(companyName.toLowerCase().replace(/[^a-z0-9]/g, ""));
  const uniqueNames = [...new Set(companyNames.filter(n => n.length >= 3))];

  const patterns = [
    /(?:visit|learn more|more (?:info|information|at)|about us|website)\s*(?:at\s*)?[:.]?\s*((?:[a-z0-9][-a-z0-9]*\.)+[a-z]{2,})\b/gi,
    /(?<![a-z0-9@./-])((?:[a-z0-9][-a-z0-9]*\.)+(?:com|io|ai|co|dev|app|tech|health|bio)(?:\.[a-z]{2})?)(?![a-z0-9-]|\.[a-z0-9])/gi,
    /[\w.+-]+@((?:[a-z0-9][-a-z0-9]*\.)+[a-z]{2,})/gi,
  ];

  const candidates = new Map<string, number>();

  function addCandidate(rawDomain: string): void {
    const domain = rawDomain.toLowerCase().replace(/^www\./, "").replace(/\.$/, "");
    if (isExtractedDomainSuspect(domain, sourceUrl) || domain.length < 4 || !domain.includes(".")) return;

    const normDomain = domain.split(".")[0].replace(/[^a-z0-9]/g, "");
    let score = candidates.get(domain) ?? 0;

    for (const name of uniqueNames) {
      if (normDomain.includes(name) || name.includes(normDomain)) {
        score += 10;
        break;
      }
    }
    score += 1;
    candidates.set(domain, score);
  }

  // Parse whole destinations before looking for domains in the remaining prose.
  const domainText = articleText.replace(/[^\s<>"'()[\]]*\/[^\s<>"')\]]*/g, (rawUrl: string, offset: number) => {
    try {
      const parsed = new URL(rawUrl);
      if (/^https?:$/.test(parsed.protocol)) {
        addCandidate(parsed.hostname);
        if (/(?:visit|learn more|more (?:info|information|at)|about us|website)\s*(?:at\s*)?[:.]?\s*$/i.test(articleText.slice(0, offset))) {
          addCandidate(parsed.hostname);
        }
      }
    } catch { /* ignore invalid URLs */ }
    return " ";
  });

  for (const pattern of patterns) {
    let match;
    while ((match = pattern.exec(domainText)) !== null) {
      addCandidate(match[1]);
    }
  }

  if (candidates.size === 0) return null;

  const sorted = [...candidates.entries()].sort((a, b) => b[1] - a[1]);
  const [bestDomain, bestScore] = sorted[0];

  if (bestScore >= 10) return bestDomain;
  if (sorted.length === 1 && bestScore >= 2) return bestDomain;

  return null;
}

function extractContextClues(
  extracted: Pick<ExtractedData, "round_reasoning" | "lead_investors" | "industry" | "location"> | null,
  articleTitle: string
): ContextClues {
  const clues: ContextClues = {};

  if (extracted?.industry && extracted.industry !== "not_stated") {
    clues.industry = extracted.industry;
  }

  if (extracted?.location && extracted.location !== "not_stated") {
    clues.location = extracted.location;
  }

  if (!clues.industry) {
    const reasoning = extracted?.round_reasoning ?? "";
    const combined = `${articleTitle} ${reasoning}`;

    const industryPatterns = [
      /\b(AI|artificial intelligence|machine learning|ML)\b/i,
      /\b(fintech|financial technology|payments|banking)\b/i,
      /\b(healthtech|healthcare|medical|biotech|pharma)\b/i,
      /\b(SaaS|software|platform|cloud)\b/i,
      /\b(cybersecurity|security|infosec)\b/i,
      /\b(e-commerce|ecommerce|retail|marketplace)\b/i,
      /\b(robotics|autonomous|automation)\b/i,
      /\b(climate|cleantech|energy|sustainability)\b/i,
      /\b(edtech|education|learning)\b/i,
      /\b(proptech|real estate)\b/i,
    ];

    for (const pattern of industryPatterns) {
      const match = combined.match(pattern);
      if (match) {
        clues.industry = match[0];
        break;
      }
    }
  }

  return clues;
}

function buildEnrichedRecord(
  company: Candidate,
  extracted: ExtractedData | null,
  domain: string,
  sourceUrl: string,
  roundLabel: string,
  articleText: string | null,
  pipelineId: string
): EnrichedRecord {
  const amountRaw = extracted?.amount_raised ?? company.amount ?? "";
  const norm = normalizeAmount(amountRaw);

  return {
    company_name: extracted?.company_name ?? company.company_name,
    company_domain: sanitizeDomain(domain),
    amount_raised: amountRaw,
    amount_raised_usd: norm?.value_usd ?? null,
    amount_raised_currency: norm?.currency ?? null,
    funding_date: resolveFundingDate(extracted?.funding_date, sourceUrl),
    round_type: extracted?.round_type ?? company.round_type ?? roundLabel,
    source_url: sourceUrl,
    lead_investors: extracted?.lead_investors ?? null,
    round_reasoning: extracted?.round_reasoning ?? null,
    industry: extracted?.industry ?? null,
    location: extracted?.location ?? null,
    article_text: articleText,
    source_count: company.sources.length,
    score: company.best_score,
    discovered_by: [...new Set(company.sources.map((s) => s.query_source))].join(","),
    discovered_by_pipeline: pipelineId,
    confidence: company.confidence,
  };
}

function buildSkipEnrichRecord(company: Candidate, roundLabel: string, pipelineId: string): EnrichedRecord {
  const amountRaw = company.amount ?? "";
  const norm = normalizeAmount(amountRaw);

  return {
    company_name: company.company_name,
    company_domain: "not_enriched",
    amount_raised: amountRaw,
    amount_raised_usd: norm?.value_usd ?? null,
    amount_raised_currency: norm?.currency ?? null,
    funding_date: extractDateFromUrl(company.best_source_url),
    round_type: company.round_type ?? roundLabel,
    source_url: company.best_source_url,
    lead_investors: null,
    round_reasoning: null,
    industry: null,
    location: null,
    article_text: null,
    source_count: company.sources.length,
    score: company.best_score,
    discovered_by: [...new Set(company.sources.map((s) => s.query_source))].join(","),
    discovered_by_pipeline: pipelineId,
    confidence: company.confidence,
  };
}

async function enrichOneCompany(
  company: Candidate,
  roundConfig: RoundConfig,
  pipelineId: string,
  enrichUntil: number
): Promise<EnrichedRecord | null> {
  if (Date.now() >= enrichUntil) return null;
  let articleText: string | null = null;
  let sourceUrl = company.best_source_url;

  if (sourceUrl) {
    articleText = await fetchUrl(sourceUrl, { maxChars: 20_000, deadlineAt: enrichUntil });
    if (!articleText) {
      for (const src of company.sources) {
        if (Date.now() >= enrichUntil) break;
        if (src.url !== sourceUrl) {
          articleText = await fetchUrl(src.url, { maxChars: 20_000, deadlineAt: enrichUntil });
          if (articleText) {
            sourceUrl = src.url;
            break;
          }
        }
      }
    }
  }
  if (Date.now() >= enrichUntil) return null;

  if (!articleText) {
    logger.info(`Not shipped (no extraction): ${company.company_name} (no article text)`);
    return null;
  }
  const extracted = await extractWithOpenAI(
    articleText,
    company.company_name,
    company.amount ?? "",
    roundConfig,
    enrichUntil
  );
  if (Date.now() >= enrichUntil) return null;
  if (!extracted?.company_name?.trim()) {
    logger.info(`Not shipped (no extraction): ${company.company_name} (extraction failed)`);
    return null;
  }
  if (extracted.company_name.trim() === roundConfig.notRoundSentinel) {
    logger.info(`Filtered post-extraction: ${company.company_name}`);
    return null;
  }

  let domain = "not_found";
  let domainSource = "not_found";

  if (articleText) {
    const articleDomain = extractDomainFromArticle(articleText, company.company_name, sourceUrl);
    if (articleDomain) {
      domain = articleDomain;
      domainSource = "article_text_extract";
    }
  }

  if (domain === "not_found") {
    const extractedDomain = extracted?.company_domain?.replace(/^www\./, "");
    if (extractedDomain && extractedDomain !== "not_stated" && !isExtractedDomainSuspect(extractedDomain, sourceUrl)) {
      domain = extractedDomain;
      domainSource = "gpt_extraction";
    }
  }

  if (domain === "not_found") {
    const clues = extractContextClues(extracted, company.sources[0]?.title ?? "");
    if (Date.now() >= enrichUntil) return null;
    const result = await lookupDomainMultiSignal(company.company_name, clues, sourceUrl, enrichUntil);
    if (Date.now() >= enrichUntil) return null;
    domain = result.domain;
    domainSource = result.source;
  }

  if (domain !== "not_found" && articleText) {
    if (Date.now() >= enrichUntil) return null;
    const vresult = await validateDomainSemantic(sourceUrl, company.company_name, domain, articleText, enrichUntil);
    if (Date.now() >= enrichUntil) return null;
    const vstatus = vresult.status;

    if (vstatus === "Wrong") {
      const corrected = vresult.correctDomain?.trim() ?? "";
      if (corrected && corrected !== "not_found" && corrected !== "not_stated" && !isDomainBlocked(corrected)) {
        logger.info(`Semantic: corrected ${domain} -> ${corrected}`);
        domain = corrected;
        domainSource = "semantic_validation";
        if (vresult.correctCompanyName && vresult.correctCompanyName !== company.company_name) {
          company = { ...company, company_name: vresult.correctCompanyName };
        }
      } else {
        logger.info(`Semantic: rejected ${domain}, no valid correction`);
        domain = "not_found";
        domainSource = "semantic_rejected";
      }
    } else if (vstatus === "Unclear") {
      logger.info("Semantic: unclear - demoting confidence");
      company = { ...company, confidence: "low" };
    } else {
      logger.info(`Semantic: correct`);
    }
  }

  logger.info(`${company.company_name} → ${domain} (${domainSource})`);

  return buildEnrichedRecord(company, extracted, domain, sourceUrl, roundConfig.roundLabel, articleText, pipelineId);
}

const ENRICH_CONCURRENCY = 5;

// Time kept back from the run deadline for Clay, Supabase and Blitz delivery.
export const OUTPUT_RESERVE_MS = 5 * 60_000;
const OUTPUT_CHUNK = 5;

async function enrichCompanies(
  companies: Candidate[],
  maxEnrich: number,
  roundConfig: RoundConfig,
  pipelineId: string,
  enrichUntil: number
): Promise<EnrichedRecord[]> {
  const enriched: EnrichedRecord[] = [];
  const toProcess = companies.slice(0, maxEnrich);

  for (let batchStart = 0; batchStart < toProcess.length; batchStart += ENRICH_CONCURRENCY) {
    if (Date.now() >= enrichUntil) {
      logger.warn(`Enrichment deadline: skipped ${toProcess.length - batchStart} candidates (not written, so not marked known)`, {
        names: toProcess.slice(batchStart).map((c) => c.company_name),
      });
      break;
    }
    const batch = toProcess.slice(batchStart, batchStart + ENRICH_CONCURRENCY);
    logger.info(`Enriching batch ${Math.floor(batchStart / ENRICH_CONCURRENCY) + 1}: ${batch.map(c => c.company_name).join(", ")}`);

    let deadlineTimer: ReturnType<typeof setTimeout> | undefined;
    const expired = new Promise<null>((resolve) => {
      if (Number.isFinite(enrichUntil)) {
        deadlineTimer = setTimeout(() => resolve(null), Math.max(0, enrichUntil - Date.now()));
      }
    });
    try {
      // Race as well as passing deadlines: a stalled client must not consume the delivery reserve.
      const results = await Promise.allSettled(
        batch.map((company) => Promise.race([enrichOneCompany(company, roundConfig, pipelineId, enrichUntil), expired]))
      );

      for (const r of results) {
        if (r.status === "fulfilled" && r.value) {
          enriched.push(r.value);
        }
      }
    } finally {
      clearTimeout(deadlineTimer);
    }
  }

  return enriched;
}

/**
 * Deliver in chunks of OUTPUT_CHUNK to Supabase and then optional Clay within the deadline.
 * Clay acceptance does not gate Supabase. The next run's known-company dedup reads Supabase,
 * so only confirmed Supabase writes are marked known; failed writes stay eligible.
 * Blitz day-0 enrichment runs last with the time left; misses go to enrichment-retry-weekly.
 */
async function deliver(
  records: EnrichedRecord[],
  config: PipelineConfig,
  webhook: { url: string; token: string } | null,
  deadlineAt: number
): Promise<void> {
  const rc = config.roundConfig;
  const controller = new AbortController();
  let deadlineTimer: ReturnType<typeof setTimeout> | undefined;
  const expired = new Promise<void>((resolve) => {
    if (Number.isFinite(deadlineAt)) {
      deadlineTimer = setTimeout(() => { controller.abort(); resolve(); }, Math.max(0, deadlineAt - Date.now()));
    }
  });
  // Race as well as abort: a slow client must not hold the task past its deadline.
  const bounded = async <T>(budgetMs: number, fallback: T, operation: (signal: AbortSignal) => Promise<T>): Promise<T> => {
    if (deadlineAt - Date.now() < budgetMs || controller.signal.aborted) return fallback;
    return Promise.race([operation(controller.signal).catch((error: unknown) => {
      if (controller.signal.aborted) return fallback;
      throw error;
    }), expired.then(() => fallback)]);
  };
  let sent = 0;
  let upserted = 0;
  let delivered = 0;
  const written: EnrichedRecord[] = [];
  try {
    const supabase = isSupabaseConfigured() && await bounded(10_000, false, (signal) => checkTable(rc.supabaseTable, signal));
    if (isSupabaseConfigured() && !supabase) logger.warn(`Supabase table ${rc.supabaseTable} unavailable within delivery budget`);

    while (delivered < records.length && deadlineAt - Date.now() >= 10_000 && !controller.signal.aborted) {
      const chunk = records.slice(delivered, delivered + OUTPUT_CHUNK);
      delivered += chunk.length;
      if (supabase) {
        for (const record of chunk) {
          let confirmed = 0;
          try {
            const count = await bounded(15_000, 0, (signal) => pushToSupabase([record], config.date, rc.supabaseTable, signal,
              (count) => { confirmed = count; }));
            if (Math.max(count, confirmed) > 0) {
              upserted++;
              written.push(record);
            }
          } catch (error) {
            if (Math.max(confirmed, error instanceof FundingWriteError ? error.upserted : 0) > 0) {
              upserted++;
              written.push(record);
            }
            throw error;
          }
        }
      }
      if (webhook) {
        const results = await Promise.all(chunk.map((r) => bounded(10_000, 0,
          (signal) => pushToWebhook([r], config.date, webhook.url, webhook.token, signal))));
        const rejected = chunk.filter((_, i) => results[i] !== 1);
        if (rejected.length) logger.warn("Clay rejected or timed out", { names: rejected.map((r) => r.company_name) });
        sent += chunk.length - rejected.length;
      }
    }
    if (delivered < records.length) {
      logger.warn(`Delivery deadline: ${records.length - delivered} rounds not delivered (not written, so not marked known)`, {
        names: records.slice(delivered).map((r) => r.company_name),
      });
    }
    if (!supabase) return;

    // Day-0 company enrichment (Blitz, free) for confirmed Supabase rows only.
    const targets = written.filter((r) => r.company_domain)
      .map((r) => ({ companyName: r.company_name, domain: r.company_domain, sourceUrl: r.source_url }));
    let done = 0;
    while (done < targets.length && deadlineAt - Date.now() >= 30_000 && !controller.signal.aborted) {
      const completed = await bounded(30_000, false, async (signal) => {
        await day0BlitzEnrich("funding_discoveries", targets.slice(done, done + OUTPUT_CHUNK), OUTPUT_CHUNK, signal);
        return true;
      });
      if (!completed) break;
      done += OUTPUT_CHUNK;
    }
    if (done < targets.length) {
      logger.warn(`Delivery deadline: Blitz skipped ${targets.length - done} rows (left for enrichment-retry-weekly)`);
    }
  } finally {
    clearTimeout(deadlineTimer);
    controller.abort();
    logger.info(`Webhook: ${sent}/${delivered} sent; Supabase: ${upserted}/${delivered} upserted to ${rc.supabaseTable} (confirmed counts${webhook ? "" : "; Clay: off"})`);
  }
}

export async function runFundingPipeline(
  config: PipelineConfig
): Promise<PipelineResult> {
  const start = Date.now();
  const deadlineAt = config.deadlineAt ?? Number.POSITIVE_INFINITY;
  const rc = config.roundConfig;
  // A configured Clay URL requires its token before work; an absent URL disables Clay.
  const webhookUrl = config.dryRun ? "" : rc.webhookUrl;
  const webhook = webhookUrl ? { url: webhookUrl, token: rc.webhookAuthToken } : null;
  if (!config.dryRun && !webhook) logger.info(`Clay delivery off for ${rc.roundLabel}`);

  logger.info(`${rc.roundLabel} pipeline starting`, {
    date: config.date,
    tbs: config.tbs,
    skipEnrich: config.skipEnrich,
    roundType: rc.roundType,
  });

  logger.info("Stage 1: Discovery");
  const rawResults = await runDiscovery(rc.queries, config.tbs);
  logger.info(`Stage 1 complete: ${rawResults.length} raw results`);

  logger.info("Stage 2: Score & Filter");
  const scored = scoreAndFilter(rawResults, rc);
  logger.info(
    `Stage 2 complete: ${scored.stats.company_count} companies (filtered ${scored.stats.filtered_count})`
  );

  let companiesForEnrich = scored.companies;

  if (config.skipKnownCompanies) {
    const days = config.skipKnownDays ?? 7;
    logger.info(`Cross-date dedup: checking Supabase for companies from last ${days} days`);
    const knownNames = await getRecentCompanyNames(rc.supabaseTable, days);
    if (knownNames.size > 0) {
      const before = companiesForEnrich.length;
      companiesForEnrich = companiesForEnrich.filter(
        (c) => !knownNames.has(c.company_name_normalized)
      );
      const skipped = before - companiesForEnrich.length;
      if (skipped > 0) {
        logger.info(`Skipped ${skipped} known companies from last ${days} days`);
      }
    } else {
      logger.info("Cross-date dedup: no known companies found (or Supabase unavailable)");
    }
  }

  // Enrichment can only lower confidence, so LOW candidates would fail the Stage 4 gate anyway.
  // Drop them here so they do not use up the maxEnrich cap.
  const lowBeforeEnrich = companiesForEnrich.filter((c) => c.confidence === "low");
  if (lowBeforeEnrich.length > 0) {
    logger.info(`Skipped ${lowBeforeEnrich.length} LOW candidates before enrichment`, {
      names: lowBeforeEnrich.map((c) => c.company_name),
    });
    companiesForEnrich = companiesForEnrich.filter((c) => c.confidence !== "low");
  }

  let enriched: EnrichedRecord[];
  if (config.skipEnrich) {
    logger.info("Stage 3: Skipped (skipEnrich)");
    enriched = companiesForEnrich.map((c) => buildSkipEnrichRecord(c, rc.roundLabel, config.pipelineId));
  } else {
    logger.info(`Stage 3: Enrich (max ${config.maxEnrich})`);
    enriched = await enrichCompanies(companiesForEnrich, config.maxEnrich, rc, config.pipelineId, deadlineAt - OUTPUT_RESERVE_MS);
    logger.info(`Stage 3 complete: ${enriched.length} enriched`);
  }

  logger.info("Stage 4: Output");

  // Confidence gate drops LOW; freshness gate drops old rounds that search date filters let through.
  const { highMedium, dropped, stale } = applyOutputGates(enriched, config.date);
  if (dropped.length > 0) {
    logger.info(`Confidence gate: dropped ${dropped.length} LOW records`, {
      names: dropped.map((r) => r.company_name),
    });
  }
  if (stale.length > 0) {
    logger.info(`Freshness gate: dropped ${stale.length} rounds older than ${MAX_ROUND_AGE_DAYS} days`, {
      names: stale.map((r) => `${r.company_name} (${r.funding_date})`),
    });
  }
  const mediumOnly = highMedium.filter((r) => r.confidence === "medium");
  if (mediumOnly.length > 0) {
    logger.info(`Medium confidence (review): ${mediumOnly.map((r) => r.company_name).join(", ")}`);
  }

  if (config.dryRun) {
    logger.info("Dry run - skipping Supabase and webhook output");
  } else {
    await deliver(highMedium, config, webhook, deadlineAt);
  }

  const durationMs = Date.now() - start;

  logger.info(`${rc.roundLabel} pipeline complete`, {
    companies: enriched.length,
    durationMs,
  });

  return {
    date: config.date,
    companyCount: highMedium.length,
    companies: highMedium,
    stats: {
      rawResults: rawResults.length,
      candidatesAfterFilter: scored.stats.company_count,
      enrichedCount: highMedium.length,
      durationMs,
    },
  };
}
