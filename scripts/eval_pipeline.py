"""
Pipeline Eval Harness

Tests domain resolution accuracy and dedup correctness against ground truth.
Run after any pipeline change to prevent regression.

Usage:
    py scripts/eval_pipeline.py                    # full eval (domain + dedup)
    py scripts/eval_pipeline.py --domain-only      # domain resolution only
    py scripts/eval_pipeline.py --dedup-only       # dedup only
    py scripts/eval_pipeline.py --offline           # validation + dedup only (no API calls)

Exit code 0 = pass (>= 90% accuracy), 1 = fail
"""

import json
import sys
import argparse
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent

from domain_resolver import (
    validate_domain,
    names_are_similar,
    fuzzy_dedup_companies,
)


# ---------------------------------------------------------------------------
# Ground truth: domains that MUST be rejected
# (from backfill-committed-20260425-0959.json — every old_domain was wrong)
# ---------------------------------------------------------------------------

BAD_DOMAIN_CASES = [
    {"company": "PvX Partners", "bad_domain": "not_found", "correct_domain": "pvxpartners.com"},
    {"company": "Hata", "bad_domain": "finanzwire.com", "correct_domain": "hata.io"},
    {"company": "Dinotisia", "bad_domain": "gobiernu.cw", "correct_domain": "dnotitia.com"},
    {"company": "&Charge", "bad_domain": "therecursive.com", "correct_domain": "and-charge.com"},
    {"company": "Epoch Biodesign", "bad_domain": "eu-startups.com", "correct_domain": "epochbiodesign.com"},
    {"company": "Slate Auto", "bad_domain": "investing.com", "correct_domain": "slate.auto"},
    {"company": "Verda", "bad_domain": "technews180.com", "correct_domain": "verda.com"},
    {"company": "Resolve AI", "bad_domain": "gunder.com", "correct_domain": "resolve.ai"},
    {"company": "Syenta", "bad_domain": "anu.edu.au", "correct_domain": "syenta.com"},
    {"company": "Cloudsmith", "bad_domain": "securitybrief.co", "correct_domain": "cloudsmith.com"},
    {"company": "Netbank", "bad_domain": "filerobot.com", "correct_domain": "netbank.ph"},
    {"company": "Loop", "bad_domain": "cdninstagram.com", "correct_domain": "loop.ai"},
    {"company": "Related Digital", "bad_domain": "economictimes.com", "correct_domain": "relateddigital.com"},
    {"company": "Kajaani", "bad_domain": "cdninstagram.com", "correct_domain": "iiwari.com"},
    {"company": "Tortugas Neuroscience", "bad_domain": "statnews.com", "correct_domain": "tortugasneuroscience.com"},
    {"company": "Era", "bad_domain": "amazonaws.com", "correct_domain": "era.app"},
    {"company": "Bluefish", "bad_domain": "t.co", "correct_domain": "bluefishai.com"},
    {"company": "JPYC Stablecoin", "bad_domain": "t.co", "correct_domain": "jpyc.co.jp"},
    {"company": "BLP Digital", "bad_domain": "giotto.ai", "correct_domain": "blp.digital"},
]

# From domain-agent test (100% accuracy on these)
KNOWN_GOOD_DOMAINS = [
    {"company": "Stripe", "domain": "stripe.com"},
    {"company": "Databricks", "domain": "databricks.com"},
    {"company": "Figma", "domain": "figma.com"},
    {"company": "Cohere", "domain": "cohere.com"},
    {"company": "Harvey", "domain": "harvey.ai"},
    {"company": "Mosaic", "domain": "mosaic.pe"},
    {"company": "Zenskar", "domain": "zenskar.com"},
    {"company": "Hata", "domain": "hata.io"},
    {"company": "ElevenLabs", "domain": "elevenlabs.io"},
    {"company": "Lovable", "domain": "lovable.dev"},
    {"company": "Clay", "domain": "clay.com"},
    {"company": "Keep", "domain": "trykeep.com"},
    {"company": "Vanta", "domain": "vanta.com"},
    {"company": "Brev", "domain": "brev.dev"},
    {"company": "Nava", "domain": "navabenefits.com"},
    {"company": "Cosaic", "domain": "cosaic.com"},
]

# Dedup test cases (from screenshot failures)
DEDUP_CASES = [
    {
        "input": [
            {"company_name": "Strider Technologies", "company_domain": "infomoney.com", "best_score": 10, "sources": [{"url": "a"}]},
            {"company_name": "Strider Technologies", "company_domain": "not_found", "best_score": 8, "sources": [{"url": "b"}]},
            {"company_name": "Strider", "company_domain": "not_found", "best_score": 12, "sources": [{"url": "c"}]},
        ],
        "expected_count": 1,
        "description": "Strider x3 -> 1",
    },
    {
        "input": [
            {"company_name": "STORM Therapeutics", "company_domain": "cdninstagram.com", "best_score": 10, "sources": [{"url": "a"}]},
            {"company_name": "Storm", "company_domain": "not_found", "best_score": 8, "sources": [{"url": "b"}]},
        ],
        "expected_count": 1,
        "description": "STORM Therapeutics + Storm -> 1",
    },
    {
        "input": [
            {"company_name": "Adcendo", "company_domain": "adcendo.com", "best_score": 15, "sources": [{"url": "d"}]},
            {"company_name": "Cursor", "company_domain": "cursor.com", "best_score": 12, "sources": [{"url": "e"}]},
        ],
        "expected_count": 2,
        "description": "Adcendo + Cursor stay separate",
    },
    {
        "input": [
            {"company_name": "Loop AI", "company_domain": "loop.ai", "best_score": 10, "sources": [{"url": "a"}]},
            {"company_name": "Loop", "company_domain": "loop.ai", "best_score": 8, "sources": [{"url": "b"}]},
        ],
        "expected_count": 1,
        "description": "Loop AI + Loop (same domain) -> 1",
    },
]


