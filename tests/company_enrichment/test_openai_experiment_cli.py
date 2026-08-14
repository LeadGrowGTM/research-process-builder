from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from scripts.company_enrichment import experiment_program
from scripts.company_enrichment.contracts import ReviewStatus
from scripts.company_enrichment.experiment_program import ExperimentProgramResult
from scripts.company_enrichment.experiment_runner import ExperimentSummary


class _Client:
    pass


class _Program:
    clients = []
    paid = []

    def __init__(self, *, model_client, **_kwargs):
        self.clients.append(model_client)

    def run(self, enrichment_id, *, allow_paid=False, resume=False):
        self.paid.append((enrichment_id, allow_paid, resume))
        summary = ExperimentSummary(
            enrichment_id, "experiment", False, 0, 0, 24, 0, 0,
            Decimal("0"), Decimal("1"),
        )
        return ExperimentProgramResult(
            summary, ReviewStatus.EXPERIMENT, Path("review.jsonl"),
        )


def test_cli_uses_injected_key_only_with_explicit_paid_opt_in(
    monkeypatch: pytest.MonkeyPatch, tmp_path, capsys,
) -> None:
    _Program.clients.clear()
    _Program.paid.clear()
    monkeypatch.setenv("OPENAI_API_KEY", "secret-test-value")
    monkeypatch.setattr(experiment_program, "ExperimentProgram", _Program)
    code = experiment_program.main([
        "--enrichment", "company-description",
        "--artifact-root", str(tmp_path),
        "--allow-paid",
        "--resume",
    ], model_client_factory=lambda *, artifact_root: _Client())
    output = capsys.readouterr().out

    assert code == 2
    assert isinstance(_Program.clients[0], _Client)
    assert _Program.paid == [("company-description", True, True)]
    assert "secret-test-value" not in output
    json.loads(output)


def test_cli_does_not_build_live_client_without_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    _Program.clients.clear()
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(experiment_program, "ExperimentProgram", _Program)
    experiment_program.main([
        "--enrichment", "growth-signals",
        "--artifact-root", str(tmp_path),
    ], model_client_factory=lambda **_kwargs: pytest.fail(
        "client must not be built",
    ))

    assert _Program.clients == [None]
