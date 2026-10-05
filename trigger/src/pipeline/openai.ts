import type { ExtractedData, RoundConfig } from "./types.js";
import { lunaJson } from "./luna.js";
import { INDUSTRIES, ROUND_TYPES } from "./taxonomy.js";

export interface SemanticValidationResult {
  correctCompanyName: string;
  correctDomain: string;
  status: "Correct" | "Wrong" | "Unclear";
  reason: string;
}

const EXTRACTION_SCHEMA = {
  type: "object",
  properties: {
    company_name: { type: "string" },
    company_domain: { type: ["string", "null"] },
    amount_raised: { type: ["string", "null"] },
    round_type: { anyOf: [{ type: "string", enum: [...ROUND_TYPES] }, { type: "null" }] },
    lead_investors: { type: ["string", "null"] },
    round_reasoning: { type: ["string", "null"] },
    industry: { anyOf: [{ type: "string", enum: [...INDUSTRIES] }, { type: "null" }] },
    location: { type: ["string", "null"] },
    funding_date: { type: ["string", "null"] },
  },
  required: [
    "company_name",
    "company_domain",
    "amount_raised",
    "round_type",
    "lead_investors",
    "round_reasoning",
    "industry",
    "location",
    "funding_date",
  ],
  additionalProperties: false,
};

export async function extractWithOpenAI(
  articleText: string,
  companyHint: string,
  amountHint: string,
  config: RoundConfig,
  deadlineAt?: number
): Promise<ExtractedData | null> {
  const prompt = config.extractionPrompt
    .replace("{{companyHint}}", companyHint)
    .replace("{{amountHint}}", amountHint)
    .replace("{{articleText}}", articleText.slice(0, 20000));

  const result = await lunaJson<ExtractedData>({
    name: "funding_extraction",
    schema: EXTRACTION_SCHEMA,
    systemPrompt:
      "You extract structured funding data from articles. Return valid JSON only, no markdown fences, no explanation.",
    userPrompt: prompt,
    maxTokens: 500,
    timeoutMs: 30_000,
    deadlineAt,
  });

  return result?.data ?? null;
}

const SEMANTIC_VALIDATION_SYSTEM = `You are a company domain verification agent. You are given a CANDIDATE domain to verify - it may be correct or wrong. Your job: find the TRUE domain, then compare.

Step 1 - Find the true domain from the article:
  a) Is the company name a markdown hyperlink like [Company](https://example.com)?
     YES → that hyperlinked URL is the true domain. Stop here.
  b) Is there a URL in the article that belongs to the company itself (not a news site, not social media)?
     YES → that is the true domain.
  c) Neither → the article does not contain a verifiable domain.

Step 2 - Validate the CANDIDATE domain from the article context:
  A candidate is VALID only if ALL of these hold:
  - Belongs to the company that raised funding (not a news site, CDN, investor, or social platform)
  - Product/service described on that site matches the article
  - Industry matches
  - Geography matches (if stated)
  NEVER accept a news/media domain as the company's domain.

Step 3 - Set status:
  - If true domain found AND it EXACTLY matches the candidate → status = "Correct"
  - If true domain found AND it DIFFERS from the candidate → status = "Wrong", set correctDomain
  - If no true domain found in article AND candidate passes Step 2 validation → status = "Correct"
  - If no true domain found AND candidate fails validation → status = "Unclear"
  - If you cannot confidently determine anything → status = "Unclear", DO NOT GUESS

Rules:
  - NEVER guess a domain not explicitly in the article
  - NEVER default to company-name.com as a guess
  - News/media sites (techcrunch.com, finsmes.com, etc.) are NEVER the company domain

reason: max 2 short sentences explaining your decision.`;

const SEMANTIC_VALIDATION_SCHEMA = {
  type: "object",
  properties: {
    correctCompanyName: { type: "string" },
    correctDomain: { type: "string" },
    status: { type: "string", enum: ["Correct", "Wrong", "Unclear"] },
    reason: { type: "string" },
  },
  required: ["correctCompanyName", "correctDomain", "status", "reason"],
  additionalProperties: false,
};

function normalizeDomain(raw: string): string {
  return raw
    .replace(/^https?:\/\//, "")
    .replace(/^www\./, "")
    .split("/")[0]
    .toLowerCase()
    .trim();
}

export async function validateDomainSemantic(
  sourceUrl: string,
  companyName: string,
  domain: string,
  rawArticleText: string,
  deadlineAt?: number
): Promise<SemanticValidationResult> {
  const fallback: SemanticValidationResult = {
    correctCompanyName: companyName,
    correctDomain: domain,
    status: "Unclear",
    reason: "validation skipped",
  };

  if (!rawArticleText) return fallback;

  const userMsg = [
    `source_url: ${sourceUrl}`,
    `company_name: ${companyName}`,
    `domain: ${domain}`,
    "",
    `Article text:\n${rawArticleText.slice(0, 8000)}`,
  ].join("\n");

  const result = await lunaJson<SemanticValidationResult>({
    name: "domain_validation",
    schema: SEMANTIC_VALIDATION_SCHEMA,
    systemPrompt: SEMANTIC_VALIDATION_SYSTEM,
    userPrompt: userMsg,
    maxTokens: 200,
    timeoutMs: 25_000,
    deadlineAt,
  });

  if (!result) return fallback;

  const parsed = result.data;
  if (parsed.correctDomain) {
    parsed.correctDomain = normalizeDomain(parsed.correctDomain);
  }
  return parsed;
}
