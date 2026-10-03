/**
 * Fixed taxonomy for the funding-signal pipeline.
 *
 * Luna JSON schemas embed these enums so the model can only emit valid
 * values. Non-LLM sources (raisingfi regex values, lgenrich/blitz free
 * text) go through normalizeRoundType/normalizeIndustry, which return
 * null for junk sentinels ("not_stated", "") instead of storing them.
 */

export const INDUSTRIES = [
  "AI/ML",
  "Fintech",
  "Healthcare",
  "Cybersecurity",
  "DevTools",
  "Data/Analytics",
  "Marketing/Sales Tech",
  "HR/Recruiting",
  "E-commerce",
  "Logistics",
  "Climate/Energy",
  "Biotech",
  "Insurance",
  "Real Estate/PropTech",
  "EdTech",
  "Legal",
  "Consumer",
  "Hardware/Robotics",
  "Media",
  "Gov/Defense",
  "Crypto/Web3",
  "Manufacturing",
  "Other",
] as const;

export type Industry = (typeof INDUSTRIES)[number];

export const ROUND_TYPES = [
  "Pre-Seed",
  "Seed",
  "Series A",
  "Series B",
  "Series C",
  "Series D+",
  "Growth",
  "Debt",
  "Grant",
  "Unknown",
] as const;

export type RoundTypeLabel = (typeof ROUND_TYPES)[number];

export const ICP_FITS = ["strong", "moderate", "weak"] as const;

export type IcpFit = (typeof ICP_FITS)[number];

/** Values that mean "no data" and must become null, never stored. */
const NULL_SENTINELS = new Set([
  "",
  "not_stated",
  "not_enriched",
  "not_found",
  "n/a",
  "na",
  "none",
  "null",
  "undefined",
  "unknown",
  "unclear",
]);

export function normalizeOptionalText(raw: unknown): string | null {
  if (typeof raw !== "string") return null;
  const t = raw.trim();
  if (!t) return null;
  if (NULL_SENTINELS.has(t.toLowerCase())) return null;
  // Paywall placeholders such as raisingfi's "🔒 Get Pro" are not data.
  if (t.startsWith("🔒")) return null;
  return t;
}

/** Provider integer fields must not send fractional or sentinel values to SQL. */
export function normalizeOptionalInteger(raw: unknown): number | null {
  if (typeof raw !== "number" && typeof raw !== "string") return null;
  if (typeof raw === "string" && !/^\d+$/.test(raw.trim())) return null;
  const value = Number(raw);
  return Number.isSafeInteger(value) && value >= 0 && value <= 2_147_483_647 ? value : null;
}

/** Canonical round label for a free-text round, or null when unusable. */
export function normalizeRoundType(raw: unknown): RoundTypeLabel | null {
  if (typeof raw === "string" && raw.trim().toLowerCase() === "unknown") return "Unknown";
  const t = normalizeOptionalText(raw);
  if (t === null) return null;
  const s = t.toLowerCase().replace(/[_-]+/g, " ").replace(/\s+/g, " ");

  if (/\bseries\s*a\b/.test(s) || /\bserie\s*a\b/.test(s)) return "Series A";
  if (/\bseries\s*b\b/.test(s) || /\bserie\s*b\b/.test(s)) return "Series B";
  if (/\bseries\s*c\b/.test(s) || /\bserie\s*c\b/.test(s)) return "Series C";
  if (/\bseries\s*[d-z]\b/.test(s)) return "Series D+";
  if (/\bpre\s*seed\b/.test(s) || /\bpreseed\b/.test(s)) return "Pre-Seed";
  if (/\bseed\b/.test(s)) return "Seed";
  if (/\bgrowth\b/.test(s) || /\bgrowth\s*equity\b/.test(s)) return "Growth";
  if (/\bdebt\b/.test(s) || /\bloan\b/.test(s) || /\bbridge\b/.test(s) || /\bconvertible\b/.test(s)) return "Debt";
  if (/\bgrant\b/.test(s)) return "Grant";
  return null;
}