def eval_validation_gate():
    """Test that validate_domain rejects all known-bad domains."""
    print("=== Eval: Validation Gate ===")
    passed = 0
    failed = 0

    for case in BAD_DOMAIN_CASES:
        bad = case["bad_domain"]
        if bad in ("not_found", "not_stated"):
            # These are handled separately (not a validation failure)
            v = validate_domain(bad, case["company"])
            if not v["valid"]:
                passed += 1
            else:
                print(f"  FAIL: {bad} accepted for {case['company']}")
                failed += 1
            continue

        v = validate_domain(bad, case["company"])
        if not v["valid"]:
            passed += 1
        else:
            print(f"  FAIL: {bad} NOT rejected for {case['company']} ({v['reason']})")
            failed += 1

    # Good domains should pass
    for case in KNOWN_GOOD_DOMAINS:
        v = validate_domain(case["domain"], case["company"])
        if v["valid"]:
            passed += 1
        else:
            print(f"  FAIL: {case['domain']} rejected for {case['company']} ({v['reason']})")
            failed += 1

    total = passed + failed
    pct = (100 * passed // total) if total else 0
    print(f"  Result: {passed}/{total} ({pct}%)")
    return passed, total


def eval_dedup():
    """Test fuzzy dedup against known failure cases."""
    print("\n=== Eval: Fuzzy Dedup ===")
    passed = 0
    failed = 0

    for case in DEDUP_CASES:
        result = fuzzy_dedup_companies(case["input"])
        if len(result) == case["expected_count"]:
            passed += 1
            print(f"  PASS: {case['description']} ({len(case['input'])} -> {len(result)})")
        else:
            failed += 1
            print(f"  FAIL: {case['description']} expected {case['expected_count']}, got {len(result)}")

    total = passed + failed
    pct = (100 * passed // total) if total else 0
    print(f"  Result: {passed}/{total} ({pct}%)")
    return passed, total


def eval_domain_resolution():
    """Test actual domain resolution against ground truth (requires API keys)."""
    try:
        from domain_resolver import resolve_domain
    except ImportError:
        print("\n=== Eval: Domain Resolution SKIPPED (import failed) ===")
        return 0, 0

    import os
    if not os.getenv("SERPER_API_KEY"):
        print("\n=== Eval: Domain Resolution SKIPPED (no SERPER_API_KEY) ===")
        return 0, 0

    print("\n=== Eval: Domain Resolution (live API calls) ===")
    passed = 0
    failed = 0

    # Test a subset to keep costs low (~$0.03)
    test_cases = KNOWN_GOOD_DOMAINS[:5]
    for case in test_cases:
        result = resolve_domain(
            company_name=case["company"],
            source_url="",
            industry="",
            use_agent_fallback=False,
        )
        if result["domain"] == case["domain"]:
            passed += 1
            print(f"  PASS: {case['company']} -> {result['domain']} (tier {result['tier']})")
        else:
            failed += 1
            print(f"  FAIL: {case['company']} expected {case['domain']}, got {result['domain']}")

    total = passed + failed
    pct = (100 * passed // total) if total else 0
    print(f"  Result: {passed}/{total} ({pct}%)")
    return passed, total


def main():
    parser = argparse.ArgumentParser(description="Pipeline eval harness")
    parser.add_argument("--domain-only", action="store_true")
    parser.add_argument("--dedup-only", action="store_true")
    parser.add_argument("--offline", action="store_true", help="Skip live API tests")
    args = parser.parse_args()

    total_passed = 0
    total_tests = 0

    if not args.domain_only:
        p, t = eval_validation_gate()
        total_passed += p
        total_tests += t

        p, t = eval_dedup()
        total_passed += p
        total_tests += t

    if not args.dedup_only and not args.offline:
        p, t = eval_domain_resolution()
        total_passed += p
        total_tests += t

    pct = (100 * total_passed // total_tests) if total_tests else 0
    print(f"\n{'='*50}")
    print(f"OVERALL: {total_passed}/{total_tests} ({pct}%)")
    threshold = 90
    if pct >= threshold:
        print(f"STATUS: PASS (>= {threshold}%)")
    else:
        print(f"STATUS: FAIL (< {threshold}%)")
    print(f"{'='*50}")

    sys.exit(0 if pct >= threshold else 1)


if __name__ == "__main__":
    main()
