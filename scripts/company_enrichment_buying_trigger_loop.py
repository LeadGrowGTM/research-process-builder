"""Run the two-idea buying-trigger prompt over cached SaaS Evidence."""

from __future__ import annotations

import argparse
from datetime import datetime
from decimal import Decimal
import json
import os
from pathlib import Path
import re
from typing import Any, Sequence

import yaml

from scripts.company_enrichment.benchmark import ExecutionTrack
from scripts.company_enrichment.contracts import (
    CompanyDossier,
    EvidenceRef,
    FieldAssertion,
    Visibility,
    canonical_json,
)
from scripts.company_enrichment.experiment_runner import ExperimentInput
from scripts.company_enrichment.openai_model_client import build_openai_model_client


ALL_IDS = tuple(f"saas-{index:02d}" for index in range(1, 11))
MODEL_ID = os.environ.get("BUYING_TRIGGER_MODEL", "gpt-4.1-mini")
CAP_USD = Decimal("1.00")
_LINEAGE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,79}$")


def _load_prompt(path: Path) -> tuple[dict[str, Any], str]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError("prompt requires YAML frontmatter")
    _, raw_meta, body = text.split("---", 2)
    metadata = yaml.safe_load(raw_meta)
    if not isinstance(metadata, dict) or metadata.get("name") != "buying-trigger-analysis":
        raise ValueError("unexpected prompt metadata")
    return metadata, body.strip()


def _load_dossiers(root: Path) -> dict[str, CompanyDossier]:
    dossiers: dict[str, CompanyDossier] = {}
    for company_id in ALL_IDS:
        value = yaml.safe_load(
            (root / "benchmarks/dossiers" / f"{company_id}.yaml").read_text(
                encoding="utf-8"
            )
        )
        dossiers[company_id] = CompanyDossier(
            company_id=company_id,
            schema_version=value["schema_version"],
            assertions=tuple(
                FieldAssertion(
                    item["field"], item["value"], tuple(item["evidence_ids"]),
                    float(item["confidence"]), Visibility(item["visibility"]),
                )
                for item in value["assertions"]
            ),
            evidence=tuple(
                EvidenceRef(
                    item["evidence_id"], item["url"],
                    datetime.fromisoformat(item["retrieved_at"]),
                    item["content_hash"], item["excerpt"],
                )
                for item in value["evidence"]
            ),
            unknowns=tuple(value.get("unknowns", ())),
        )
    return dossiers


def _company_name(dossier: CompanyDossier) -> str:
    return next(str(item.value) for item in dossier.assertions if item.field == "identity")


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    temporary.write_text(canonical_json(value) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def run(*, root: Path, lineage: str, resume: bool) -> dict[str, Any]:
    metadata, prompt = _load_prompt(
        root / "prompts/company-enrichment/buying-trigger-analysis.md"
    )
    run_root = root / "runs/company-enrichment/buying-trigger" / lineage
    result_path = run_root / "results.json"
    if result_path.exists() and resume:
        return json.loads(result_path.read_text(encoding="utf-8"))
    if run_root.exists() and any(run_root.iterdir()) and not resume:
        raise ValueError("lineage already exists; pass --resume or choose a new lineage")
    dossiers = _load_dossiers(root)
    client = build_openai_model_client(artifact_root=run_root / "provider")
    requests = tuple(
        ExperimentInput(
            "buying-trigger-analysis", company_id, MODEL_ID, dossiers[company_id],
            f"buying-trigger-analysis-v{metadata['version']}", prompt,
        )
        for company_id in ALL_IDS
    )
    estimate = Decimal(client.estimate(requests, ExecutionTrack.SYNCHRONOUS))
    if estimate > CAP_USD:
        raise ValueError(f"estimated model cost {estimate} exceeds {CAP_USD} cap")

    rows: list[dict[str, Any]] = []
    actual = Decimal("0")
    for request in requests:
        execution = client.execute((request,), ExecutionTrack.SYNCHRONOUS)[0]
        actual += Decimal(execution.actual_cost_usd)
        values = {item.field: str(item.value) for item in execution.assertions}
        citations = {item.field: list(item.evidence_ids) for item in execution.assertions}
        rows.append({
            "company_id": request.company_id,
            "company_name": _company_name(request.dossier),
            "campaign_idea_1": values.get("campaign_idea_1", "unknown"),
            "campaign_idea_2": values.get("campaign_idea_2", "unknown"),
            "citations": citations,
            "unknowns": list(execution.unknowns),
            "requested_model": request.requested_model_id,
            "resolved_model": execution.resolved_model_id,
        })
    result = {
        "approval": False,
        "lineage": lineage,
        "prompt_name": metadata["name"],
        "prompt_version": str(metadata["version"]),
        "requested_model": MODEL_ID,
        "source_purchases": 0,
        "total_model_cost_usd": str(actual),
        "results": rows,
    }
    _atomic_json(result_path, result)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lineage", required=True)
    parser.add_argument("--allow-paid", action="store_true")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args(argv)
    if not _LINEAGE.fullmatch(args.lineage):
        parser.error("--lineage must be task-scoped text")
    if not args.allow_paid:
        print(canonical_json({"approval": False, "error": "--allow-paid is required"}))
        return 2
    try:
        result = run(root=Path(__file__).resolve().parents[1], lineage=args.lineage,
                     resume=args.resume)
    except (ValueError, OSError) as error:
        print(canonical_json({"approval": False, "error": type(error).__name__,
                              "message": str(error)}))
        return 2
    print(canonical_json(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
