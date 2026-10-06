import { logger } from "@trigger.dev/sdk";
import { companyDomain } from "./additional-signals.js";
import { lookupDomainMultiSignal, type ContextClues } from "./domain-lookup.js";
import { extractDomainFromArticle } from "./pipeline.js";

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
  cache: Map<string, string | null>;
}

export function companyDomainBudget(deadlineAt: number, limit = 20): CompanyDomainBudget {
  return { deadlineAt, remainingLookups: limit, cache: new Map() };
}

export function companyDomainCacheKey(row: CompanyDomainRow): string {
  // A provider company ID allows reuse across jobs; otherwise keep distinct source identities separate.
  return JSON.stringify([row.company_name, row.industry || "", row.location || "", row.company_identity || row.source_url].map((part) => part.trim().toLowerCase()));
}

/** Batch orchestration over the existing article and search resolvers. */
export async function fillCompanyDomains<T extends CompanyDomainRow>(rows: T[], budget: CompanyDomainBudget): Promise<Array<T & { company_domain: string | null }>> {
  const filled: Array<T & { company_domain: string | null }> = [];
  for (const row of rows) {
    const name = row.company_name.trim();
    const key = companyDomainCacheKey(row);
    let domain = companyDomain(row.company_domain) || companyDomain(row.maker_website) || companyDomain(row.company_website);
    if (!domain) {
      const fromArticle = extractDomainFromArticle(row.article_text || row.description || "", row.company_name, row.source_url);
      // A single first-party URL must match the company name to pass the existing extractor.
      const fromUrl = fromArticle || extractDomainFromArticle(row.source_url, row.company_name, "");
      domain = companyDomain(fromUrl);
    }
    if (!domain && name && budget.cache.has(key)) domain = budget.cache.get(key) || "";
    if (!domain && name && !budget.cache.has(key) && budget.remainingLookups > 0 && Date.now() + 5_000 <= budget.deadlineAt) {
      budget.remainingLookups--;
      const clues: ContextClues = {
        industry: row.industry || undefined,
        location: row.location || undefined,
        productOrService: row.description?.slice(0, 500) || undefined,
      };
      try {
        const result = await lookupDomainMultiSignal(row.company_name, clues, row.source_url, Math.min(budget.deadlineAt, Date.now() + 60_000));
        if (Date.now() <= budget.deadlineAt && result.confidence === "high") domain = companyDomain(result.domain);
      } catch (error) {
        logger.warn("Company domain lookup failed", { company: row.company_name, error: error instanceof Error ? error.message : String(error) });
      }
      budget.cache.set(key, domain || null);
    }
    if (domain && name) budget.cache.set(key, domain);
    filled.push({ ...row, company_domain: domain || null });
  }
  return filled;
}

/** PostgREST requires identical keys per batch. Omit unknown domains to preserve stored values on upsert. */
export function launchDomainBatches<T extends CompanyDomainRow>(rows: T[]): Array<Array<Omit<T, "company_domain"> & { company_domain?: string }>> {
  const resolved = rows.filter((row) => row.company_domain).map((row) => ({ ...row, company_domain: row.company_domain! }));
  const missing = rows.filter((row) => !row.company_domain).map(({ company_domain: _domain, ...row }) => row);
  return [resolved, missing].filter((batch) => batch.length > 0);
}
