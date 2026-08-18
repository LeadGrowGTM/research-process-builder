from __future__ import annotations

from datetime import date, datetime, timezone
from hashlib import sha256
from pathlib import Path

import pytest
import yaml

from scripts.company_enrichment.adapters.ads_channels import ad_finding_to_source_records
from scripts.company_enrichment.ads_collect import ads_request, collect_ads, evidence_ref
from scripts.company_enrichment.contracts import (
    CompanyDossier, EvidenceRef, FieldAssertion, Visibility,
)
from scripts.company_enrichment.evidence import EvidenceStore
from scripts.company_enrichment.providers import (
    AdFinding, AdStatus, AdsRequest, AuthenticationFailure, RetryableFailure, SourceObservation,
)
from scripts.company_enrichment.signal_loop import CollectRequest


NOW = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)
GOOGLE_EXCERPT = '{"domain":"agencyanalytics.com","running_ads":true,"total_creatives":12}'
META_EXCERPT = '{"summary":{"active_ads_count":3},"ads":[{"headline":"Enterprise plan","cta_text":"Contact us"}]}'


def _base(company_id: str = "saas-01", *, identity: str | None = "AgencyAnalytics") -> CompanyDossier:
    excerpt = f"About {company_id}."
    evidence = (EvidenceRef(
        "ev-" + sha256(excerpt.encode()).hexdigest()[:16],
        "https://agencyanalytics.com/company/about", NOW,
        sha256(excerpt.encode()).hexdigest(), excerpt,
    ),)
    assertions = ()
    if identity is not None:
        assertions = (FieldAssertion(
            "identity", identity, (evidence[0].evidence_id,), 0.8, Visibility.MESSAGE_SAFE,
        ),)
    return CompanyDossier(company_id, "1.0", assertions, evidence, ("news", "ads"))


def _google(status: AdStatus = AdStatus.ACTIVE) -> AdFinding:
    return AdFinding(
        "google", status, date(2026, 1, 2), None, None, None, None, None, None,
        (SourceObservation("https://adstransparency.google.com/?domain=agencyanalytics.com",
                           GOOGLE_EXCERPT),),
        0.9,
    )


def _meta() -> AdFinding:
    return AdFinding(
        "meta", AdStatus.ACTIVE, date(2026, 3, 4), None, None, None, None, "Contact us",
        "https://agencyanalytics.com/p/enterprise",
        (SourceObservation("https://www.facebook.com/ads/library/?view_all_page_id=1", META_EXCERPT),),
        0.9,
    )


class StubProvider:
    def __init__(self, result) -> None:
        self.result = result
        self.requests: list[AdsRequest] = []

    def inspect(self, request: AdsRequest) -> AdFinding:
        self.requests.append(request)
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


def _request(tmp_path: Path, base: CompanyDossier | None = None) -> CollectRequest:
    base = base or _base()
    return CollectRequest(base.company_id, base, tmp_path)


def test_evidence_ref_matches_evidence_store_derivation(tmp_path: Path):
    (record,) = ad_finding_to_source_records(_google(), retrieved_at=NOW)
    ref = evidence_ref(record)
    assert ref == EvidenceStore(tmp_path / "store").put(record)
    assert ref.evidence_id == "ev-" + ref.content_hash[:16]
    assert ref.content_hash == sha256(GOOGLE_EXCERPT.encode("utf-8")).hexdigest()


def test_ads_request_prefers_corpus_domain_then_base_evidence_origin(tmp_path: Path):
    request = ads_request(_request(tmp_path))
    assert request == AdsRequest("AgencyAnalytics", "https://agencyanalytics.com")

    (tmp_path / "benchmarks").mkdir()
    (tmp_path / "benchmarks" / "companies.yaml").write_text(yaml.safe_dump({
        "version": "1.0", "as_of": "2026-08-12", "status": "research_complete",
        "source": {"kind": "test"},
        "companies": [{
            "id": "saas-01", "company_name": "AgencyAnalytics Inc", "domain": "www.agencyanalytics.com",
            "linkedin_company_url": None, "primary_cohort": "b2b_saas", "shared_core": False,
            "difficulty": "easy", "seed_status": "verified", "seed": {}, "gaps": [],
        }],
    }), encoding="utf-8")
    request = ads_request(_request(tmp_path))
    assert request == AdsRequest("AgencyAnalytics", "https://www.agencyanalytics.com")

    request = ads_request(_request(tmp_path, _base(identity=None)))
    assert request.company_name == "AgencyAnalytics Inc"


