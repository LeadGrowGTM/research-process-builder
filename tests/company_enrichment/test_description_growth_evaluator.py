from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from scripts.company_enrichment.contracts import CompanyDossier, EvidenceRef
from scripts.company_enrichment.description_growth_evaluator import (
    CaseScore,
    claimed_signal_kinds,
    score_description_payload,
    score_growth_payload,
)
from scripts.company_enrichment.description_growth_ground_truth import (
    DescriptionGroundTruth,
    DescriptionGrowthRecord,
    FactComponent,
    GrowthGroundTruth,
    GrowthSignal,
)


DESCRIPTION_WEIGHTS = {
    "identity": Decimal(".25"),
    "offering": Decimal(".25"),
    "audience": Decimal(".20"),
    "citation": Decimal(".20"),
    "readability": Decimal(".10"),
}
GROWTH_WEIGHTS = {
    "verdict": Decimal(".45"),
    "signals": Decimal(".35"),
    "citation": Decimal(".20"),
}
EXCERPT = (
    "Acme Reporting is the only reporting and insights platform built for"
    " marketing agencies. Prove your value to clients, win back the hours you"
    " lose to manual reporting, and keep more of the clients you have worked"
    " hard to earn. Trusted by 7,000+ marketing agencies. See jobs Founded 2010"
)


def _evidence(evidence_id: str, excerpt: str = EXCERPT) -> EvidenceRef:
    return EvidenceRef(
        evidence_id,
        "https://example.com/about",
        datetime(2026, 8, 12, tzinfo=timezone.utc),
        "a" * 64,
        excerpt,
    )


def _dossier() -> CompanyDossier:
    return CompanyDossier(
        "saas-99", "1.0", (), (_evidence("ev-1"), _evidence("ev-2")),
    )


def _record(growth: GrowthGroundTruth) -> DescriptionGrowthRecord:
    return DescriptionGrowthRecord(
        company_id="saas-99",
        as_of=date(2026, 8, 25),
        description=DescriptionGroundTruth(
            identity=FactComponent(
                "Acme Reporting", ("Acme Reporting",), ("ev-1",),
            ),
            offering=FactComponent(
                "client reporting software",
                ("client reporting software", "reporting platform"),
                ("ev-1",),
            ),
            audience=FactComponent(
                "marketing agencies", ("marketing agencies", "agencies"),
                ("ev-1",),
            ),
        ),
        growth=growth,
    )


def _growth_truth() -> GrowthGroundTruth:
    return GrowthGroundTruth(
        verdict="growth_signals",
        signals=(
            GrowthSignal(
                kind="hiring",
                source="dossier",
                observed_at=date(2026, 8, 12),
                evidence_ids=("ev-1",),
                quote="See jobs",
            ),
            GrowthSignal(
                kind="customer_scale",
                source="dossier",
                observed_at=date(2026, 8, 12),
                evidence_ids=("ev-1",),
                quote="Trusted by 7,000+ marketing agencies.",
            ),
            GrowthSignal(
                kind="publishing",
                source="page-signals-v1",
                observed_at=date(2026, 8, 25),
                url="https://example.com/blog",
            ),
        ),
    )


def _no_signal_record() -> DescriptionGrowthRecord:
    return _record(GrowthGroundTruth(verdict="no_signal", signals=()))


def _description_output(description_value: str) -> dict:
    return {
        "assertions": [
            {
                "field": "identity",
                "value": "Acme Reporting",
                "evidence_ids": ["ev-1"],
            },
            {
                "field": "description",
                "value": description_value,
                "evidence_ids": ["ev-1"],
            },
            {
                "field": "offers",
                "value": "client reporting software",
                "evidence_ids": ["ev-1"],
            },
        ],
        "unknowns": [],
    }


PARAPHRASE = (
    "Acme Reporting sells a reporting platform that helps marketing agencies"
    " show results to their clients without manual work."
)


def test_paraphrased_description_scores_full():
    case = score_description_payload(
        _description_output(PARAPHRASE),
        _record(_growth_truth()),
        _dossier(),
        weights=DESCRIPTION_WEIGHTS,
    )
    assert isinstance(case, CaseScore)
    assert case.score == Decimal("1.00")
    assert case.hard_failures == ()


def test_verbatim_parroting_is_a_hard_failure():
    case = score_description_payload(
        _description_output(EXCERPT),
        _record(_growth_truth()),
        _dossier(),
        weights=DESCRIPTION_WEIGHTS,
    )
    assert "verbatim_parroting" in case.hard_failures
    assert case.components["readability"] == Decimal("0")


def test_missing_description_is_a_hard_failure():
    output = {
        "assertions": [
            {
                "field": "identity",
                "value": "Acme Reporting",
                "evidence_ids": ["ev-1"],
            },
        ],
        "unknowns": [],
    }
    case = score_description_payload(
        output, _record(_growth_truth()), _dossier(),
        weights=DESCRIPTION_WEIGHTS,
    )
    assert "missing_description" in case.hard_failures
    assert case.components["offering"] == Decimal("0")


