"""Score stored company-description / growth-signals outputs against the
dated observable ground truth in benchmarks/description-growth/.

Reads experiment outcomes.jsonl artifacts (no network, no model spend),
scores each case with the semantic evaluator, and prints a JSON report with
development / holdout means and hard failures per (enrichment, model, track)
group.

Usage:
    py scripts/company_enrichment_description_growth_eval.py \
        --outcomes runs/company-enrichment/experiments-luna/company-description/outcomes.jsonl \
        [--output output/description-growth-eval.json]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.company_enrichment.cli import _rehydrate_dossier  # noqa: E402
from scripts.company_enrichment.contracts import canonical_json  # noqa: E402
from scripts.company_enrichment.description_growth_evaluator import (  # noqa: E402
    score_description_payload,
    score_growth_payload,
)
from scripts.company_enrichment.description_growth_ground_truth import (  # noqa: E402
    load_description_growth_dataset,
)

_TRACK_SCORERS = {
    "company-description": score_description_payload,
    "growth-signals": score_growth_payload,
}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if line.strip():
            value = json.loads(line)
            if isinstance(value, dict):
                rows.append(value)
    return rows


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def evaluate_outcomes(
    outcome_paths: Sequence[Path], *, repo_root: Path, dossier_root: Path,
) -> dict[str, Any]:
    dossiers = {
        company_id: _rehydrate_dossier(dossier_root / f"{company_id}.yaml")
        for company_id in (f"saas-{index:02d}" for index in range(1, 11))
    }
    dataset = load_description_growth_dataset(repo_root, dossiers)
    weights = {
        "company-description": dataset.description_rubric.weights,
        "growth-signals": dataset.growth_rubric.weights,
    }
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for path in outcome_paths:
        for row in _read_jsonl(path):
            enrichment_id = row.get("enrichment_id")
            if enrichment_id not in _TRACK_SCORERS:
                continue
            company_id = row.get("company_id")
            if company_id not in dataset.records:
                continue
            result = row.get("result")
            if not isinstance(result, Mapping):
                continue
            output = result.get("output")
            if not isinstance(output, Mapping):
                continue
            key = (
                enrichment_id,
                str(row.get("requested_model_id")),
                str(row.get("execution_track")),
            )
            scorer = _TRACK_SCORERS[enrichment_id]
            case = scorer(
                output, dataset.records[company_id], dossiers[company_id],
                weights=weights[enrichment_id],
            )
            groups.setdefault(key, []).append({
                "company_id": company_id,
                "components": {
                    name: float(value) for name, value in case.components.items()
                },
                "score": float(case.score),
                "hard_failures": list(case.hard_failures),
            })
    development = set(dataset.development_ids)
    holdout = set(dataset.holdout_ids)
    report_groups = []
    for (enrichment_id, model, track), cases in sorted(groups.items()):
        cases = sorted(cases, key=lambda item: item["company_id"])
        development_scores = [
            item["score"] for item in cases if item["company_id"] in development
        ]
        holdout_scores = [
            item["score"] for item in cases if item["company_id"] in holdout
        ]
        report_groups.append({
            "cases": cases,
            "development_mean": _mean(development_scores),
            "enrichment_id": enrichment_id,
            "execution_track": track,
            "hard_failures": sorted(
                f"{item['company_id']}:{reason}"
                for item in cases for reason in item["hard_failures"]
            ),
            "holdout_mean": _mean(holdout_scores),
            "requested_model_id": model,
        })
    return {
        "dataset_hash": dataset.dataset_hash,
        "groups": report_groups,
        "schema_version": "1.0",
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Score stored description/growth outputs against the dated"
            " observable ground truth (no network, no model spend)."
        ),
    )
    parser.add_argument(
        "--outcomes", action="append", type=Path, required=True,
        help="Path to an experiment outcomes.jsonl (repeatable).",
    )
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument(
        "--dossier-root", type=Path, default=Path("benchmarks/dossiers"),
    )
    parser.add_argument(
        "--output", type=Path,
        help="Optional path for the JSON report (refuses to overwrite).",
    )
    args = parser.parse_args(argv)
    report = evaluate_outcomes(
        tuple(args.outcomes),
        repo_root=args.repo_root,
        dossier_root=args.dossier_root,
    )
    rendered = canonical_json(report)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(rendered + "\n")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
