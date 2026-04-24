"""
Domain Resolution End-to-End Test
3-tier waterfall: article extract → GPT → Serper search

Usage: python scripts/test_domain_resolution.py
Reads API keys from ../../.env (SPIDER_API_KEY, SERPER_API_KEY, OPENAI_API_KEY)
"""

import json
import os
import re
import sys
from pathlib import Path

_script_dir = Path(__file__).resolve().parent

from dotenv import load_dotenv
load_dotenv(_script_dir.parent / ".env")
load_dotenv(Path.home() / ".env", override=False)

sys.path.insert(0, os.environ.get("SHARED_SCRIPTS_PATH", str(_script_dir)))

import requests

SPIDER_API_KEY = os.getenv("SPIDER_API_KEY")
SERPER_API_KEY = os.getenv("SERPER_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# ────────────────────────────────────────────────────────────
# Domain block lists
# ────────────────────────────────────────────────────────────

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
    "techfundingnews.com", "fiercebiotech.com",
}

TRACKER_DOMAINS = {
    "googletagmanager.com", "googleapis.com", "cloudfront.net",
    "wistia.com", "cision.com", "adobedtm.com", "licdn.com", "fbcdn.net", "yimg.com",
}

SOCIAL_DOMAINS = {
    "linkedin.com", "crunchbase.com", "wikipedia.org", "facebook.com",
    "twitter.com", "x.com", "github.com", "youtube.com", "instagram.com", "reddit.com",
    "pitchbook.com", "glassdoor.com", "angel.co", "wellfound.com",
    "g2.com", "capterra.com", "trustpilot.com",
}

BLOCKED_DOMAINS = NEWS_DOMAINS | TRACKER_DOMAINS | SOCIAL_DOMAINS


def is_blocked(domain: str) -> bool:
    d = domain.lower().replace("www.", "")
    return any(d == b or d.endswith("." + b) for b in BLOCKED_DOMAINS)


def normalize_domain(raw: str) -> str:
    """Extract hostname from URL or bare domain."""
    raw = raw.strip().lower()
    if "://" in raw:
        raw = raw.split("://", 1)[1]
    raw = raw.split("/")[0]
    return raw


def domain_matches_company(domain: str, company: str) -> bool:
    """Check if company name appears meaningfully in domain."""
    d = domain.replace("www.", "").split(".")[0]
    # Strip special chars from company name
    cn = re.sub(r"[^a-z0-9]", "", company.lower())
    dn = re.sub(r"[^a-z0-9]", "", d.lower())
    if len(cn) < 3 or len(dn) < 2:
        return False
    return cn in dn or dn in cn


# ────────────────────────────────────────────────────────────
# Tier 1: Spider article fetch + regex extraction
# ────────────────────────────────────────────────────────────

DOMAIN_PATTERNS = [
    # "visit us at example.com" / "learn more at example.com"
    r"(?:visit|learn more|more at|website at|available at|find us at|go to)[:\s]+(?:https?://)?([a-z0-9][-a-z0-9]*\.[a-z]{2,}(?:\.[a-z]{2})?)",
    # Explicit URL with http
    r"https?://([a-z0-9][-a-z0-9]*\.[a-z]{2,}(?:\.[a-z]{2})?)/",
    # Email domains  -> "info@example.com"
    r"@([a-z0-9][-a-z0-9]*\.[a-z]{2,}(?:\.[a-z]{2})?)",
    # "at example.com" as standalone
    r"\bat\s+([a-z0-9][-a-z0-9]*\.(?:com|io|ai|co|org|net|dev|app|tech|health|bio|xyz))\b",
]

URL_ANY = re.compile(
    r"(?:https?://)?([a-z0-9][-a-z0-9]*\.(?:com|io|ai|co|org|net|dev|app|tech|health|bio|xyz|gg|so|cc|me|co\.[a-z]{2}))\b",
    re.IGNORECASE,
)


def fetch_article_spider(url: str) -> str | None:
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
    except Exception as e:
        print(f"      Spider error: {e}")
    return None


