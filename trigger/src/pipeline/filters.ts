import type {
  RawResult,
  RoundConfig,
  Candidate,
  FilteredItem,
  Stage2Result,
} from "./types.js";
import { scoreConfidence } from "./confidence-scorer.js";

const VC_PATTERNS =
  /\b(Capital|Ventures|Partners|Fund|Investment|Advisors|Management|Sequoia|Andreessen|Bessemer|Greylock|Accel|Lightspeed|GV|YC|a16z|Khosla|NEA|Insight|Tiger Global|Coatue|General Catalyst)\b/i;

const AMOUNT_PATTERN =
  /(?:[$€£¥₹]|\b(?:EUR|GBP|KRW|INR|JPY|CHF|CAD|AUD|SEK|NOK|DKK|BRL|RUB|USD|RS)(?=\b|\d))\s*[\d,.]+\s*(?:[KMBTkmbt]|million|billion|thousand|\b[Cc]rore\b|\b[Cc]r\b)(?:\b|$|[\s,;.])|\d+\s*(?:million|billion|thousand|\b[Cc]rore\b|\b[Cc]r\b)\b/i;

const SOCIAL_DOMAINS = new Set([
  "whatsapp.com", "whatsapp.net", "t.me", "telegram.me", "telegram.org",
  "twitter.com", "x.com", "instagram.com", "facebook.com",
  "tiktok.com", "snapchat.com",
]);

// Job boards, company-profile directories and event pages repeat old rounds with no news date.
const NON_NEWS_SOURCE =
  /(?:^|\.)(?:yespress\.io|preqin\.com|bebee\.com|remotesource\.com|jobaaj\.com|himalayas\.app|securities\.io|wikitia\.com|multiples\.vc|eventbrite\.[a-z.]+|glassdoor\.[a-z.]+|jooble\.org|jobgrin\.co\.in|jobs\.biospace\.com)$/i;

const TIER_S_DOMAINS = new Set([
  "thesaasnews.com",
  "finsmes.com",
  "alleywatch.com",
  "infotechlead.com",
  "vcnewsdaily.com",
]);

const TIER_A_DOMAINS = new Set([
  "businesswire.com",
  "prnewswire.com",
  "einpresswire.com",
  "ventureburn.com",
  "tech.eu",
  "eu-startups.com",
  "pulse2.com",
  "siliconangle.com",
]);

export function normalizeCompanyName(name: string): string {
  name = name.trim();
  name = name.replace(
    /\s*[,.]?\s*\b(Inc|Ltd|Corp|LLC|GmbH|Co|PLC|SA|AG|BV|Pty|SAS|SRL)\b\.?\s*$/i,
    ""
  );
  name = name.replace(/\s+Tag$/i, "");
  name = name.replace(/[\s,.\-:;]+$/, "");
  return name.toLowerCase().trim();
}

function levenshtein(a: string, b: string): number {
  if (a.length === 0) return b.length;
  if (b.length === 0) return a.length;
  const matrix: number[][] = [];
  for (let i = 0; i <= b.length; i++) matrix[i] = [i];
  for (let j = 0; j <= a.length; j++) matrix[0][j] = j;
  for (let i = 1; i <= b.length; i++) {
    for (let j = 1; j <= a.length; j++) {
      const cost = b[i - 1] === a[j - 1] ? 0 : 1;
      matrix[i][j] = Math.min(
        matrix[i - 1][j] + 1,
        matrix[i][j - 1] + 1,
        matrix[i - 1][j - 1] + cost
      );
    }
  }
  return matrix[b.length][a.length];
}

function areFuzzyMatch(a: string, b: string): boolean {
  if (a === b) return true;
  if (a.includes(b) || b.includes(a)) return true;
  const stripped = (s: string) => s.replace(/[^a-z0-9]/g, "");
  const sa = stripped(a);
  const sb = stripped(b);
  if (sa === sb) return true;
  if (sa.includes(sb) || sb.includes(sa)) return true;
  const maxLen = Math.max(sa.length, sb.length);
  if (maxLen <= 4) return sa === sb;
  const threshold = maxLen <= 8 ? 1 : 2;
  return levenshtein(sa, sb) <= threshold;
}

