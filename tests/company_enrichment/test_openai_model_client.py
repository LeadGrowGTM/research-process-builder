from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
from types import SimpleNamespace

from scripts.company_enrichment.benchmark import ExecutionTrack
from scripts.company_enrichment.contracts import (
    CompanyDossier,
    EvidenceRef,
    FieldAssertion,
    HumanCorrection,
    Visibility,
)
from scripts.company_enrichment.experiment_runner import ExperimentInput
from scripts.company_enrichment.openai_model_client import MODEL_PRICES, OpenAIModelClient


NOW = datetime(2026, 8, 13, tzinfo=timezone.utc)


def _request(model: str = "gpt-4.1-mini") -> ExperimentInput:
    evidence = EvidenceRef(
        "ev-1", "https://example.test/about", NOW, "a" * 64,
        "Example builds reporting software for marketing teams.",
    )
    dossier = CompanyDossier(
        "saas-01", "1.0",
        (FieldAssertion(
            "description", "BENCHMARK TRUTH MUST NOT LEAK", ("ev-1",),
            1.0, Visibility.MESSAGE_SAFE,
        ),),
        (evidence,),
        corrections=(HumanCorrection(
            "correction-1", "description", "SECRET CORRECTED TRUTH",
            "reviewer", NOW,
        ),),
    )
    return ExperimentInput("company-description", "saas-01", model, dossier)


class _SyncResponses:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            id="resp_123",
            model="gpt-4.1-mini-2025-04-14",
            output_text=(
                '{"assertions":[{"field":"description","value":"Reporting '
                'software","evidence_ids":["ev-1"],"confidence":0.9,'
                '"visibility":"message_safe"}],"unknowns":["identity","offers"]}'
            ),
            usage=SimpleNamespace(input_tokens=1000, output_tokens=100),
            model_dump=lambda mode="json": {
                "id": "resp_123",
                "model": "gpt-4.1-mini-2025-04-14",
                "output_text": "persisted raw response",
                "usage": {"input_tokens": 1000, "output_tokens": 100},
            },
        )


def test_sync_uses_evidence_only_structured_responses_and_is_idempotent(
    tmp_path: Path,
) -> None:
    responses = _SyncResponses()
    sdk = SimpleNamespace(responses=responses)
    client = OpenAIModelClient(artifact_root=tmp_path, sdk_client=sdk)

    first = client.execute((_request(),), ExecutionTrack.SYNCHRONOUS)
    second = client.execute((_request(),), ExecutionTrack.SYNCHRONOUS)

    assert first == second
    assert len(responses.calls) == 1
    call = responses.calls[0]
    assert call["model"] == "gpt-4.1-mini"
    assert call["store"] is True
    assert call["text"]["format"]["type"] == "json_schema"
    assert call["text"]["format"]["strict"] is True
    assert "Example builds reporting software" in call["input"]
    assert "BENCHMARK TRUTH" not in call["input"]
    assert "SECRET CORRECTED TRUTH" not in call["input"]
    assert first[0].resolved_model_id == "gpt-4.1-mini-2025-04-14"
    assert first[0].actual_cost_usd == str(
        Decimal("1000") * Decimal("0.40") / Decimal("1000000")
        + Decimal("100") * Decimal("1.60") / Decimal("1000000")
    )
    state_files = tuple((tmp_path / "openai" / "sync").glob("*.json"))
    assert len(state_files) == 1
    state = state_files[0].read_text(encoding="utf-8")
    assert "resp_123" in state
    assert "persisted raw response" in state


