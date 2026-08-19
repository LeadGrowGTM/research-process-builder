from __future__ import annotations

from decimal import Decimal

import pytest

from scripts.company_enrichment.adapters.parallel_task import (
    ALLOWED_TASK_PROCESSORS, ParallelTaskClient, build_parallel_task,
)
from scripts.company_enrichment.budgets import BudgetLedger
from scripts.company_enrichment.providers import (
    AuthenticationFailure, BudgetFailure, ContractFailure, RetryableFailure,
    TerminalFailure,
)

SUBMITTED = {"run_id": "trun_1", "status": "queued", "processor": "base"}


class RecordingPost:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def __call__(self, url, payload, headers):
        self.calls.append((url, payload, headers))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class RecordingGet:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def __call__(self, url, headers):
        self.calls.append((url, headers))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _client(post_response=SUBMITTED, get_response=None, **kwargs):
    post = RecordingPost(post_response)
    get = RecordingGet(get_response or {})
    return ParallelTaskClient(post, get, "secret-key", **kwargs), post, get


def _completed_result(content, basis=None):
    return {
        "run": {"run_id": "trun_1", "status": "completed"},
        "output": {"type": "json", "content": content, "basis": basis or []},
    }


def test_create_run_posts_payload_and_charges_budget():
    client, post, _ = _client(processor="base", budget_usd="0.05")

    run = client.create_run("What does acme.example sell?")

    url, payload, headers = post.calls[0]
    assert url == "https://api.parallel.ai/v1/tasks/runs"
    assert payload == {"processor": "base", "input": "What does acme.example sell?"}
    assert headers["x-api-key"] == "secret-key"
    assert run.run_id == "trun_1" and run.status == "queued"
    assert run.cost_usd == ALLOWED_TASK_PROCESSORS["base"] == "0.01"
    assert client.spent_usd == "0.01"
    assert client.remaining_usd == "0.04"


def test_output_schema_wraps_into_task_spec():
    client, post, _ = _client()
    schema = {"type": "object", "properties": {"answer": {"type": "string"}}}

    client.create_run({"company": "acme"}, output_schema=schema,
                      source_policy={"after_date": "2025-08-19"})

    payload = post.calls[0][1]
    assert payload["task_spec"] == {"output_schema": {"type": "json", "json_schema": schema}}
    assert payload["source_policy"] == {"after_date": "2025-08-19"}


def test_processors_above_base_are_rejected_at_construction():
    for processor in ("core", "core2x", "pro", "ultra", "ultra2x"):
        with pytest.raises(ValueError):
            _client(processor=processor)


def test_budget_exhaustion_blocks_submit_before_any_http():
    client, post, _ = _client(processor="base", budget_usd="0.015")

    client.create_run("first")
    assert client.spent_usd == "0.01"
    with pytest.raises(BudgetFailure):
        client.create_run("second")
    assert len(post.calls) == 1
    assert client.spent_usd == "0.01"


def test_ambiguous_submit_failure_is_terminal_not_retryable_and_stays_charged():
    client, post, _ = _client(post_response=RetryableFailure("HTTP 503"))
    with pytest.raises(TerminalFailure):
        client.create_run("q")
    assert client.spent_usd == "0.01"

    client2, _, _ = _client(post_response=RuntimeError("socket reset secret-key"))
    with pytest.raises(TerminalFailure) as info:
        client2.create_run("q")
    assert "secret-key" not in str(info.value)


def test_submit_auth_and_contract_failures_pass_through():
    client, _, _ = _client(post_response=AuthenticationFailure("rejected"))
    with pytest.raises(AuthenticationFailure):
        client.create_run("q")
    assert client.spent_usd == "0.00"
    client, _, _ = _client(post_response=ContractFailure("bad request"))
    with pytest.raises(ContractFailure):
        client.create_run("q")
    assert client.spent_usd == "0.00"
    client, _, _ = _client(post_response="not an object")
    with pytest.raises(ContractFailure):
        client.create_run("q")
    assert client.spent_usd == "0.01"
    client, _, _ = _client(post_response={"status": "queued"})
    with pytest.raises(ContractFailure):
        client.create_run("q")
    assert client.spent_usd == "0.01"


def test_definite_no_run_response_stays_charged_not_refunded():
    client, _, _ = _client(post_response={"run_id": "trun_1"})
    with pytest.raises(ContractFailure):
        client.create_run("q")
    assert client.spent_usd == "0.01"


def test_ledger_reserve_release_on_auth_and_contract_failures(tmp_path):
    ledger = BudgetLedger(tmp_path / "costs.jsonl", {"parallel-task": "1.00"})
    client, _, _ = _client(
        post_response=AuthenticationFailure("rejected"), ledger=ledger,
    )
    with pytest.raises(AuthenticationFailure):
        client.create_run("q")
    assert client.spent_usd == "0.00"
    assert ledger.reserved("parallel-task") == Decimal("0")
    assert ledger.spent("parallel-task") == Decimal("0")

    client, _, _ = _client(post_response=ContractFailure("bad request"), ledger=ledger)
    with pytest.raises(ContractFailure):
        client.create_run("q")
    assert client.spent_usd == "0.00"
    assert ledger.reserved("parallel-task") == Decimal("0")
    assert ledger.spent("parallel-task") == Decimal("0")


