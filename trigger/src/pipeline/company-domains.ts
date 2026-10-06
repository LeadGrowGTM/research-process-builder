import { logger } from "@trigger.dev/sdk";
import { companyDomain } from "./additional-signals.js";
import { lookupDomainMultiSignal, type ContextClues } from "./domain-lookup.js";
import { extractDomainFromArticle } from "./pipeline.js";
import { validateDomainSemantic } from "./openai.js";
import { hasTime, LAUNCH_WRITE_TIMEOUT_MS } from "./launch-budget.js";

export interface CompanyDomainRow {
  company_name: string;
  company_domain?: string | null;
  source_url: string;
  maker_website?: string | null;
  company_website?: string | null;
  article_text?: string | null;
  description?: string | null;
  industry?: string | null;
  location?: string | null;
  company_identity?: string;
}

export interface CompanyDomainBudget {
  deadlineAt: number;
  remainingLookups: number;
  cache: Map<string, { domain: string | null; resolution: CompanyDomainResolution }>;
}

export interface CompanyDomainResolution {
  source: "stored" | "article" | "url" | "search" | "cache" | "none";
  lookupDomain: string | null;
  confidence: "high" | "medium" | "low" | null;
  rejectedReason: string | null;
}

interface CompanyDomainOptions {
  validateFunding?: boolean;
  onResolution?: (resolution: CompanyDomainResolution) => void;
}

export function companyDomainBudget(deadlineAt: number, limit = 20): CompanyDomainBudget {
  return { deadlineAt, remainingLookups: limit, cache: new Map() };
}

export function companyDomainCacheKey(row: CompanyDomainRow): string {
  // A provider company ID allows reuse across jobs; otherwise keep distinct source identities separate.
  return JSON.stringify([row.company_name, row.industry || "", row.location || "", row.company_identity || row.source_url].map((part) => part.trim().toLowerCase()));
}

const NON_COMPANY_NAMES = new Set(["fundraising news", "funding news", "newsroom"]);

export function companyDomainNameRejection(name: string): string | null {
  const normalized = name.trim().replace(/\s+/g, " ").toLowerCase();
  if (!normalized) return "missing_company_name";
  if (NON_COMPANY_NAMES.has(normalized)) return "non_company_name";
  if (normalized.split(" ").length > 6) return "company_name_too_long";
  if (/[。！？；，：]/.test(normalized)) return "cjk_sentence_punctuation";
  return null;
}

