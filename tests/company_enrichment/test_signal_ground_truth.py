from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
from pathlib import Path

import pytest
import yaml

from scripts.company_enrichment.contracts import (
    CompanyDossier, EvidenceRef, FieldAssertion, Visibility,
)
from scripts.company_enrichment.signal_evidence import (
    load_signal_dossier, save_signal_dossier, signal_dossier,
)
from scripts.company_enrichment.signal_ground_truth import (
    ALL_IDS, DEVELOPMENT_IDS, HOLDOUT_IDS, EvaluatorSignalDataset, SignalDataset,
    SignalGroundTruthRecord, dataset_hash, dataset_loader,
    issue_evaluator_dataset_capability, load_signal_dataset, validate_weights,
)


ENRICHMENT = "running-ads-offer-intelligence"
WEIGHTS = {"status": Decimal(".6"), "landing_page": Decimal(".2"), "offer": Decimal(".2")}
NOW = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)
PROMPT = "# Running ads\n\nReturn cited ads facts using only the supplied Evidence."


_LIBRARY_URLS = {
    "google": "https://adstransparency.google.com/advertiser/AR-{seed}?region=anywhere",
    "meta": "https://www.facebook.com/ads/library/?view_all_page_id={seed}",
}


def _ref(company_id: str, name: str, excerpt: str) -> EvidenceRef:
    """Fixture Evidence; ``google``/``meta`` names get ad-library URLs so the ads
    contract treats them as channel evidence, everything else is a website page."""
    seed = f"{company_id}-{name}"
    url = _LIBRARY_URLS.get(name, "https://example.test/{seed}").format(seed=seed)
    return EvidenceRef(
        f"ev-{seed}", url, NOW,
        sha256(excerpt.encode("utf-8")).hexdigest(), excerpt,
    )


def base_dossier(company_id: str) -> CompanyDossier:
    evidence = (_ref(company_id, "about", f"About {company_id}."),)
    assertion = FieldAssertion(
        "identity", company_id, (evidence[0].evidence_id,), 0.8, Visibility.MESSAGE_SAFE,
    )
    return CompanyDossier(company_id, "1.0", (assertion,), evidence, ("ads",))


def build_signal_repo(
    root: Path, *, enrichment_id: str = ENRICHMENT, weights=WEIGHTS,
    ground_truth=None,
) -> Path:
    """Write base dossiers, signal dossiers, split, rubric, prompt, and ground truth."""
    signal_root = root / "benchmarks" / "signals" / enrichment_id
    (root / "benchmarks" / "dossiers").mkdir(parents=True, exist_ok=True)
    (signal_root / "ground-truth").mkdir(parents=True, exist_ok=True)
    for company_id in ALL_IDS:
        base = base_dossier(company_id)
        save_signal_dossier(root / "benchmarks" / "dossiers" / f"{company_id}.yaml", base)
        merged = signal_dossier(
            company_id, base, (_ref(company_id, "google", '{"running_ads": true}'),),
        )
        save_signal_dossier(signal_root / f"{company_id}.yaml", merged)
        record = {
            "company_id": company_id,
            "channels": {"google": {
                "status": "active", "evidence_ids": [f"ev-{company_id}-google"],
            }},
            "as_of": "2026-08-18",
        }
        if ground_truth is not None:
            record = ground_truth(company_id, record)
        (signal_root / "ground-truth" / f"{company_id}.yaml").write_text(
            yaml.safe_dump(record, sort_keys=True), encoding="utf-8",
        )
    (signal_root / "split.yaml").write_text(yaml.safe_dump({
        "development": list(DEVELOPMENT_IDS), "holdout": list(HOLDOUT_IDS),
    }), encoding="utf-8")
    (signal_root / "rubric.yaml").write_text(yaml.safe_dump({
        "weights": {key: float(value) for key, value in weights.items()},
        "threshold": 0.90,
    }), encoding="utf-8")
    prompt = root / "prompts" / "company-enrichment" / f"{enrichment_id}.md"
    prompt.parent.mkdir(parents=True, exist_ok=True)
    prompt.write_text(PROMPT, encoding="utf-8")
    return signal_root


def load_dossiers(root: Path, enrichment_id: str = ENRICHMENT) -> dict[str, CompanyDossier]:
    signal_root = root / "benchmarks" / "signals" / enrichment_id
    return {cid: load_signal_dossier(signal_root / f"{cid}.yaml") for cid in ALL_IDS}


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    build_signal_repo(tmp_path)
    return tmp_path