def test_price_table_estimate_and_cached_usage_use_decimal_rates(
    tmp_path: Path,
) -> None:
    assert MODEL_PRICES["gpt-5-nano"].input_per_million == Decimal("0.05")
    assert MODEL_PRICES["gpt-4.1-mini"].cached_input_per_million == Decimal("0.10")
    assert MODEL_PRICES["gpt-4.1-mini"].batch_output_per_million == Decimal("0.80")
    # gpt-5.6-luna list rates as of 2026-08-20 (Mitch): 0.20 in / 0.02 cache
    # read / 0.25 cache write / 1.20 out; batch is 50% of in/out.
    assert MODEL_PRICES["gpt-5.6-luna"].input_per_million == Decimal("0.20")
    assert MODEL_PRICES["gpt-5.6-luna"].cached_input_per_million == Decimal("0.02")
    assert MODEL_PRICES["gpt-5.6-luna"].cache_write_input_per_million == Decimal("0.25")
    assert MODEL_PRICES["gpt-5.6-luna"].output_per_million == Decimal("1.20")
    assert MODEL_PRICES["gpt-5.6-luna"].batch_input_per_million == Decimal("0.10")
    # gpt-4o-mini is a benchmark model, not production-approved (Mitch, 2026-08-20).
    assert MODEL_PRICES["gpt-4o-mini"].input_per_million == Decimal("0.15")
    assert MODEL_PRICES["gpt-4o-mini"].output_per_million == Decimal("0.60")
    estimator = OpenAIModelClient(
        artifact_root=tmp_path / "estimate", sdk_client=SimpleNamespace(),
    )
    synchronous = Decimal(estimator.estimate(
        (_request("gpt-4.1-mini"),), ExecutionTrack.SYNCHRONOUS,
    ))
    batch = Decimal(estimator.estimate(
        (_request("gpt-4.1-mini"),), ExecutionTrack.BATCH,
    ))
    assert synchronous > 0
    assert batch == synchronous / 2

    responses = _SyncResponses()
    original_create = responses.create

    def with_cache(**kwargs):
        response = original_create(**kwargs)
        response.usage.input_tokens_details = SimpleNamespace(cached_tokens=400)
        return response

    responses.create = with_cache
    execution = OpenAIModelClient(
        artifact_root=tmp_path / "actual",
        sdk_client=SimpleNamespace(responses=responses),
    ).execute((_request(),), ExecutionTrack.SYNCHRONOUS)[0]
    expected = (
        Decimal("600") * Decimal("0.40")
        + Decimal("400") * Decimal("0.10")
        + Decimal("100") * Decimal("1.60")
    ) / Decimal("1000000")
    assert execution.actual_cost_usd == str(expected)

    # Cache writes bill at the model's write rate on the synchronous track
    # (gpt-5.6-luna writes cost more than plain input: 0.25 vs 0.20).
    write_responses = _SyncResponses()
    original_write_create = write_responses.create

    def with_cache_write(**kwargs):
        response = original_write_create(**kwargs)
        response.usage.input_tokens_details = SimpleNamespace(
            cached_tokens=100, cache_write_tokens=700,
        )
        return response

    write_responses.create = with_cache_write
    luna_execution = OpenAIModelClient(
        artifact_root=tmp_path / "cache-write",
        sdk_client=SimpleNamespace(responses=write_responses),
    ).execute((_request("gpt-5.6-luna"),), ExecutionTrack.SYNCHRONOUS)[0]
    luna_expected = (
        Decimal("200") * Decimal("0.20")
        + Decimal("100") * Decimal("0.02")
        + Decimal("700") * Decimal("0.25")
        + Decimal("100") * Decimal("1.20")
    ) / Decimal("1000000")
    assert luna_execution.actual_cost_usd == str(luna_expected)


def test_estimate_covers_full_request_body_and_provider_framing(
    tmp_path: Path,
) -> None:
    class ConservativeUsageResponses(_SyncResponses):
        def create(self, **kwargs):
            response = super().create(**kwargs)
            body = {key: value for key, value in kwargs.items() if key != 'extra_headers'}
            input_tokens = len(json.dumps(
                body, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
            ).encode('utf-8')) + 1024
            response.usage = SimpleNamespace(
                input_tokens=input_tokens,
                output_tokens=kwargs['max_output_tokens'],
            )
            return response

    request = _request('gpt-4.1-mini')
    responses = ConservativeUsageResponses()
    client = OpenAIModelClient(
        artifact_root=tmp_path,
        sdk_client=SimpleNamespace(responses=responses),
    )

    estimate = Decimal(client.estimate((request,), ExecutionTrack.SYNCHRONOUS))
    actual = Decimal(client.execute((request,), ExecutionTrack.SYNCHRONOUS)[0].actual_cost_usd)

    assert actual <= estimate


