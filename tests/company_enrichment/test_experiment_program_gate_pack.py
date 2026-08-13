from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path

from scripts.company_enrichment.contracts import ReviewStatus
from scripts.company_enrichment.experiment_program import ExperimentProgram
from scripts.company_enrichment.experiment_runner import ExperimentSummary


NOW = datetime(2026, 8, 13, 12, tzinfo=timezone.utc)


class CandidateRunner:
    gate_path: str = ""
    include_outputs: bool = True

    def run(self, enrichment_id, **_kwargs):
        return ExperimentSummary(
            enrichment_id, "candidate", False, 24, 0, 24, 24, 0,
            Decimal("0.20"), Decimal("1.00"),
            programmed_gate_score=0.95,
            gate_artifact_path=self.gate_path,
            blind_outputs=(
                tuple({
                    "output_id": f"output-{index:032x}",
                    "content": {"description": f"actual output {index}"},
                } for index in range(24))
                if self.include_outputs else ()
            ),
        )


def _gate_manifest(path: Path, score: float) -> None:
    path.write_text(json.dumps({
        "case_count": 24,
        "programmed_gate_score": score,
        "groups": [
            {"report_path": f"report-{index}.json", "report_hash": "a" * 64}
            for index in range(8)
        ],
    }) + "\n", encoding="utf-8")


def test_program_candidate_persists_gate_and_anonymized_blind_pack(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "aggregate-gate.json"
    _gate_manifest(manifest, 0.95)
    runner = CandidateRunner()
    runner.gate_path = str(manifest)
    program = ExperimentProgram(
        artifact_root=tmp_path,
        dossier_root=Path("benchmarks/dossiers"),
        model_client=None,
        as_of=NOW,
        runner_factory=lambda **_kwargs: runner,
    )

    result = program.run("company-description")
    rows = tuple(
        json.loads(line) for line in result.review_path.read_text(
            encoding="utf-8",
        ).splitlines()
    )

    assert result.review_status is ReviewStatus.CANDIDATE
    candidate = rows[-1]
    assert candidate["gate_evidence"]["score"] == 0.95
    assert candidate["gate_evidence"]["threshold"] == 0.90
    assert candidate["gate_evidence"]["artifact_hash"] == (
        __import__("hashlib").sha256(manifest.read_bytes()).hexdigest()
    )
    assert candidate["blind_review_pack"]["dimensions"] == [
        "readability", "specificity", "usefulness", "casualness",
        "non-creepiness",
    ]
    serialized = json.dumps(candidate["blind_review_pack"])
    assert "requested_model" not in serialized
    assert "resolved_model" not in serialized
    assert "provider" not in serialized
    assert candidate["to_status"] == "candidate"


def test_program_refuses_candidate_without_passing_gate(tmp_path: Path) -> None:
    class BadRunner(CandidateRunner):
        def run(self, enrichment_id, **kwargs):
            return replace(
                super().run(enrichment_id, **kwargs),
                programmed_gate_score=0.89,
            )

    manifest = tmp_path / "aggregate-gate.json"
    _gate_manifest(manifest, 0.89)
    runner = BadRunner()
    runner.gate_path = str(manifest)
    program = ExperimentProgram(
        artifact_root=tmp_path,
        dossier_root=Path("benchmarks/dossiers"),
        model_client=None,
        as_of=NOW,
        runner_factory=lambda **_kwargs: runner,
    )

    result = program.run("company-description")

    assert result.review_status is ReviewStatus.EXPERIMENT


def test_program_fails_closed_when_candidate_gate_artifact_is_missing(
    tmp_path: Path,
) -> None:
    runner = CandidateRunner()
    runner.gate_path = str(tmp_path / "missing-aggregate-gate.json")
    program = ExperimentProgram(
        artifact_root=tmp_path,
        dossier_root=Path("benchmarks/dossiers"),
        model_client=None,
        as_of=NOW,
        runner_factory=lambda **_kwargs: runner,
    )

    with __import__("pytest").raises(ValueError, match="artifact is missing"):
        program.run("company-description")


def test_program_refuses_candidate_without_actual_blind_outputs(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "aggregate-gate.json"
    _gate_manifest(manifest, 0.95)
    runner = CandidateRunner()
    runner.gate_path = str(manifest)
    runner.include_outputs = False
    program = ExperimentProgram(
        artifact_root=tmp_path,
        dossier_root=Path("benchmarks/dossiers"),
        model_client=None,
        as_of=NOW,
        runner_factory=lambda **_kwargs: runner,
    )

    result = program.run("company-description")

    assert result.review_status is ReviewStatus.EXPERIMENT
    assert len(result.review_path.read_text(encoding="utf-8").splitlines()) == 1