def extract_domain_from_article(text: str, company_name: str, source_domain: str) -> str | None:
    """Tier 1: Regex extraction from article markdown."""
    # First pass: contextual patterns (higher confidence)
    for pat in DOMAIN_PATTERNS:
        for m in re.finditer(pat, text, re.IGNORECASE):
            d = normalize_domain(m.group(1))
            if not is_blocked(d) and d != source_domain:
                if domain_matches_company(d, company_name):
                    return d

    # Second pass: any URL in text that matches company name
    for m in URL_ANY.finditer(text):
        d = normalize_domain(m.group(1))
        if not is_blocked(d) and d != source_domain:
            if domain_matches_company(d, company_name):
                return d

    return None


# ────────────────────────────────────────────────────────────
# Tier 2: GPT extraction with domain validation
# ────────────────────────────────────────────────────────────

def is_suspect_domain(domain: str, company_name: str) -> bool:
    """Return True if the GPT-returned domain looks wrong."""
    if not domain or domain in ("not_stated", "not_found", ""):
        return True
    d = normalize_domain(domain)
    if is_blocked(d):
        return True
    return False


def extract_domain_gpt(article_text: str, company_name: str, source_domain: str) -> str | None:
    """Tier 2: GPT-4o-mini extraction with suspect-domain validation."""
    if not OPENAI_API_KEY or not article_text:
        return None

    messages = [
        {"role": "system", "content": "You extract the official website domain for a company from article text. Return ONLY the bare domain (e.g. 'hata.io'). No JSON, no explanation, no http://. Return 'not_found' if not in article."},
        {"role": "user", "content": f"Company: {company_name}\n\nArticle:\n{article_text[:6000]}\n\nWhat is {company_name}'s official website domain?"},
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
            # Clean up response
            raw = re.sub(r"^(https?://|www\.)", "", raw).split("/")[0].strip()
            if raw and not is_suspect_domain(raw, company_name) and raw != source_domain:
                return raw
    except Exception as e:
        print(f"      GPT error: {e}")
    return None


# ────────────────────────────────────────────────────────────
# Tier 3: Serper search fallback + Crunchbase snippet mining
# ────────────────────────────────────────────────────────────

def serper_search(query: str, num: int = 5) -> list[dict]:
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
    except Exception as e:
        print(f"      Serper error: {e}")
    return []


def extract_domains_from_text_broad(text: str) -> list[str]:
    """Extract any domain-like pattern from text."""
    pattern = re.compile(
        r"\b([a-z0-9][-a-z0-9]*\.(?:com|io|ai|co|org|net|dev|app|tech|health|bio|xyz|gg|so|cc|me))\b",
        re.IGNORECASE,
    )
    return list(dict.fromkeys(m.lower() for m in pattern.findall(text)))


def find_domain_serper(company_name: str, source_domain: str) -> tuple[str | None, str]:
    """Tier 3: Serper searches + Crunchbase snippet mining. Returns (domain, evidence)."""
    candidates: dict[str, dict] = {}

    def score_candidate(domain: str, evidence: str, bonus: int = 0):
        d = normalize_domain(domain)
        if is_blocked(d) or d == source_domain:
            return
        entry = candidates.setdefault(d, {"score": 0, "evidence": []})
        entry["evidence"].append(evidence)
        if domain_matches_company(d, company_name):
            entry["score"] += 5
        if re.search(r"\.(com|io|ai|co)$", d):
            entry["score"] += 1
        entry["score"] += bonus

    # Search 1: direct company website search
    items = serper_search(f'"{company_name}" startup website', 5)
    for item in items:
        link = item.get("link", "")
        if "://" not in link:
            continue
        d = normalize_domain(new_url_hostname(link))
        score_candidate(d, "startup_search", 0)
        # Also mine snippet
        for sd in extract_domains_from_text_broad(item.get("snippet", "")):
            score_candidate(sd, "snippet_startup_search", 0)

    # Search 2: Crunchbase snippet mining
    items = serper_search(f'site:crunchbase.com "{company_name}"', 3)
    for item in items:
        snippet = item.get("snippet", "")
        for sd in extract_domains_from_text_broad(snippet):
            score_candidate(sd, "crunchbase_snippet", 3)  # bonus for Crunchbase
        # Also link itself
        link = item.get("link", "")
        if "://" in link:
            d = normalize_domain(new_url_hostname(link))
            # crunchbase.com itself is blocked but other links in results aren't
            if not is_blocked(d):
                score_candidate(d, "crunchbase_link", 0)

    if not candidates:
        return None, "no candidates found"

    sorted_cands = sorted(candidates.items(), key=lambda x: x[1]["score"], reverse=True)
    best_domain, best_meta = sorted_cands[0]
    evidence = f"score={best_meta['score']} [{', '.join(best_meta['evidence'][:3])}]"
    return best_domain, evidence


def new_url_hostname(url: str) -> str:
    try:
        from urllib.parse import urlparse
        return urlparse(url).hostname or ""
    except Exception:
        return ""


# ────────────────────────────────────────────────────────────
# 3-tier waterfall
# ────────────────────────────────────────────────────────────

def resolve_domain(company_name: str, source_url: str) -> dict:
    """
    Run 3-tier domain resolution waterfall.
    Returns dict with: domain, tier, evidence, article_fetched
    """
    source_domain = normalize_domain(new_url_hostname(source_url)) if "://" in source_url else ""

    # Tier 1: Spider article fetch + regex
    print(f"      Tier 1: Fetching article...")
    article_text = fetch_article_spider(source_url)
    if article_text:
        print(f"      Article fetched ({len(article_text)} chars), running regex...")
        d = extract_domain_from_article(article_text, company_name, source_domain)
        if d:
            return {"domain": d, "tier": 1, "tier_name": "article_regex", "evidence": "regex match in article", "article_fetched": True}

        # Tier 2: GPT extraction
        print(f"      Tier 2: Running GPT extraction...")
        d = extract_domain_gpt(article_text, company_name, source_domain)
        if d:
            return {"domain": d, "tier": 2, "tier_name": "gpt_extract", "evidence": "GPT extracted from article", "article_fetched": True}
    else:
        print(f"      Article fetch failed, skipping Tier 1+2")

    # Tier 3: Serper search
    print(f"      Tier 3: Running Serper search...")
    d, evidence = find_domain_serper(company_name, source_domain)
    if d:
        return {"domain": d, "tier": 3, "tier_name": "serper_search", "evidence": evidence, "article_fetched": article_text is not None}

    return {"domain": "not_found", "tier": 0, "tier_name": "not_found", "evidence": "all tiers failed", "article_fetched": article_text is not None}


# ────────────────────────────────────────────────────────────
# Evaluation
# ────────────────────────────────────────────────────────────

KNOWN_BAD_DOMAINS = {
    "www.finanzwire.com", "finanzwire.com",
    "therecursive.com", "www.therecursive.com",
    "gobiernu.cw", "www.gobiernu.cw",
}


def classify_result(company_name: str, expected_domain: str, resolved_domain: str) -> str:
    exp = normalize_domain(expected_domain)
    res = normalize_domain(resolved_domain)

    if res == "not_found":
        return "NOT_FOUND"

    # Expected was a known-bad domain — did we improve?
    if exp in KNOWN_BAD_DOMAINS or is_blocked(exp):
        if not is_blocked(res):
            return "IMPROVED"
        return "NOT_FOUND"

    # Expected was correct — did we match?
    if res == exp or res.replace("www.", "") == exp.replace("www.", ""):
        return "CORRECT"

    # We got something but it differs from expected
    return "WRONG"


# ────────────────────────────────────────────────────────────
# Main
# ────────────────────────────────────────────────────────────

def main():
    output_path = Path(__file__).resolve().parent.parent / "output" / "series-a-2026-04-21.json"
    data = json.loads(output_path.read_text(encoding="utf-8"))
    companies = data["companies"]

    print(f"\n{'='*70}")
    print(f"  DOMAIN RESOLUTION END-TO-END TEST")
    print(f"  Companies: {len(companies)} | Date: 2026-04-21")
    print(f"  APIs: Spider={'YES' if SPIDER_API_KEY else 'NO'} | Serper={'YES' if SERPER_API_KEY else 'NO'} | OpenAI={'YES' if OPENAI_API_KEY else 'NO'}")
    print(f"{'='*70}\n")

    results = []

    for i, company in enumerate(companies):
        name = company["company_name"]
        expected = company["company_domain"]
        source_url = company["source_url"]

        print(f"\n[{i+1}/{len(companies)}] {name}")
        print(f"    Expected: {expected}")
        print(f"    Source:   {source_url[:80]}")

        resolution = resolve_domain(name, source_url)
        resolved = resolution["domain"]
        verdict = classify_result(name, expected, resolved)

        print(f"    Resolved: {resolved} (Tier {resolution['tier']}: {resolution['tier_name']})")
        print(f"    Verdict:  {verdict}")

        results.append({
            "company": name,
            "expected_domain": expected,
            "resolved_domain": resolved,
            "tier": resolution["tier"],
            "tier_name": resolution["tier_name"],
            "evidence": resolution["evidence"],
            "article_fetched": resolution["article_fetched"],
            "verdict": verdict,
        })

    # ── Summary ──
    print(f"\n{'='*70}")
    print(f"  RESULTS SUMMARY")
    print(f"{'='*70}")

    verdicts = [r["verdict"] for r in results]
    correct = verdicts.count("CORRECT")
    improved = verdicts.count("IMPROVED")
    wrong = verdicts.count("WRONG")
    not_found = verdicts.count("NOT_FOUND")
    total = len(results)

    tier_counts = {}
    for r in results:
        t = r["tier_name"]
        tier_counts[t] = tier_counts.get(t, 0) + 1

    print(f"\n  Per-Company Results:")
    print(f"  {'Company':<40} {'Expected Domain':<28} {'Resolved Domain':<28} {'Tier':<18} Verdict")
    print(f"  {'-'*40} {'-'*28} {'-'*28} {'-'*18} {'-'*10}")
    for r in results:
        tier_label = f"T{r['tier']}:{r['tier_name']}" if r['tier'] > 0 else "NOT_FOUND"
        print(f"  {r['company'][:40]:<40} {r['expected_domain'][:28]:<28} {r['resolved_domain'][:28]:<28} {tier_label:<18} {r['verdict']}")

    print(f"\n  Verdict Breakdown:")
    print(f"    CORRECT   : {correct:>3} ({100*correct//total if total else 0}%)")
    print(f"    IMPROVED  : {improved:>3} ({100*improved//total if total else 0}%)")
    print(f"    WRONG     : {wrong:>3} ({100*wrong//total if total else 0}%)")
    print(f"    NOT_FOUND : {not_found:>3} ({100*not_found//total if total else 0}%)")
    print(f"    ---------------------------------")
    hit_rate = 100 * (correct + improved) // total if total else 0
    print(f"    HIT RATE  : {correct + improved:>3}/{total} = {hit_rate}%")

    print(f"\n  Tier Breakdown:")
    for t, count in sorted(tier_counts.items()):
        print(f"    {t:<20}: {count}")

    print(f"\n  Known-Bad Domains Fixed:")
    for r in results:
        if r["verdict"] == "IMPROVED":
            print(f"    {r['company']}: {r['expected_domain']} → {r['resolved_domain']} (T{r['tier']})")

    print(f"\n  Wrong Resolutions:")
    for r in results:
        if r["verdict"] == "WRONG":
            print(f"    {r['company']}: expected={r['expected_domain']} got={r['resolved_domain']}")

    print(f"\n  Not Found:")
    for r in results:
        if r["verdict"] == "NOT_FOUND":
            print(f"    {r['company']}: expected={r['expected_domain']}")

    print(f"\n{'='*70}\n")

    # Save results
    out_path = Path(__file__).resolve().parent.parent / "output" / "domain-resolution-test-2026-04-22.json"
    out_path.write_text(json.dumps({"results": results, "summary": {
        "correct": correct, "improved": improved, "wrong": wrong, "not_found": not_found,
        "total": total, "hit_rate_pct": hit_rate, "tier_counts": tier_counts,
    }}, indent=2), encoding="utf-8")
    print(f"  Full results saved to: {out_path}")


if __name__ == "__main__":
    main()