def test_uncited_component_drops_citation_dimension():
    output = _description_output(PARAPHRASE)
    output["assertions"][1]["evidence_ids"] = ["ev-2"]
    output["assertions"][2]["evidence_ids"] = ["ev-2"]
    case = score_description_payload(
        output, _record(_growth_truth()), _dossier(),
        weights=DESCRIPTION_WEIGHTS,
    )
    assert case.components["citation"] == Decimal("1") / Decimal("3")
    assert case.hard_failures == ()


def test_unknown_evidence_id_is_a_hard_failure():
    output = _description_output(PARAPHRASE)
    output["assertions"][0]["evidence_ids"] = ["ev-missing"]
    case = score_description_payload(
        output, _record(_growth_truth()), _dossier(),
        weights=DESCRIPTION_WEIGHTS,
    )
    assert "uncited_identity" in case.hard_failures


def test_off_target_description_is_a_hard_failure():
    output = _description_output(
        "A company that builds industrial welding robots for shipyards.",
    )
    output["assertions"][2]["value"] = "welding robots"
    case = score_description_payload(
        output, _record(_growth_truth()), _dossier(),
        weights=DESCRIPTION_WEIGHTS,
    )
    assert "off_target_description" in case.hard_failures


def test_correct_unknown_on_no_signal_scores_full():
    output = {"assertions": [], "unknowns": ["growth"]}
    case = score_growth_payload(
        output, _no_signal_record(), _dossier(), weights=GROWTH_WEIGHTS,
    )
    assert case.score == Decimal("1.00")
    assert case.hard_failures == ()


def test_growth_claim_on_no_signal_is_a_hard_failure():
    output = {
        "assertions": [
            {
                "field": "growth",
                "value": "The company is hiring rapidly.",
                "evidence_ids": ["ev-1"],
            },
        ],
        "unknowns": [],
    }
    case = score_growth_payload(
        output, _no_signal_record(), _dossier(), weights=GROWTH_WEIGHTS,
    )
    assert case.score == Decimal("0.00")
    assert "unsupported_growth_claim" in case.hard_failures


def test_unknown_when_signals_exist_scores_zero_without_hard_failure():
    output = {"assertions": [], "unknowns": ["growth"]}
    case = score_growth_payload(
        output, _record(_growth_truth()), _dossier(), weights=GROWTH_WEIGHTS,
    )
    assert case.components["verdict"] == Decimal("0")
    assert case.hard_failures == ()


def test_grounded_growth_claim_scores_full():
    output = {
        "assertions": [
            {
                "field": "growth",
                "value": (
                    "The company shows hiring activity (careers page, see jobs)"
                    " and is trusted by 7,000+ customers."
                ),
                "evidence_ids": ["ev-1"],
            },
        ],
        "unknowns": [],
    }
    case = score_growth_payload(
        output, _record(_growth_truth()), _dossier(), weights=GROWTH_WEIGHTS,
    )
    assert case.score == Decimal("1.00")
    assert case.hard_failures == ()


def test_fabricated_funding_claim_is_a_hard_failure():
    output = {
        "assertions": [
            {
                "field": "growth",
                "value": (
                    "The company recently raised a large funding round and is"
                    " hiring."
                ),
                "evidence_ids": ["ev-1"],
            },
        ],
        "unknowns": [],
    }
    case = score_growth_payload(
        output, _record(_growth_truth()), _dossier(), weights=GROWTH_WEIGHTS,
    )
    assert "fabricated_funding" in case.hard_failures
    assert case.components["signals"] == Decimal("0.5")


def test_uncited_growth_claim_is_a_hard_failure():
    output = {
        "assertions": [
            {
                "field": "growth",
                "value": "The company is hiring (see jobs).",
                "evidence_ids": [],
            },
        ],
        "unknowns": [],
    }
    case = score_growth_payload(
        output, _record(_growth_truth()), _dossier(), weights=GROWTH_WEIGHTS,
    )
    assert "uncited_growth" in case.hard_failures
    assert case.components["citation"] == Decimal("0")


def test_claimed_signal_kinds_lexicon():
    kinds = claimed_signal_kinds(
        "Raised a Series D round, hiring for open roles, and trusted by"
        " 7,000+ customers.",
    )
    assert {"funding", "hiring", "customer_scale"} <= kinds
    assert "expansion" not in kinds


def test_growth_weights_must_match_components():
    output = {"assertions": [], "unknowns": ["growth"]}
    with pytest.raises(ValueError, match="rubric weights"):
        score_growth_payload(
            output, _no_signal_record(), _dossier(),
            weights={"verdict": Decimal("1.0")},
        )
