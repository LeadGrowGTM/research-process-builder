"""
Unified Domain Resolution Module

Single source of truth for finding a company's official website domain.
Consolidates logic from test_domain_resolution.py, backfill_domains.py,
and pipeline_base.py into one production-grade module.

Architecture:
  validate_domain()     — gate that rejects bad domains before acceptance
  resolve_domain()      — 3-tier waterfall: article regex → GPT extract → Serper search
  resolve_domain_agent() — GPT agent with tool-calling (higher accuracy, higher cost)
  fuzzy_dedup_companies() — token-overlap + domain-based dedup

Usage:
    from domain_resolver import resolve_domain, validate_domain, resolve_domain_agent
"""

import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

_script_dir = Path(__file__).resolve().parent
load_dotenv(_script_dir.parent / ".env")
load_dotenv(Path.home() / ".env", override=False)

_shared = os.environ.get("SHARED_SCRIPTS_PATH", str(_script_dir))
sys.path.insert(0, _shared)

import requests

SPIDER_API_KEY = os.getenv("SPIDER_API_KEY")
SERPER_API_KEY = os.getenv("SERPER_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# ---------------------------------------------------------------------------
# Unified block lists (merged from all implementations + backfill learnings)
# ---------------------------------------------------------------------------

NEWS_DOMAINS = {
    "businesswire.com", "prnewswire.com", "finsmes.com", "thesaasnews.com",
    "techcrunch.com", "yahoo.com", "finance.yahoo.com", "reuters.com",
    "bloomberg.com", "eu-startups.com", "tech.eu", "venturebeat.com",
    "finanzwire.com", "therecursive.com", "netinfluencer.com",
    "biospace.com", "kitsapsun.com", "cincinnati.com", "bandt.com.au",
    "bandt.com", "digitaltoday.co.kr", "gobiernu.cw", "finance.biggo.com",
    "thequantuminsider.com", "alleywatch.com", "vcnewsdaily.com",
    "infotechlead.com", "siliconangle.com", "techround.co.uk",
    "pulse2.com", "ventureburn.com", "globenewswire.com",
    "einpresswire.com", "startupnews.fyi", "uktech.news",
    "techfundingnews.com", "fiercebiotech.com", "sdxcentral.com",
    "channele2e.com", "forbes.com", "fortune.com", "cnbc.com", "wsj.com",
    "investing.com", "technews180.com", "securitybrief.co.nz",
    "securitybrief.co", "statnews.com", "economictimes.com",
    "infomoney.com", "livemint.com", "moneycontrol.com",
}

SOCIAL_DOMAINS = {
    "linkedin.com", "crunchbase.com", "wikipedia.org", "facebook.com",
    "twitter.com", "x.com", "github.com", "youtube.com", "instagram.com",
    "reddit.com", "pitchbook.com", "glassdoor.com", "angel.co",
    "wellfound.com", "g2.com", "capterra.com", "trustpilot.com",
    "medium.com", "substack.com", "tiktok.com",
}

DATA_PLATFORM_DOMAINS = {
    "dealroom.co", "dealroom.com", "tracxn.com", "owler.com",
    "zoominfo.com", "apollo.io", "ycombinator.com", "cbinsights.com",
    "craft.co", "harmonic.ai", "similar.ai",
}

TRACKER_DOMAINS = {
    "googletagmanager.com", "googleapis.com", "cloudfront.net",
    "wistia.com", "cision.com", "adobedtm.com", "licdn.com",
    "fbcdn.net", "yimg.com",
}

CDN_DOMAINS = {
    "cdninstagram.com", "amazonaws.com", "cloudfront.net",
    "akamaized.net", "fastly.net", "cloudflare.com",
    "cdn.jsdelivr.net", "unpkg.com", "filerobot.com",
    "giotto.ai",
}

SHORT_URL_DOMAINS = {
    "t.co", "bit.ly", "tinyurl.com", "goo.gl", "ow.ly",
}

BLOCKED_DOMAINS = (
    NEWS_DOMAINS | SOCIAL_DOMAINS | DATA_PLATFORM_DOMAINS
    | TRACKER_DOMAINS | CDN_DOMAINS | SHORT_URL_DOMAINS
)

CDN_PATTERNS = re.compile(
    r"^(cdn[.\-]|static[.\-]|assets[.\-]|media[.\-]|img[.\-]|images[.\-])",
    re.IGNORECASE,
)

EDU_PATTERN = re.compile(r"\.edu(\.[a-z]{2})?$", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Core utilities
# ---------------------------------------------------------------------------

def normalize_domain(raw: str) -> str:
    if not raw:
        return ""
    raw = raw.strip().lower()
    if "://" in raw:
        raw = raw.split("://", 1)[1]
    return raw.split("/")[0].replace("www.", "")


def _url_hostname(url: str) -> str:
    try:
        return urlparse(url).hostname or ""
    except Exception:
        return ""


def is_blocked(domain: str) -> bool:
    if not domain:
        return True
    d = domain.lower().replace("www.", "")
    if any(d == b or d.endswith("." + b) for b in BLOCKED_DOMAINS):
        return True
    if CDN_PATTERNS.match(d):
        return True
    if EDU_PATTERN.search(d):
        return True
    return False


def domain_matches_company(domain: str, company: str) -> bool:
    d = domain.replace("www.", "").split(".")[0]
    cn = re.sub(r"[^a-z0-9]", "", company.lower())
    dn = re.sub(r"[^a-z0-9]", "", d.lower())
    if len(cn) < 3 or len(dn) < 2:
        return False
    return cn in dn or dn in cn


# ---------------------------------------------------------------------------
# Validation gate — call BEFORE accepting any domain
# ---------------------------------------------------------------------------

def validate_domain(
    domain: str,
    company_name: str,
    source_domain: str = "",
) -> dict:
    """
    Validate a candidate domain. Returns:
        {"valid": True/False, "reason": str, "confidence": "high"|"medium"|"low"}
    """
    d = normalize_domain(domain)

    if not d or d in ("not_stated", "not_found", ""):
        return {"valid": False, "reason": "empty or placeholder", "confidence": "low"}

    if is_blocked(d):
        return {"valid": False, "reason": f"blocked domain: {d}", "confidence": "high"}

    if source_domain and d == normalize_domain(source_domain):
        return {"valid": False, "reason": f"matches source article domain: {d}", "confidence": "high"}

    if domain_matches_company(d, company_name):
        return {"valid": True, "reason": "name matches domain", "confidence": "high"}

    return {"valid": True, "reason": "accepted (name mismatch — low confidence)", "confidence": "low"}


# ---------------------------------------------------------------------------
# Tier 1: Article regex extraction
# ---------------------------------------------------------------------------

DOMAIN_PATTERNS = [
    r"(?:visit|learn more|more at|website at|available at|find us at|go to)[:\s]+(?:https?://)?([a-z0-9][-a-z0-9]*\.[a-z]{2,}(?:\.[a-z]{2})?)",
    r"https?://([a-z0-9][-a-z0-9]*\.[a-z]{2,}(?:\.[a-z]{2})?)/",
    r"@([a-z0-9][-a-z0-9]*\.[a-z]{2,}(?:\.[a-z]{2})?)",
    r"\bat\s+([a-z0-9][-a-z0-9]*\.(?:com|io|ai|co|org|net|dev|app|tech|health|bio|xyz))\b",
]

URL_ANY = re.compile(
    r"(?:https?://)?([a-z0-9][-a-z0-9]*\.(?:com|io|ai|co|org|net|dev|app|tech|health|bio|xyz|gg|so|cc|me|pe|co\.[a-z]{2}))\b",
    re.IGNORECASE,
)


def _fetch_article_spider(url: str) -> str | None:
    if not SPIDER_API_KEY:
        return None
    try:
        resp = requests.post(
            "https://api.spider.cloud/crawl",
            headers={"Authorization": f"Bearer {SPIDER_API_KEY}", "Content-Type": "application/json"},
            json={"url": url, "limit": 1, "return_format": "markdown"},
            timeout=25,
        )
        if resp.status_code == 200:
            data = resp.json()
            content = ""
            if isinstance(data, list) and data:
                content = data[0].get("content", "")
            elif isinstance(data, dict):
                content = data.get("content", "")
            if content and len(content) > 200:
                return content[:15000]
    except Exception:
        pass
    return None


def _extract_domain_from_article(text: str, company_name: str, source_domain: str) -> str | None:
    for pat in DOMAIN_PATTERNS:
        for m in re.finditer(pat, text, re.IGNORECASE):
            d = normalize_domain(m.group(1))
            v = validate_domain(d, company_name, source_domain)
            if v["valid"] and v["confidence"] == "high":
                return d

    for m in URL_ANY.finditer(text):
        d = normalize_domain(m.group(1))
        v = validate_domain(d, company_name, source_domain)
        if v["valid"] and v["confidence"] == "high":
            return d

    return None


# ---------------------------------------------------------------------------
# Tier 2: GPT extraction from article
# ---------------------------------------------------------------------------

def _extract_domain_gpt(article_text: str, company_name: str, source_domain: str) -> str | None:
    if not OPENAI_API_KEY or not article_text:
        return None

    messages = [
        {"role": "system", "content": (
            "You extract the official website domain for a company from article text. "
            "Return ONLY the bare domain (e.g. 'hata.io'). No JSON, no explanation, no http://. "
            "Return 'not_found' if not in article.\n\n"
            "CRITICAL RULES:\n"
            "- NEVER return the article source domain\n"
            "- NEVER return CDN domains (cdninstagram.com, amazonaws.com, cloudfront.net, filerobot.com)\n"
            "- NEVER return data platforms (dealroom.co, crunchbase.com, pitchbook.com, tracxn.com)\n"
            "- NEVER return news/media domains (infomoney.com, economictimes.com, statnews.com)\n"
            "- NEVER return social media domains (linkedin.com, twitter.com, instagram.com)\n"
            "- NEVER return short URL domains (t.co, bit.ly)\n"
            "- If you cannot find the ACTUAL company website in the article text, return 'not_found'"
        )},
        {"role": "user", "content": (
            f"Company: {company_name}\n"
            f"Source article domain (DO NOT return this): {source_domain}\n\n"
            f"Article:\n{article_text[:6000]}\n\n"
            f"What is {company_name}'s official website domain?"
        )},
    ]
    try:
        resp = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
            json={"model": "gpt-4o-mini", "temperature": 0, "max_tokens": 50, "messages": messages},
            timeout=20,
        )
        if resp.status_code == 200:
            raw = resp.json()["choices"][0]["message"]["content"].strip().lower()
            raw = re.sub(r"^(https?://|www\.)", "", raw).split("/")[0].strip()
            v = validate_domain(raw, company_name, source_domain)
            if v["valid"]:
                return raw
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Tier 3: Serper search with candidate scoring
# ---------------------------------------------------------------------------

def _serper_search(query: str, num: int = 5) -> list[dict]:
    if not SERPER_API_KEY:
        return []
    try:
        resp = requests.post(
            "https://google.serper.dev/search",
            headers={"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"},
            json={"q": query, "num": num},
            timeout=15,
        )
        if resp.status_code == 200:
            return resp.json().get("organic", [])
    except Exception:
        pass
    return []


def _extract_domains_from_text(text: str) -> list[str]:
    pattern = re.compile(
        r"\b([a-z0-9][-a-z0-9]*\.(?:com|io|ai|co|org|net|dev|app|tech|health|bio|xyz|gg|so|cc|me|pe))\b",
        re.IGNORECASE,
    )
    return list(dict.fromkeys(m.lower() for m in pattern.findall(text)))


def _find_domain_serper(company_name: str, source_domain: str, industry: str = "") -> tuple[str | None, str]:
    candidates: dict[str, dict] = {}

    def score_candidate(domain: str, evidence: str, bonus: int = 0):
        d = normalize_domain(domain)
        v = validate_domain(d, company_name, source_domain)
        if not v["valid"]:
            return
        entry = candidates.setdefault(d, {"score": 0, "evidence": []})
        entry["evidence"].append(evidence)
        if domain_matches_company(d, company_name):
            entry["score"] += 5
        if re.search(r"\.(com|io|ai|co)$", d):
            entry["score"] += 1
        entry["score"] += bonus

    industry_hint = f" {industry}" if industry else ""

    # Search 1: direct website search with industry context
    items = _serper_search(f'"{company_name}"{industry_hint} startup website', 5)
    for item in items:
        link = item.get("link", "")
        if "://" in link:
            d = normalize_domain(_url_hostname(link))
            score_candidate(d, "direct_search", 0)
        for sd in _extract_domains_from_text(item.get("snippet", "")):
            score_candidate(sd, "snippet_direct", 0)

    # Search 2: Crunchbase snippet mining
    items = _serper_search(f'site:crunchbase.com "{company_name}"', 3)
    for item in items:
        for sd in _extract_domains_from_text(item.get("snippet", "")):
            score_candidate(sd, "crunchbase_snippet", 3)

    # Search 3: funding context (catches obscure startups)
    if not candidates or max((c["score"] for c in candidates.values()), default=0) < 5:
        items = _serper_search(f'"{company_name}"{industry_hint} startup funding', 5)
        for item in items:
            link = item.get("link", "")
            if "://" in link:
                d = normalize_domain(_url_hostname(link))
                score_candidate(d, "funding_search", 0)
            for sd in _extract_domains_from_text(item.get("snippet", "")):
                score_candidate(sd, "snippet_funding", 1)

    if not candidates:
        return None, "no candidates found"

    sorted_cands = sorted(candidates.items(), key=lambda x: x[1]["score"], reverse=True)
    best_domain, best_meta = sorted_cands[0]

    # Require minimum score: name-matching domains get 5+ points automatically.
    # Low-score candidates (no name match) are likely wrong company.
    if best_meta["score"] < 3:
        return None, f"best candidate {best_domain} too low confidence (score={best_meta['score']})"

    evidence = f"score={best_meta['score']} [{', '.join(best_meta['evidence'][:3])}]"
    return best_domain, evidence


# ---------------------------------------------------------------------------
# Industry detection (shared with backfill agent)
# ---------------------------------------------------------------------------

INDUSTRY_PATTERNS = [
    (re.compile(r"\b(AI|artificial intelligence|machine learning|ML)\b", re.I), "AI"),
    (re.compile(r"\b(fintech|financial technology|payments|banking|insurtech)\b", re.I), "fintech"),
    (re.compile(r"\b(healthtech|healthcare|medical|biotech|pharma)\b", re.I), "healthtech"),
    (re.compile(r"\b(SaaS|software|platform|cloud)\b", re.I), "SaaS"),
    (re.compile(r"\b(cybersecurity|security|infosec)\b", re.I), "cybersecurity"),
    (re.compile(r"\b(e-commerce|ecommerce|retail|marketplace)\b", re.I), "ecommerce"),
    (re.compile(r"\b(robotics|autonomous|automation)\b", re.I), "robotics"),
    (re.compile(r"\b(climate|cleantech|energy|sustainability)\b", re.I), "cleantech"),
    (re.compile(r"\b(edtech|education|learning)\b", re.I), "edtech"),
    (re.compile(r"\b(proptech|real estate|construction)\b", re.I), "proptech"),
    (re.compile(r"\b(logistics|supply chain|shipping)\b", re.I), "logistics"),
    (re.compile(r"\b(devtools|developer|infrastructure)\b", re.I), "devtools"),
]


def detect_industry(text: str) -> str:
    if not text:
        return ""
    snippet = text[:3000]
    for pattern, label in INDUSTRY_PATTERNS:
        if pattern.search(snippet):
            return label
    return ""


# ---------------------------------------------------------------------------
# GPT agent resolver (tool-calling, higher accuracy, higher cost)
# ---------------------------------------------------------------------------

_AGENT_SYSTEM_PROMPT = """You find the official website domain for a startup that recently raised funding. You have a web search tool.

SEARCH STRATEGY (in order):
1. Primary: "{company_name}" {industry} website
2. If ambiguous: site:crunchbase.com "{company_name}" -- Crunchbase snippets often contain the actual domain
3. If still ambiguous: add location to disambiguate
4. If common-word name: search "{company_name}" {industry} startup funding -- funding articles link to the actual company

IMPORTANT:
- These are STARTUPS that raised venture funding. Not large enterprises or legacy companies.
- The domain often does NOT match the company name. Examples: Keep -> trykeep.com, Gong -> gong.io
- Crunchbase snippets are your best friend for obscure startups.
- Look at SERP snippet descriptions to verify the domain matches the RIGHT company in the RIGHT industry
- NEVER return social media, news/media, investor, directory, CDN, or data platform domains
- Return ONLY the bare domain (e.g. "hata.io", "mosaic.pe")
- If confident, return after 1 search. If ambiguous, refine (max 3 searches)

RESPONSE FORMAT (when done):
{"domain": "example.com", "confidence": "high|medium|low", "evidence": "brief reason"}"""

_SEARCH_TOOL_DEF = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "Search Google. Returns titles, URLs, and snippet descriptions.",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Search query"}},
            "required": ["query"],
        },
    },
}

