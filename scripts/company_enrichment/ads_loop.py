"""Running-ads signal loop: the ``SignalSpec`` plus the ``--draft-ground-truth`` phase.

Usage (from the repo root)::

    py scripts/company_enrichment_ads_loop.py --collect --company saas-01 [--overwrite] [--dry-run]
    py scripts/company_enrichment_ads_loop.py --draft-ground-truth --company saas-01
    py scripts/company_enrichment_ads_loop.py --evaluate --lineage <name> --allow-paid
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Sequence

from .ads_collect import ENRICHMENT_ID, collect_ads
from .ads_contracts import ads_output_contract
from .ads_evaluator import (
    ADS_EVALUATION_DEPENDENCIES, WEIGHTS, score_ads, validate_ads_record,
)
from .ads_ground_truth_draft import write_ads_ground_truth_draft
from .contracts import canonical_json
from .openai_model_client import build_openai_model_client
from .signal_evidence import load_signal_dossier, signal_dossier_path
from .signal_ground_truth import ALL_IDS, dataset_loader
from . import signal_loop
from .signal_loop import SignalSpec


BENCHMARK_DIR = Path("benchmarks/signals") / ENRICHMENT_ID
PROMPT_PATH = Path("prompts/company-enrichment") / f"{ENRICHMENT_ID}.md"
DRAFT_FLAG = "--draft-ground-truth"


def build_spec() -> SignalSpec:
    return SignalSpec(
        enrichment_id=ENRICHMENT_ID,
        fields=("ads",),
        benchmark_dir=BENCHMARK_DIR,
        output_contract=ads_output_contract,
        load_ground_truth=dataset_loader(ENRICHMENT_ID, WEIGHTS, validate_ads_record),
        score=score_ads,
        evaluation_dependencies=ADS_EVALUATION_DEPENDENCIES,
        prompt_path=PROMPT_PATH,
        weights=WEIGHTS,
        collect=collect_ads,
    )


def draft_ground_truth(repo_root: Path, company_ids: Sequence[str]) -> dict:
    unknown = sorted(set(company_ids) - set(ALL_IDS))
    if unknown:
        raise ValueError(f"unknown company IDs: {unknown}")
    written = []
    for company_id in company_ids:
        source = signal_dossier_path(repo_root, ENRICHMENT_ID, company_id)
        if not source.is_file():
            raise ValueError(f"no collected signal dossier for {company_id}; run --collect first")
        written.append(str(write_ads_ground_truth_draft(repo_root, load_signal_dossier(source))))
    return {"approval": False, "enrichment_id": ENRICHMENT_ID, "phase": "draft_ground_truth",
            "targets": written}


def main(
    argv: Sequence[str] | None = None, *, repo_root: Path | None = None,
    artifact_root: Path | None = None, model_client_factory=build_openai_model_client,
) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    root = Path(repo_root or Path(__file__).resolve().parents[2])
    if DRAFT_FLAG in args:
        parser = argparse.ArgumentParser(
            description=f"{ENRICHMENT_ID} ground-truth drafts",
            usage=f"%(prog)s {DRAFT_FLAG} [--company ID ...]",
        )
        parser.add_argument(DRAFT_FLAG, action="store_true", required=True,
                            help="pre-fill ground-truth drafts from collected signal dossiers")
        parser.add_argument("--company", action="append", default=None)
        parsed = parser.parse_args(args)
        try:
            result = draft_ground_truth(root, tuple(parsed.company or ALL_IDS))
        except ValueError as error:
            print(canonical_json({"approval": False, "error": type(error).__name__,
                                  "message": str(error)}))
            return 2
        print(canonical_json(result))
        return 0
    return signal_loop.main(
        build_spec(), args, repo_root=root, artifact_root=artifact_root,
        model_client_factory=model_client_factory,
    )
