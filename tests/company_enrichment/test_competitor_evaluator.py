from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
from pathlib import Path

import pytest

from scripts.company_enrichment.competitor_contracts import Competitor
from scripts.company_enrichment.competitor_evaluator import (
    COMPETITOR_ENRICHMENT_ID, COMPETITOR_WEIGHTS, entry_is_cited, score_competitors,
    subject_identity, validate_competitor_record,
)
from scripts.company_enrichment.contracts import (
    CompanyDossier, EvidenceRef, FieldAssertion, Visibility,
)
from scripts.company_enrichment.signal_ground_truth import (
    SignalGroundTruthRecord, dataset_loader, load_signal_dataset,
)
from tests.company_enrichment.test_signal_ground_truth import build_signal_repo, load_dossiers

NOW = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)


def _ref(name: str, excerpt: str, url: str = "https://g2.com/compare") -> EvidenceRef:
    return EvidenceRef(f"ev-{name}", url, NOW, sha256(excerpt.encode()).hexdigest(), excerpt)


DOSSIER = CompanyDossier("saas-01", "1.0", (
    FieldAssertion("identity", "AgencyAnalytics", ("ev-about",), 0.8, Visibility.MESSAGE_SAFE),
), (
    _ref("about", "About AgencyAnalytics", "https://www.agencyanalytics.com/company/about"),
    _ref("own", "AgencyAnalytics vs DashThis vs Whatagraph: compare reporting tools",
         "https://agencyanalytics.com/competitors"),
    _ref("alts", "Top AgencyAnalytics alternatives: TapClicks (tapclicks.com), Swydo",
         "https://blog.example/alternatives"),
    _ref("thread", "Agencies build client reports in Looker Studio instead",
         "https://reddit.com/r/agency"),
    _ref("noise", "Best marketing analytics tools 2026", "https://listicle.example"),
))
RECORD = SignalGroundTruthRecord("saas-01", {
    "named": [
        {"name": "DashThis", "aliases": ["Dash This"], "domain": "dashthis.com"},
        {"name": "Whatagraph", "aliases": [], "domain": "whatagraph.com"},
        {"name": "TapClicks", "aliases": [], "domain": "tapclicks.com"},
    ],
    "inferred": [{"name": "Looker Studio", "aliases": ["Google Data Studio"], "domain": None}],
    "evidence_ids": ["ev-own", "ev-alts", "ev-thread"],
})


def _competitor(name, evidence, *, domain=None, relationship="direct", why="compared"):
    return {"name": name, "domain": domain, "relationship": relationship, "why": why,
            "evidence_ids": list(evidence)}


def _payload(named=(), inferred=(), unknowns=None):
    value = {"competitors": {"named": list(named), "inferred": list(inferred), "conflicts": []}}
    if unknowns is not None:
        value["unknowns"] = unknowns
    return value


PERFECT = _payload(
    [_competitor("DashThis", ["ev-own"]), _competitor("Whatagraph", ["ev-own"]),
     _competitor("TapClicks", ["ev-alts"], domain="tapclicks.com")],
    [_competitor("Looker Studio", ["ev-thread"], relationship="alternative")],
)


def test_perfect_payload_scores_one():
    case = score_competitors(PERFECT, RECORD, DOSSIER)
    assert case.score == Decimal("1") and case.hard_failures == ()
    assert set(case.components) == set(COMPETITOR_WEIGHTS)
    assert sum(COMPETITOR_WEIGHTS.values()) == Decimal("1")


def test_alias_and_domain_matching_are_case_and_punctuation_insensitive():
    payload = _payload(
        [_competitor("dash this, inc.", ["ev-own"]),
         _competitor("Unknown Vendor", ["ev-own"], domain="WWW.Whatagraph.com"),
         _competitor("TapClicks", ["ev-alts"])],
        [_competitor("Google Data Studio", ["ev-thread"], relationship="alternative")],
    )
    case = score_competitors(payload, RECORD, DOSSIER)
    assert case.components["named_set"] == Decimal("1")
    assert case.components["labeling"] == Decimal("1")
    # "Unknown Vendor" is not in ev-own, but its domain is not either -> hallucinated
    assert case.hard_failures == ("hallucinated_competitor",)


def test_named_set_f1_and_labeling_penalize_promotion():
    payload = _payload(
        [_competitor("DashThis", ["ev-own"]),
         _competitor("Looker Studio", ["ev-thread"], relationship="alternative")],  # promoted
        [_competitor("TapClicks", ["ev-alts"])],  # demoted
    )
    case = score_competitors(payload, RECORD, DOSSIER)
    # named payload: DashThis (hit), Looker (miss) -> P=1/2; GT named 3 -> R=1/3
    assert case.components["named_set"] == Decimal("2") * Decimal(".5") * (
        Decimal("1") / Decimal("3")) / (Decimal(".5") + Decimal("1") / Decimal("3"))
    assert case.components["labeling"] == Decimal("1") / Decimal("3")
    assert case.components["citation"] == Decimal("1")
    assert case.hard_failures == ()


