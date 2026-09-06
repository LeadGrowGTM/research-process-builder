from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
import shutil

import pytest
import yaml

from scripts.company_enrichment.contracts import (
    CompanyDossier,
    EvidenceRef,
    FieldAssertion,
    Visibility,
)
from scripts.company_enrichment.description_growth_ground_truth import (
    FactComponent,
    GrowthGroundTruth,
    GrowthSignal,
    load_description_growth_dataset,
)


ROOT = Path(__file__).resolve().parents[2]
EXPECTED_ID_SEQUENCE = tuple(f"saas-{index:02d}" for index in range(1, 11))
DEVELOPMENT_IDS = (
    "saas-01", "saas-02", "saas-04", "saas-05", "saas-07", "saas-09",
)
HOLDOUT_IDS = ("saas-03", "saas-06", "saas-08", "saas-10")


def _load_dossier(path: Path) -> CompanyDossier:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    evidence = tuple(
        EvidenceRef(
            item["evidence_id"],
            item["url"],
            datetime.fromisoformat(item["retrieved_at"]),
            item["content_hash"],
            item["excerpt"],
        )
        for item in value["evidence"]
    )
    assertions = tuple(
        FieldAssertion(
            item["field"],
            item["value"],
            tuple(item["evidence_ids"]),
            item["confidence"],
            Visibility(item["visibility"]),
        )
        for item in value["assertions"]
    )
    return CompanyDossier(
        value["company_id"],
        value["schema_version"],
        assertions,
        evidence,
        tuple(value.get("unknowns", ())),
    )


@pytest.fixture(scope="module")
def dossiers():
    return {
        path.stem: _load_dossier(path)
        for path in sorted((ROOT / "benchmarks" / "dossiers").glob("saas-*.yaml"))
    }


def _copy_dataset(tmp_path: Path) -> Path:
    destination = tmp_path / "benchmarks" / "description-growth"
    shutil.copytree(ROOT / "benchmarks" / "description-growth", destination)
    return destination


def test_real_dataset_loads(dossiers):
    dataset = load_description_growth_dataset(ROOT, dossiers)
    assert dataset.all_ids == EXPECTED_ID_SEQUENCE
    assert dataset.development_ids == DEVELOPMENT_IDS
    assert dataset.holdout_ids == HOLDOUT_IDS
    assert set(dataset.records) == set(EXPECTED_ID_SEQUENCE)
    assert len(dataset.dataset_hash) == 64
    assert sum(
        dataset.description_rubric.weights.values(), Decimal("0"),
    ) == Decimal("1.0")
    assert sum(
        dataset.growth_rubric.weights.values(), Decimal("0"),
    ) == Decimal("1.0")


def test_real_dataset_spot_checks(dossiers):
    dataset = load_description_growth_dataset(ROOT, dossiers)
    saas_01 = dataset.records["saas-01"]
    assert saas_01.description.identity.canonical == "AgencyAnalytics"
    assert saas_01.growth.verdict == "growth_signals"
    assert "customer_scale" in saas_01.growth.kinds
    funding = [
        signal for signal in dataset.records["saas-04"].growth.signals
        if signal.kind == "funding"
    ]
    assert funding and funding[0].observed_at.isoformat() == "2023-10-19"
    assert all(
        signal.source == "dossier" or signal.url
        for record in dataset.records.values()
        for signal in record.growth.signals
    )


def test_every_dossier_quote_is_verbatim(dossiers):
    dataset = load_description_growth_dataset(ROOT, dossiers)
    for record in dataset.records.values():
        evidence = {
            item.evidence_id: item
            for item in dossiers[record.company_id].evidence
        }
        for signal in record.growth.signals:
            if signal.source == "dossier":
                assert any(
                    signal.quote in evidence[evidence_id].excerpt
                    for evidence_id in signal.evidence_ids
                )


