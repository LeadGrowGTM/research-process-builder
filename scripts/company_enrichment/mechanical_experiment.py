"""Validate benchmark mechanics against published cached Evidence.

This is deliberately not a live model comparison and cannot create a Candidate.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path

from .benchmark import BenchmarkCase, BenchmarkRunner, ExecutionTrack, ExperimentPlan
from .cli import _rehydrate_dossier
from .contracts import EnrichmentResult, ResultStatus
from .executors import P0_ENRICHMENTS
from .experiment_runner import EXPERIMENT_ENRICHMENTS, FIXED_SAAS_CORE


MECHANICAL_MODEL_ID = "deterministic/source-extractor-v1"


def run_mechanical_validation(
    artifact_root: Path,
    *,
    dossier_root: Path = Path("benchmarks/dossiers"),
    resume: bool = False,
) -> dict[str, object]:
    artifact_root = Path(artifact_root)
    benchmark = BenchmarkRunner(artifact_root)
    reports = []
    for enrichment_id in EXPERIMENT_ENRICHMENTS:
        cases = []
        for company_id in FIXED_SAAS_CORE:
            dossier = _rehydrate_dossier(dossier_root / f"{company_id}.yaml")
            fields = set(P0_ENRICHMENTS[enrichment_id])
            assertions = tuple(
                item for item in dossier.assertions if item.field in fields
            )
            covered = {item.field for item in assertions}
            unknowns = tuple(field for field in fields if field not in covered)
            result = EnrichmentResult(
                enrichment_id, company_id, "1.0", ResultStatus.COMPLETE,
                {
                    "assertions": assertions,
                    "evidence": dossier.evidence,
                    "unknowns": unknowns,
                    "requested_model": MECHANICAL_MODEL_ID,
                    "resolved_model": MECHANICAL_MODEL_ID,
                },
            )
            cases.append(BenchmarkCase(
                result, dossier,
                datetime(2026, 8, 12, 23, 59, 59, tzinfo=timezone.utc),
                0, "0", "0", len(dossier.evidence), len(dossier.evidence), 0,
            ))
        plan = ExperimentPlan(
            "mechanical-cached-evidence-v1", enrichment_id,
            ExecutionTrack.SYNCHRONOUS, MECHANICAL_MODEL_ID,
            tuple(cases), 90,
        )
        path = benchmark.report_path(plan)
        if not (resume and path.is_file()):
            benchmark.run(plan)
        reports.append(str(path))

    manifest = {
        "approval": False,
        "candidate": False,
        "company_ids": list(FIXED_SAAS_CORE),
        "enrichment_ids": list(EXPERIMENT_ENRICHMENTS),
        "kind": "mechanical_cached_evidence_validation",
        "live_model_calls": 0,
        "model_id": MECHANICAL_MODEL_ID,
        "model_outputs_fabricated": False,
        "reports": reports,
        "source_purchases": 0,
        "status": "validated_mechanics_only",
    }
    path = artifact_root / "mechanical-validation.json"
    if path.exists() and not resume:
        raise FileExistsError("mechanical validation manifest already exists")
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    return manifest


if __name__ == "__main__":
    print(json.dumps(run_mechanical_validation(
        Path("runs/company-enrichment/experiments"),
    ), sort_keys=True))