def test_citation_fraction_and_hallucination_hard_failure():
    payload = _payload([
        _competitor("DashThis", ["ev-own"]),
        _competitor("Swydo", ["ev-thread"]),  # cited excerpt does not mention Swydo
    ])
    case = score_competitors(payload, RECORD, DOSSIER)
    assert case.components["citation"] == Decimal(".5")
    assert case.hard_failures == ("hallucinated_competitor",)
    assert entry_is_cited(Competitor("Swydo", None, "direct", "w", ("ev-alts",)),
                          {"ev-alts": DOSSIER.evidence[2].excerpt})


def test_subject_listed_as_competitor_is_hard_failure():
    name, domain = subject_identity(DOSSIER)
    assert (name, domain) == ("AgencyAnalytics", "agencyanalytics.com")
    payload = _payload([_competitor("DashThis", ["ev-own"]),
                        _competitor("Agency Analytics", ["ev-own"])])
    case = score_competitors(payload, RECORD, DOSSIER)
    assert "self_competitor" in case.hard_failures
    by_domain = _payload([_competitor("Some Name", ["ev-about"], domain="agencyanalytics.com")])
    assert "self_competitor" in score_competitors(by_domain, RECORD, DOSSIER).hard_failures


def test_zero_ground_truth_all_or_nothing():
    empty = SignalGroundTruthRecord("saas-01", {"named": [], "inferred": [],
                                                "evidence_ids": ["ev-noise"]})
    clean = score_competitors(_payload(unknowns=["competitors"]), empty, DOSSIER)
    assert clean.score == Decimal("1") and clean.hard_failures == ()
    silent = score_competitors(_payload(), empty, DOSSIER)
    assert silent.score == Decimal("0") and silent.hard_failures == ()
    invented = score_competitors(_payload([_competitor("DashThis", ["ev-own"])]), empty, DOSSIER)
    assert invented.score == Decimal("0") and invented.hard_failures == ("invented_competitor",)


def test_empty_payload_against_populated_truth_scores_zero():
    case = score_competitors(_payload(unknowns=["competitors"]), RECORD, DOSSIER)
    assert case.components["named_set"] == Decimal("0")
    assert case.components["citation"] == Decimal("0")
    assert case.components["labeling"] == Decimal("1")  # nothing mislabeled
    assert case.hard_failures == ()


def test_contract_violation_is_hard_failure():
    case = score_competitors({"competitors": {"named": []}}, RECORD, DOSSIER)
    assert case.score == Decimal("0") and case.hard_failures == ("contract_violation",)
    payload = _payload([_competitor("DashThis", ["ev-own"], relationship="rival")])
    assert score_competitors(payload, RECORD, DOSSIER).hard_failures == ("contract_violation",)


def test_validate_competitor_record_shape():
    validate_competitor_record(RECORD, DOSSIER)
    with pytest.raises(ValueError, match="named, inferred, evidence_ids"):
        validate_competitor_record(SignalGroundTruthRecord("saas-01", {"named": [], "evidence_ids": ["ev-own"]}), DOSSIER)
    with pytest.raises(ValueError, match="exactly name, aliases, domain"):
        validate_competitor_record(SignalGroundTruthRecord("saas-01", {
            "named": [{"name": "X"}], "inferred": [], "evidence_ids": ["ev-own"]}), DOSSIER)
    with pytest.raises(ValueError, match="bare host name"):
        validate_competitor_record(SignalGroundTruthRecord("saas-01", {
            "named": [{"name": "X", "aliases": [], "domain": "not a host"}], "inferred": [],
            "evidence_ids": ["ev-own"]}), DOSSIER)


def test_dataset_loader_accepts_competitor_records(tmp_path: Path):
    def ground_truth(company_id, _record_value):
        return {
            "company_id": company_id,
            "named": [{"name": "Rival", "aliases": [], "domain": "rival.example"}],
            "inferred": [],
            "evidence_ids": [f"ev-{company_id}-google"],
        }

    build_signal_repo(tmp_path, enrichment_id=COMPETITOR_ENRICHMENT_ID,
                      weights=COMPETITOR_WEIGHTS, ground_truth=ground_truth)
    loader = dataset_loader(COMPETITOR_ENRICHMENT_ID, COMPETITOR_WEIGHTS,
                            validate_competitor_record)
    dataset = loader(tmp_path, load_dossiers(tmp_path, COMPETITOR_ENRICHMENT_ID))
    assert dataset.records["saas-01"].body["named"][0]["name"] == "Rival"

    def bad(company_id, _record_value):
        value = ground_truth(company_id, _record_value)
        value["named"][0].pop("aliases")
        return value

    other = tmp_path / "bad"
    build_signal_repo(other, enrichment_id=COMPETITOR_ENRICHMENT_ID,
                      weights=COMPETITOR_WEIGHTS, ground_truth=bad)
    with pytest.raises(ValueError, match="exactly name, aliases, domain"):
        load_signal_dataset(other, load_dossiers(other, COMPETITOR_ENRICHMENT_ID),
                            enrichment_id=COMPETITOR_ENRICHMENT_ID, weights=COMPETITOR_WEIGHTS,
                            validate_record=validate_competitor_record)
