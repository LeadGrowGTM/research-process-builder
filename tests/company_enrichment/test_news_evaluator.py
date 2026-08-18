from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from hashlib import sha256
from pathlib import Path

import pytest

from scripts.company_enrichment.contracts import (
    CompanyDossier, EvidenceRef, FieldAssertion, Visibility,
)
from scripts.company_enrichment.news_evaluator import (
    NEWS_ENRICHMENT_ID, NEWS_WEIGHTS, date_is_cited, date_variants, score_news,
    validate_news_record,
)
from scripts.company_enrichment.signal_ground_truth import (
    SignalGroundTruthRecord, dataset_loader, load_signal_dataset,
)
from tests.company_enrichment.test_signal_ground_truth import build_signal_repo, load_dossiers

NOW = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)


def _ref(name: str, excerpt: str, url: str = "https://www.prnewswire.com/x") -> EvidenceRef:
    return EvidenceRef(f"ev-{name}", url, NOW, sha256(excerpt.encode()).hexdigest(), excerpt)


DOSSIER = CompanyDossier("saas-01", "1.0", (
    FieldAssertion("identity", "AgencyAnalytics", ("ev-about",), 0.8, Visibility.MESSAGE_SAFE),
), (
    _ref("about", "About AgencyAnalytics", "https://agencyanalytics.com/about"),
    _ref("lead", "Title: AgencyAnalytics expands leadership\nDate: Apr 11, 2024\nSnippet: hires"),
    _ref("launch", "Detected date: January 30, 2024\nAgencyAnalytics released Smart Reports",
         "https://agencyanalytics.com/blog/smart-reports"),
    _ref("undated", "AgencyAnalytics offers 85+ integrations", "https://g2.com/agency"),
    _ref("old", "Date: 2025-01-15\nAgencyAnalytics won an award", "https://awards.example/a"),
))


def _record(events, as_of="2026-08-18", window=180) -> SignalGroundTruthRecord:
    body = {"events": events, "as_of": as_of}
    if window is not None:
        body["recent_window_days"] = window
    return SignalGroundTruthRecord("saas-01", body)


LEAD = {"date": "2024-04-11", "headline_aliases": ["Expanded leadership team"],
        "source_domain": "prnewswire.com", "kind": "news", "event_type": "leadership",
        "evidence_ids": ["ev-lead"]}
LAUNCH = {"date": "2024-01-30", "headline_aliases": ["Released Smart Reports"],
          "source_domain": "agencyanalytics.com", "kind": "launch", "event_type": "feature",
          "evidence_ids": ["ev-launch"]}
RECORD = _record([LEAD, LAUNCH], as_of="2024-06-01", window=365)


def _payload(news=(), launches=(), unknowns=None):
    value = {"news": list(news), "launches": list(launches)}
    if unknowns is not None:
        value["unknowns"] = unknowns
    return value


def _lead(**overrides):
    value = {"date": "2024-04-11", "headline": "Expanded leadership team",
             "event_type": "leadership", "why_it_matters": "scaling",
             "source_url": "https://www.prnewswire.com/x", "evidence_ids": ["ev-lead"]}
    value.update(overrides)
    return value


def _launch(**overrides):
    value = {"date": "2024-01-30", "headline": "Released Smart Reports",
             "event_type": "feature", "why_it_matters": "faster reports",
             "source_url": "https://agencyanalytics.com/blog/smart-reports",
             "evidence_ids": ["ev-launch"]}
    value.update(overrides)
    return value


def test_perfect_payload_scores_one():
    case = score_news(_payload([_lead()], [_launch()]), RECORD, DOSSIER)
    assert case.score == Decimal("1") and case.hard_failures == ()
    assert set(case.components) == set(NEWS_WEIGHTS)


def test_weights_total_one():
    assert sum(NEWS_WEIGHTS.values()) == Decimal("1")