/** Batch orchestration over the existing article and search resolvers. */
export async function fillCompanyDomains<T extends CompanyDomainRow>(rows: T[], budget: CompanyDomainBudget, useArticleEvidence = true, options: CompanyDomainOptions = {}): Promise<Array<T & { company_domain: string | null }>> {
  const filled: Array<T & { company_domain: string | null }> = [];
  for (const row of rows) {
    const name = row.company_name.trim();
    const key = companyDomainCacheKey(row);
    let resolution: CompanyDomainResolution = { source: "none", lookupDomain: null, confidence: null, rejectedReason: null };
    let domain = companyDomain(row.company_domain) || companyDomain(row.maker_website) || companyDomain(row.company_website);
    if (domain) resolution = { ...resolution, source: "stored", lookupDomain: domain, confidence: "high" };
    if (!domain && useArticleEvidence) {
      const fromArticle = extractDomainFromArticle(row.article_text || row.description || "", row.company_name, row.source_url);
      // Keep the publisher check active when inspecting the source URL itself.
      const fromUrl = fromArticle || extractDomainFromArticle(row.source_url, row.company_name, row.source_url);
      domain = companyDomain(fromUrl);
      if (domain) resolution = { ...resolution, source: fromArticle ? "article" : "url", lookupDomain: fromUrl, confidence: "high" };
    }
    if (!domain && options.validateFunding && row.article_text) {
      // A funding story on the company's own site (firecrawl.dev/blog/...) names its domain; the article must still confirm it.
      const fromSource = companyDomain(row.source_url);
      if (fromSource && fromSource.split(".")[0].replace(/[^a-z0-9]/g, "") === name.toLowerCase().replace(/[^a-z0-9]/g, "")) {
        domain = fromSource;
        resolution = { ...resolution, source: "url", lookupDomain: fromSource, confidence: "medium" };
      }
    }
    const nameRejection = companyDomainNameRejection(name);
    if (!domain && nameRejection) {
      resolution.rejectedReason = nameRejection;
      if (!options.onResolution) logger.info("Company domain lookup skipped", { company: row.company_name, ...resolution });
    } else if (!domain && budget.cache.has(key)) {
      const cached = budget.cache.get(key)!;
      domain = cached.domain || "";
      resolution = { ...cached.resolution, source: "cache" };
      if (!domain && options.validateFunding && row.article_text && ["medium_requires_article_validation", "semantic_unclear", "semantic_wrong", "semantic_validation_failed"].includes(resolution.rejectedReason || "")) {
        domain = companyDomain(resolution.lookupDomain);
        resolution.rejectedReason = null;
      }
    } else if (!domain && budget.remainingLookups > 0 && Date.now() + 5_000 <= budget.deadlineAt) {
      budget.remainingLookups--;
      resolution.source = "search";
      const clues: ContextClues = {
        industry: row.industry || undefined,
        location: row.location || undefined,
        productOrService: row.description?.slice(0, 500) || undefined,
      };
      try {
        const result = await lookupDomainMultiSignal(row.company_name, clues, row.source_url, Math.min(budget.deadlineAt, Date.now() + 60_000));
        resolution.lookupDomain = result.domain || null;
        resolution.confidence = result.confidence;
        logger.info(`Domain lookup ${row.company_name}: ${result.domain} (${result.confidence}) - ${(result.evidence ?? "").slice(0, 200)}`);
        if (Date.now() > budget.deadlineAt) resolution.rejectedReason = "deadline_exceeded";
        else {
          domain = companyDomain(result.domain);
          if (!domain) resolution.rejectedReason = "no_usable_domain";
        }
      } catch (error) {
        resolution.rejectedReason = "lookup_failed";
        logger.warn("Company domain lookup failed", { company: row.company_name, error: error instanceof Error ? error.message : String(error) });
      }
    } else if (!domain) {
      resolution.rejectedReason = budget.remainingLookups <= 0 ? "lookup_budget_exhausted" : "deadline_exceeded";
    }
    if (domain && resolution.confidence !== "high" && !(options.validateFunding && row.article_text && resolution.confidence === "medium")) {
      domain = "";
      resolution.rejectedReason = options.validateFunding && resolution.confidence === "medium" ? "medium_requires_article_validation" : "confidence_not_high";
    }
    if (domain && options.validateFunding && row.article_text) {
      try {
        if (Date.now() >= budget.deadlineAt) {
          domain = "";
          resolution.rejectedReason = "deadline_exceeded";
        } else {
          const validation = await validateDomainSemantic(row.source_url, row.company_name, domain, row.article_text, budget.deadlineAt);
          if (Date.now() >= budget.deadlineAt) {
            domain = "";
            resolution.rejectedReason = "deadline_exceeded";
          } else if (validation.status === "Wrong") {
            domain = companyDomain(validation.correctDomain);
            if (!domain) resolution.rejectedReason = "semantic_wrong";
          } else if (validation.status !== "Correct" && resolution.confidence !== "high") {
            // Matches the daily path: Unclear keeps a high-confidence domain and rejects anything weaker.
            domain = "";
            resolution.rejectedReason = "semantic_unclear";
          }
        }
      } catch (error) {
        domain = "";
        resolution.rejectedReason = "semantic_validation_failed";
        logger.warn("Company domain validation failed", { company: row.company_name, error: error instanceof Error ? error.message : String(error) });
      }
    }
    if (name && resolution.source !== "none") budget.cache.set(key, { domain: domain || null, resolution });
    options.onResolution?.(resolution);
    filled.push({ ...row, company_domain: domain || null });
  }
  return filled;
}

/** Domains are filled by conditional PATCH after upsert, so reruns preserve stored values. */
export function launchDomainBatches<T extends CompanyDomainRow>(rows: T[]): Array<Array<Omit<T, "company_domain">>> {
  return rows.length ? [rows.map(({ company_domain: _domain, ...row }) => row)] : [];
}

export async function patchLaunchDomains(rows: CompanyDomainRow[], tableUrl: string, headers: Record<string, string>, deadlineAt: number): Promise<void> {
  if (!hasTime(deadlineAt, LAUNCH_WRITE_TIMEOUT_MS)) return;
  const repairDeadline = Math.min(deadlineAt, Date.now() + LAUNCH_WRITE_TIMEOUT_MS);
  for (const row of rows) {
    if (!row.company_domain) continue;
    if (!hasTime(repairDeadline, 1)) break;
    const query = new URLSearchParams({ source_url: `eq.${row.source_url}`, or: "(company_domain.is.null,company_domain.eq.)" });
    try {
      const response = await fetch(`${tableUrl}?${query}`, {
        method: "PATCH", headers,
        body: JSON.stringify({ company_domain: row.company_domain }),
        signal: AbortSignal.timeout(Math.min(LAUNCH_WRITE_TIMEOUT_MS, repairDeadline - Date.now())),
      });
      if (!response.ok) logger.warn("Launch domain repair failed", { sourceUrl: row.source_url, status: response.status });
    } catch (error) {
      logger.warn("Launch domain repair failed", { sourceUrl: row.source_url, error: error instanceof Error ? error.message : String(error) });
    }
  }
}
