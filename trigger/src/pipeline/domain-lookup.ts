import { searchSerper } from "./serper.js";
import { isLunaConfigured, lunaChat, type LunaToolDef } from "./luna.js";

const DISQUALIFIED_DOMAINS = new Set([
  "linkedin.com",
  "crunchbase.com",
  "wikipedia.org",
  "twitter.com",
  "x.com",
  "facebook.com",
  "bloomberg.com",
  "pitchbook.com",
  "glassdoor.com",
  "indeed.com",
  "ycombinator.com",
  "github.com",
  "youtube.com",
  "instagram.com",
  "tiktok.com",
  "reddit.com",
  "medium.com",
  "substack.com",
  "angel.co",
  "wellfound.com",
  "g2.com",
  "capterra.com",
  "trustpilot.com",
  "apple.com",
  "play.google.com",
  "apps.apple.com",
]);

const NEWS_AND_MEDIA_DOMAINS = new Set([
  "techcrunch.com",
  "thesaasnews.com",
  "finsmes.com",
  "businesswire.com",
  "prnewswire.com",
  "einpresswire.com",
  "globenewswire.com",
  "yahoo.com",
  "finance.yahoo.com",
  "reuters.com",
  "bloomberg.com",
  "eu-startups.com",
  "tech.eu",
  "venturebeat.com",
  "siliconangle.com",
  "alleywatch.com",
  "vcnewsdaily.com",
  "infotechlead.com",
  "therecursive.com",
  "finanzwire.com",
  "biospace.com",
  "fiercebiotech.com",
  "digitaltoday.co.kr",
  "netinfluencer.com",
  "bandt.com.au",
  "kitsapsun.com",
  "cincinnati.com",
  "thequantuminsider.com",
  "techround.co.uk",
  "pulse2.com",
  "ventureburn.com",
  "techstartups.com",
  "startupnews.fyi",
  "wired.com",
  "theverge.com",
  "arstechnica.com",
  "zdnet.com",
  "cnet.com",
  "forbes.com",
  "fortune.com",
  "cnbc.com",
  "axios.com",
  "inc.com",
  "fastcompany.com",
  "businessinsider.com",
  "insider.com",
  "wsj.com",
  "nytimes.com",
  "theinformation.com",
  "sifted.eu",
  "dealstreetasia.com",
  "techinasia.com",
  "krasia.com",
  "inc42.com",
  "yourstory.com",
  "entrackr.com",
  "contxto.com",
  "labsnews.com",
  "startupdaily.net",
  "uktech.news",
  "eu-startups.com",
  "silicon.co.uk",
  "techfundingnews.com",
  "fundingpost.com",
  "crunchbase.com",
  "pitchbook.com",
  "cbinsights.com",
  "news.google.com",
  "google.com",
  "bing.com",
  "finance.biggo.com",
  "gobiernu.cw",
  "chosun.com",
  "biz.chosun.com",
  "chosun.co.kr",
  "hankyung.com",
  "mk.co.kr",
  "sedaily.com",
  "edaily.co.kr",
  "etnews.com",
  "zdnet.co.kr",
  "bloter.net",
  "platum.kr",
  "thebell.co.kr",
  "dealsite.co.kr",
  "f6s.com",
  "startupranking.com",
  "startupblink.com",
  "tracxn.com",
  "owler.com",
  "zoominfo.com",
  "dnb.com",
  "apollo.io",
  "technews180.com",
  "securitybrief.co",
  "investing.com",
  "seekingalpha.com",
  "marketwatch.com",
  "barrons.com",
  "ft.com",
  "economist.com",
  "itwire.com",
  "securityweek.com",
  "securityboulevard.com",
  "helpnetsecurity.com",
  "theregister.com",
  "computing.co.uk",
  "channele2e.com",
  "sdxcentral.com",
]);

function isDomainDisqualified(domain: string): boolean {
  const clean = domain.replace(/^www\./, "");
  for (const d of DISQUALIFIED_DOMAINS) {
    if (clean === d || clean.endsWith(`.${d}`)) return true;
  }
  return false;
}

function isDomainNews(domain: string): boolean {
  const clean = domain.replace(/^www\./, "");
  for (const d of NEWS_AND_MEDIA_DOMAINS) {
    if (clean === d || clean.endsWith(`.${d}`)) return true;
  }
  return false;
}