const RAISE_VERB = /^(?:has\s+)?(?:raises?|raised|secures?|secured|closes?|closed|lands?|landed|nabs?|bags?|gets?|receives?|completes?)\b/i;
const PLACE_BASED_PREFIX = /^\S+-based\s+(?:\S+\s+){0,4}?(?:startup|start-up|firm|company|platform)\s+/i;
const DESCRIPTOR_PREFIX = /^(?:\S+\s+){2,5}?(?:startup|start-up|firm|platform)\s+/i;

/** True when text starts with a capitalized name of 1-4 words followed by a raise verb. */
function startsWithNameThenRaise(text: string): boolean {
  const words = text.split(/\s+/);
  for (let n = 1; n <= 4 && n < words.length; n++) {
    if (!/^[A-Z]/.test(words[n - 1])) return false;
    if (RAISE_VERB.test(words.slice(n).join(" "))) return true;
  }
  return false;
}

/**
 * Removes editorial lead-ins so the title starts at the company name:
 * "Exclusive: ...", "Mumbai-based lending-tech startup Rezolv ...", and
 * "Singapore fintech firm IPID raises ...". A descriptor needs two words in front of it and a
 * raise verb after the name, so names like "The Company Store" or "Open Platform Labs" survive.
 */
export function stripEditorialPrefix(title: string): string {
  const t = title.replace(/^exclusive:\s*/i, "");
  const based = t.match(PLACE_BASED_PREFIX);
  if (based && /^[A-Z]/.test(t.slice(based[0].length))) return t.slice(based[0].length);
  const descriptor = t.match(DESCRIPTOR_PREFIX);
  if (descriptor && startsWithNameThenRaise(t.slice(descriptor[0].length))) return t.slice(descriptor[0].length);
  return t;
}