def test_date_tolerance_and_month_only_matching():
    case = score_news(_payload([_lead(date="2024-04-13")], [_launch(date="2024-01")]),
                      RECORD, DOSSIER)
    assert case.components["events"] == Decimal("1")
    far = score_news(_payload([_lead(date="2024-04-20")], [_launch()]), RECORD, DOSSIER)
    assert far.components["events"] == Decimal(".5")  # P=.5 R=.5


def test_wrong_kind_placement_costs_kind_not_events():
    # launch reported under news with a valid news event_type
    payload = _payload([_lead(), _launch(event_type="other")], [])
    case = score_news(payload, RECORD, DOSSIER)
    assert case.components["events"] == Decimal("1")
    assert case.components["kind"] == Decimal(".5")
    assert case.components["citation"] == Decimal("1")
    assert case.score == Decimal(".60") + Decimal(".25") + Decimal(".15") * Decimal(".5")


def test_citation_requires_ground_truth_evidence_overlap():
    payload = _payload([_lead(evidence_ids=["ev-old", "ev-lead"])],
                       [_launch(evidence_ids=["ev-undated"])])
    case = score_news(payload, RECORD, DOSSIER)
    # launch date "January 30, 2024" is not in ev-undated -> uncited_date hard failure
    assert "uncited_date" in case.hard_failures
    payload = _payload([_lead(evidence_ids=["ev-old", "ev-lead"])], [_launch()])
    case = score_news(payload, RECORD, DOSSIER)
    assert case.components["citation"] == Decimal("1")
    assert case.hard_failures == ()


def test_missing_and_extra_events_reduce_f1():
    only_lead = score_news(_payload([_lead()], []), RECORD, DOSSIER)
    assert only_lead.components["events"] == Decimal("2") / Decimal("3")  # P=1 R=.5
    extra = _lead(date="2025-01-15", source_url="https://awards.example/a",
                  evidence_ids=["ev-old"], event_type="award")
    with_extra = score_news(_payload([_lead(), extra], [_launch()]), RECORD, DOSSIER)
    assert with_extra.components["events"] == Decimal("2") * Decimal("2") / Decimal("3") * 1 / (
        Decimal("2") / Decimal("3") + 1)
    assert with_extra.components["citation"] == Decimal("2") / Decimal("3")


def test_uncited_date_is_a_hard_failure():
    payload = _payload([_lead(date="2024-04-12")], [_launch()])
    case = score_news(payload, RECORD, DOSSIER)
    assert case.hard_failures == ("uncited_date",)
    assert case.components["events"] == Decimal("1")  # score still computed, gate fails


def test_zero_ground_truth_events_all_or_nothing():
    empty = _record([])
    clean = score_news(_payload(unknowns=["news", "launches"]), empty, DOSSIER)
    assert clean.score == Decimal("1") and clean.hard_failures == ()
    silent = score_news(_payload(unknowns=["news"]), empty, DOSSIER)
    assert silent.score == Decimal("0") and silent.hard_failures == ()
    invented = score_news(_payload([_lead()], [], unknowns=["launches"]), empty, DOSSIER)
    assert invented.score == Decimal("0") and invented.hard_failures == ("invented_event",)


def test_optional_old_events_do_not_hurt_recall():
    record = _record([LEAD, LAUNCH], as_of="2026-08-18", window=180)  # both older than window
    case = score_news(_payload(unknowns=["news", "launches"]), record, DOSSIER)
    assert case.components["events"] == Decimal("0")  # P=0 -> F1 0 (nothing reported)
    reported = score_news(_payload([_lead()], []), record, DOSSIER)
    assert reported.components["events"] == Decimal("1")  # matched optional counts, recall 1


def test_contract_violation_is_hard_failure_with_zero_score():
    case = score_news({"news": [_lead(date="April 11")], "launches": []}, RECORD, DOSSIER)
    assert case.score == Decimal("0") and case.hard_failures == ("contract_violation",)
    case = score_news({"news": [_lead(evidence_ids=["ev-nope"])], "launches": []}, RECORD, DOSSIER)
    assert case.hard_failures == ("contract_violation",)


