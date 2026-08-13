from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path

import pytest

from scripts.company_enrichment.benchmark import BenchmarkRunner, ExecutionTrack
from scripts.company_enrichment.cli import _rehydrate_dossier
from scripts.company_enrichment.experiment_runner import (
    FIXED_SAAS_CORE,
    ExperimentRunner,
    ModelExecution,
)


AS_OF = datetime(2026, 8, 12, 23, 59, 59, tzinfo=timezone.utc)


@dataclass
class CountingClient:
    executions: int = 0

    def estimate(self, requests, track):
        return str(Decimal("0.01") * len(requests))

    def execute(self, requests, track):
        self.executions += 1
        values = []
        for request in requests:
            fields = {
                "company-description": ("identity", "description", "offers"),
                "icp-persona-analysis": ("icp", "personas"),
                "growth-signals": ("growth",),
            }[request.enrichment_id]
            assertions = tuple(
                item for item in request.dossier.assertions if item.field in fields
            )
            covered = {item.field for item in assertions}
            values.append(ModelExecution(
                request.company_id, assertions,
                tuple(field for field in fields if field not in covered),
                request.requested_model_id, 1, "0.01",
            ))
        return tuple(values)


def _runner(tmp_path: Path, client, fault_hook=None) -> ExperimentRunner:
    return ExperimentRunner(
        artifact_root=tmp_path,
        dossiers={
            item: _rehydrate_dossier(Path("benchmarks/dossiers") / f"{item}.yaml")
            for item in FIXED_SAAS_CORE
        },
        model_client=client,
        benchmark_runner=BenchmarkRunner(tmp_path),
        as_of=AS_OF,
        fault_hook=fault_hook,
    )


@pytest.mark.parametrize(
    "crash_step", ("after_reconcile", "after_partial_journal", "before_report"),
)
def test_resume_recovers_group_without_repurchase_after_crash(
    tmp_path: Path, crash_step: str,
) -> None:
    client = CountingClient()
    crashed = False

    def fault(step):
        nonlocal crashed
        if step == crash_step and not crashed:
            crashed = True
            raise RuntimeError("simulated process interruption")

    with pytest.raises(RuntimeError, match="simulated process interruption"):
        _runner(tmp_path, client, fault).run(
            "company-description", allow_paid=True,
        )
    calls_at_crash = client.executions

    summary = _runner(tmp_path, client).run(
        "company-description", allow_paid=True, resume=True,
    )

    assert client.executions == calls_at_crash + 7
    assert summary.completed_cases == 24
    rows = tuple(
        json.loads(line) for line in (
            tmp_path / "company-description" / "outcomes.jsonl"
        ).read_text(encoding="utf-8").splitlines()
    )
    assert len([row for row in rows if row["status"] == "completed"]) == 24
    assert len({
        (row["company_id"], row["requested_model_id"], row["execution_track"])
        for row in rows if row["status"] == "completed"
    }) == 24
    assert len(list(tmp_path.rglob("report.json"))) == 8
    budget_rows = tuple(
        json.loads(line) for line in (
            tmp_path / "company-description" / "budget.jsonl"
        ).read_text(encoding="utf-8").splitlines()
    )
    assert sum(row["kind"] == "reserve" for row in budget_rows) == 8
    assert sum(row["kind"] == "reconcile" for row in budget_rows) == 8


def test_invalid_model_output_is_journaled_and_not_promoted(tmp_path: Path) -> None:
    class InvalidClient(CountingClient):
        def execute(self, requests, track):
            values = list(super().execute(requests, track))
            values[0] = ModelExecution(
                values[0].company_id, (), (), values[0].resolved_model_id,
                1, "0.01",
            )
            return tuple(values)

    summary = _runner(tmp_path, InvalidClient()).run(
        "growth-signals", allow_paid=True,
    )
    rows = tuple(
        json.loads(line) for line in (
            tmp_path / "growth-signals" / "outcomes.jsonl"
        ).read_text(encoding="utf-8").splitlines()
    )

    assert summary.status == "experiment"
    assert any(
        row["status"] == "failed" and row["failure"] == "contract_invalid"
        for row in rows
    )
    assert len(list(tmp_path.rglob("report.json"))) == 8


def test_client_exception_is_journaled_as_retryable_failure(tmp_path: Path) -> None:
    class BrokenClient(CountingClient):
        def execute(self, requests, track):
            self.executions += 1
            if self.executions == 1:
                raise RuntimeError("provider timeout")
            return super().execute(requests, track)

    summary = _runner(tmp_path, BrokenClient()).run(
        "icp-persona-analysis", allow_paid=True,
    )
    rows = tuple(
        json.loads(line) for line in (
            tmp_path / "icp-persona-analysis" / "outcomes.jsonl"
        ).read_text(encoding="utf-8").splitlines()
    )

    assert summary.status == "experiment"
    assert sum(row["failure"] == "retryable" for row in rows) == 3
    assert not (
        tmp_path / "icp-persona-analysis" / "blind-output-map.json"
    ).exists()

    resumed = _runner(tmp_path, BrokenClient(executions=1)).run(
        "icp-persona-analysis", allow_paid=True, resume=True,
    )

    assert resumed.completed_cases == 24
    assert (
        tmp_path / "icp-persona-analysis" / "blind-output-map.json"
    ).is_file()
