from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.company_enrichment.benchmark import ExecutionTrack
from scripts.company_enrichment.contracts import CompanyDossier, EvidenceRef, canonical_json
from scripts.company_enrichment.experiment_runner import ExperimentInput
from scripts.company_enrichment.openai_model_client import OpenAIModelClient


NOW = datetime(2026, 8, 14, tzinfo=timezone.utc)
PROMPT = "Return structured ICPs using only supplied Evidence."
SECRET_PROMPT = PROMPT + " Do not persist sk-super-secret-test-value."


def _object(properties, required):
    return {
        "type": "object", "properties": properties, "required": required,
        "additionalProperties": False,
    }


EVIDENCE_IDS = {
    "type": "array", "items": {"type": "string", "enum": ["ev-1"]},
    "minItems": 1, "uniqueItems": True,
}
SEGMENT = _object({
    "buyer": {"type": "string"}, "need": {"type": "string"},
    "object": {"type": "string"}, "evidence_ids": EVIDENCE_IDS,
}, ["buyer", "need", "object", "evidence_ids"])
OUTPUT_CONTRACT = _object({
    "primary_icp": SEGMENT,
    "secondary_icps": {"type": "array", "items": SEGMENT, "maxItems": 2},
    "outcomes": {"type": "array", "items": _object({
        "text": {"type": "string"}, "evidence_ids": EVIDENCE_IDS,
    }, ["text", "evidence_ids"])},
    "observed_personas": {"type": "array", "items": _object({
        "role": {"type": "string"}, "evidence_ids": EVIDENCE_IDS,
    }, ["role", "evidence_ids"])},
    "inferred_personas": {"type": "array", "items": _object({
        "role": {"type": "string"}, "based_on_evidence_ids": EVIDENCE_IDS,
    }, ["role", "based_on_evidence_ids"])},
}, [
    "primary_icp", "secondary_icps", "outcomes", "observed_personas",
    "inferred_personas",
])
OUTPUT = {
    "primary_icp": {
        "buyer": "Marketing agencies", "need": "automated reporting",
        "object": "multi-channel client campaigns", "evidence_ids": ["ev-1"],
    },
    "secondary_icps": [],
    "outcomes": [{"text": "save reporting time", "evidence_ids": ["ev-1"]}],
    "observed_personas": [],
    "inferred_personas": [{
        "role": "agency reporting manager", "based_on_evidence_ids": ["ev-1"],
    }],
}


def _request(*, prompt_id="candidate-1", prompt_text=PROMPT,
             output_contract=OUTPUT_CONTRACT):
    evidence = EvidenceRef(
        "ev-1", "https://example.test/about", NOW, "a" * 64,
        "Marketing agencies automate reporting for multi-channel campaigns.",
    )
    return ExperimentInput(
        "icp-persona-analysis", "saas-01", "gpt-4.1-mini",
        CompanyDossier("saas-01", "1.0", (), (evidence,)),
        prompt_id, prompt_text, output_contract,
    )


def _response():
    return SimpleNamespace(
        id="resp_icp", model="gpt-4.1-mini-2025-04-14",
        output_text=json.dumps(OUTPUT),
        usage=SimpleNamespace(input_tokens=100, output_tokens=50),
        model_dump=lambda mode="json": {
            "id": "resp_icp", "model": "gpt-4.1-mini-2025-04-14",
            "input": SECRET_PROMPT,
            "instructions": "Follow this instruction: " + SECRET_PROMPT,
            "output_text": json.dumps(OUTPUT),
            "usage": {"input_tokens": 100, "output_tokens": 50},
            "diagnostics": {
                "input": SECRET_PROMPT,
                "instructions": SECRET_PROMPT,
            },
        },
    )


def _hashes(request):
    return {
        "prompt_id": request.prompt_id,
        "prompt_sha256": sha256(request.prompt_text.encode()).hexdigest(),
        "output_contract_sha256": sha256(
            canonical_json(request.output_contract).encode()
        ).hexdigest(),
    }


def test_experiment_input_deep_freezes_output_contract():
    source = {"type": "object", "properties": {"answer": {"type": "string"}}}
    request = _request(output_contract=source)
    source["properties"]["answer"]["type"] = "number"

    assert request.output_contract["properties"]["answer"]["type"] == "string"
    with pytest.raises(TypeError):
        request.output_contract["properties"]["answer"]["type"] = "boolean"