def test_date_variants_and_citation_check():
    variants = date_variants("2024-04-11")
    assert {"2024-04-11", "April 11, 2024", "Apr 11, 2024", "11 April 2024", "4/11/2024"} <= set(variants)
    assert set(date_variants("2024-04")) == {"2024-04", "April 2024", "Apr 2024"}
    from scripts.company_enrichment.news_contracts import NewsEvent
    event = NewsEvent("2024-01-30", "h", "feature", "w", "https://a.example/x", ("ev-launch",))
    assert date_is_cited(event, {"ev-launch": "released on JANUARY 30, 2024"})
    assert not date_is_cited(event, {"ev-launch": "released in 2024"})


def test_validate_news_record_shape():
    validate_news_record(RECORD, DOSSIER)
    with pytest.raises(ValueError, match="unexpected keys"):
        validate_news_record(_record([LEAD]).__class__("saas-01", {"events": [], "as_of": "2026-01-01", "x": 1}), DOSSIER)
    with pytest.raises(ValueError, match="kind must be news or launch"):
        validate_news_record(_record([{**LEAD, "kind": "launches"}]), DOSSIER)
    with pytest.raises(ValueError, match="event_type is not valid"):
        validate_news_record(_record([{**LEAD, "event_type": "feature"}]), DOSSIER)
    with pytest.raises(ValueError, match="bare domain"):
        validate_news_record(_record([{**LEAD, "source_domain": "prnewswire.com/x"}]), DOSSIER)
    with pytest.raises(ValueError, match="keys must exactly match"):
        validate_news_record(_record([{**LEAD, "extra": 1}]), DOSSIER)
    validate_news_record(_record([{**LEAD, "date": date(2024, 4, 11)}], window=None), DOSSIER)


def test_dataset_loader_accepts_news_records(tmp_path: Path):
    def ground_truth(company_id, _record_value):
        return {
            "company_id": company_id, "as_of": "2026-08-18", "recent_window_days": 180,
            "events": [{
                "date": "2026-07-01", "headline_aliases": ["Launched google ads"],
                "source_domain": "example.test", "kind": "launch", "event_type": "product",
                "evidence_ids": [f"ev-{company_id}-google"],
            }],
        }

    build_signal_repo(tmp_path, enrichment_id=NEWS_ENRICHMENT_ID, weights=NEWS_WEIGHTS,
                      ground_truth=ground_truth)
    loader = dataset_loader(NEWS_ENRICHMENT_ID, NEWS_WEIGHTS, validate_news_record)
    dataset = loader(tmp_path, load_dossiers(tmp_path, NEWS_ENRICHMENT_ID))
    assert dataset.records["saas-01"].body["events"][0]["kind"] == "launch"

    def bad(company_id, _record_value):
        value = ground_truth(company_id, _record_value)
        value["events"][0]["kind"] = "press"
        return value

    other = tmp_path / "bad"
    build_signal_repo(other, enrichment_id=NEWS_ENRICHMENT_ID, weights=NEWS_WEIGHTS,
                      ground_truth=bad)
    with pytest.raises(ValueError, match="kind must be news or launch"):
        load_signal_dataset(other, load_dossiers(other, NEWS_ENRICHMENT_ID),
                            enrichment_id=NEWS_ENRICHMENT_ID, weights=NEWS_WEIGHTS,
                            validate_record=validate_news_record)


def test_first_party_copy_of_wire_release_matches_via_shared_evidence():
    # ground truth records the wire domain; the payload cites the company's own
    # newsroom copy of the same release but shares the Evidence ID, so it matches
    lead_first_party = _lead(source_url="https://agencyanalytics.com/newsroom/leadership")
    case = score_news(_payload([lead_first_party], [_launch()]), RECORD, DOSSIER)
    assert case.components["events"] == Decimal("1")
    assert case.components["citation"] == Decimal("1")
    # a different domain AND no shared Evidence is still a miss
    stranger = _lead(source_url="https://example.org/lead", evidence_ids=["ev-about"])
    miss = score_news(_payload([stranger], [_launch()]), RECORD, DOSSIER)
    assert miss.components["events"] == Decimal(".5")