def test_ads_request_requires_a_company_name(tmp_path: Path):
    with pytest.raises(ValueError, match="no identity assertion or corpus fixture"):
        ads_request(_request(tmp_path, _base(identity=None)))


def test_collect_builds_content_addressed_evidence_and_channel_assertion(tmp_path: Path):
    google, meta = StubProvider(_google()), StubProvider(_meta())
    base = _base()

    dossier = collect_ads(_request(tmp_path, base), providers={"google": google, "meta": meta},
                          now=NOW)

    assert google.requests == [AdsRequest("AgencyAnalytics", "https://agencyanalytics.com")]
    assert dossier.company_id == "saas-01"
    assert dossier.evidence[0] == base.evidence[0]
    assert dossier.assertions[:len(base.assertions)] == base.assertions
    google_ref = evidence_ref(ad_finding_to_source_records(_google(), retrieved_at=NOW)[0])
    meta_ref = evidence_ref(ad_finding_to_source_records(_meta(), retrieved_at=NOW)[0])
    assert dossier.evidence[1:] == (google_ref, meta_ref)
    assert {item.evidence_id for item in dossier.evidence[1:]} == {
        "ev-" + sha256(GOOGLE_EXCERPT.encode()).hexdigest()[:16],
        "ev-" + sha256(META_EXCERPT.encode()).hexdigest()[:16],
    }
    ads = dossier.assertions[-1]
    assert ads.field == "ads"
    assert ads.evidence_ids == (google_ref.evidence_id, meta_ref.evidence_id)
    assert ads.visibility is Visibility.MESSAGE_SAFE
    assert ads.value == {"channels": [
        {"channel": "google", "status": "active", "started_on": "2026-01-02", "ended_on": None,
         "landing_page": None, "call_to_action": None,
         "evidence_ids": [google_ref.evidence_id], "failure": None},
        {"channel": "meta", "status": "active", "started_on": "2026-03-04", "ended_on": None,
         "landing_page": "https://agencyanalytics.com/p/enterprise", "call_to_action": "Contact us",
         "evidence_ids": [meta_ref.evidence_id], "failure": None},
    ]}
    assert dossier.unknowns == ("news",)
    assert base.unknowns == ("news", "ads")


def test_provider_failure_lands_as_unknown_channel_with_reason(tmp_path: Path, capsys):
    dossier = collect_ads(
        _request(tmp_path),
        providers={"google": StubProvider(_google(AdStatus.INACTIVE)),
                   "meta": StubProvider(RetryableFailure("scraper busy"))},
        now=NOW,
    )
    ads = dossier.assertions[-1]
    google, meta = ads.value["channels"]
    assert google["status"] == "inactive" and google["failure"] is None
    assert meta == {
        "channel": "meta", "status": "unknown", "started_on": None, "ended_on": None,
        "landing_page": None, "call_to_action": None, "evidence_ids": [],
        "failure": {"kind": "retryable", "message": "scraper busy"},
    }
    assert len(dossier.evidence) == 2
    assert ads.evidence_ids == (dossier.evidence[1].evidence_id,)
    assert "ads" not in dossier.unknowns
    assert "scraper busy" in capsys.readouterr().err


def test_all_channels_unknown_marks_ads_unknown_without_assertion(tmp_path: Path):
    base = _base()
    dossier = collect_ads(
        _request(tmp_path, base),
        providers={"google": StubProvider(AdFinding.unknown("google")),
                   "meta": StubProvider(AuthenticationFailure("no key"))},
        now=NOW,
    )
    assert dossier.evidence == base.evidence
    assert dossier.assertions == base.assertions
    assert dossier.unknowns == ("news", "ads")


def test_non_provider_errors_and_naive_clock_raise(tmp_path: Path):
    with pytest.raises(ValueError, match="timezone-aware"):
        collect_ads(_request(tmp_path), providers={}, now=NOW.replace(tzinfo=None))
    with pytest.raises(RuntimeError, match="boom"):
        collect_ads(_request(tmp_path), providers={"google": StubProvider(RuntimeError("boom"))},
                    now=NOW)