const INDUSTRY_RULES: Array<{ label: Industry; test: RegExp }> = [
  { label: "AI/ML", test: /artificial intelligence|machine learning|generative ai|\bllm\b|\bai\b|\bml\b|ai\/ml|ai powered|ai-driven/ },
  { label: "Fintech", test: /fintech|financial|payment|banking|lending|neobank|wealth|trading|accounting|invoice|payroll/ },
  { label: "Healthcare", test: /healthcare|healthtech|digital health|medical|clinical|hospital|patient|care delivery|telehealth|health / },
  { label: "Biotech", test: /biotech|life science|pharma|genomic|therapeutic|drug|synthetic biology|crispr/ },
  { label: "Cybersecurity", test: /cybersecurity|cyber security|infosec|threat|vulnerability|zero trust|identity security|soc 2|endpoint security/ },
  { label: "DevTools", test: /devtools|developer tool|devops|api platform|sdk|observability|ci\/cd|version control|code / },
  { label: "Data/Analytics", test: /data\/analytics|\banalytics\b|\bbig data\b|business intelligence|data platform|data infra|data pipe|dashboard|etl/ },
  { label: "Marketing/Sales Tech", test: /marketingsales|martech|salestech|sales tech|marketing tech|advertising|adtech|crm|seo|content marketing|demand gen|lead gen/ },
  { label: "HR/Recruiting", test: /\bhr\b|human resource|recruit|hiring|talent|workforce|hrtech|applicant track|people ops|staffing/ },
  { label: "E-commerce", test: /e-?commerce|retail|marketplace|d2c|direct-to-consumer|storefront|shopify|grocery|fashion/ },
  { label: "Logistics", test: /logistic|supply chain|freight|shipping|warehouse|fulfill|delivery|fleet|procurement/ },
  { label: "Climate/Energy", test: /climate|energy|cleantech|clean tech|sustainab|solar|wind|battery|carbon|hydrogen|nuclear fusion|grid/ },
  { label: "Insurance", test: /insurance|insurtech|underwrit|claims/ },
  { label: "Real Estate/PropTech", test: /real estate|proptech|property|rental|mortgage|housing/ },
  { label: "EdTech", test: /edtech|education|learning|tutor|school|university|upskill|e-learning|classroom/ },
  { label: "Legal", test: /legal|law firm|legaltech|attorney|contract lifecycle|e-?discovery|litigation/ },
  { label: "Consumer", test: /consumer|social network|dating|gaming|game |video game|fitness app|food delivery|travel app|music|creator economy|social media/ },
  { label: "Hardware/Robotics", test: /hardware|robotics|robot |drone|iot|semiconductor|chip |wearable|3d print|sensor|autonomous vehicle|space / },
  { label: "Media", test: /media|entertainment|streaming|podcast|news|publish|video platform|music label|hollywood|newsletter/ },
  { label: "Gov/Defense", test: /govtech|government|defense|defence|military|public sector|federal|intelligence comm/ },
  { label: "Crypto/Web3", test: /crypto|web3|blockchain|bitcoin|defi|\bnft\b|tokeni|smart contract|wallet infra/ },
  { label: "Manufacturing", test: /manufacturing|industrial|factory|fabrication|cnc|additive|supply\b/ },
];

/**
 * Canonical industry for free-text input, or null when the input is a
 * junk sentinel. Recognized-but-unlisted text (e.g. generic "SaaS")
 * maps to "Other" so only enum values are stored.
 */
export function normalizeIndustry(raw: unknown): Industry | null {
  const t = normalizeOptionalText(raw);
  if (t === null) return null;
  const lower = t.toLowerCase();
  // Accept exact canonical labels first (case-insensitive).
  for (const label of INDUSTRIES) {
    if (label.toLowerCase() === lower) return label;
  }
  const flat = lower.replace(/[^a-z0-9 ]/g, " ").replace(/\s+/g, " ");
  for (const rule of INDUSTRY_RULES) {
    if (rule.test.test(lower) || rule.test.test(` ${flat} `)) return rule.label;
  }
  return "Other";
}

