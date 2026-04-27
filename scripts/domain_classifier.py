"""
Domain classifier with persistent JSON cache.

Replaces the band-aid pattern of accreting hardcoded entries to BLOCKED_DOMAINS
every time a new news/legal/data-platform site sneaks past validate_domain.

Architecture:
  classify_domain(domain) -> {"category": str, "confidence": str, "source": str}

  1. Check cache (data/domain_classifications.json — committed to git for shared
     learning across runs/machines).
  2. On miss: call gpt-4o-mini with the domain string + light heuristic features.
     Persist verdict to cache.

Categories:
  real_company          actual company website (accept as company domain)
  news                  news/aggregator/press wire
  social                social media / community / dev platform
  data_platform         dealroom/tracxn/crunchbase-style aggregators
  legal                 law firm / professional services
  cdn                   content delivery / static asset host
  tracker               analytics / tag manager
  short_url             link shortener
  edu                   .edu / academic
  unknown               classifier couldn't decide

Cost: ~$0.0001 per classification (gpt-4o-mini, ~150 input tokens, ~30 output).
A typical pipeline run hits 0-5 unknown domains, so amortized cost is trivial.
After ~30 production runs the cache covers the long tail.
"""

import json
import os
import re
import sys
import threading
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

_SCRIPT_DIR = Path(__file__).resolve().parent
_CACHE_PATH = _SCRIPT_DIR.parent / "data" / "domain_classifications.json"

# Workspace-root dotenv (Everything_CC/.env) is the primary key store.
# Same pattern used in domain_resolver.py / pipeline_base.py.
load_dotenv(_SCRIPT_DIR.parent / ".env")
load_dotenv(_SCRIPT_DIR.parent.parent / ".env", override=False)
load_dotenv(Path.home() / ".env", override=False)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

VALID_CATEGORIES = {
    "real_company", "news", "social", "data_platform", "legal",
    "cdn", "tracker", "short_url", "edu", "unknown",
}
BLOCKING_CATEGORIES = VALID_CATEGORIES - {"real_company", "unknown"}

_cache_lock = threading.Lock()
_cache: dict[str, dict] | None = None


def _load_cache() -> dict[str, dict]:
    global _cache
    if _cache is not None:
        return _cache
    if _CACHE_PATH.exists():
        try:
            with open(_CACHE_PATH, "rb") as f:
                _cache = json.loads(f.read().decode("utf-8"))
        except Exception:
            _cache = {}
    else:
        _cache = {}
    return _cache


def _save_cache() -> None:
    cache = _load_cache()
    _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _CACHE_PATH.with_suffix(".json.tmp")
    with open(tmp, "wb") as f:
        f.write(json.dumps(cache, indent=2, sort_keys=True, ensure_ascii=False).encode("utf-8"))
    tmp.replace(_CACHE_PATH)


def _normalize(domain: str) -> str:
    if not domain:
        return ""
    d = domain.strip().lower()
    if "://" in d:
        d = d.split("://", 1)[1]
    return d.split("/")[0].replace("www.", "")


def seed_cache(seed_map: dict[str, str]) -> int:
    """
    Seed cache with hand-curated category assignments. Used to bootstrap from
    the legacy BLOCKED_DOMAINS sets so we don't burn tokens re-classifying
    known-bad domains. Idempotent — existing entries are kept.
    """
    cache = _load_cache()
    added = 0
    with _cache_lock:
        for raw, cat in seed_map.items():
            d = _normalize(raw)
            if not d or cat not in VALID_CATEGORIES:
                continue
            if d not in cache:
                cache[d] = {
                    "category": cat,
                    "confidence": "high",
                    "source": "seed",
                    "ts": datetime.utcnow().isoformat() + "Z",
                }
                added += 1
        if added:
            _save_cache()
    return added