def test_provider_errors_never_echo_credentials(tmp_path: Path) -> None:
    class _FailingResponses:
        def create(self, **_kwargs):
            raise RuntimeError("Authorization: Bearer sk-super-secret-test-value")

    client = OpenAIModelClient(
        artifact_root=tmp_path,
        sdk_client=SimpleNamespace(responses=_FailingResponses()),
    )

    try:
        client.execute((_request(),), ExecutionTrack.SYNCHRONOUS)
    except Exception as error:
        message = str(error)
    else:
        raise AssertionError("provider error should propagate in sanitized form")

    assert "OpenAI Responses create failed" in message
    assert "sk-super-secret-test-value" not in message
    assert "Bearer" not in message


def test_model_mistakes_are_preserved_for_benchmark_scoring(tmp_path: Path) -> None:
    responses = _SyncResponses()
    original = responses.create

    def duplicate_output(**kwargs):
        response = original(**kwargs)
        response.output_text = json.dumps({
            "assertions": [
                {"field": "description", "value": "First", "evidence_ids": ["ev-1"], "confidence": 0.8, "visibility": "message_safe"},
                {"field": "description", "value": "Second", "evidence_ids": ["ev-1"], "confidence": 0.7, "visibility": "message_safe"},
            ],
            "unknowns": ["identity", "description", "offers"],
        })
        return response

    responses.create = duplicate_output
    client = OpenAIModelClient(
        artifact_root=tmp_path, sdk_client=SimpleNamespace(responses=responses),
    )

    execution = client.execute((_request(),), ExecutionTrack.SYNCHRONOUS)[0]

    assert [item.field for item in execution.assertions] == [
        "description", "description",
    ]
    assert execution.unknowns == ("identity", "description", "offers")


def test_response_is_persisted_before_decode_failure(tmp_path: Path) -> None:
    responses = _SyncResponses()
    original = responses.create

    def invalid_output(**kwargs):
        response = original(**kwargs)
        response.output_text = "not-json"
        response.model_dump = lambda mode="json": {
            "model": response.model, "output_text": "not-json",
            "usage": {"input_tokens": 10, "output_tokens": 2},
        }
        return response

    responses.create = invalid_output
    client = OpenAIModelClient(
        artifact_root=tmp_path, sdk_client=SimpleNamespace(responses=responses),
    )

    for _attempt in range(2):
        try:
            client.execute((_request(),), ExecutionTrack.SYNCHRONOUS)
        except ValueError as error:
            assert "invalid structured JSON" in str(error)
        else:
            raise AssertionError("invalid provider JSON must fail decoding")

    assert len(responses.calls) == 2
    assert len({
        call["extra_headers"]["Idempotency-Key"] for call in responses.calls
    }) == 2
    state = json.loads(next(
        (tmp_path / "openai" / "sync").glob("*.json")
    ).read_text(encoding="utf-8"))
    assert state["status"] == "terminal"
    assert state["attempt_index"] == 1
    assert len(state["attempt_history"]) == 1


def test_request_reserves_four_thousand_output_tokens(tmp_path: Path) -> None:
    responses = _SyncResponses()
    client = OpenAIModelClient(
        artifact_root=tmp_path, sdk_client=SimpleNamespace(responses=responses),
    )

    client.execute((_request("gpt-5-nano"),), ExecutionTrack.SYNCHRONOUS)

    assert responses.calls[0]["max_output_tokens"] == 4096
    assert responses.calls[0]["reasoning"] == {"effort": "minimal"}


class _BatchFiles:
    def __init__(self, output_factory=None) -> None:
        self.created: list[dict] = []
        self.custom_ids: list[str] = []
        self.output_factory = output_factory
        self.content_calls = 0

    def create(self, **kwargs):
        self.created.append(kwargs)
        payload = kwargs["file"].read().decode("utf-8")
        self.custom_ids = [
            json.loads(line)["custom_id"] for line in payload.splitlines()
        ]
        return SimpleNamespace(id="file_input")

    def content(self, file_id):
        self.content_calls += 1
        assert file_id == "file_output"
        rows = self.output_factory(self.custom_ids)
        return SimpleNamespace(text="\n".join(json.dumps(row) for row in rows))


