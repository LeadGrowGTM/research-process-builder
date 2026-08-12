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


def test_generated_seed_corpus_preserves_seed_provenance_and_explicit_gaps() -> None:
    data = yaml.safe_load(Path("benchmarks/companies.yaml").read_text(encoding="utf-8"))
    companies = data["companies"]

    assert data["status"] == "proposed_seed"
    assert data["source"]["kind"] == "ai_ark_export"
    assert data["source"]["provenance"] == "unverified_seed"
    assert len(companies) == 60
    assert sum(item["shared_core"] for item in companies) == 15
    assert all(item["seed_status"] == "unverified_seed" for item in companies)
    assert all(item["domain"] and item["seed"]["description"] and item["seed"]["industry"] for item in companies)
    assert all("target_customer" in item["gaps"] for item in companies)
    assert sum("products_services" in item["gaps"] for item in companies) == 12

    serialized = str(data).casefold()
    assert "company_email" not in serialized
    assert "company_phone" not in serialized
