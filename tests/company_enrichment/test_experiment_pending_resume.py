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
class DurablePendingClient:
    state_path: Path
    crash_when_first_batch_is_pending: bool = False

    def estimate(self, requests, track):
        return str(Decimal("0.01") * len(requests))

    def has_pending(self, requests, track):
        return self._state().get(self._key(requests, track), {}).get("status") == "pending"

    def execute(self, requests, track):
        if track is ExecutionTrack.BATCH:
            state = self._state()
            key = self._key(requests, track)
            job = state.get(key)
            if job is None:
                job = {"job_id": f"provider-job-{len(state) + 1}", "status": "pending", "submissions": 1}
                state[key] = job
                self._write_state(state)
            if self.crash_when_first_batch_is_pending:
                self.crash_when_first_batch_is_pending = False
                raise SystemExit("simulated process death while provider batch is pending")
            job["status"] = "completed"
            self._write_state(state)

        values = []
        for request in requests:
            fields = ("identity", "description", "offers")
            assertions = tuple(
                item for item in request.dossier.assertions if item.field in fields
            )
            covered = {item.field for item in assertions}
            values.append(ModelExecution(
                request.company_id,
                assertions,
                tuple(field for field in fields if field not in covered),
                request.requested_model_id,
                1,
                "0.01",
            ))
        return tuple(values)

    @staticmethod
    def _key(requests, track):
        return f"{requests[0].requested_model_id}--{track.value}"

    def _state(self):
        if not self.state_path.exists():
            return {}
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def _write_state(self, state):
        self.state_path.write_text(json.dumps(state), encoding="utf-8")


def _runner(tmp_path: Path, client: DurablePendingClient) -> ExperimentRunner:
    return ExperimentRunner(
        artifact_root=tmp_path,
        dossiers={
            item: _rehydrate_dossier(Path("benchmarks/dossiers") / f"{item}.yaml")
            for item in FIXED_SAAS_CORE
        },
        model_client=client,
        benchmark_runner=BenchmarkRunner(tmp_path),
        as_of=AS_OF,
    )


def test_resume_reuses_pending_provider_job_and_active_budget_reservation(
    tmp_path: Path,
) -> None:
    provider_state = tmp_path / "provider-jobs.json"
    first_client = DurablePendingClient(
        provider_state, crash_when_first_batch_is_pending=True,
    )

    with pytest.raises(SystemExit, match="provider batch is pending"):
        _runner(tmp_path, first_client).run(
            "company-description", allow_paid=True,
        )

    budget_path = tmp_path / "company-description" / "budget.jsonl"
    rows_at_crash = tuple(
        json.loads(line)
        for line in budget_path.read_text(encoding="utf-8").splitlines()
    )
    pending_job = json.loads(provider_state.read_text(encoding="utf-8"))[
        "gpt-5-nano--batch"
    ]
    assert pending_job == {
        "job_id": "provider-job-1",
        "status": "pending",
        "submissions": 1,
    }
    assert sum(row["kind"] == "reserve" for row in rows_at_crash) == 2
    assert sum(row["kind"] == "reconcile" for row in rows_at_crash) == 1
    assert not any(row["kind"] == "release" for row in rows_at_crash)

    summary = _runner(
        tmp_path, DurablePendingClient(provider_state),
    ).run("company-description", allow_paid=True, resume=True)

    provider_jobs = json.loads(provider_state.read_text(encoding="utf-8"))
    assert provider_jobs["gpt-5-nano--batch"] == {
        "job_id": "provider-job-1",
        "status": "completed",
        "submissions": 1,
    }
    assert len(provider_jobs) == 3
    assert all(job["submissions"] == 1 for job in provider_jobs.values())
    budget_rows = tuple(
        json.loads(line)
        for line in budget_path.read_text(encoding="utf-8").splitlines()
    )
    assert sum(row["kind"] == "reserve" for row in budget_rows) == 6
    assert sum(row["kind"] == "reconcile" for row in budget_rows) == 6
    assert not any(row["kind"] == "release" for row in budget_rows)
    assert summary.completed_cases == 18
    assert summary.model_cost_usd == Decimal("0.18")