MAX_AGENT_ROUNDS = 3


def _format_serper_results(items: list[dict]) -> str:
    if not items:
        return "No results found."
    lines = []
    for i, item in enumerate(items):
        lines.append(f"[{i+1}] {item.get('title', '')}\n    URL: {item.get('link', '')}\n    {item.get('snippet', '')}")
    return "\n\n".join(lines)


def resolve_domain_agent(
    company_name: str,
    industry: str = "",
    source_domain: str = "",
) -> dict:
    """
    GPT agent with tool-calling for domain resolution.
    Higher accuracy than waterfall, costs ~$0.02/call.
    Returns: {"domain": str, "confidence": str, "evidence": str, "searches": int, "gpt_calls": int}
    """
    if not OPENAI_API_KEY:
        return {"domain": "not_found", "confidence": "low", "evidence": "no API key", "searches": 0, "gpt_calls": 0}

    context_parts = [f"Company: {company_name}"]
    if industry:
        context_parts.append(f"Industry: {industry}")
    if source_domain:
        context_parts.append(f"Source article domain (DO NOT return this): {source_domain}")

    messages = [
        {"role": "system", "content": _AGENT_SYSTEM_PROMPT},
        {"role": "user", "content": "\n".join(context_parts)},
    ]

    search_count = 0
    gpt_calls = 0

    for round_num in range(MAX_AGENT_ROUNDS + 1):
        try:
            gpt_calls += 1
            resp = requests.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
                json={
                    "model": "gpt-4o-mini",
                    "temperature": 0,
                    "max_tokens": 300,
                    "messages": messages,
                    "tools": [_SEARCH_TOOL_DEF],
                    "tool_choice": "auto" if round_num < MAX_AGENT_ROUNDS else "none",
                },
                timeout=30,
            )
            if not resp.ok:
                break

            msg = resp.json()["choices"][0]["message"]

            if msg.get("tool_calls"):
                messages.append(msg)
                for tc in msg["tool_calls"]:
                    args = json.loads(tc["function"]["arguments"])
                    query = args.get("query", "")
                    search_count += 1
                    items = _serper_search(query, 5)
                    messages.append({"role": "tool", "tool_call_id": tc["id"], "content": _format_serper_results(items)})
                continue

            content = (msg.get("content") or "").strip()
            parsed = {}
            try:
                m = re.search(r"\{[\s\S]*\}", content)
                if m:
                    parsed = json.loads(m.group(0))
            except Exception:
                pass

            domain = normalize_domain(parsed.get("domain", ""))
            confidence = parsed.get("confidence", "low")
            evidence = parsed.get("evidence", "")

            v = validate_domain(domain, company_name, source_domain)
            if not v["valid"]:
                domain = "not_found"
                confidence = "low"

            return {"domain": domain, "confidence": confidence, "evidence": evidence, "searches": search_count, "gpt_calls": gpt_calls}
        except Exception:
            break

    return {"domain": "not_found", "confidence": "low", "evidence": "agent failed", "searches": search_count, "gpt_calls": gpt_calls}


