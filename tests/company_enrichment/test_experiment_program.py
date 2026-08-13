from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path

from scripts.company_enrichment.contracts import ReviewStatus
from scripts.company_enrichment.experiment_program import ExperimentProgram, main
from scripts.company_enrichment.experiment_runner import ExperimentSummary


AS_OF = datetime(2026, 8, 12, 12, tzinfo=timezone.utc)


@dataclass
class StubRunner:
    summary_status: str
    gate_path: str | None = None

    def run(self, enrichment_id, **_kwargs):
        return ExperimentSummary(
            enrichment_id, self.summary_status, False,
            24 if self.summary_status == "candidate" else 0,
            0, 24, 24 if self.summary_status == "candidate" else 0,
            0, __import__("decimal").Decimal("0.25"),
            __import__("decimal").Decimal("1.00"),
            None if self.summary_status == "candidate" else "auth gap",
            0.95 if self.summary_status == "candidate" else None,
            self.gate_path if self.summary_status == "candidate" else None,
            blind_outputs=(
                tuple({
                    "output_id": f"output-{index:032x}",
                    "content": {"description": f"actual output {index}"},
                } for index in range(24))
                if self.summary_status == "candidate" else ()
            ),
        )


def _factory(status, gate_path=None):
    def build(**_kwargs):
        return StubRunner(status, gate_path)
    return build


def test_program_composes_candidate_only_review_history(tmp_path: Path) -> None:
    gate = tmp_path / "aggregate-gate.json"
    gate.write_text(json.dumps({
        "case_count": 24,
        "programmed_gate_score": 0.95,
        "groups": [
            {"report_path": f"report-{index}.json", "report_hash": "a" * 64}
            for index in range(8)
        ],
    }) + "\n", encoding="utf-8")
    program = ExperimentProgram(
        artifact_root=tmp_path,
        dossier_root=Path("benchmarks/dossiers"),
        model_client=None,
        as_of=AS_OF,
        runner_factory=_factory("candidate", str(gate)),
    )

    result = program.run("company-description")
    records = tuple(
        json.loads(line) for line in result.review_path.read_text(
            encoding="utf-8",
        ).splitlines()
    )

    assert result.review_status is ReviewStatus.CANDIDATE
    assert [item["to_status"] for item in records] == [
        "experiment", "candidate",
    ]
    assert not any(item["to_status"] == "approved" for item in records)
    assert records[1]["prior_record_hash"] == records[0]["record_hash"]


def test_auth_blocked_program_stays_experiment_and_resume_is_append_only(
    tmp_path: Path,
) -> None:
    program = ExperimentProgram(
        artifact_root=tmp_path,
        dossier_root=Path("benchmarks/dossiers"),
        model_client=None,
        as_of=AS_OF,
        runner_factory=_factory("experiment"),
    )

    first = program.run("growth-signals")
    original = first.review_path.read_bytes()
    second = program.run("growth-signals", resume=True)

    assert first.review_status is second.review_status is ReviewStatus.EXPERIMENT
    assert second.review_path.read_bytes() == original


def test_cli_records_all_three_auth_gaps_without_live_clients(
    tmp_path: Path, capsys,
) -> None:
    code = main(["--artifact-root", str(tmp_path)])
    payload = json.loads(capsys.readouterr().out)

    assert code == 2
    assert len(payload) == 3
    assert {item["review_status"] for item in payload} == {"experiment"}
    assert all(item["summary"]["authentication_gap"] for item in payload)
    assert all(item["summary"]["approved"] is False for item in payload)
