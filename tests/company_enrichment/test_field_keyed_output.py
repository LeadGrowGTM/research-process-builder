from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.company_enrichment.benchmark import ExecutionTrack
from scripts.company_enrichment.contracts import CompanyDossier, EvidenceRef, Visibility
from scripts.company_enrichment.experiment_runner import ExperimentInput
from scripts.company_enrichment.openai_model_client import OpenAIModelClient


NOW = datetime(2026, 8, 18, tzinfo=timezone.utc)
CONTRACT = {"type": "object", "properties": {"ads": {"type": "object"}}}
ICP_CONTRACT = {"type": "object", "properties": {"primary_icp": {"type": "object"}}}


def _dossier(company_id: str = "saas-01") -> CompanyDossier:
    evidence = (
        EvidenceRef("ev-base", "https://example.test/about", NOW, "a" * 64, "About Example."),
        EvidenceRef("ev-google", "https://adstransparency.google.com/?domain=example.test",
                    NOW, "b" * 64, '{"running_ads":true}'),
    )
    return CompanyDossier(company_id, "1.0", (), evidence)


def _request(enrichment_id: str = "running-ads-offer-intelligence", contract=CONTRACT):
    return ExperimentInput(
        enrichment_id, "saas-01", "gpt-4.1-mini", _dossier(), "baseline",
        "Return cited ads facts.", contract,
    )


def _client(tmp_path: Path, output: dict) -> OpenAIModelClient:
    class Responses:
        def create(self, **_kwargs):
            return SimpleNamespace(
                id="resp_1", model="gpt-4.1-mini-2025-04-14",
                output_text=json.dumps(output),
                usage=SimpleNamespace(input_tokens=10, output_tokens=5),
            )
    return OpenAIModelClient(
        artifact_root=tmp_path, sdk_client=SimpleNamespace(responses=Responses()),
    )


ADS_OUTPUT = {
    "ads": {
        "google": {"status": "active", "evidence_ids": ["ev-google"]},
        "meta": {"status": "unknown", "evidence_ids": ["ev-base"], "angle": None},
    },
}


def test_field_keyed_output_becomes_one_assertion_per_field(tmp_path: Path):
    execution = _client(tmp_path, ADS_OUTPUT).execute(
        (_request(),), ExecutionTrack.SYNCHRONOUS,
    )[0]

    assert len(execution.assertions) == 1
    assertion = execution.assertions[0]
    assert assertion.field == "ads"
    assert assertion.value == ADS_OUTPUT["ads"]
    assert assertion.evidence_ids == ("ev-google", "ev-base")
    assert assertion.confidence == 1.0
    assert assertion.visibility is Visibility.MESSAGE_SAFE
    assert execution.unknowns == ()


def test_field_keyed_output_accepts_declared_unknowns_within_scope(tmp_path: Path):
    output = {
        "news": {"events": [{"headline": "Launch", "evidence_ids": ["ev-base"]}]},
        "launches": {"events": []},
        "unknowns": ["launches"],
    }
    execution = _client(tmp_path, output).execute(
        (_request("news-product-launches"),), ExecutionTrack.SYNCHRONOUS,
    )[0]

    values = {item.field: item for item in execution.assertions}
    assert set(values) == {"news", "launches"}
    assert values["news"].evidence_ids == ("ev-base",)
    assert values["launches"].evidence_ids == ()
    assert execution.unknowns == ("launches",)


def test_field_keyed_output_treats_empty_collection_as_unknown(tmp_path: Path):
    # the model returned nothing for launches but forgot to declare it unknown;
    # an explicitly empty collection is an unknown, not a contract failure
    output = {
        "news": [{"headline": "Launch", "evidence_ids": ["ev-base"]}],
        "launches": [],
        "unknowns": [],
    }
    execution = _client(tmp_path, output).execute(
        (_request("news-product-launches"),), ExecutionTrack.SYNCHRONOUS,
    )[0]
    assert execution.unknowns == ("launches",)
    # an object whose list members are all empty counts too
    output = {"ads": {"channels": []}, "unknowns": []}
    execution = _client(tmp_path, output).execute((_request(),), ExecutionTrack.SYNCHRONOUS)[0]
    assert execution.unknowns == ("ads",)


@pytest.mark.parametrize("output, message", [
    ({"ads": {"google": {"evidence_ids": ["ev-foreign"]}}}, "outside the dossier"),
    ({"ads": {"google": {"evidence_ids": []}}}, "non-empty"),
    ({"ads": {"google": {"evidence_ids": ["ev-base"]}}, "extra": 1}, "exactly the requested"),
    ({"competitors": {"evidence_ids": ["ev-base"]}}, "exactly the requested"),
    ({"ads": {"google": {"status": "active"}}}, "cites no Evidence"),
    ({"ads": {"google": {"evidence_ids": ["ev-base"]}}, "unknowns": ["news"]},
     "outside enrichment scope"),
    ([{"ads": {}}], "must be an object"),
])
def test_field_keyed_output_rejects_invalid_payloads(tmp_path: Path, output, message):
    with pytest.raises(ValueError, match=message):
        _client(tmp_path, output).execute((_request(),), ExecutionTrack.SYNCHRONOUS)


def test_icp_nested_contract_still_uses_icp_validation(tmp_path: Path):
    icp_output = {
        "primary_icp": {
            "buyer": "Agencies", "need": "reporting", "object": "campaigns",
            "evidence_ids": ["ev-base"],
        },
        "secondary_icps": [], "outcomes": [], "observed_personas": [],
        "inferred_personas": [],
    }
    execution = _client(tmp_path, icp_output).execute(
        (_request("icp-persona-analysis", ICP_CONTRACT),), ExecutionTrack.SYNCHRONOUS,
    )[0]

    assert [item.field for item in execution.assertions] == ["icp", "personas"]
    with pytest.raises(ValueError):
        _client(tmp_path / "second", ADS_OUTPUT).execute(
            (_request("icp-persona-analysis", ICP_CONTRACT),), ExecutionTrack.SYNCHRONOUS,
        )


def test_legacy_flat_output_without_contract_is_unchanged(tmp_path: Path):
    legacy = {
        "assertions": [{
            "field": "ads", "value": "Runs Google ads", "evidence_ids": ["ev-google"],
            "confidence": 0.7, "visibility": "message_safe",
        }],
        "unknowns": [],
    }
    request = ExperimentInput(
        "running-ads-offer-intelligence", "saas-01", "gpt-4.1-mini", _dossier(),
    )
    execution = _client(tmp_path, legacy).execute((request,), ExecutionTrack.SYNCHRONOUS)[0]

    assert execution.assertions[0].value == "Runs Google ads"
    assert execution.assertions[0].confidence == 0.7


def test_field_keyed_contract_raises_output_token_cap(tmp_path: Path):
    from scripts.company_enrichment.openai_model_client import OpenAIModelClient
    field_keyed = _request("news-product-launches")
    assert OpenAIModelClient._max_output_tokens("gpt-4.1-mini", field_keyed) == 4096
    # ICP and legacy requests keep the historical default
    assert OpenAIModelClient._max_output_tokens("gpt-4.1-mini") == 1024