def test_public_dataset_exposes_only_development_records(repo: Path):
    dataset = load_signal_dataset(
        repo, load_dossiers(repo), enrichment_id=ENRICHMENT, weights=WEIGHTS,
    )

    assert isinstance(dataset, SignalDataset)
    assert dataset.enrichment_id == ENRICHMENT
    assert set(dataset.records) == set(DEVELOPMENT_IDS)
    assert dataset.development_ids == DEVELOPMENT_IDS
    assert dataset.holdout_ids == HOLDOUT_IDS
    assert set(dataset.holdout_manifest) == set(HOLDOUT_IDS)
    record = dataset.records["saas-01"]
    assert isinstance(record, SignalGroundTruthRecord)
    assert record.body["channels"]["google"]["status"] == "active"
    assert record.all_evidence_ids == {"ev-saas-01-google"}
    with pytest.raises(TypeError):
        record.body["channels"]["google"]["status"] = "inactive"


def test_evaluator_capability_unseals_all_records(repo: Path):
    loader = dataset_loader(ENRICHMENT, WEIGHTS)
    dossiers = load_dossiers(repo)
    public = loader(repo, dossiers)
    sealed = loader(repo, dossiers, capability=issue_evaluator_dataset_capability())

    assert isinstance(sealed, EvaluatorSignalDataset)
    assert set(sealed.records) == set(ALL_IDS)
    assert sealed.public.dataset_hash == public.dataset_hash
    with pytest.raises(PermissionError):
        loader(repo, dossiers, capability=object())


def test_split_is_locked(repo: Path):
    split = repo / "benchmarks/signals" / ENRICHMENT / "split.yaml"
    split.write_text(yaml.safe_dump({
        "development": ["saas-01", "saas-02", "saas-03", "saas-05", "saas-07", "saas-09"],
        "holdout": ["saas-04", "saas-06", "saas-08", "saas-10"],
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="split drift"):
        load_signal_dataset(repo, load_dossiers(repo), enrichment_id=ENRICHMENT, weights=WEIGHTS)


def test_weights_must_total_one_and_match_the_spec(repo: Path):
    with pytest.raises(ValueError, match="total 1.0"):
        validate_weights({"status": Decimal(".6"), "offer": Decimal(".3")})
    with pytest.raises(ValueError, match="total 1.0"):
        load_signal_dataset(
            repo, load_dossiers(repo), enrichment_id=ENRICHMENT,
            weights={"status": Decimal(".6"), "offer": Decimal(".3")},
        )
    rubric = repo / "benchmarks/signals" / ENRICHMENT / "rubric.yaml"
    rubric.write_text(yaml.safe_dump({
        "weights": {"status": 0.5, "landing_page": 0.3, "offer": 0.2}, "threshold": 0.90,
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="do not match the spec"):
        load_signal_dataset(repo, load_dossiers(repo), enrichment_id=ENRICHMENT, weights=WEIGHTS)


def test_dataset_hash_changes_when_a_signal_file_byte_changes(repo: Path):
    signal_root = repo / "benchmarks/signals" / ENRICHMENT
    before = dataset_hash(signal_root)
    holdout_before = load_signal_dataset(
        repo, load_dossiers(repo), enrichment_id=ENRICHMENT, weights=WEIGHTS,
    ).holdout_hash

    path = signal_root / "saas-03.yaml"
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    after = load_signal_dataset(
        repo, load_dossiers(repo), enrichment_id=ENRICHMENT, weights=WEIGHTS,
    )
    assert after.dataset_hash != before
    assert after.holdout_hash != holdout_before


def test_dataset_hash_changes_when_ground_truth_changes(repo: Path):
    signal_root = repo / "benchmarks/signals" / ENRICHMENT
    before = dataset_hash(signal_root)
    path = signal_root / "ground-truth" / "saas-06.yaml"
    path.write_text(path.read_text(encoding="utf-8").replace("2026-08-18", "2026-08-19"),
                    encoding="utf-8")
    assert dataset_hash(signal_root) != before


def test_ground_truth_must_cite_signal_dossier_evidence(tmp_path: Path):
    def poison(company_id, record):
        if company_id == "saas-08":
            record["channels"]["google"]["evidence_ids"] = ["ev-not-in-dossier"]
        return record
    build_signal_repo(tmp_path, ground_truth=poison)
    with pytest.raises(ValueError, match="saas-08 cites Evidence absent"):
        load_signal_dataset(
            tmp_path, load_dossiers(tmp_path), enrichment_id=ENRICHMENT, weights=WEIGHTS,
        )


def test_missing_signal_file_fails_closed(repo: Path):
    (repo / "benchmarks/signals" / ENRICHMENT / "saas-10.yaml").unlink()
    with pytest.raises(ValueError, match="signal dossier files must be exactly"):
        dataset_hash(repo / "benchmarks/signals" / ENRICHMENT)
