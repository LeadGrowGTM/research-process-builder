from __future__ import annotations

from datetime import date

import pytest

from scripts.company_enrichment.news_contracts import (
    LAUNCH_EVENT_TYPES, NEWS_EVENT_TYPES, NewsEvent, NewsOutput, news_output_contract,
    normalize_event_date, parse_news_output,
)
from tests.company_enrichment.test_signal_ground_truth import base_dossier


def _event(**overrides):
    value = {
        "date": "2024-04-11", "headline": "Expanded leadership team with new hires",
        "event_type": "leadership", "why_it_matters": "Signals scaling",
        "source_url": "https://www.prnewswire.com/x", "evidence_ids": ["ev-saas-01-about"],
    }
    value.update(overrides)
    return value


RETAINED = {"ev-saas-01-about", "ev-2"}


def test_parse_valid_output_and_events_property():
    output = parse_news_output({
        "news": [_event()],
        "launches": [_event(event_type="feature", date="2024-01",
                            headline="Released Smart Reports")],
        "unknowns": [],
    }, RETAINED)
    assert isinstance(output, NewsOutput)
    assert output.news[0].date == "2024-04-11" and output.news[0].is_full_date
    assert output.launches[0].date == "2024-01" and output.launches[0].month == "2024-01"
    assert [kind for kind, _ in output.events] == ["news", "launches"]
    assert output.unknowns == ()


def test_unknowns_optional_and_must_match_empty_collections():
    output = parse_news_output({"news": [], "launches": [], "unknowns": ["news", "launches"]},
                               RETAINED)
    assert output.unknowns == ("news", "launches")
    assert parse_news_output({"news": [], "launches": []}, RETAINED).unknowns == ()
    with pytest.raises(ValueError, match="declared unknown but contains events"):
        parse_news_output({"news": [_event()], "launches": [], "unknowns": ["news"]}, RETAINED)
    with pytest.raises(ValueError, match="only name news or launches"):
        parse_news_output({"news": [], "launches": [], "unknowns": ["ads"]}, RETAINED)


@pytest.mark.parametrize("bad, message", [
    ({"date": "April 11, 2024"}, "YYYY-MM-DD or YYYY-MM"),
    ({"date": "2024-13"}, "out of range"),
    ({"date": "2024-02-30"}, "real calendar date"),
    ({"date": "recently"}, "YYYY-MM-DD or YYYY-MM"),
    ({"event_type": "feature"}, "news event_type must be one of"),
    ({"headline": " ".join(["word"] * 17)}, "headline exceeds"),
    ({"why_it_matters": " ".join(["w"] * 21)}, "why_it_matters exceeds"),
    ({"source_url": "prnewswire.com/x"}, "absolute HTTP"),
    ({"evidence_ids": []}, "must contain evidence IDs"),
    ({"evidence_ids": ["ev-missing"]}, "retained Evidence"),
    ({"extra": 1}, "unexpected keys"),
])
def test_parse_rejects_invalid_events(bad, message):
    with pytest.raises(ValueError, match=message):
        parse_news_output({"news": [_event(**bad)], "launches": []}, RETAINED)


def test_parse_rejects_wrong_top_level_shape():
    with pytest.raises(ValueError, match="exactly news and launches"):
        parse_news_output({"news": []}, RETAINED)
    with pytest.raises(ValueError, match="must be an array"):
        parse_news_output({"news": {}, "launches": []}, RETAINED)
    with pytest.raises(ValueError, match="launches event_type"):
        parse_news_output({"news": [], "launches": [_event(event_type="funding")]}, RETAINED)


def test_normalize_event_date_accepts_yaml_dates():
    assert normalize_event_date(date(2024, 4, 11)) == "2024-04-11"
    assert normalize_event_date(" 2024-04 ") == "2024-04"


def test_news_event_is_immutable_and_normalized():
    event = NewsEvent("2024-04-11", "  Two   words ", "LEADERSHIP", "why", "https://a.example/x",
                      ("ev-1",))
    assert event.headline == "Two words" and event.event_type == "leadership"
    assert event.evidence_ids == ("ev-1",)
    with pytest.raises(ValueError, match="unique evidence IDs"):
        NewsEvent("2024-04-11", "h", "other", "w", "https://a.example/x", ("ev-1", "ev-1"))
    with pytest.raises(AttributeError):
        event.date = "2025-01-01"


def test_output_contract_is_strict_and_evidence_closed():
    dossier = base_dossier("saas-01")
    schema = news_output_contract(dossier)
    assert set(schema["properties"]) == {"news", "launches", "unknowns"}
    assert schema["required"] == ["news", "launches", "unknowns"]
    assert schema["additionalProperties"] is False
    news_item = schema["properties"]["news"]["items"]
    assert news_item["properties"]["event_type"]["enum"] == list(NEWS_EVENT_TYPES)
    launch_item = schema["properties"]["launches"]["items"]
    assert launch_item["properties"]["event_type"]["enum"] == list(LAUNCH_EVENT_TYPES)
    assert news_item["properties"]["evidence_ids"]["items"]["enum"] == ["ev-saas-01-about"]
    assert news_item["properties"]["evidence_ids"]["minItems"] == 1
    assert schema["properties"]["unknowns"]["items"]["enum"] == ["news", "launches"]