# ---------------------------------------------------------------------------
# Main entry point: 3-tier waterfall
# ---------------------------------------------------------------------------

def resolve_domain(
    company_name: str,
    source_url: str,
    article_text: str | None = None,
    industry: str = "",
    use_agent_fallback: bool = False,
) -> dict:
    """
    Run 3-tier domain resolution waterfall.

    Tier 1: Regex extraction from article text (free)
    Tier 2: GPT extraction from article text (~$0.001)
    Tier 3: Serper search with candidate scoring (~$0.003-0.009)
    Agent fallback: GPT agent with tool-calling (~$0.02) — only if use_agent_fallback=True

    Returns: {"domain": str, "tier": int, "tier_name": str, "evidence": str,
              "confidence": str, "article_fetched": bool}
    """
    source_domain = normalize_domain(_url_hostname(source_url)) if source_url and "://" in source_url else ""

    if not industry and article_text:
        industry = detect_industry(article_text)

    # Tier 1: fetch article if needed, then regex
    fetched = article_text is not None
    if not article_text and source_url:
        article_text = _fetch_article_spider(source_url)
        fetched = article_text is not None

    if article_text:
        d = _extract_domain_from_article(article_text, company_name, source_domain)
        if d:
            return {"domain": d, "tier": 1, "tier_name": "article_regex", "evidence": "regex match in article",
                    "confidence": "high", "article_fetched": fetched}

        # Tier 2: GPT extraction
        d = _extract_domain_gpt(article_text, company_name, source_domain)
        if d:
            v = validate_domain(d, company_name, source_domain)
            return {"domain": d, "tier": 2, "tier_name": "gpt_extract", "evidence": "GPT extracted from article",
                    "confidence": v["confidence"], "article_fetched": fetched}

    # Tier 3: Serper search
    d, evidence = _find_domain_serper(company_name, source_domain, industry)
    if d:
        v = validate_domain(d, company_name, source_domain)
        return {"domain": d, "tier": 3, "tier_name": "serper_search", "evidence": evidence,
                "confidence": v["confidence"], "article_fetched": fetched}

    # Agent fallback (optional, higher cost)
    if use_agent_fallback:
        result = resolve_domain_agent(company_name, industry, source_domain)
        if result["domain"] != "not_found":
            return {"domain": result["domain"], "tier": 4, "tier_name": "agent",
                    "evidence": result["evidence"], "confidence": result["confidence"],
                    "article_fetched": fetched}

    return {"domain": "not_found", "tier": 0, "tier_name": "not_found",
            "evidence": "all tiers failed", "confidence": "low", "article_fetched": fetched}


