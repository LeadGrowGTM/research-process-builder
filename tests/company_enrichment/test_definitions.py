from pathlib import Path

import pytest

from scripts.company_enrichment.definitions import (
    DefinitionError,
    load_definition,
    load_registry,
)


MANIFEST_ROOT = Path("enrichments/p0")
EXPECTED_IDS = (
    "analogy-value-translator",
    "company-description",
    "competitor-intelligence",
    "growth-signals",
    "icp-persona-analysis",
    "job-opportunity-mining",
    "news-product-launches",
    "running-ads-offer-intelligence",
)


def test_registry_loads_exactly_the_eight_unique_p0_definitions() -> None:
    registry = load_registry(MANIFEST_ROOT)
    assert tuple(registry) == EXPECTED_IDS
    assert all(item.priority == "P0" for item in registry.values())


def test_definition_exposes_required_execution_and_gate_policy() -> None:
    item = load_definition(MANIFEST_ROOT / "company-description.yaml")
    assert item.id == "company-description"
    assert item.version == "1.0.0"
    assert item.required_inputs == ("company_name", "domain")
    assert item.execution_mode == "search-and-scrape"
    assert item.provider_candidates == ("parallel-search", "gtm-waterfall")
    assert item.fallback_order == ("parallel-search", "gtm-waterfall")
    assert item.caps == {"queries": 8, "scrapes": 8, "retries": 2, "paid_cost_usd": 1.0}
    assert item.output_visibility == "message_safe"
    assert item.benchmark_dataset_version == "b2b-companies-1.0"
    assert item.automated_gate == "candidate_only"
    assert item.human_gate == "blind_verdict_required"


def test_manifest_rejects_unknown_keys(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text(
        (MANIFEST_ROOT / "company-description.yaml").read_text(encoding="utf-8")
        + "unexpected: true\n",
        encoding="utf-8",
    )
    with pytest.raises(DefinitionError, match="unknown keys.*unexpected"):
        load_definition(path)


def test_manifest_rejects_environment_interpolation(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    source = (MANIFEST_ROOT / "company-description.yaml").read_text(encoding="utf-8")
    path.write_text(source.replace("company_name", "${COMPANY_NAME}"), encoding="utf-8")
    with pytest.raises(DefinitionError, match="environment interpolation"):
        load_definition(path)


def test_manifest_rejects_non_semantic_version(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    source = (MANIFEST_ROOT / "company-description.yaml").read_text(encoding="utf-8")
    path.write_text(source.replace('version: "1.0.0"', 'version: "1"'), encoding="utf-8")
    with pytest.raises(DefinitionError, match="semantic version"):
        load_definition(path)


def test_manifest_rejects_secret_bearing_keys(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text(
        (MANIFEST_ROOT / "company-description.yaml").read_text(encoding="utf-8")
        + "api_key: do-not-store-this\n",
        encoding="utf-8",
    )
    with pytest.raises(DefinitionError, match="secret-bearing key"):
        load_definition(path)
