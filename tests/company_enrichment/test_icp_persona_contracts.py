import pytest

from scripts.company_enrichment.icp_persona_contracts import (
    InferredPersona,
    IcpPersonaOutput,
    ObservedPersona,
    Outcome,
    PrimaryICP,
    SecondaryICP,
    parse_icp_output,
    render_segment,
)


def primary():
    return PrimaryICP("Marketing agencies", "automated reporting", "multi-channel client campaigns", ("ev-1",))


def secondary():
    return SecondaryICP("Operations leads", "workflow visibility", "client delivery", ("ev-1",))


def personas():
    return (ObservedPersona("agency owner", ("ev-1",)),), (InferredPersona("reporting manager", ("ev-1",)),)


def payload(evidence_ids=None):
    return {
        "primary_icp": {"buyer": "Marketing agencies", "need": "automated reporting", "object": "multi-channel client campaigns", "evidence_ids": evidence_ids or ["ev-1"]},
        "secondary_icps": [],
        "outcomes": [{"text": "save time", "evidence_ids": ["ev-1"]}],
        "observed_personas": [{"role": "agency owner", "evidence_ids": ["ev-1"]}],
        "inferred_personas": [{"role": "reporting manager", "based_on_evidence_ids": ["ev-1"]}],
    }


def test_renders_primary_segment_deterministically():
    value = PrimaryICP(
        buyer="Marketing agencies",
        need="automated reporting",
        object="multi-channel client campaigns",
        evidence_ids=("ev-1",),
    )
    assert render_segment(value) == (
        "Marketing agencies that need automated reporting "
        "for multi-channel client campaigns"
    )


def test_rejects_third_secondary_and_unknown_evidence():
    with pytest.raises(ValueError, match="at most two"):
        IcpPersonaOutput(primary(), (secondary(), secondary(), secondary()), (), *personas())
    with pytest.raises(ValueError, match="retained Evidence"):
        parse_icp_output(payload(evidence_ids=["made-up"]), {"ev-1"})


def test_parse_omits_unsupported_optional_claims_and_keeps_outcomes_separate():
    result = parse_icp_output({**payload(), "unsupported": "invented"}, {"ev-1"})
    assert result.primary_icp == primary()
    assert result.outcomes[0].text == "save time"
    assert render_segment(result.primary_icp).startswith("Marketing agencies that need")


def test_types_are_immutable_and_persona_evidence_is_required():
    assert getattr(PrimaryICP, "__dataclass_params__").frozen
    with pytest.raises(ValueError, match="observed role"):
        parse_icp_output({**payload(), "observed_personas": [{"role": "", "evidence_ids": ["ev-1"]}]}, {"ev-1"})
    with pytest.raises(ValueError, match="based_on_evidence_ids"):
        parse_icp_output({**payload(), "inferred_personas": [{"role": "manager", "based_on_evidence_ids": []}]}, {"ev-1"})