def _batch_rows(custom_ids):
    rows = []
    for custom_id in custom_ids:
        rows.append({
            "custom_id": custom_id,
            "error": None,
            "response": {
                "status_code": 200,
                "body": {
                    "id": "resp_batch",
                    "model": "gpt-4.1-mini-2025-04-14",
                    "output": [{
                        "type": "message",
                        "content": [{
                            "type": "output_text",
                            "text": (
                                '{"assertions":[{"field":"description",'
                                '"value":"Reporting software","evidence_ids":["ev-1"],'
                                '"confidence":0.9,"visibility":"message_safe"}],'
                                '"unknowns":["identity","offers"]}'
                            ),
                        }],
                    }],
                    "usage": {
                        "input_tokens": 1000,
                        "input_tokens_details": {"cached_tokens": 200},
                        "output_tokens": 100,
                    },
                },
            },
        })
    return rows


class _BatchJobs:
    def __init__(self, *, fail_retrieve=False) -> None:
        self.created: list[dict] = []
        self.retrieved: list[str] = []
        self.fail_retrieve = fail_retrieve

    def create(self, **kwargs):
        self.created.append(kwargs)
        return SimpleNamespace(
            id="batch_123", status="in_progress", input_file_id="file_input",
            output_file_id=None, error_file_id=None,
        )

    def retrieve(self, batch_id):
        self.retrieved.append(batch_id)
        if self.fail_retrieve:
            raise RuntimeError("Bearer sk-batch-secret-value")
        return SimpleNamespace(
            id=batch_id, status="completed", input_file_id="file_input",
            output_file_id="file_output", error_file_id=None,
        )


def test_batch_uploads_responses_jsonl_and_records_actual_discounted_cost(
    tmp_path: Path,
) -> None:
    files = _BatchFiles(_batch_rows)
    batches = _BatchJobs()
    client = OpenAIModelClient(
        artifact_root=tmp_path,
        sdk_client=SimpleNamespace(files=files, batches=batches),
        poll_interval_seconds=0,
    )

    result = client.execute((_request(),), ExecutionTrack.BATCH)

    assert files.created[0]["purpose"] == "batch"
    created = batches.created[0]
    assert created["completion_window"] == "24h"
    assert created["endpoint"] == "/v1/responses"
    assert created["input_file_id"] == "file_input"
    assert created["extra_headers"]["Idempotency-Key"].startswith(
        "company-enrichment-batch-",
    )
    assert result[0].resolved_model_id == "gpt-4.1-mini-2025-04-14"
    expected = (
        Decimal("800") * Decimal("0.20")
        + Decimal("200") * Decimal("0.05")
        + Decimal("100") * Decimal("0.80")
    ) / Decimal("1000000")
    assert result[0].actual_cost_usd == str(expected)
    state = next((tmp_path / "openai" / "batch").glob("*.json"))
    assert json.loads(state.read_text(encoding="utf-8"))["status"] == "completed"


def test_batch_job_is_persisted_before_polling_and_resumed_without_resubmit(
    tmp_path: Path,
) -> None:
    first_files = _BatchFiles(_batch_rows)
    first_batches = _BatchJobs(fail_retrieve=True)
    first = OpenAIModelClient(
        artifact_root=tmp_path,
        sdk_client=SimpleNamespace(files=first_files, batches=first_batches),
        poll_interval_seconds=0,
    )

    try:
        first.execute((_request(),), ExecutionTrack.BATCH)
    except BaseException as error:
        assert "sk-batch-secret-value" not in str(error)
    else:
        raise AssertionError("poll failure should propagate")

    state_path = next((tmp_path / "openai" / "batch").glob("*.json"))
    pending = json.loads(state_path.read_text(encoding="utf-8"))
    assert pending["batch_id"] == "batch_123"
    assert pending["status"] == "pending"
    assert pending["provider_status"] == "in_progress"
    assert first.has_pending((_request(),), ExecutionTrack.BATCH) is True

    resumed_files = _BatchFiles(_batch_rows)
    resumed_files.custom_ids = first_files.custom_ids
    resumed_batches = _BatchJobs()
    resumed = OpenAIModelClient(
        artifact_root=tmp_path,
        sdk_client=SimpleNamespace(files=resumed_files, batches=resumed_batches),
        poll_interval_seconds=0,
    )
    result = resumed.execute((_request(),), ExecutionTrack.BATCH)

    assert resumed_files.created == []
    assert resumed_batches.created == []
    assert resumed_batches.retrieved == ["batch_123"]
    assert result[0].company_id == "saas-01"
    assert resumed.has_pending((_request(),), ExecutionTrack.BATCH) is False


