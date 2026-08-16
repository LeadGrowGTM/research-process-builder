from dataclasses import FrozenInstanceError
from datetime import datetime
from decimal import Decimal
from pathlib import Path
import shutil

import pytest
import yaml

from scripts.company_enrichment import icp_persona_ground_truth as ground_truth
from scripts.company_enrichment.contracts import (
    CompanyDossier,
    EvidenceRef,
    FieldAssertion,
    Visibility,
)
from scripts.company_enrichment.icp_persona_ground_truth import dataset_hash, load_icp_dataset


ROOT = Path(__file__).resolve().parents[2]
EXPECTED_IDS = {f"saas-{index:02d}" for index in range(1, 11)}
EXPECTED_ID_SEQUENCE = tuple(f"saas-{index:02d}" for index in range(1, 11))
DEVELOPMENT_IDS = (
    "saas-01", "saas-02", "saas-04", "saas-05", "saas-07", "saas-09",
)
HOLDOUT_IDS = ("saas-03", "saas-06", "saas-08", "saas-10")
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


def _evaluator_dataset(root, dossiers):
    capability = ground_truth.issue_evaluator_dataset_capability()
    return ground_truth.load_icp_dataset_for_evaluator(
        root, dossiers, capability=capability,
    )


def test_dataset_is_exact_ten_with_locked_six_four_split(dossiers):
    dataset = load_icp_dataset(ROOT, dossiers)
    assert dataset.all_ids == EXPECTED_ID_SEQUENCE
    assert tuple(dataset.records) == DEVELOPMENT_IDS
    assert dataset.development_ids == DEVELOPMENT_IDS
    assert dataset.holdout_ids == HOLDOUT_IDS


def test_ordinary_dataset_cannot_enumerate_or_access_holdout_answers(dossiers):
    dataset = load_icp_dataset(ROOT, dossiers)
    assert set(dataset.records).isdisjoint(HOLDOUT_IDS)
    with pytest.raises(KeyError):
        dataset.records["saas-03"]
    assert not hasattr(dataset, "holdout_records")
    assert set(dataset.holdout_manifest) == set(HOLDOUT_IDS)
    assert all(len(item_hash) == 64 for item_hash in dataset.holdout_manifest.values())
    assert len(dataset.holdout_hash) == 64


def test_capability_gated_evaluator_dataset_can_access_all_ten(dossiers):
    with pytest.raises(PermissionError, match="evaluator capability"):
        ground_truth.load_icp_dataset_for_evaluator(
            ROOT, dossiers, capability=object(),
        )
    evaluator_dataset = _evaluator_dataset(ROOT, dossiers)
    assert set(evaluator_dataset.records) == EXPECTED_IDS
    assert evaluator_dataset.public.records.keys() == dict.fromkeys(DEVELOPMENT_IDS).keys()


def test_every_expected_component_closes_over_dossier_evidence(dossiers):
    dataset = _evaluator_dataset(ROOT, dossiers)
    for record in dataset.records.values():
        assert record.all_evidence_ids <= {
            item.evidence_id for item in dossiers[record.company_id].evidence
        }


def test_primary_targets_match_human_review(dossiers):
    dataset = _evaluator_dataset(ROOT, dossiers)
    for company_id, (buyer, need, object_) in EXPECTED_PRIMARY.items():
        primary = dataset.records[company_id].primary_icp
        assert (primary.buyer, primary.need, primary.object) == (buyer, need, object_)

    assert all(
        not record.secondary_icps
        for record in dataset.records.values()
    )


