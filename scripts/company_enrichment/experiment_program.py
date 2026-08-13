from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
import json
from hashlib import sha256
import os
from pathlib import Path
from typing import Callable, Sequence

from ._locking import file_lock
from .benchmark import BenchmarkRunner
from .cli import _rehydrate_dossier
from .contracts import ReviewStatus, canonical_json
from .experiment_runner import (
    EXPERIMENT_ENRICHMENTS,
    FIXED_SAAS_CORE,
    ExperimentRunner,
    ExperimentSummary,
    ModelClient,
)
from .review import (
    BlindReviewOutput, BlindReviewPack, BlindReviewScorecard,
    ProgrammedGateEvidence,
    ReviewActor, ReviewHistory, ReviewRecord, ReviewVerdict,
)


@dataclass(frozen=True, slots=True)
class ExperimentProgramResult:
    summary: ExperimentSummary
    review_status: ReviewStatus
    review_path: Path


class ExperimentProgram:
    def __init__(
        self,
        *,
        artifact_root: Path,
        dossier_root: Path,
        model_client: ModelClient | None,
        as_of: datetime,
        runner_factory: Callable[..., ExperimentRunner] = ExperimentRunner,
    ) -> None:
        self._root = Path(artifact_root)
        self._dossier_root = Path(dossier_root)
        self._model_client = model_client
        self._as_of = as_of
        self._runner_factory = runner_factory

    def run(
        self, enrichment_id: str, *, allow_paid: bool = False,
        resume: bool = False,
    ) -> ExperimentProgramResult:
        experiment_id = f"initial-{enrichment_id}"
        review_path = self._root / enrichment_id / "review.jsonl"
        history = self._load_history(experiment_id, review_path)
        if history.status is ReviewStatus.PROPOSED:
            history = history.record_automation(
                ReviewStatus.EXPERIMENT, occurred_at=self._as_of,
            )
            self._append_record(review_path, history.records[-1])

        dossiers = {
            company_id: _rehydrate_dossier(
                self._dossier_root / f"{company_id}.yaml"
            )
            for company_id in FIXED_SAAS_CORE
        }
        runner = self._runner_factory(
            artifact_root=self._root,
            dossiers=dossiers,
            model_client=self._model_client,
            benchmark_runner=BenchmarkRunner(self._root),
            as_of=self._as_of,
        )
        summary = runner.run(
            enrichment_id, allow_paid=allow_paid, resume=resume,
        )
        if (
            summary.status == ReviewStatus.CANDIDATE.value
            and history.status is ReviewStatus.EXPERIMENT
            and summary.programmed_gate_score is not None
            and summary.programmed_gate_score >= summary.gate_threshold
            and summary.programmed_gate_score >= 0.90
            and len(summary.blind_outputs) == summary.completed_cases == 24
            and len({
                str(item.get("output_id")) for item in summary.blind_outputs
            }) == 24
        ):
            gate_path = summary.gate_artifact_path
            if not gate_path:
                raise ValueError("candidate requires an aggregate gate artifact")
            path = Path(gate_path)
            if not path.is_file():
                raise ValueError("candidate aggregate gate artifact is missing")
            gate_manifest = json.loads(path.read_text(encoding="utf-8"))
            groups = gate_manifest.get("groups")
            if (
                gate_manifest.get("programmed_gate_score")
                != summary.programmed_gate_score
                or gate_manifest.get("case_count") != summary.completed_cases
                or not isinstance(groups, list)
                or len(groups) != 8
                or any(
                    not isinstance(item.get("report_path"), str)
                    or not isinstance(item.get("report_hash"), str)
                    or len(item["report_hash"]) != 64
                    for item in groups
                )
            ):
                raise ValueError("candidate aggregate gate artifact is invalid")
            artifact_hash = sha256(path.read_bytes()).hexdigest()
            gate = ProgrammedGateEvidence(
                summary.programmed_gate_score, summary.gate_threshold,
                gate_path, artifact_hash,
            )
            pack = BlindReviewPack.create(
                experiment_id,
                outputs=tuple(
                    BlindReviewOutput(
                        str(item["output_id"]), item["content"],
                    ) for item in summary.blind_outputs
                ),
                created_at=self._as_of,
            )
            history = history.record_automation(
                ReviewStatus.CANDIDATE, occurred_at=self._as_of,
                gate_evidence=gate, blind_review_pack=pack,
            )
            self._append_record(review_path, history.records[-1])
        if history.status is ReviewStatus.APPROVED:
            raise ValueError("experiment automation cannot produce Approval")
        return ExperimentProgramResult(summary, history.status, review_path)

    @staticmethod
    def _load_history(experiment_id: str, path: Path) -> ReviewHistory:
        if not path.exists():
            return ReviewHistory.start(experiment_id)
        records = []
        for line in path.read_text(encoding="utf-8").splitlines():
            value = json.loads(line)
            gate_value = value.get("gate_evidence")
            gate = (
                ProgrammedGateEvidence(
                    score=gate_value["score"],
                    threshold=gate_value["threshold"],
                    artifact_path=gate_value["artifact_path"],
                    artifact_hash=gate_value["artifact_hash"],
                    gate_kind=gate_value.get(
                        "gate_kind", "programmed_ground_truth",
                    ),
                )
                if gate_value is not None else None
            )
            pack_value = value.get("blind_review_pack")
            pack = (
                BlindReviewPack(
                    pack_id=pack_value["pack_id"],
                    experiment_id=pack_value["experiment_id"],
                    created_at=datetime.fromisoformat(pack_value["created_at"]),
                    outputs=tuple(
                        BlindReviewOutput(
                            item["output_id"], item["content"],
                        ) for item in pack_value["outputs"]
                    ),
                    dimensions=tuple(pack_value["dimensions"]),
                )
                if pack_value is not None else None
            )
            score_value = value.get("scorecard")
            scorecard = (
                BlindReviewScorecard(
                    readability=score_value["readability"],
                    specificity=score_value["specificity"],
                    usefulness=score_value["usefulness"],
                    casualness=score_value["casualness"],
                    non_creepiness=score_value["non_creepiness"],
                )
                if score_value is not None else None
            )
            records.append(ReviewRecord(
                sequence=value["sequence"],
                experiment_id=value["experiment_id"],
                from_status=ReviewStatus(value["from_status"]),
                to_status=ReviewStatus(value["to_status"]),
                actor=ReviewActor(value["actor"]),
                occurred_at=datetime.fromisoformat(value["occurred_at"]),
                prior_record_hash=value["prior_record_hash"],
                record_hash=value["record_hash"],
                verdict=(
                    ReviewVerdict(value["verdict"])
                    if value.get("verdict") else None
                ),
                reviewer_id=value.get("reviewer_id"),
                blind=bool(value.get("blind", False)),
                gate_evidence=gate,
                blind_review_pack=pack,
                blind_review_pack_id=value.get("blind_review_pack_id"),
                scorecard=scorecard,
            ))
        return ReviewHistory(experiment_id, tuple(records))

    @staticmethod
    def _append_record(path: Path, record: ReviewRecord) -> None:
        lock = path.with_suffix(path.suffix + ".lock")
        with file_lock(lock):
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(canonical_json(record) + "\n")
                stream.flush()
                os.fsync(stream.fileno())


def _json_default(value):
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "__dataclass_fields__"):
        return {
            name: getattr(value, name)
            for name in value.__dataclass_fields__
        }
    raise TypeError(f"cannot serialize {type(value).__name__}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the fixed three-company enrichment experiment matrix.",
    )
    parser.add_argument(
        "--enrichment", choices=EXPERIMENT_ENRICHMENTS + ("all",),
        default="all",
    )
    parser.add_argument(
        "--artifact-root", type=Path,
        default=Path("runs/company-enrichment/experiments"),
    )
    parser.add_argument(
        "--dossier-root", type=Path, default=Path("benchmarks/dossiers"),
    )
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args(argv)
    enrichments = (
        EXPERIMENT_ENRICHMENTS if args.enrichment == "all"
        else (args.enrichment,)
    )
    program = ExperimentProgram(
        artifact_root=args.artifact_root,
        dossier_root=args.dossier_root,
        model_client=None,
        as_of=datetime(2026, 8, 12, 23, 59, 59, tzinfo=timezone.utc),
    )
    results = tuple(
        program.run(item, resume=args.resume) for item in enrichments
    )
    print(json.dumps(results, default=_json_default, sort_keys=True))
    return 2 if any(item.summary.authentication_gap for item in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
