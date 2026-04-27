"""Quick unit tests for domain_resolver module."""

from domain_resolver import validate_domain, names_are_similar, fuzzy_dedup_companies, match_existing_company

def test_validate_domain():
    """All known-bad domains from screenshot + backfill should be rejected."""
    tests = [
        ("infomoney.com", "Strider Technologies", "infomoney.com"),
        ("dealroom.co", "Cursor", ""),
        ("cdninstagram.com", "STORM Therapeutics", ""),
        ("cdninstagram.com", "Omni", ""),
        ("t.co", "Bluefish", ""),
        ("amazonaws.com", "Era", ""),
        ("filerobot.com", "Netbank", ""),
        ("economictimes.com", "Related Digital", ""),
        ("investing.com", "Slate Auto", ""),
        ("statnews.com", "Tortugas Neuroscience", ""),
        ("giotto.ai", "BLP Digital", ""),
        ("securitybrief.co", "Cloudsmith", ""),
        ("technews180.com", "Verda", ""),
        # Good domains should pass
    ]

    print("=== validate_domain: reject bad domains ===")
    all_pass = True
    for domain, company, source in tests:
        v = validate_domain(domain, company, source)
        if v["valid"]:
            print(f"  FAIL: {domain} accepted for {company} ({v['reason']})")
            all_pass = False
        else:
            print(f"  PASS: {domain} rejected ({v['reason']})")

    # Good domains should pass
    good_tests = [
        ("stripe.com", "Stripe", ""),
        ("hata.io", "Hata", ""),
        ("mosaic.pe", "Mosaic", ""),
        ("trykeep.com", "Keep", ""),
        ("resolve.ai", "Resolve AI", ""),
    ]
    print("\n=== validate_domain: accept good domains ===")
    for domain, company, source in good_tests:
        v = validate_domain(domain, company, source)
        if not v["valid"]:
            print(f"  FAIL: {domain} rejected for {company} ({v['reason']})")
            all_pass = False
        else:
            print(f"  PASS: {domain} accepted ({v['confidence']})")

    print(f"\nvalidate_domain: {'ALL PASS' if all_pass else 'FAILURES DETECTED'}")
    return all_pass


def test_names_are_similar():
    """Token overlap and Levenshtein similarity checks."""
    pairs = [
        ("Strider Technologies", "Strider", True),
        ("Strider Technologies", "Strider Technologies", True),
        ("Cursor", "Cursor AI", True),
        ("Dinotisia", "Dnotitia", True),
        ("OpenAI", "Stripe", False),
        ("Clay", "Mosaic", False),
        ("STORM Therapeutics", "Storm", True),
        ("Epoch Biodesign", "Epoch", True),
        ("BLP Digital", "BLP", True),
    ]

    print("\n=== names_are_similar ===")
    all_pass = True
    for a, b, expected in pairs:
        result = names_are_similar(a, b)
        status = "PASS" if result == expected else "FAIL"
        if result != expected:
            all_pass = False
        print(f"  {status}: '{a}' ~ '{b}' -> {result} (expected {expected})")

    print(f"\nnames_are_similar: {'ALL PASS' if all_pass else 'FAILURES DETECTED'}")
    return all_pass


def test_fuzzy_dedup():
    """Strider x3 should collapse to 1 entry."""
    companies = [
        {"company_name": "Strider Technologies", "company_domain": "infomoney.com", "best_score": 10, "sources": [{"url": "a"}]},
        {"company_name": "Strider Technologies", "company_domain": "not_found", "best_score": 8, "sources": [{"url": "b"}]},
        {"company_name": "Strider", "company_domain": "not_found", "best_score": 12, "sources": [{"url": "c"}]},
        {"company_name": "Adcendo", "company_domain": "adcendo.com", "best_score": 15, "sources": [{"url": "d"}]},
    ]

    deduped = fuzzy_dedup_companies(companies)

    print("\n=== fuzzy_dedup_companies ===")
    print(f"  Input: {len(companies)} companies")
    print(f"  Output: {len(deduped)} companies")
    for c in deduped:
        merged = c.get("_merged_from", [])
        print(f"    {c['company_name']} (score={c['best_score']}, sources={len(c['sources'])}) merged_from={merged}")

    all_pass = True
    if len(deduped) != 2:
        print(f"  FAIL: expected 2 companies, got {len(deduped)}")
        all_pass = False
    else:
        print(f"  PASS: 4 -> 2 companies")

    # Strider group should have highest score (12) and 3 sources
    strider = [c for c in deduped if "strider" in c["company_name"].lower()]
    if strider:
        s = strider[0]
        if s["best_score"] != 12:
            print(f"  FAIL: Strider score should be 12, got {s['best_score']}")
            all_pass = False
        if len(s["sources"]) != 3:
            print(f"  FAIL: Strider should have 3 sources, got {len(s['sources'])}")
            all_pass = False

    print(f"\nfuzzy_dedup: {'ALL PASS' if all_pass else 'FAILURES DETECTED'}")
    return all_pass


def test_match_existing_company():
    """Cross-day dedup: match new company against recent rows by domain or fuzzy name."""
    recent = [
        {"id": 1, "company_name": "Strider Technologies", "company_domain": "strider.tech"},
        {"id": 2, "company_name": "Adcendo", "company_domain": "adcendo.com"},
        {"id": 3, "company_name": "Loop AI", "company_domain": "loop.ai"},
        {"id": 4, "company_name": "Cursor", "company_domain": ""},
    ]

    cases = [
        # (new_row, expected_match_id_or_None, label)
        ({"company_name": "Strider", "company_domain": "strider.tech"}, 1, "exact domain"),
        ({"company_name": "Strider Tech", "company_domain": ""}, 1, "fuzzy name no domain"),
        ({"company_name": "Adcendo Inc", "company_domain": "adcendo.com"}, 2, "name+domain"),
        ({"company_name": "Loop", "company_domain": "loop.ai"}, 3, "domain priority"),
        ({"company_name": "Cursor", "company_domain": ""}, 4, "name only"),
        ({"company_name": "Stripe", "company_domain": "stripe.com"}, None, "no match"),
        ({"company_name": "Adcendo", "company_domain": "www.adcendo.com"}, 2, "www prefix normalized"),
        ({"company_name": "", "company_domain": ""}, None, "empty input"),
    ]

    print("=== match_existing_company ===")
    all_pass = True
    for new_row, expected_id, label in cases:
        result = match_existing_company(new_row, recent)
        result_id = result.get("id") if result else None
        if result_id == expected_id:
            print(f"  PASS: {label} -> {result_id}")
        else:
            print(f"  FAIL: {label} expected {expected_id}, got {result_id}")
            all_pass = False
    print(f"\nmatch_existing_company: {'ALL PASS' if all_pass else 'FAILURES DETECTED'}")
    return all_pass


if __name__ == "__main__":
    r1 = test_validate_domain()
    r2 = test_names_are_similar()
    r3 = test_fuzzy_dedup()
    r4 = test_match_existing_company()

    print(f"\n{'='*50}")
    print(f"OVERALL: {'ALL PASS' if all([r1, r2, r3, r4]) else 'FAILURES DETECTED'}")
    print(f"{'='*50}")