def _gpt_classify(domain: str) -> dict:
    """One gpt-4o-mini call. Returns category + confidence."""
    if not OPENAI_API_KEY:
        return {"category": "unknown", "confidence": "low", "source": "no_api_key"}

    prompt = (
        f"Classify this internet domain into ONE category:\n"
        f"  domain: {domain}\n\n"
        "Categories:\n"
        "  real_company   — an actual company / startup official website\n"
        "  news           — news outlet / blog / press wire / aggregator\n"
        "  social         — social network, dev platform, community, review site\n"
        "  data_platform  — startup database / VC database / market intelligence\n"
        "  legal          — law firm or professional services\n"
        "  cdn            — content delivery network / static asset host\n"
        "  tracker        — analytics / tag manager / pixel\n"
        "  short_url      — link shortener\n"
        "  edu            — university / academic\n"
        "  unknown        — cannot determine\n\n"
        "Use ONLY the domain string. Do NOT visit the site.\n"
        "If the domain looks like a normal company name (e.g. 'auth0.com', 'zenskar.com', 'humblerobotics.com') and you don't recognize it as a known publisher/aggregator, default to real_company.\n"
        "Return strict JSON: {\"category\":\"...\",\"confidence\":\"high|medium|low\"}"
    )

    try:
        resp = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "gpt-4o-mini",
                "temperature": 0,
                "max_tokens": 60,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": "You classify internet domains. Output strict JSON."},
                    {"role": "user", "content": prompt},
                ],
            },
            timeout=20,
        )
        resp.raise_for_status()
        body = json.loads(resp.json()["choices"][0]["message"]["content"])
        cat = body.get("category", "unknown")
        if cat not in VALID_CATEGORIES:
            cat = "unknown"
        conf = body.get("confidence", "low")
        if conf not in ("high", "medium", "low"):
            conf = "low"
        return {"category": cat, "confidence": conf, "source": "gpt-4o-mini"}
    except Exception as e:
        return {"category": "unknown", "confidence": "low", "source": f"error:{type(e).__name__}"}


def classify_domain(domain: str) -> dict:
    """
    Public entry. Returns {"category", "confidence", "source", "blocked"}.

    Cache-first: domains already classified are returned without any API call.
    On cache miss, calls gpt-4o-mini, persists, returns.
    """
    d = _normalize(domain)
    if not d:
        return {"category": "unknown", "confidence": "low", "source": "empty", "blocked": False}

    cache = _load_cache()
    if d in cache:
        v = dict(cache[d])
        v["blocked"] = v["category"] in BLOCKING_CATEGORIES
        return v

    verdict = _gpt_classify(d)
    verdict["ts"] = datetime.utcnow().isoformat() + "Z"
    with _cache_lock:
        cache[d] = verdict
        _save_cache()
    out = dict(verdict)
    out["blocked"] = verdict["category"] in BLOCKING_CATEGORIES
    return out


def is_blocked_smart(domain: str) -> bool:
    """Convenience wrapper. True if classifier says this is a non-real-company site."""
    return classify_domain(domain).get("blocked", False)


# ---------------------------------------------------------------------------
# CLI: ad-hoc classify, dump cache, seed cache from BLOCKED_DOMAINS
# ---------------------------------------------------------------------------

def _cli():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("domain", nargs="?", help="Domain to classify")
    ap.add_argument("--seed-from-resolver", action="store_true",
                    help="Seed cache from domain_resolver hardcoded blocklists")
    ap.add_argument("--show", action="store_true", help="Print cache stats")
    args = ap.parse_args()

    if args.seed_from_resolver:
        from domain_resolver import (
            NEWS_DOMAINS, SOCIAL_DOMAINS, DATA_PLATFORM_DOMAINS,
            LEGAL_SERVICES_DOMAINS, CDN_DOMAINS, TRACKER_DOMAINS, SHORT_URL_DOMAINS,
        )
        seed = {}
        for d in NEWS_DOMAINS: seed[d] = "news"
        for d in SOCIAL_DOMAINS: seed[d] = "social"
        for d in DATA_PLATFORM_DOMAINS: seed[d] = "data_platform"
        for d in LEGAL_SERVICES_DOMAINS: seed[d] = "legal"
        for d in CDN_DOMAINS: seed[d] = "cdn"
        for d in TRACKER_DOMAINS: seed[d] = "tracker"
        for d in SHORT_URL_DOMAINS: seed[d] = "short_url"
        added = seed_cache(seed)
        print(f"Seeded {added} new entries (cache total: {len(_load_cache())})")
        return

    if args.show:
        cache = _load_cache()
        by_cat: dict[str, int] = {}
        for v in cache.values():
            by_cat[v.get("category", "unknown")] = by_cat.get(v.get("category", "unknown"), 0) + 1
        print(f"Cache: {len(cache)} entries")
        for cat in sorted(by_cat, key=lambda x: -by_cat[x]):
            print(f"  {cat:16s} {by_cat[cat]}")
        return

    if not args.domain:
        ap.error("provide a domain or --show / --seed-from-resolver")
    v = classify_domain(args.domain)
    print(json.dumps(v, indent=2))


if __name__ == "__main__":
    _cli()