def test_ledger_reconciles_charged_spend_on_ambiguous_transport_failure(tmp_path):
    ledger = BudgetLedger(tmp_path / "costs.jsonl", {"parallel-task": "1.00"})
    client, _, _ = _client(
        post_response=RetryableFailure("HTTP 503"), ledger=ledger,
    )
    with pytest.raises(TerminalFailure):
        client.create_run("q")
    assert client.spent_usd == "0.01"
    assert ledger.reserved("parallel-task") == Decimal("0")
    assert ledger.spent("parallel-task") == Decimal("0.01")


def test_ledger_reconciles_on_budget_failure_raised_by_transport(tmp_path):
    ledger = BudgetLedger(tmp_path / "costs.jsonl", {"parallel-task": "1.00"})
    client, _, _ = _client(
        post_response=BudgetFailure("blocked upstream"), ledger=ledger,
    )
    with pytest.raises(BudgetFailure):
        client.create_run("q")
    assert client.spent_usd == "0.01"
    assert ledger.reserved("parallel-task") == Decimal("0")
    assert ledger.spent("parallel-task") == Decimal("0.01")


def test_ledger_reconciles_on_successful_submit(tmp_path):
    ledger = BudgetLedger(tmp_path / "costs.jsonl", {"parallel-task": "1.00"})
    client, _, _ = _client(ledger=ledger)
    client.create_run("q")
    assert client.spent_usd == "0.01"
    assert ledger.reserved("parallel-task") == Decimal("0")
    assert ledger.spent("parallel-task") == Decimal("0.01")


def test_ledger_enforces_durable_ceiling_across_two_client_instances(tmp_path):
    ledger = BudgetLedger(tmp_path / "costs.jsonl", {"parallel-task": "0.015"})
    first, _, _ = _client(ledger=ledger)
    second, _, _ = _client(ledger=ledger)

    first.create_run("first")
    assert first.spent_usd == "0.01"

    with pytest.raises(BudgetFailure):
        second.create_run("second")
    assert second.spent_usd == "0"
    assert ledger.spent("parallel-task") == Decimal("0.01")


def test_get_result_parses_output_and_citation_observations():
    basis = [{
        "field": "answer",
        "citations": [
            {"url": "https://acme.example/about", "title": "About Acme",
             "excerpts": ["Acme serves finance teams."]},
            {"url": "https://acme.example/about", "excerpts": ["duplicate dropped"]},
            {"url": "not a url", "excerpts": ["dropped"]},
        ],
        "reasoning": "found on site", "confidence": "high",
    }]
    client, _, get = _client(get_response=_completed_result({"answer": "dashboards"}, basis))

    result = client.get_result("trun_1", timeout_seconds=30)

    assert get.calls[0][0] == "https://api.parallel.ai/v1/tasks/runs/trun_1/result?timeout=30"
    assert result.content == {"answer": "dashboards"}
    assert result.output_type == "json"
    assert len(result.citations) == 1
    assert result.citations[0].url == "https://acme.example/about"
    assert "About Acme" in result.citations[0].excerpt


def test_get_result_active_run_is_retryable_and_failed_run_terminal():
    client, _, _ = _client(get_response={
        "run": {"status": "running"}, "output": {},
    })
    with pytest.raises(RetryableFailure):
        client.get_result("trun_1")
    client, _, _ = _client(get_response={
        "run": {"status": "failed"}, "output": {},
    })
    with pytest.raises(TerminalFailure):
        client.get_result("trun_1")
    client, _, _ = _client(get_response={"run": "nope"})
    with pytest.raises(ContractFailure):
        client.get_result("trun_1")


def test_client_rejects_bad_configuration():
    with pytest.raises(AuthenticationFailure):
        ParallelTaskClient(RecordingPost({}), RecordingGet({}), "")
    with pytest.raises(ValueError):
        _client(budget_usd="not-a-number")
    with pytest.raises(ValueError):
        _client(budget_usd="0")
    client, _, _ = _client()
    with pytest.raises(ValueError):
        client.create_run("")
    with pytest.raises(ValueError):
        client.get_result("")


def test_build_parallel_task_reads_env_only_inside_factory(monkeypatch):
    monkeypatch.delenv("PARALLEL_API_KEY", raising=False)
    with pytest.raises(AuthenticationFailure):
        build_parallel_task()
    monkeypatch.setenv("PARALLEL_API_KEY", "abc")
    post = RecordingPost(SUBMITTED)
    get = RecordingGet(_completed_result({"answer": "x"}))
    client = build_parallel_task(processor="lite", budget_usd="0.10",
                                 http_post=post, http_get=get)
    client.create_run("q")
    assert post.calls[0][2]["x-api-key"] == "abc"
