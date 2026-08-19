from collections import Counter
from pathlib import Path

import yaml


def test_b2b_company_selection_has_60_10_per_cohort_and_15_core() -> None:
    data = yaml.safe_load(Path("benchmarks/company-selection.yaml").read_text(encoding="utf-8"))
    companies = data["companies"]

    assert len(companies) == 60
    assert len({item["id"] for item in companies}) == 60
    assert len({item["name"].casefold() for item in companies}) == 60
    assert Counter(item["cohort"] for item in companies) == {
        "local_b2b_services": 10,
        "b2b_saas": 10,
        "recently_funded_b2b": 10,
        "well_known_b2b": 10,
        "b2b_agencies": 10,
        "b2b_commerce_suppliers": 10,
    }
    assert sum(item["core"] for item in companies) == 15


def test_every_cohort_has_easy_ambiguous_and_hard_cases() -> None:
    data = yaml.safe_load(Path("benchmarks/company-selection.yaml").read_text(encoding="utf-8"))
    by_cohort: dict[str, set[str]] = {}
    for item in data["companies"]:
        by_cohort.setdefault(item["cohort"], set()).add(item["difficulty"])

    assert all(levels == {"easy", "ambiguous", "hard"} for levels in by_cohort.values())


def test_published_corpus_preserves_raw_seed_provenance_without_promoting_it() -> None:
    data = yaml.safe_load(Path("benchmarks/companies.yaml").read_text(encoding="utf-8"))
    companies = data["companies"]

    assert data["status"] == "research_complete"
    assert data["source"]["kind"] == "ai_ark_export"
    assert data["source"]["provenance"] == "unverified_seed"
    assert len(companies) == 60
    assert sum(item["shared_core"] for item in companies) == 15
    assert all(item["seed_status"] == "verified" for item in companies)
    assert all(item["domain"] and item["seed"]["description"] and item["seed"]["industry"] for item in companies)
    assert all(item["gaps"] == [] for item in companies)
    assert all(item["b2b_buyer"] and item["business_offer"] for item in companies)
    assert all(item["cohort_evidence_url"] for item in companies)
    assert all(item["selection_reason"] for item in companies)
    assert all(item["secondary_tags"] for item in companies)
    assert all(item["expected_ad_channels"] for item in companies)
    assert len(list(Path("benchmarks/dossiers").glob("*.yaml"))) == 60

    serialized = str(data).casefold()
    assert "company_email" not in serialized
    assert "company_phone" not in serialized
