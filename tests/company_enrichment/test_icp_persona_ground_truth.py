from dataclasses import FrozenInstanceError
from datetime import datetime
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
from scripts.company_enrichment.icp_persona_ground_truth import load_icp_dataset


ROOT = Path(__file__).resolve().parents[2]
EXPECTED_IDS = {f"saas-{index:02d}" for index in range(1, 11)}
EXPECTED_PRIMARY = {
    "saas-01": ("Marketing agencies", "automated reporting", "multi-channel client campaigns"),
    "saas-02": ("Enterprises", "composable workflow automation", "business processes and AI orchestration"),
    "saas-03": ("B2B sales teams", "shared deal workspaces", "complex buying journeys"),
    "saas-04": ("Manufacturers", "product cost and manufacturability analysis", "design and sourcing decisions"),
    "saas-05": ("Regulated enterprises and government agencies", "governed data archiving", "compliance and AI readiness"),
    "saas-06": ("Procurement teams", "predictive sourcing automation", "supplier negotiations"),
    "saas-07": ("HR leaders", "continuous performance management", "enterprise workforces"),
    "saas-08": ("Enterprise IT teams", "incident intelligence and automation", "IT operations"),
    "saas-09": ("Marketing teams", "branded link and QR management", "digital campaigns"),
    "saas-10": ("Commercial real-estate lenders", "automated loan administration", "construction finance workflows"),
}


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
    destination = tmp_path / "benchmarks" / "icp-persona"
    shutil.copytree(ROOT / "benchmarks" / "icp-persona", destination)
    return tmp_path


def test_dataset_is_exact_ten_with_locked_six_four_split(dossiers):
    dataset = load_icp_dataset(ROOT, dossiers)
    assert set(dataset.records) == EXPECTED_IDS
    assert dataset.development_ids == (
        "saas-01", "saas-02", "saas-04", "saas-05", "saas-07", "saas-09",
    )
    assert dataset.holdout_ids == ("saas-03", "saas-06", "saas-08", "saas-10")


def test_every_expected_component_closes_over_dossier_evidence(dossiers):
    dataset = load_icp_dataset(ROOT, dossiers)
    for record in dataset.records.values():
        assert record.all_evidence_ids <= {
            item.evidence_id for item in dossiers[record.company_id].evidence
        }


def test_primary_targets_and_saas_01_secondaries_match_human_review(dossiers):
    dataset = load_icp_dataset(ROOT, dossiers)
    for company_id, (buyer, need, object_) in EXPECTED_PRIMARY.items():
        primary = dataset.records[company_id].primary_icp
        assert (primary.buyer, primary.need, primary.object) == (buyer, need, object_)

    assert [
        (item.buyer, item.need, item.object)
        for item in dataset.records["saas-01"].secondary_icps
    ] == [
        ("SEO agencies", "organic-search performance reporting", "client accounts"),
        ("Paid-media agencies", "cross-channel advertising performance reporting", "clients"),
    ]
    assert all(
        not record.secondary_icps
        for company_id, record in dataset.records.items()
        if company_id != "saas-01"
    )


def test_rubric_and_records_are_immutable(dossiers):
    dataset = load_icp_dataset(ROOT, dossiers)
    assert dataset.rubric.weights == {
        "buyer": Decimal(".25"),
        "need": Decimal(".20"),
        "object": Decimal(".20"),
        "citation": Decimal(".20"),
        "persona": Decimal(".10"),
        "readability": Decimal(".05"),
    }
    assert dataset.rubric.threshold == Decimal(".90")
    with pytest.raises(TypeError):
        dataset.records["saas-11"] = dataset.records["saas-01"]
    with pytest.raises(FrozenInstanceError):
        dataset.rubric.threshold = Decimal(".80")


@pytest.mark.parametrize(
    ("path", "mutate", "message"),
    [
        ("split.yaml", lambda value: value["holdout"].append("saas-01"), "overlap"),
        ("split.yaml", lambda value: value["development"].remove("saas-09"), "split"),
        ("rubric.yaml", lambda value: value["weights"].update({"buyer": 0.24}), "total"),
        ("rubric.yaml", lambda value: value.update({"threshold": 0.89}), "0.90"),
        (
            "ground-truth/saas-01.yaml",
            lambda value: value["acceptable_aliases"].update({"primary.buyer": []}),
            "aliases",
        ),
        (
            "ground-truth/saas-01.yaml",
            lambda value: value["evidence_by_component"].update(
                {"primary.buyer": ["ev-not-retained"]}
            ),
            "Evidence",
        ),
        (
            "ground-truth/saas-01.yaml",
            lambda value: value["expected"]["secondary_icps"].append(
                {
                    "buyer": "Invented buyers",
                    "need": "invented work",
                    "object": "invented use cases",
                    "evidence_ids": ["ev-b852e22ac88f1aad"],
                }
            ),
            "at most two",
        ),
    ],
)
def test_rejects_dataset_drift(tmp_path, dossiers, path, mutate, message):
    root = _copy_dataset(tmp_path)
    target = root / "benchmarks" / "icp-persona" / path
    value = yaml.safe_load(target.read_text(encoding="utf-8"))
    mutate(value)
    target.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        load_icp_dataset(root, dossiers)


def test_rejects_missing_or_extra_ground_truth_files(tmp_path, dossiers):
    missing_root = _copy_dataset(tmp_path / "missing")
    (missing_root / "benchmarks" / "icp-persona" / "ground-truth" / "saas-10.yaml").unlink()
    with pytest.raises(ValueError, match="exactly saas-01 through saas-10"):
        load_icp_dataset(missing_root, dossiers)

    extra_root = _copy_dataset(tmp_path / "extra")
    shutil.copy(
        extra_root / "benchmarks" / "icp-persona" / "ground-truth" / "saas-10.yaml",
        extra_root / "benchmarks" / "icp-persona" / "ground-truth" / "saas-11.yaml",
    )
    with pytest.raises(ValueError, match="exactly saas-01 through saas-10"):
        load_icp_dataset(extra_root, dossiers)


def test_dataset_hash_covers_file_bytes_and_referenced_evidence_hashes(tmp_path, dossiers):
    baseline = load_icp_dataset(ROOT, dossiers).dataset_hash

    root = _copy_dataset(tmp_path)
    rubric = root / "benchmarks" / "icp-persona" / "rubric.yaml"
    rubric.write_bytes(rubric.read_bytes() + b"\n")
    assert load_icp_dataset(root, dossiers).dataset_hash != baseline

    changed = dict(dossiers)
    dossier = changed["saas-01"]
    first = next(
        item
        for item in dossier.evidence
        if item.evidence_id == "ev-f4256ff7de9e092b"
    )
    replacement = EvidenceRef(
        first.evidence_id,
        first.url,
        first.retrieved_at,
        "0" * 64,
        first.excerpt,
    )
    changed["saas-01"] = CompanyDossier(
        dossier.company_id,
        dossier.schema_version,
        dossier.assertions,
        tuple(replacement if item.evidence_id == first.evidence_id else item for item in dossier.evidence),
        dossier.unknowns,
        dossier.corrections,
    )
    assert load_icp_dataset(ROOT, changed).dataset_hash != baseline