function isDomainSuspectByTLD(domain: string): boolean {
  const clean = domain.replace(/^www\./, "").toLowerCase();
  const parts = clean.split(".");
  if (parts.length < 2) return false;

  // Check all TLD segments for institutional patterns
  // Handles: .edu, .edu.au, .ac.uk, .ac.kr, .gov, .gov.uk, .go.kr, .mil, .mil.us
  for (let i = 1; i < parts.length; i++) {
    const segment = parts[i];
    if (
      segment === "edu" ||
      segment === "ac" ||
      segment === "gov" ||
      segment === "go" ||
      segment === "mil"
    ) {
      return true;
    }
  }
  return false;
}

export function isDomainBlocked(domain: string): boolean {
  return isDomainDisqualified(domain) || isDomainNews(domain) || isDomainSuspectByTLD(domain);
}


export interface DomainResult {
  domain: string;
  confidence: "high" | "medium" | "low";
  source: "article_extract" | "search_validated" | "search_only" | "crunchbase_signal";
  evidence: string;
}

export interface ContextClues {
  industry?: string;
  productOrService?: string;
  location?: string;
  founderName?: string;
}

const MAX_SEARCH_ROUNDS = 3;

const SEARCH_TOOL: LunaToolDef = {
  name: "web_search",
  description: "Search Google via Serper. Use regular web search (not news). Returns titles, URLs, and meta description snippets. Use industry + company name + 'website' as your primary query pattern. Use location to disambiguate common names.",
  parameters: {
    type: "object",
    properties: {
      query: {
        type: "string",
        description: "The search query. Use industry context to disambiguate. Example: 'fintech Hata website' or 'AI legal Harvey company website'",
      },
    },
    required: ["query"],
    additionalProperties: false,
  },
};

const DOMAIN_RESOLVE_SCHEMA = {
  type: "object",
  properties: {
    domain: { type: "string" },
    confidence: { type: "string", enum: ["high", "medium", "low"] },
    evidence: { type: "string" },
  },
  required: ["domain", "confidence", "evidence"],
  additionalProperties: false,
};

const DOMAIN_RESOLVE_SYSTEM = `You find the official website domain for a company mentioned in a funding, product launch, or hiring signal. You have a web search tool.

SEARCH STRATEGY (in order):
1. Primary: "{company_name}" {industry} website
2. If ambiguous: site:crunchbase.com "{company_name}" - Crunchbase snippets often contain the actual domain in text like "Company (domain.com) raised..."
3. If still ambiguous: add location to disambiguate
4. If common-word name (Keep, Clay, Era): search "{company_name}" {industry} startup funding - funding articles link to the actual company

IMPORTANT:
- Match the company and context provided. It may be a startup or an established company.
- The domain often does NOT match the company name. Examples: Keep -> trykeep.com, Gong -> gong.io, Plaid -> plaid.com. Don't assume {name}.com is correct - verify from search results.
- Crunchbase snippets are your best friend for obscure startups. The snippet text often contains the domain directly.
- Look at SERP snippet descriptions to verify the domain matches the RIGHT company in the RIGHT industry
- NEVER return social media, news/media, investor, or directory domains (linkedin, crunchbase, pitchbook, techcrunch, etc.)
- NEVER return the source article domain
- Return ONLY the bare domain (e.g. "hata.io", "mosaic.pe") - no protocol, no www, no path
- If confident, return after 1 search. If ambiguous, refine (max 3 searches)
- If you cannot determine the domain, return "not_found"

RESPONSE FORMAT (when done searching):
{"domain": "example.com", "confidence": "high|medium|low", "evidence": "brief reason"}`;

async function executeSearchTool(query: string, deadlineAt?: number): Promise<string> {
  const items = await searchSerper(query, 5, "", deadlineAt);
  if (items.length === 0) return "No results found.";

  return items
    .map((item, i) => {
      const link = (item.link ?? "").slice(0, 500);
      const title = (item.title ?? "").slice(0, 200);
      const snippet = (item.snippet ?? "").slice(0, 1000);
      return `[${i + 1}] ${title}\n    URL: ${link}\n    ${snippet}`;
    })
    .join("\n\n");
}

