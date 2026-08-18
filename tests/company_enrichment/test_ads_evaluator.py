from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from scripts.company_enrichment.ads_evaluator import (
    WEIGHTS, normalized_landing_page, score_ads, token_recall, validate_ads_record,
)
from scripts.company_enrichment.contracts import CompanyDossier, EvidenceRef
from scripts.company_enrichment.signal_ground_truth import (
    SignalGroundTruthRecord, validate_weights,
)


NOW = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)
DOSSIER = CompanyDossier("saas-01", "1.0", (), (
    EvidenceRef("ev-about", "https://example.test/about", NOW, "a" * 64, "about"),
    EvidenceRef("ev-google", "https://adstransparency.google.com/?domain=x", NOW, "b" * 64, "{}"),
    EvidenceRef("ev-meta", "https://www.facebook.com/ads/library/?view_all_page_id=1", NOW,
                "c" * 64, "{}"),
))
GT_FULL = {
    "as_of": "2026-08-18",
    "channels": {
        "google": {"status": "active", "evidence_ids": ["ev-google"]},
        "meta": {
            "status": "active", "landing_page": "https://www.example.com/p/enterprise/",
            "observed_offer": "enterprise agency reporting plan",
            "offer_aliases": ["Enterprise plan demo"], "call_to_action": "Contact us",
            "evidence_ids": ["ev-meta"],
        },
    },
}
GT_STATUS_ONLY = {
    "as_of": "2026-08-18",
    "channels": {
        "google": {"status": "inactive", "evidence_ids": ["ev-google"]},
        "meta": {"status": "unknown"},
    },
}


def _record(body=GT_FULL) -> SignalGroundTruthRecord:
    return SignalGroundTruthRecord("saas-01", body)


def _google(status="active", **overrides):
    entry = {"channel": "google", "status": status, "angle": None, "offer": None,
             "call_to_action": None, "landing_page": None, "evidence_ids": ["ev-google"]}
    entry.update(overrides)
    return entry


def _meta(status="active", **overrides):
    entry = {"channel": "meta", "status": status, "angle": "stop building reports by hand",
             "offer": "the Enterprise agency reporting plan", "call_to_action": "Contact us",
             "landing_page": "https://example.com/p/enterprise", "evidence_ids": ["ev-meta"]}
    entry.update(overrides)
    return entry


def _payload(*channels, unknowns=None):
    payload = {"ads": {"channels": list(channels)}}
    if unknowns is not None:
        payload["unknowns"] = unknowns
    return payload


def test_weights_are_a_valid_rubric():
    assert dict(validate_weights(WEIGHTS)) == {
        "status": Decimal(".6"), "landing_page": Decimal(".2"), "offer": Decimal(".2"),
    }


def test_perfect_payload_scores_one():
    case = score_ads(_payload(_google(), _meta()), _record(), DOSSIER)
    assert case.company_id == "saas-01"
    assert case.hard_failures == ()
    assert case.components == {"status": Decimal("1"), "landing_page": Decimal("1"),
                               "offer": Decimal("1")}
    assert case.score == Decimal("1.0000")


def test_component_weights_apply_per_channel_fractions():
    # google status wrong, meta landing page on a different path, offer via CTA only
    payload = _payload(
        _google("inactive"),
        _meta(landing_page="https://example.com/pricing", offer="powerful platform"),
    )
    case = score_ads(payload, _record(), DOSSIER)
    assert case.components == {"status": Decimal(".5"), "landing_page": Decimal("0"),
                               "offer": Decimal("1")}
    assert case.score == Decimal(".5") * WEIGHTS["status"] + WEIGHTS["offer"]
    assert case.score == Decimal("0.5000")
    assert case.hard_failures == ()


def test_offer_matches_alias_by_token_recall_without_cta():
    payload = _payload(_google(), _meta(offer="Book an enterprise plan demo", call_to_action="Sign up"))
    case = score_ads(payload, _record(), DOSSIER)
    assert case.components["offer"] == Decimal("1")

    payload = _payload(_google(), _meta(offer="a free trial", call_to_action="Sign up"))
    case = score_ads(payload, _record(), DOSSIER)
    assert case.components["offer"] == Decimal("0")
    assert case.score == Decimal("0.8000")


def test_missing_channel_matches_only_when_ground_truth_is_unknown():
    case = score_ads(_payload(_google("inactive")), _record(GT_STATUS_ONLY), DOSSIER)
    assert case.components == {"status": Decimal("1")}
    assert case.score == Decimal("1.0000")

    case = score_ads(_payload(_meta("inactive")), _record(), DOSSIER)
    assert case.components["status"] == Decimal("0")


def test_renormalizes_away_landing_page_and_offer_when_ground_truth_has_none():
    case = score_ads(_payload(_google("inactive"), _meta("inactive", angle=None, offer=None,
                                                          call_to_action=None, landing_page=None)),
                     _record(GT_STATUS_ONLY), DOSSIER)
    assert case.components == {"status": Decimal(".5")}
    assert case.score == Decimal("0.5000")
    assert case.hard_failures == ()