# ---------------------------------------------------------------------------
# Fuzzy company name dedup
# ---------------------------------------------------------------------------

def _tokenize_name(name: str) -> set[str]:
    STOP_WORDS = {"inc", "ltd", "corp", "llc", "gmbh", "co", "plc", "sa", "ag", "bv", "pty", "sas", "srl", "the", "tag"}
    tokens = set(re.sub(r"[^a-z0-9\s]", "", name.lower()).split())
    return tokens - STOP_WORDS


def _levenshtein(s1: str, s2: str) -> int:
    if len(s1) < len(s2):
        return _levenshtein(s2, s1)
    if len(s2) == 0:
        return len(s1)
    prev = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        curr = [i + 1]
        for j, c2 in enumerate(s2):
            curr.append(min(prev[j + 1] + 1, curr[j] + 1, prev[j] + (c1 != c2)))
        prev = curr
    return prev[len(s2)]


def names_are_similar(name_a: str, name_b: str) -> bool:
    """Check if two company names likely refer to the same company."""
    tokens_a = _tokenize_name(name_a)
    tokens_b = _tokenize_name(name_b)

    if not tokens_a or not tokens_b:
        return False

    # Token subset: "Strider" is subset of "Strider Technologies"
    if tokens_a.issubset(tokens_b) or tokens_b.issubset(tokens_a):
        return True

    # Levenshtein on normalized full name
    norm_a = re.sub(r"[^a-z0-9]", "", name_a.lower())
    norm_b = re.sub(r"[^a-z0-9]", "", name_b.lower())
    max_len = max(len(norm_a), len(norm_b))
    if max_len > 0:
        dist = _levenshtein(norm_a, norm_b)
        ratio = 1 - (dist / max_len)
        if ratio >= 0.85:
            return True
        if dist <= 2 and min(len(norm_a), len(norm_b)) >= 4:
            return True

    return False