export async function lookupDomainMultiSignal(
  companyName: string,
  clues: ContextClues,
  sourceUrl?: string,
  deadlineAt?: number
): Promise<DomainResult> {
  if (!isLunaConfigured()) {
    return { domain: "not_found", confidence: "low", source: "search_only", evidence: "no OPENAI_API_KEY" };
  }

  let sourceDomain = "";
  try { sourceDomain = sourceUrl ? new URL(sourceUrl).hostname.replace(/^www\./, "") : ""; } catch { /* No usable source hostname. */ }

  const contextParts: string[] = [`Company: ${companyName}`];
  if (clues.industry) contextParts.push(`Industry: ${clues.industry}`);
  if (clues.productOrService) contextParts.push(`Product/service: ${clues.productOrService}`);
  if (clues.location) contextParts.push(`Location: ${clues.location}`);
  if (clues.founderName) contextParts.push(`Founder: ${clues.founderName}`);
  if (sourceDomain) contextParts.push(`Source article domain (DO NOT return this): ${sourceDomain}`);

  const messages: Array<Record<string, unknown>> = [
    { role: "system", content: DOMAIN_RESOLVE_SYSTEM },
    { role: "user", content: contextParts.join("\n") },
  ];

  let searchCount = 0;
  let hasSearchEvidence = false;

  for (let round = 0; round < MAX_SEARCH_ROUNDS + 1; round++) {
    if (deadlineAt !== undefined && Date.now() >= deadlineAt) break;
    try {
      const chat = await lunaChat({
        name: "funding_domain_resolution",
        schema: DOMAIN_RESOLVE_SCHEMA,
        messages,
        tools: [SEARCH_TOOL],
        toolChoice: searchCount < MAX_SEARCH_ROUNDS && round < MAX_SEARCH_ROUNDS ? "auto" : "none",
        maxTokens: 300,
        timeoutMs: 30_000,
        deadlineAt,
      });

      if (!chat) {
        return { domain: "not_found", confidence: "low", source: "search_only", evidence: "luna unavailable" };
      }

      if (chat.toolCalls.length > 0) {
        messages.push({
          role: "assistant",
          content: chat.content,
          tool_calls: chat.toolCalls.map((tc) => ({
            id: tc.id,
            type: "function",
            function: { name: tc.name, arguments: tc.args },
          })),
        });

        for (const tc of chat.toolCalls) {
          let query = "";
          try {
            query = (JSON.parse(tc.args) as { query?: string }).query ?? "";
          } catch {
            query = "";
          }
          const allowed = tc.name === "web_search" && typeof query === "string" && query.trim() && searchCount < MAX_SEARCH_ROUNDS;
          let searchResult = "Search unavailable or search budget reached.";
          if (allowed && (deadlineAt === undefined || Date.now() < deadlineAt)) {
            searchCount++;
            searchResult = await executeSearchTool(query.slice(0, 500), deadlineAt);
            if (searchResult !== "No results found.") hasSearchEvidence = true;
          }
          messages.push({
            role: "tool",
            tool_call_id: tc.id,
            content: searchResult,
          });
        }
        continue;
      }

      const content = (chat.content ?? "").trim();
      let parsed: { domain?: string; confidence?: string; evidence?: string } = {};
      try {
        parsed = JSON.parse(content) as typeof parsed;
      } catch {
        // fall through
      }

      const rawDomain = (parsed.domain ?? "").trim().toLowerCase().replace(/^https?:\/\//, "").replace(/^www\./, "").split(/[/?#]/)[0];

      if (!hasSearchEvidence || !/^(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}$/.test(rawDomain) || isDomainBlocked(rawDomain) || rawDomain === sourceDomain) {
        return {
          domain: "not_found",
          confidence: "low",
          source: "search_only",
          evidence: `${searchCount} searches, agent returned: ${rawDomain || "empty"} - ${parsed.evidence ?? "no evidence"}`,
        };
      }

      const confidence = (parsed.confidence === "high" || parsed.confidence === "medium" || parsed.confidence === "low")
        ? parsed.confidence
        : "medium";

      return {
        domain: rawDomain,
        confidence,
        source: "search_validated",
        evidence: `${searchCount} searches - ${parsed.evidence ?? "agent resolved"}`,
      };
    } catch {
      break;
    }
  }

  return {
    domain: "not_found",
    confidence: "low",
    source: "search_only",
    evidence: `agent failed after ${searchCount} searches`,
  };
}