def test_unknown_ads_payload_scores_status_against_unknown_channels():
    case = score_ads(_payload(unknowns=["ads"]), _record(GT_STATUS_ONLY), DOSSIER)
    assert case.components == {"status": Decimal(".5")}
    assert case.hard_failures == ()


def test_hard_failure_status_overclaim():
    case = score_ads(_payload(_google("active"), _meta("active", angle=None, offer=None,
                                                        call_to_action=None, landing_page=None)),
                     _record(GT_STATUS_ONLY), DOSSIER)
    assert case.hard_failures == ("status_overclaim:google", "status_overclaim:meta")

    only_google = {"as_of": "2026-08-18",
                   "channels": {"google": {"status": "active", "evidence_ids": ["ev-google"]}}}
    case = score_ads(_payload(_google(), _meta()), _record(only_google), DOSSIER)
    assert case.hard_failures == ("status_overclaim:meta",)


def test_hard_failure_google_creative_fields():
    case = score_ads(_payload(_google(offer="automated reporting"), _meta()), _record(), DOSSIER)
    assert case.hard_failures == ("google_creative_fields",)
    case = score_ads(_payload(_google(landing_page="https://example.com/"), _meta()),
                     _record(), DOSSIER)
    assert case.hard_failures == ("google_creative_fields",)


def test_hard_failure_unretained_evidence_and_invalid_output():
    case = score_ads(_payload(_google(evidence_ids=["ev-nope"]), _meta()), _record(), DOSSIER)
    assert case.hard_failures == ("unretained_evidence:google",)
    assert case.components["status"] == Decimal("1")

    case = score_ads({"ads": {"channels": [{"channel": "tiktok"}]}}, _record(), DOSSIER)
    assert case.hard_failures == ("invalid_output",)
    assert case.score == Decimal("0")
    assert case.components == {"status": Decimal("0"), "landing_page": Decimal("0"),
                               "offer": Decimal("0")}


def test_helpers():
    assert token_recall("Book an Enterprise plan demo", "enterprise plan demo") == Decimal("1")
    assert token_recall("enterprise", "enterprise agency reporting plan") == Decimal(".25")
    assert token_recall(None, "x") == Decimal("0")
    assert token_recall("x", "the of") == Decimal("0")
    assert normalized_landing_page("https://WWW.Example.com/p/enterprise/?utm=1") == (
        "example.com", "/p/enterprise")
    assert normalized_landing_page("https://example.com") == ("example.com", "/")
    assert normalized_landing_page(None) is None


@pytest.mark.parametrize("body, message", [
    ({"channels": {}}, "keys must be"),
    ({"as_of": "2026-08-18", "channels": {}}, "non-empty mapping"),
    ({"as_of": "2026-08-18", "channels": {"tiktok": {"status": "active", "evidence_ids": ["ev-meta"]}}},
     "not a supported ad channel"),
    ({"as_of": "2026-08-18", "channels": {"google": {"status": "paused", "evidence_ids": ["ev-google"]}}},
     "status must be one of"),
    ({"as_of": "2026-08-18", "channels": {"google": {"status": "unknown", "evidence_ids": ["ev-google"]}}},
     "exactly when its status is known"),
    ({"as_of": "2026-08-18", "channels": {"meta": {"status": "active"}}},
     "exactly when its status is known"),
    ({"as_of": "2026-08-18", "channels": {"meta": {"status": "active", "evidence_ids": ["ev-meta"],
                                                    "landing_page": "example.com/p"}}},
     "absolute HTTP"),
    ({"as_of": "2026-08-18", "channels": {"meta": {"status": "active", "evidence_ids": ["ev-meta"],
                                                    "offer_aliases": "enterprise"}}},
     "list of text"),
    ({"as_of": "2026-08-18", "channels": {"meta": {"status": "active", "evidence_ids": ["ev-meta"],
                                                    "headline": "x"}}},
     "unexpected keys"),
    ({"as_of": "2026-08-18", "channels": {"google": {"status": "active", "evidence_ids": ["ev-google"],
                                                      "observed_offer": "x"}}},
     "must not carry copy"),
    ({"as_of": "2026-08-18", "channels": {"meta": {"status": "active", "evidence_ids": ["ev-meta"],
                                                    "observed_offer": "TODO_HUMAN"}}},
     "TODO_HUMAN placeholder"),
])
def test_validate_ads_record_rejects_bad_ground_truth(body, message):
    with pytest.raises(ValueError, match=message):
        validate_ads_record(_record(body), DOSSIER)


def test_validate_ads_record_accepts_full_and_status_only_records():
    validate_ads_record(_record(GT_FULL), DOSSIER)
    validate_ads_record(_record(GT_STATUS_ONLY), DOSSIER)