def test_saas_01_evidence_supports_channels_but_not_specialized_buyers(dossiers):
    evidence_text = " ".join(
        item.excerpt for item in dossiers["saas-01"].evidence
    ).casefold()
    assert "marketing agencies" in evidence_text
    assert "seo, ppc" in evidence_text
    assert "seo agencies" not in evidence_text
    assert "paid-media agencies" not in evidence_text
    assert "paid media agencies" not in evidence_text
    assert not load_icp_dataset(ROOT, dossiers).records["saas-01"].secondary_icps


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
            lambda value: value["expected"].update(
                {"secondary_icps": [
                    {
                    "buyer": "Invented buyers",
                    "need": "invented work",
                    "object": "invented use cases",
                    "evidence_ids": ["ev-b852e22ac88f1aad"],
                    }
                    for _ in range(3)
                ]}
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


def _secondary_with(change):
    value = {
        "buyer": "Supported buyers",
        "need": "supported work",
        "object": "supported use cases",
        "evidence_ids": ["ev-f4256ff7de9e092b"],
    }
    change(value)
    return [value]


@pytest.mark.parametrize(
    ("filename", "change", "message"),
    [
        (
            "saas-01.yaml",
            lambda value: value["expected"].update({"unexpected": True}),
            "expected output keys must exactly",
        ),
        (
            "saas-01.yaml",
            lambda value: value["expected"].pop("outcomes"),
            "expected output keys must exactly",
        ),
        (
            "saas-01.yaml",
            lambda value: value["expected"]["primary_icp"].update({"unexpected": True}),
            "primary ICP keys must exactly",
        ),
        (
            "saas-01.yaml",
            lambda value: value["expected"]["primary_icp"].pop("buyer"),
            "primary ICP keys must exactly",
        ),
        (
            "saas-01.yaml",
            lambda value: value["expected"].update({
                "secondary_icps": _secondary_with(
                    lambda item: item.update({"unexpected": True})
                )
            }),
            "secondary ICP keys must exactly",
        ),
        (
            "saas-01.yaml",
            lambda value: value["expected"].update({
                "secondary_icps": _secondary_with(lambda item: item.pop("object"))
            }),
            "secondary ICP keys must exactly",
        ),
        (
            "saas-01.yaml",
            lambda value: value["expected"]["outcomes"][0].update({"unexpected": True}),
            "outcome keys must exactly",
        ),
        (
            "saas-01.yaml",
            lambda value: value["expected"]["outcomes"][0].pop("text"),
            "outcome keys must exactly",
        ),
        (
            "saas-02.yaml",
            lambda value: value["expected"]["observed_personas"][0].update({"unexpected": True}),
            "observed persona keys must exactly",
        ),
        (
            "saas-02.yaml",
            lambda value: value["expected"]["observed_personas"][0].pop("role"),
            "observed persona keys must exactly",
        ),
        (
            "saas-01.yaml",
            lambda value: value["expected"]["inferred_personas"][0].update({"unexpected": True}),
            "inferred persona keys must exactly",
        ),
        (
            "saas-01.yaml",
            lambda value: value["expected"]["inferred_personas"][0].pop("role"),
            "inferred persona keys must exactly",
        ),
    ],
)
def test_ground_truth_rejects_unknown_or_missing_nested_keys(
    tmp_path, dossiers, filename, change, message,
):
    root = _copy_dataset(tmp_path)
    target = root / "benchmarks" / "icp-persona" / "ground-truth" / filename
    value = yaml.safe_load(target.read_text(encoding="utf-8"))
    change(value)
    target.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        load_icp_dataset(root, dossiers)


def test_dataset_hash_rejects_partial_record_mappings(dossiers):
    public = load_icp_dataset(ROOT, dossiers)
    with pytest.raises(ValueError, match="exactly ten records"):
        dataset_hash(
            ROOT / "benchmarks" / "icp-persona",
            public.records,
            dossiers,
        )


def test_dataset_hash_rejects_missing_or_extra_files(tmp_path, dossiers):
    records = _evaluator_dataset(ROOT, dossiers).records

    missing_root = _copy_dataset(tmp_path / "missing")
    missing_dataset = missing_root / "benchmarks" / "icp-persona"
    (missing_dataset / "ground-truth" / "saas-10.yaml").unlink()
    with pytest.raises(ValueError, match="exactly saas-01 through saas-10"):
        dataset_hash(missing_dataset, records, dossiers)

    extra_root = _copy_dataset(tmp_path / "extra")
    extra_dataset = extra_root / "benchmarks" / "icp-persona"
    shutil.copy(
        extra_dataset / "ground-truth" / "saas-10.yaml",
        extra_dataset / "ground-truth" / "saas-11.yaml",
    )
    with pytest.raises(ValueError, match="exactly saas-01 through saas-10"):
        dataset_hash(extra_dataset, records, dossiers)
