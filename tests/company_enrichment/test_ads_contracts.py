from __future__ import annotations

from datetime import datetime, timezone

import pytest

from scripts.company_enrichment.ads_contracts import (
    AD_CHANNELS, AD_STATUSES, AdChannelOutput, AdsOutput, ads_output_contract, parse_ads_output,
)
from scripts.company_enrichment.contracts import CompanyDossier, EvidenceRef


NOW = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)
RETAINED = {"ev-google", "ev-meta", "ev-about"}


def _channel(**overrides):
    values = {
        "channel": "meta", "status": "active", "angle": "stop building reports by hand",
        "offer": "enterprise agency reporting plan", "call_to_action": "Contact us",
        "landing_page": "https://example.com/p/enterprise", "evidence_ids": ["ev-meta"],
    }
    values.update(overrides)
    return values


def _payload(*channels, unknowns=None):
    payload = {"ads": {"channels": list(channels)}}
    if unknowns is not None:
        payload["unknowns"] = unknowns
    return payload


def test_parse_valid_output_normalizes_text_and_keeps_order():
    google = _channel(channel="google", angle=None, offer=None, call_to_action=None,
                      landing_page=None, evidence_ids=["ev-google"])
    output = parse_ads_output(_payload(google, _channel(offer="  enterprise   plan ")), RETAINED)

    assert isinstance(output, AdsOutput)
    assert [item.channel for item in output.channels] == ["google", "meta"]
    assert output.channel("meta").offer == "enterprise plan"
    assert output.channel("google").has_copy is False
    assert output.channel("meta").has_copy is True
    assert output.cited_evidence_ids == {"ev-google", "ev-meta"}
    assert output.channel("linkedin") is None


def test_parse_accepts_unknown_ads_with_empty_channels():
    output = parse_ads_output(_payload(unknowns=["ads"]), RETAINED)
    assert output.channels == ()


@pytest.mark.parametrize("payload, message", [
    ("not a mapping", "must be an object"),
    ({"channels": []}, "exactly ads"),
    ({"ads": {"channels": [], "extra": 1}}, "exactly a channels list"),
    ({"ads": {"channels": {}}}, "must be a list"),
    (_payload(_channel(channel="linkedin")), "channel must be one of"),
    (_payload(_channel(status="paused")), "status must be one of"),
    (_payload(_channel(landing_page="example.com/p")), "landing_page must be null"),
    (_payload(_channel(offer="")), "offer must be null or non-empty"),
    (_payload(_channel(evidence_ids=[])), "evidence_ids must contain"),
    (_payload(_channel(evidence_ids=["ev-meta", "ev-meta"])), "unique evidence IDs"),
    (_payload(_channel(evidence_ids=["ev-missing"])), "retained Evidence"),
    (_payload(_channel(), _channel()), "at most once"),
    (_payload(_channel(extra="x")), "unexpected keys"),
    (_payload(), "unknown exactly when"),
    (_payload(_channel(), unknowns=["ads"]), "unknown exactly when"),
    (_payload(unknowns=["news"]), "may only name ads"),
])
def test_parse_rejects_contract_violations(payload, message):
    with pytest.raises(ValueError, match=message):
        parse_ads_output(payload, RETAINED)


def test_typed_values_are_frozen_and_validated():
    channel = AdChannelOutput("google", "inactive", None, None, None, None, ("ev-google",))
    with pytest.raises(AttributeError):
        channel.status = "active"  # type: ignore[misc]
    with pytest.raises(ValueError, match="channel must be one of"):
        AdChannelOutput("tiktok", "active", None, None, None, None, ("ev-google",))
    with pytest.raises(ValueError, match="AdChannelOutput values"):
        AdsOutput(("google",))  # type: ignore[arg-type]


def test_output_contract_restricts_evidence_to_dossier_ids():
    dossier = CompanyDossier("saas-01", "1.0", (), (
        EvidenceRef("ev-about", "https://example.test/about", NOW, "a" * 64, "about"),
        EvidenceRef("ev-google", "https://adstransparency.google.com/?domain=x", NOW, "b" * 64, "{}"),
    ))
    schema = ads_output_contract(dossier)

    assert schema["required"] == ["ads", "unknowns"]
    assert schema["additionalProperties"] is False
    assert set(schema["properties"]) == {"ads", "unknowns"}
    channel = schema["properties"]["ads"]["properties"]["channels"]["items"]
    assert channel["properties"]["channel"]["enum"] == list(AD_CHANNELS)
    assert channel["properties"]["status"]["enum"] == list(AD_STATUSES)
    # only ad-library Evidence may support a channel; website Evidence is excluded
    assert channel["properties"]["evidence_ids"]["items"]["enum"] == ["ev-google"]
    assert channel["properties"]["evidence_ids"]["minItems"] == 1
    assert channel["properties"]["angle"] == {"type": ["string", "null"]}
    assert set(channel["required"]) == set(channel["properties"])
    assert schema["properties"]["unknowns"]["items"]["enum"] == ["ads"]


def test_output_contract_falls_back_to_all_ids_without_ad_library_evidence():
    dossier = CompanyDossier("saas-01", "1.0", (), (
        EvidenceRef("ev-about", "https://example.test/about", NOW, "a" * 64, "about"),
    ))
    channel = ads_output_contract(dossier)["properties"]["ads"]["properties"]["channels"]["items"]
    assert channel["properties"]["evidence_ids"]["items"]["enum"] == ["ev-about"]