def test_icp_override_uses_candidate_prompt_nested_schema_and_identity_digest(
    tmp_path: Path,
):
    client = OpenAIModelClient(artifact_root=tmp_path, sdk_client=SimpleNamespace())
    request = _request()
    body = client._body(request)

    assert body["input"].startswith(PROMPT)
    assert body["text"]["format"]["schema"]["properties"]["primary_icp"]
    assert body["text"]["format"]["schema"] == OUTPUT_CONTRACT
    assert client._request_digest(request) != client._request_digest(
        replace(request, prompt_id="candidate-2")
    )


def test_sync_artifact_hashes_and_structured_field_values(tmp_path: Path):
    class Responses:
        def create(self, **_kwargs):
            return _response()

    request = _request(prompt_text=SECRET_PROMPT)
    client = OpenAIModelClient(
        artifact_root=tmp_path, sdk_client=SimpleNamespace(responses=Responses()),
    )
    execution = client.execute((request,), ExecutionTrack.SYNCHRONOUS)[0]

    values = {item.field: item.value for item in execution.assertions}
    assert values["icp"] == {
        "primary_icp": OUTPUT["primary_icp"], "secondary_icps": [],
        "outcomes": OUTPUT["outcomes"],
    }
    assert values["personas"] == {
        "observed_personas": [], "inferred_personas": OUTPUT["inferred_personas"],
    }
    state_path = next((tmp_path / "openai" / "sync").glob("*.json"))
    state_text = state_path.read_text(encoding="utf-8")
    state = json.loads(state_text)
    assert {key: state[key] for key in _hashes(request)} == _hashes(request)
    assert state["requested_model_id"] == "gpt-4.1-mini"
    assert state["execution"]["resolved_model_id"] == "gpt-4.1-mini-2025-04-14"
    provider = state["provider_response"]
    assert provider["input"] == "[redacted]"
    assert provider["instructions"] == "[redacted]"
    assert provider["diagnostics"] == {
        "input": "[redacted]", "instructions": "[redacted]",
    }
    assert provider["usage"]["input_tokens"] == 100
    assert SECRET_PROMPT not in state_text
    assert "sk-super-secret-test-value" not in state_text


class _BatchFiles:
    def __init__(self):
        self.rows = ""

    def create(self, *, file, purpose):
        assert purpose == "batch"
        custom_ids = [json.loads(line)["custom_id"] for line in
                      file.getvalue().decode().splitlines()]
        self.rows = "\n".join(json.dumps({
            "custom_id": custom_id, "error": None,
            "response": {"status_code": 200, "body": {
                "model": "gpt-4.1-mini-2025-04-14",
                "input": SECRET_PROMPT,
                "instructions": "Follow this instruction: " + SECRET_PROMPT,
                "output_text": json.dumps(OUTPUT),
                "usage": {"input_tokens": 100, "output_tokens": 50},
                "diagnostics": {
                    "input": SECRET_PROMPT,
                    "instructions": SECRET_PROMPT,
                },
            }},
        }) for custom_id in custom_ids)
        return SimpleNamespace(id="file_input")

    def content(self, output_file_id):
        assert output_file_id == "file_output"
        return SimpleNamespace(text=self.rows)


class _BatchJobs:
    def create(self, **_kwargs):
        return SimpleNamespace(id="batch_1", status="validating")

    def retrieve(self, batch_id):
        assert batch_id == "batch_1"
        return SimpleNamespace(status="completed", output_file_id="file_output")


def test_batch_artifact_records_exact_hashes_without_prompt_secret(tmp_path: Path):
    request = _request(prompt_text=SECRET_PROMPT)
    client = OpenAIModelClient(
        artifact_root=tmp_path,
        sdk_client=SimpleNamespace(files=_BatchFiles(), batches=_BatchJobs()),
        poll_interval_seconds=0,
    )
    execution = client.execute((request,), ExecutionTrack.BATCH)[0]

    state_path = next((tmp_path / "openai" / "batch").glob("*.json"))
    state_text = state_path.read_text(encoding="utf-8")
    state = json.loads(state_text)
    assert state["request_metadata"] == [{
        "company_id": "saas-01", "enrichment_id": "icp-persona-analysis",
        "requested_model_id": "gpt-4.1-mini", **_hashes(request),
    }]
    assert state["executions"][0]["resolved_model_id"] == execution.resolved_model_id
    body = state["provider_output"][0]["response"]["body"]
    assert body["input"] == "[redacted]"
    assert body["instructions"] == "[redacted]"
    assert body["diagnostics"] == {
        "input": "[redacted]", "instructions": "[redacted]",
    }
    assert body["usage"]["input_tokens"] == 100
    assert SECRET_PROMPT not in state_text
    assert "sk-super-secret-test-value" not in state_text