def test_dataset_hash_changes_when_ground_truth_changes(tmp_path, dossiers):
    dataset_root = _copy_dataset(tmp_path)
    baseline = load_description_growth_dataset(tmp_path, dossiers)
    record_path = dataset_root / "ground-truth" / "saas-01.yaml"
    value = yaml.safe_load(record_path.read_text(encoding="utf-8"))
    value["description"]["audience"]["aliases"].append("ad agencies")
    record_path.write_text(yaml.safe_dump(value), encoding="utf-8")
    changed = load_description_growth_dataset(tmp_path, dossiers)
    assert changed.dataset_hash != baseline.dataset_hash


def test_rejects_missing_ground_truth_file(tmp_path, dossiers):
    dataset_root = _copy_dataset(tmp_path)
    (dataset_root / "ground-truth" / "saas-10.yaml").unlink()
    with pytest.raises(ValueError, match="exactly saas-01 through saas-10"):
        load_description_growth_dataset(tmp_path, dossiers)


def test_rejects_split_drift(tmp_path, dossiers):
    dataset_root = _copy_dataset(tmp_path)
    split_path = dataset_root / "split.yaml"
    value = yaml.safe_load(split_path.read_text(encoding="utf-8"))
    value["development"], value["holdout"] = (
        value["development"][:-1] + [value["holdout"][0]],
        value["holdout"][1:] + [value["development"][-1]],
    )
    split_path.write_text(yaml.safe_dump(value), encoding="utf-8")
    with pytest.raises(ValueError, match="split drift"):
        load_description_growth_dataset(tmp_path, dossiers)


def test_rejects_rubric_drift(tmp_path, dossiers):
    dataset_root = _copy_dataset(tmp_path)
    rubric_path = dataset_root / "rubric.yaml"
    value = yaml.safe_load(rubric_path.read_text(encoding="utf-8"))
    value["growth"]["weights"]["verdict"] = 0.5
    rubric_path.write_text(yaml.safe_dump(value), encoding="utf-8")
    with pytest.raises(ValueError, match="approved rubric"):
        load_description_growth_dataset(tmp_path, dossiers)


def test_rejects_non_verbatim_quote(tmp_path, dossiers):
    dataset_root = _copy_dataset(tmp_path)
    record_path = dataset_root / "ground-truth" / "saas-01.yaml"
    value = yaml.safe_load(record_path.read_text(encoding="utf-8"))
    value["growth"]["signals"][0]["quote"] = "an invented quote"
    record_path.write_text(yaml.safe_dump(value), encoding="utf-8")
    with pytest.raises(ValueError, match="verbatim excerpt"):
        load_description_growth_dataset(tmp_path, dossiers)


def test_rejects_unknown_evidence_id(tmp_path, dossiers):
    dataset_root = _copy_dataset(tmp_path)
    record_path = dataset_root / "ground-truth" / "saas-01.yaml"
    value = yaml.safe_load(record_path.read_text(encoding="utf-8"))
    value["description"]["identity"]["evidence_ids"] = ["ev-0000000000000000"]
    record_path.write_text(yaml.safe_dump(value), encoding="utf-8")
    with pytest.raises(ValueError, match="absent from the dossier"):
        load_description_growth_dataset(tmp_path, dossiers)


def test_component_aliases_must_include_canonical():
    with pytest.raises(ValueError, match="canonical"):
        FactComponent(
            canonical="reporting software",
            aliases=("analytics platform",),
            evidence_ids=("ev-1234567812345678",),
        )


def test_no_signal_verdict_rejects_listed_signals():
    signal = GrowthSignal(
        kind="hiring",
        source="page-signals-v1",
        observed_at=date(2026, 8, 25),
        url="https://example.com/careers",
    )
    with pytest.raises(ValueError, match="no_signal"):
        GrowthGroundTruth(verdict="no_signal", signals=(signal,))


def test_page_signal_requires_url():
    with pytest.raises(ValueError, match="require a url"):
        GrowthSignal(
            kind="hiring",
            source="page-signals-v1",
            observed_at=date(2026, 8, 25),
        )


def test_dossier_signal_requires_quote_and_evidence():
    with pytest.raises(ValueError, match="evidence_ids and a quote"):
        GrowthSignal(
            kind="funding",
            source="dossier",
            observed_at=date(2026, 8, 12),
        )