def fuzzy_dedup_companies(companies: list[dict], name_key: str = "company_name", domain_key: str = "company_domain", score_key: str = "best_score") -> list[dict]:
    """
    Two-pass dedup:
      1. Fuzzy name matching (token overlap + Levenshtein)
      2. Domain-based merge (same domain = same company)

    Keeps the record with highest score in each group.
    Returns deduped list sorted by score descending.
    """
    if not companies:
        return []

    # Pass 1: Group by fuzzy name similarity
    groups: list[list[int]] = []
    assigned = set()

    for i in range(len(companies)):
        if i in assigned:
            continue
        group = [i]
        assigned.add(i)
        for j in range(i + 1, len(companies)):
            if j in assigned:
                continue
            if names_are_similar(companies[i][name_key], companies[j][name_key]):
                group.append(j)
                assigned.add(j)
        groups.append(group)

    # Merge each group: keep highest score, combine sources
    merged = []
    for group in groups:
        best_idx = max(group, key=lambda idx: companies[idx].get(score_key, 0))
        winner = dict(companies[best_idx])
        if "sources" in winner:
            all_sources = []
            for idx in group:
                all_sources.extend(companies[idx].get("sources", []))
            winner["sources"] = all_sources
            winner["source_count"] = len(all_sources)
        if len(group) > 1:
            alt_names = [companies[idx][name_key] for idx in group if idx != best_idx]
            winner["_merged_from"] = alt_names
        merged.append(winner)

    # Pass 2: Domain-based merge
    domain_groups: dict[str, list[int]] = {}
    for i, c in enumerate(merged):
        d = normalize_domain(c.get(domain_key, ""))
        if d and d not in ("not_found", "not_stated", "not_enriched", ""):
            domain_groups.setdefault(d, []).append(i)

    final = []
    domain_merged = set()
    for i, c in enumerate(merged):
        if i in domain_merged:
            continue
        d = normalize_domain(c.get(domain_key, ""))
        if d and d in domain_groups and len(domain_groups[d]) > 1:
            group = domain_groups[d]
            best_idx = max(group, key=lambda idx: merged[idx].get(score_key, 0))
            winner = dict(merged[best_idx])
            alt_names = [merged[idx][name_key] for idx in group if idx != best_idx]
            existing_merged = winner.get("_merged_from", [])
            winner["_merged_from"] = existing_merged + alt_names
            final.append(winner)
            domain_merged.update(group)
        else:
            final.append(c)

    final.sort(key=lambda x: x.get(score_key, 0), reverse=True)
    return final