def test_failed_batch_row_is_terminal_and_retry_uses_new_attempt(
    tmp_path: Path,
) -> None:
    def failed_rows(custom_ids):
        rows = _batch_rows(custom_ids)
        rows[0]['response']['status_code'] = 500
        return rows

    first = OpenAIModelClient(
        artifact_root=tmp_path,
        sdk_client=SimpleNamespace(
            files=_BatchFiles(failed_rows), batches=_BatchJobs(),
        ),
        poll_interval_seconds=0,
    )

    try:
        first.execute((_request(),), ExecutionTrack.BATCH)
    except Exception as error:
        assert 'non-success response' in str(error)
    else:
        raise AssertionError('failed Batch row must fail the attempt')

    state_path = next((tmp_path / 'openai' / 'batch').glob('*.json'))
    assert json.loads(state_path.read_text(encoding='utf-8'))['status'] == 'terminal'

    resumed_files = _BatchFiles(_batch_rows)
    resumed_jobs = _BatchJobs()
    resumed = OpenAIModelClient(
        artifact_root=tmp_path,
        sdk_client=SimpleNamespace(files=resumed_files, batches=resumed_jobs),
        poll_interval_seconds=0,
    )
    result = resumed.execute((_request(),), ExecutionTrack.BATCH)

    assert result[0].company_id == 'saas-01'
    completed = json.loads(state_path.read_text(encoding='utf-8'))
    assert completed['status'] == 'completed'
    assert len(completed['attempt_history']) == 1


def test_terminal_batch_is_persisted_and_next_resume_submits_new_attempt(
    tmp_path: Path,
) -> None:
    class TerminalJobs(_BatchJobs):
        def retrieve(self, batch_id):
            self.retrieved.append(batch_id)
            return SimpleNamespace(
                id=batch_id, status='failed', input_file_id='file_input',
                output_file_id=None, error_file_id='file_error',
            )

    first_files = _BatchFiles(_batch_rows)
    first = OpenAIModelClient(
        artifact_root=tmp_path,
        sdk_client=SimpleNamespace(files=first_files, batches=TerminalJobs()),
        poll_interval_seconds=0,
    )

    try:
        first.execute((_request(),), ExecutionTrack.BATCH)
    except Exception as error:
        assert 'terminal status failed' in str(error)
    else:
        raise AssertionError('terminal Batch must fail the attempt')

    state_path = next((tmp_path / 'openai' / 'batch').glob('*.json'))
    terminal = json.loads(state_path.read_text(encoding='utf-8'))
    assert terminal['status'] == 'terminal'
    assert first.has_pending((_request(),), ExecutionTrack.BATCH) is False

    resumed_files = _BatchFiles(_batch_rows)
    resumed_jobs = _BatchJobs()
    resumed = OpenAIModelClient(
        artifact_root=tmp_path,
        sdk_client=SimpleNamespace(files=resumed_files, batches=resumed_jobs),
        poll_interval_seconds=0,
    )
    result = resumed.execute((_request(),), ExecutionTrack.BATCH)

    assert len(resumed_files.created) == 1
    assert len(resumed_jobs.created) == 1
    assert result[0].company_id == 'saas-01'
    completed = json.loads(state_path.read_text(encoding='utf-8'))
    assert completed['status'] == 'completed'
    assert len(completed['attempt_history']) == 1