function extractCompanyNameFromTitle(title: string): string {
  const m1 = title.match(
    /^([A-Z][\w\s.&'-]{1,40}?)\s+(?:has\s+)?(?:raises?|raised|secures?|secured|closes?|closed|announces?|gets?|lands?|landed|nabs?|bags?|receives?|completes?)\b/i
  );
  if (m1) {
    const name = m1[1].trim();
    if (!VC_PATTERNS.test(name)) return name;
  }

  const m2 = title.match(
    /(?:in|into|backs?|for)\s+([A-Z][\w\s.&'-]{1,30}?)(?:\s*[,.]|\s+to\b|\s+for\b|$)/
  );
  if (m2) {
    const name = m2[1].trim();
    if (!VC_PATTERNS.test(name)) return name;
  }

  return "";
}

const SKIP_NAMES = new Set([
  "u.s", "u.s.", "us", "funding", "startup", "the",
  "series a", "series a funding",
  "series b", "series b funding",
  "series c", "series c funding",
]);

export function scoreAndFilter(rawResults: RawResult[], config: RoundConfig): Stage2Result {
  const candidates = new Map<string, Candidate>();
  const filteredOut: FilteredItem[] = [];

  for (const r of rawResults) {
    const title = r.title ?? "";
    const snippet = r.snippet ?? "";
    const combined = `${title} ${snippet}`;
    const url = r.source_url ?? "";
    const domain = (r.source_domain ?? "").replace(/^www\./, "");

    if (NON_NEWS_SOURCE.test(domain)) {
      filteredOut.push({
        title: title.slice(0, 80),
        reason: "non-news source (job board/profile/event page)",
        url,
      });
      continue;
    }

    if (config.noisePatterns.test(title)) {
      filteredOut.push({
        title: title.slice(0, 80),
        reason: "noise (report/listicle/filing)",
        url,
      });
      continue;
    }

    const titleHasRound = config.roundPattern.test(title);
    const titleHasHardNon = config.nonRoundPattern.test(title);
    const titleHasSoftNon = config.softNonPattern.test(title);
    const hasRound = config.roundPattern.test(combined);
    const hasHardNon = config.nonRoundPattern.test(combined);

    if (titleHasHardNon) {
      filteredOut.push({
        title: title.slice(0, 80),
        reason: `non-${config.roundLabel} in title`,
        url,
      });
      continue;
    }

    if (titleHasSoftNon && !titleHasRound) {
      filteredOut.push({
        title: title.slice(0, 80),
        reason: `soft exclusion in title, no ${config.roundLabel}`,
        url,
      });
      continue;
    }

    if (hasHardNon && !hasRound) {
      filteredOut.push({
        title: title.slice(0, 80),
        reason: `non-${config.roundLabel} round detected`,
        url,
      });
      continue;
    }

    if (!hasRound) {
      if (!/(?:raises?|raised|secures?|closes?)\s+[$€£]/i.test(combined)) {
        filteredOut.push({
          title: title.slice(0, 80),
          reason: `no ${config.roundLabel} and no funding amount`,
          url,
        });
        continue;
      }
    }

    const nameTitle = stripEditorialPrefix(title);
    let company = extractCompanyNameFromTitle(nameTitle);
    if (!company) {
      const fallback = nameTitle.split(" - ")[0].split(" | ")[0];
      const parts = fallback.split(
        /\s+(?:Raises?|Secures?|Closes?|Announces?)\b/i
      );
      company = (parts[0] ?? "").trim().slice(0, 50);
    }

    company = company.replace(/\s+Tag$/i, "").trim();
    company = company.replace(/^\[PDF\]\s*/, "").trim();

    if (!company || company.length < 3) continue;
    if (SKIP_NAMES.has(company.toLowerCase())) continue;
    if (company.length > 45) {
      filteredOut.push({
        title: title.slice(0, 80),
        reason: "company name too long (likely bad parse)",
        url,
      });
      continue;
    }

    const needsDisambiguation = VC_PATTERNS.test(company);
    const amountMatch = AMOUNT_PATTERN.exec(combined);
    const amount = amountMatch ? amountMatch[0] : "";

    let sourceQuality: number;
    if (SOCIAL_DOMAINS.has(domain)) sourceQuality = 0;
    else if (TIER_S_DOMAINS.has(domain)) sourceQuality = 4;
    else if (TIER_A_DOMAINS.has(domain)) sourceQuality = 5;
    else if (domain.includes("crunchbase") || domain.includes("techcrunch"))
      sourceQuality = 3;
    else sourceQuality = 2;

    let dataCompleteness = 1;
    if (company) dataCompleteness++;
    if (amount) dataCompleteness++;
    if (hasRound) dataCompleteness++;
    if (/(?:led by|investors?|participated)/i.test(combined))
      dataCompleteness++;

    const score = sourceQuality * dataCompleteness;
    const norm = normalizeCompanyName(company);

    if (!candidates.has(norm)) {
      candidates.set(norm, {
        company_name: company.trim(),
        company_name_normalized: norm,
        amount,
        round_type: hasRound ? config.roundLabel : "Unknown",
        needs_disambiguation: needsDisambiguation,
        sources: [],
        best_score: 0,
        best_source_url: "",
      });
    }

    const c = candidates.get(norm)!;
    c.sources.push({
      url,
      domain,
      score,
      query_source: r.query_source ?? "",
      title: title.slice(0, 100),
      snippet: snippet.slice(0, 200),
    });

    if (score > c.best_score) {
      c.best_score = score;
      c.best_source_url = url;
      if (amount && !c.amount) c.amount = amount;
    }
  }

  const merged = new Map<string, Candidate>();
  const norms = [...candidates.keys()];
  const consumed = new Set<string>();

  for (const norm of norms) {
    if (consumed.has(norm)) continue;
    const primary = candidates.get(norm)!;
    for (const otherNorm of norms) {
      if (otherNorm === norm || consumed.has(otherNorm)) continue;
      if (!areFuzzyMatch(norm, otherNorm)) continue;
      const other = candidates.get(otherNorm)!;
      primary.sources.push(...other.sources);
      if (other.best_score > primary.best_score) {
        primary.best_score = other.best_score;
        primary.best_source_url = other.best_source_url;
      }
      if (!primary.amount && other.amount) primary.amount = other.amount;
      consumed.add(otherNorm);
    }
    merged.set(norm, primary);
  }

  for (const c of merged.values()) {
    const bestSrc = c.sources.reduce(
      (best, src) => (src.score > best.score ? src : best),
      c.sources[0]
    );
    if (bestSrc) {
      const scores = scoreConfidence(c.company_name, bestSrc.title, bestSrc.snippet, bestSrc.domain);
      c.confidence = scores.composite;
      c.confidenceReasons = scores.reasons;
    }
  }

  const companies = [...merged.values()].sort(
    (a, b) => b.best_score - a.best_score
  );

  return {
    companies,
    filtered_out: filteredOut,
    stats: {
      raw_count: rawResults.length,
      company_count: companies.length,
      filtered_count: filteredOut.length,
    },
  };
}