/** Canonical ICP fit value, defaulting to "weak" for anything unrecognized. */
export function normalizeIcpFit(raw: unknown): IcpFit {
  const t = typeof raw === "string" ? raw.trim().toLowerCase() : "";
  if (t === "strong" || t === "moderate" || t === "weak") return t;
  return "weak";
}

/** signal_companies.signal_type derived from a normalized round label. */
export function signalTypeForRound(round: string | null): string {
  switch (round) {
    case "Series A":
      return "series_a";
    case "Series B":
      return "series_b";
    case "Series C":
      return "series_c";
    case "Seed":
    case "Pre-Seed":
      return "seed";
    default:
      return "funded";
  }
}

const NON_DOMAIN_VALUES = new Set([
  "not_found",
  "not_stated",
  "not_enriched",
  "",
]);

/**
 * Favicon URL for a company domain. Only builds the google s2 URL when a
 * real domain is present; never stores a logo.dev token URL.
 */
export function logoUrlForDomain(domain: string | null | undefined): string | null {
  if (!domain) return null;
  const d = domain.trim().toLowerCase().replace(/^https?:\/\//, "").replace(/^www\./, "").split(/[/?#]/)[0];
  if (!d || NON_DOMAIN_VALUES.has(d) || !/^(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}$/.test(d)) return null;
  return `https://www.google.com/s2/favicons?domain=${d}&sz=128`;
}

const SOURCE_NAMES: Record<string, string> = {
  "alleywatch.com": "AlleyWatch",
  "bloomberg.com": "Bloomberg",
  "businesswire.com": "Business Wire",
  "einpresswire.com": "EIN Presswire",
  "eu-startups.com": "EU-Startups",
  "finsmes.com": "FinSMEs",
  "infotechlead.com": "InfoTechLead",
  "prnewswire.com": "PR Newswire",
  "reuters.com": "Reuters",
  "sec.gov": "SEC Form D",
  "tech.eu": "Tech.eu",
  "techcrunch.com": "TechCrunch",
  "techround.co.uk": "TechRound",
  "thesaasnews.com": "The SaaS News",
  "vcnewsdaily.com": "VC News Daily",
  "venturebeat.com": "VentureBeat",
};

const SECRET_QUERY_SEGMENT = /^(?:token|secret|password|passwd|pwd|auth|authentication|authorization|credential|credentials|apikey|oauth)\d*$/;

/** Secret-bearing query names. "auth" does not match a longer word such as "author". */
function isSecretQueryKey(key: string): boolean {
  const segments = key
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .toLowerCase()
    .split(/[^a-z0-9]+/)
    .filter(Boolean);
  if (segments.some((segment) => SECRET_QUERY_SEGMENT.test(segment))) return true;
  for (let i = 0; i < segments.length - 1; i++) {
    if (segments[i] === "api" && /^key\d*$/.test(segments[i + 1])) return true;
  }
  return false;
}

/**
 * True when a URL can be shown on a public signal. Requires https, no
 * embedded credentials, and no query key that carries a secret.
 */
export function isPublicHttpsUrl(value: string | null | undefined): boolean {
  if (!value) return false;
  try {
    const url = new URL(value);
    if (url.protocol !== "https:" || url.username || url.password) return false;
    for (const key of url.searchParams.keys()) {
      if (isSecretQueryKey(key)) return false;
    }
    return true;
  } catch {
    return false;
  }
}

/** Human-readable publisher label derived only from the source URL. */
export function sourceNameForUrl(sourceUrl: string | null | undefined): string | null {
  if (!sourceUrl) return null;
  try {
    const url = new URL(sourceUrl);
    const hostname = url.hostname.toLowerCase().replace(/^www\./, "");
    if (!hostname) return null;
    if (
      (hostname === "x.com" || hostname === "twitter.com") &&
      url.pathname.toLowerCase().startsWith("/raisingfi/")
    ) {
      return "@raisingfi on X";
    }
    return SOURCE_NAMES[hostname] ?? hostname;
  } catch {
    return null;
  }
}
