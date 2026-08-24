from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from scripts.company_enrichment.contracts import (
    CompanyDossier, EvidenceRef, FieldAssertion, Visibility,
)
from scripts.company_enrichment.signal_evidence import (
    load_cached_dossiers, load_signal_dossier, save_signal_dossier, signal_dossier,
    signal_dossier_path,
)


ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)


def _ref(evidence_id: str, excerpt: str = "excerpt", hash_char: str = "c") -> EvidenceRef:
    return EvidenceRef(
        evidence_id, f"https://example.test/{evidence_id}", NOW, hash_char * 64, excerpt,
    )


def _base() -> CompanyDossier:
    evidence = (_ref("ev-base-1", "About Example.", "a"), _ref("ev-base-2", "Reviews.", "b"))
    assertion = FieldAssertion(
        "identity", "Example", ("ev-base-1",), 0.8, Visibility.MESSAGE_SAFE,
    )
    return CompanyDossier("saas-01", "1.0", (assertion,), evidence, ("news",))


def test_signal_dossier_appends_new_refs_and_keeps_base_untouched():
    base = _base()
    ads = FieldAssertion(
        "ads", {"google": {"status": "active"}}, ("ev-google", "ev-base-1"), 0.9,
        Visibility.MESSAGE_SAFE,
    )
    merged = signal_dossier(
        "saas-01", base, (_ref("ev-google", "google ads"),), (ads,), ("launches",),
    )

    assert merged.schema_version == base.schema_version
    assert [item.evidence_id for item in merged.evidence] == [
        "ev-base-1", "ev-base-2", "ev-google",
    ]
    assert merged.assertions == (*base.assertions, ads)
    assert merged.unknowns == ("news", "launches")
    assert base.evidence == (_ref("ev-base-1", "About Example.", "a"),
                             _ref("ev-base-2", "Reviews.", "b"))
    assert base.unknowns == ("news",)


def test_signal_dossier_dedups_identical_refs_and_rejects_collisions():
    base = _base()
    same = _ref("ev-base-1", "About Example.", "a")
    merged = signal_dossier("saas-01", base, (same, _ref("ev-new"), _ref("ev-new")))
    assert [item.evidence_id for item in merged.evidence] == [
        "ev-base-1", "ev-base-2", "ev-new",
    ]

    with pytest.raises(ValueError, match="collision"):
        signal_dossier("saas-01", base, (_ref("ev-base-1", "different text", "a"),))
    with pytest.raises(ValueError, match="collision"):
        signal_dossier("saas-01", base, (_ref("ev-new"), _ref("ev-new", "other")))


def test_signal_dossier_requires_matching_company_and_cited_evidence():
    base = _base()
    with pytest.raises(ValueError, match="company ID"):
        signal_dossier("saas-02", base, ())
    orphan = FieldAssertion("ads", "x", ("ev-missing",), 0.9, Visibility.MESSAGE_SAFE)
    with pytest.raises(ValueError, match="missing evidence"):
        signal_dossier("saas-01", base, (), (orphan,))


def test_signal_dossier_yaml_round_trip(tmp_path: Path):
    merged = signal_dossier(
        "saas-01", _base(), (_ref("ev-google", "google ads"),),
        (FieldAssertion("ads", {"google": {"status": "active", "started_on": None}},
                        ("ev-google",), 0.9, Visibility.MESSAGE_SAFE),),
        ("launches",),
    )
    path = signal_dossier_path(tmp_path, "running-ads-offer-intelligence", "saas-01")
    assert path == tmp_path / "benchmarks/signals/running-ads-offer-intelligence/saas-01.yaml"

    save_signal_dossier(path, merged)
    loaded = load_signal_dossier(path)

    assert loaded == merged
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert value["schema_version"] == "1.0"
    assert value["corrections"] == []
    assert [item["evidence_id"] for item in value["evidence"]] == [
        "ev-base-1", "ev-base-2", "ev-google",
    ]
    assert not list(path.parent.glob("*.tmp"))


def test_load_cached_dossiers_reads_frozen_base_dossiers():
    dossiers = load_cached_dossiers(ROOT, ("saas-01", "saas-10"))
    assert set(dossiers) == {"saas-01", "saas-10"}
    assert dossiers["saas-01"].company_id == "saas-01"
    assert dossiers["saas-01"].evidence