def test_batch_output_is_persisted_before_decode_failure(tmp_path: Path) -> None:
    def invalid_rows(custom_ids):
        return [{
            "custom_id": custom_id,
            "error": None,
            "response": {
                "status_code": 200,
                "body": {
                    "model": "gpt-4.1-mini-2025-04-14",
                    "output": [{"type": "message", "content": [{
                        "type": "output_text", "text": "not-json",
                    }]}],
                    "usage": {"input_tokens": 10, "output_tokens": 2},
                },
            },
        } for custom_id in custom_ids]

    files = _BatchFiles(invalid_rows)
    batches = _BatchJobs()
    client = OpenAIModelClient(
        artifact_root=tmp_path,
        sdk_client=SimpleNamespace(files=files, batches=batches),
        poll_interval_seconds=0,
    )

    for _attempt in range(2):
        try:
            client.execute((_request(),), ExecutionTrack.BATCH)
        except ValueError as error:
            assert "invalid structured JSON" in str(error)
        else:
            raise AssertionError("invalid Batch JSON must fail decoding")

    assert files.content_calls == 2
    assert len(batches.created) == 2
    assert len({
        call["extra_headers"]["Idempotency-Key"] for call in batches.created
    }) == 2
    state = json.loads(next(
        (tmp_path / "openai" / "batch").glob("*.json")
    ).read_text(encoding="utf-8"))
    assert state["status"] == "terminal"
    assert state["attempt_index"] == 1
    assert len(state["attempt_history"]) == 1

def test_legacy_request_body_is_byte_for_byte_compatible(tmp_path: Path) -> None:
    client = OpenAIModelClient(
        artifact_root=tmp_path, sdk_client=SimpleNamespace(),
    )

    assert client._body(_request()) == {
        "model": "gpt-4.1-mini",
        "input": (
            "Produce the requested company enrichment using only the supplied "
            "Evidence. Do not infer unsupported facts. Cite one or more supplied "
            "evidence_id values for every assertion; put unsupported requested "
            "fields in unknowns. Company ID: saas-01\n"
            "Enrichment: company-description\n"
            'Requested fields: ["identity", "description", "offers"]\n'
            "Evidence: [{\"content_hash\": \"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\", \"evidence_id\": \"ev-1\", "
            "\"excerpt\": \"Example builds reporting software for marketing "
            "teams.\", \"retrieved_at\": \"2026-08-13T00:00:00+00:00\", "
            "\"url\": \"https://example.test/about\"}]"
        ),
        "max_output_tokens": 1024,
        "store": True,
        "text": {"format": {
            "type": "json_schema",
            "name": "company_enrichment",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "assertions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "field": {"type": "string", "enum": [
                                    "identity", "description", "offers",
                                ]},
                                "value": {"type": "string"},
                                "evidence_ids": {
                                    "type": "array",
                                    "items": {"type": "string", "enum": ["ev-1"]},
                                    "minItems": 1,
                                },
                                "confidence": {
                                    "type": "number", "minimum": 0, "maximum": 1,
                                },
                                "visibility": {"type": "string", "enum": [
                                    "message_safe", "filter_only",
                                ]},
                            },
                            "required": [
                                "field", "value", "evidence_ids", "confidence",
                                "visibility",
                            ],
                            "additionalProperties": False,
                        },
                    },
                    "unknowns": {"type": "array", "items": {
                        "type": "string",
                        "enum": ["identity", "description", "offers"],
                    }},
                },
                "required": ["assertions", "unknowns"],
                "additionalProperties": False,
            },
        }},
    }

    request = _request()
    assert client.request_fingerprint(request) == (
        "dce793046fd3aed2d5817a7ba80f8e08d4451f951a5e1bfc128963a6abaacad3"
    )
    assert client._batch_path((request,)).name == (
        "d54b534a1956b6d13caf325a5c0b989ab28b09be691c2747303d22c45fe95aae.json"
    )


def test_full_gpt41_tier_is_not_a_priced_model(tmp_path: Path) -> None:
    from scripts.company_enrichment.openai_model_client import MODEL_PRICES
    # Workspace policy: never the full gpt-4.1 tier. Keeping it out of the price
    # table makes estimate()/execute() reject it before any provider call.
    assert "gpt-4.1" not in MODEL_PRICES
    client = OpenAIModelClient(artifact_root=tmp_path, sdk_client=SimpleNamespace())
    try:
        client.estimate((_request("gpt-4.1"),), ExecutionTrack.SYNCHRONOUS)
    except ValueError:
        pass
    else:
        raise AssertionError("gpt-4.1 must be rejected before any provider call")
