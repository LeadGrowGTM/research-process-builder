from __future__ import annotations

import pytest

from scripts.company_enrichment.competitor_contracts import (
    Competitor, CompetitorsOutput, RELATIONSHIPS, competitors_output_contract, normalize_domain,
    normalize_name, parse_competitors_output,
)
from tests.company_enrichment.test_signal_ground_truth import base_dossier

RETAINED = {"ev-saas-01-about", "ev-2"}


def _competitor(**overrides):
    value = {"name": "DashThis", "domain": "dashthis.com", "relationship": "direct",
             "why": "Listed on the subject's competitor page", "evidence_ids": ["ev-2"]}
    value.update(overrides)
    return value


def _payload(named=(), inferred=(), conflicts=(), unknowns=None):
    value = {"competitors": {"named": list(named), "inferred": list(inferred),
                             "conflicts": list(conflicts)}}
    if unknowns is not None:
        value["unknowns"] = unknowns
    return value


def test_parse_valid_output():
    output = parse_competitors_output(_payload(
        [_competitor()],
        [_competitor(name="Looker Studio", domain=None, relationship="alternative")],
        [{"note": "partner vs competitor", "evidence_ids": ["ev-2", "ev-saas-01-about"]}],
        unknowns=[],
    ), RETAINED)
    assert isinstance(output, CompetitorsOutput)
    assert output.named[0].key == "dashthis" and output.named[0].domain == "dashthis.com"
    assert output.inferred[0].domain is None
    assert output.conflicts[0].note == "partner vs competitor"
    assert [bucket for bucket, _ in output.entries] == ["named", "inferred"]


def test_unknowns_only_when_both_buckets_empty():
    output = parse_competitors_output(_payload(unknowns=["competitors"]), RETAINED)
    assert output.unknowns == ("competitors",)
    assert parse_competitors_output(_payload(), RETAINED).unknowns == ()
    with pytest.raises(ValueError, match="declared unknown but contains entries"):
        parse_competitors_output(_payload([_competitor()], unknowns=["competitors"]), RETAINED)
    with pytest.raises(ValueError, match="only name competitors"):
        parse_competitors_output(_payload(unknowns=["news"]), RETAINED)


@pytest.mark.parametrize("bad, message", [
    ({"relationship": "rival"}, "relationship must be one of"),
    ({"evidence_ids": []}, "must contain evidence IDs"),
    ({"evidence_ids": ["ev-missing"]}, "retained Evidence"),
    ({"domain": ""}, "text or null"),
    ({"name": ""}, "name must be non-empty"),
    ({"extra": True}, "unexpected keys"),
])
def test_parse_rejects_invalid_competitors(bad, message):
    with pytest.raises(ValueError, match=message):
        parse_competitors_output(_payload([_competitor(**bad)]), RETAINED)


def test_parse_merges_duplicate_companies_and_repeated_ids():
    # the same company in both buckets folds into the first (named) entry with
    # the union of citations; a repeated evidence id inside one entry is dropped
    output = parse_competitors_output(
        _payload([_competitor(evidence_ids=["ev-2", "ev-2"])],
                 [_competitor(name="Dash This Inc.", evidence_ids=["ev-saas-01-about"])]),
        RETAINED,
    )
    assert [item.name for item in output.named] == ["DashThis"]
    assert output.inferred == ()
    assert output.named[0].evidence_ids == ("ev-2", "ev-saas-01-about")


def test_parse_rejects_bad_shapes():
    with pytest.raises(ValueError, match="exactly competitors"):
        parse_competitors_output({"named": []}, RETAINED)
    with pytest.raises(ValueError, match="named, inferred, and conflicts"):
        parse_competitors_output({"competitors": {"named": [], "inferred": []}}, RETAINED)
    with pytest.raises(ValueError, match="note and evidence_ids"):
        parse_competitors_output(_payload(conflicts=[{"note": "x"}]), RETAINED)
    with pytest.raises(ValueError, match="retained Evidence"):
        parse_competitors_output(_payload(conflicts=[{"note": "x", "evidence_ids": ["ev-nope"]}]),
                                 RETAINED)


def test_normalizers():
    assert normalize_name("Dash This, Inc.") == "dashthis"
    assert normalize_name("TapClicks LLC") == "tapclicks"
    assert normalize_name("Google Data Studio") == "googledatastudio"
    assert normalize_domain("https://www.DashThis.com/pricing") == "dashthis.com"
    assert normalize_domain(None) is None
    competitor = Competitor("Whatagraph", "WWW.whatagraph.com", "DIRECT", "why", ("ev-1",))
    assert competitor.domain == "whatagraph.com" and competitor.relationship == "direct"
    with pytest.raises(AttributeError):
        competitor.name = "x"


def test_output_contract_is_strict_and_evidence_closed():
    schema = competitors_output_contract(base_dossier("saas-01"))
    assert schema["required"] == ["competitors", "unknowns"]
    assert schema["additionalProperties"] is False
    body = schema["properties"]["competitors"]
    assert body["required"] == ["named", "inferred", "conflicts"]
    item = body["properties"]["named"]["items"]
    assert item["properties"]["relationship"]["enum"] == list(RELATIONSHIPS)
    assert item["properties"]["domain"]["type"] == ["string", "null"]
    assert item["properties"]["evidence_ids"]["items"]["enum"] == ["ev-saas-01-about"]
    assert item["properties"]["evidence_ids"]["minItems"] == 1
    conflict = body["properties"]["conflicts"]["items"]
    assert conflict["required"] == ["note", "evidence_ids"]
    assert schema["properties"]["unknowns"]["items"]["enum"] == ["competitors"]


def test_malformed_domain_becomes_null():
    assert normalize_domain("service now.com") is None
    assert normalize_domain("nodots") is None
    assert normalize_domain("https://www.ServiceNow.com/x") == "servicenow.com"
