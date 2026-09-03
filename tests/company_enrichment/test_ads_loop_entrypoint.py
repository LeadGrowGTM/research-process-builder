from __future__ import annotations

from datetime import date
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path

import yaml

from scripts.company_enrichment.ads_ground_truth_draft import (
    TODO_HUMAN, draft_ads_ground_truth, drafts_dir,
)
from scripts.company_enrichment.ads_evaluator import ADS_EVALUATION_DEPENDENCIES
from scripts.company_enrichment.ads_loop import BENCHMARK_DIR, ENRICHMENT_ID, build_spec, main
from scripts.company_enrichment.benchmark import ExecutionTrack
from scripts.company_enrichment.contracts import (
    EvidenceRef, FieldAssertion, Visibility, canonical_json,
)
from scripts.company_enrichment.experiment_runner import ExperimentInput, ModelExecution
from scripts.company_enrichment.signal_evidence import (
    load_signal_dossier, save_signal_dossier, signal_dossier, signal_dossier_path,
)
from scripts.company_enrichment.signal_ground_truth import DEVELOPMENT_IDS, HOLDOUT_IDS
from tests.company_enrichment.test_signal_ground_truth import _ref, base_dossier, build_signal_repo


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_spec_binds_the_ads_enrichment():
    spec = build_spec()
    assert spec.enrichment_id == ENRICHMENT_ID == "running-ads-offer-intelligence"
    assert spec.fields == ("ads",)
    assert spec.benchmark_dir == BENCHMARK_DIR == Path("benchmarks/signals") / ENRICHMENT_ID
    assert spec.prompt_path == Path("prompts/company-enrichment") / f"{ENRICHMENT_ID}.md"
    assert spec.rubric == "status:0.6,landing_page:0.2,offer:0.2"
    assert spec.evaluation_dependencies == ADS_EVALUATION_DEPENDENCIES
    assert spec.collect is not None
    assert (REPO_ROOT / spec.prompt_path).is_file()
    split = yaml.safe_load((REPO_ROOT / spec.benchmark_dir / "split.yaml").read_text())
    assert split == {"development": list(DEVELOPMENT_IDS), "holdout": list(HOLDOUT_IDS)}
    rubric = yaml.safe_load((REPO_ROOT / spec.benchmark_dir / "rubric.yaml").read_text())
    assert {key: Decimal(str(value)) for key, value in rubric["weights"].items()} == dict(spec.weights)
    assert Decimal(str(rubric["threshold"])) == Decimal(".90")


def test_dry_run_collect_plans_saas_01_without_network(capsys):
    assert main(["--collect", "--company", "saas-01", "--dry-run"], repo_root=REPO_ROOT) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["dry_run"] is True and plan["phase"] == "collect"
    assert plan["enrichment_id"] == ENRICHMENT_ID
    assert plan["planned"] == ["saas-01"] or plan["skipped_existing"] == ["saas-01"]
    assert plan["targets"] == [] or plan["targets"] == [
        str(signal_dossier_path(REPO_ROOT, ENRICHMENT_ID, "saas-01")),
    ]


def _ads_dossier(company_id: str):
    google = _ref(company_id, "google", '{"running_ads": true}')
    meta = _ref(company_id, "meta", '{"summary": {"active_ads_count": 2}}')
    assertion = FieldAssertion("ads", {"channels": [
        {"channel": "google", "status": "active", "started_on": "2026-01-02", "ended_on": None,
         "landing_page": None, "call_to_action": None, "evidence_ids": [google.evidence_id],
         "failure": None},
        {"channel": "meta", "status": "active", "started_on": None, "ended_on": None,
         "landing_page": "https://example.test/p/enterprise", "call_to_action": "Contact us",
         "evidence_ids": [meta.evidence_id], "failure": None},
    ]}, (google.evidence_id, meta.evidence_id), 0.8, Visibility.MESSAGE_SAFE)
    return signal_dossier(company_id, base_dossier(company_id), (google, meta), (assertion,))


def test_draft_ground_truth_prefills_from_deterministic_assertion():
    dossier = _ads_dossier("saas-01")
    draft = draft_ads_ground_truth(dossier)
    assert draft == {
        "company_id": "saas-01", "as_of": "2026-08-18",
        "channels": {
            "google": {"status": "active", "evidence_ids": ["ev-saas-01-google"]},
            "meta": {"status": "active", "evidence_ids": ["ev-saas-01-meta"],
                     "landing_page": "https://example.test/p/enterprise",
                     "call_to_action": "Contact us", "observed_offer": TODO_HUMAN,
                     "offer_aliases": [TODO_HUMAN]},
        },
    }
    unknown = draft_ads_ground_truth(base_dossier("saas-02"), today=date(2026, 8, 19))
    assert unknown == {"company_id": "saas-02", "as_of": "2026-08-19",
                       "channels": {"google": {"status": "unknown"}, "meta": {"status": "unknown"}}}


def test_draft_flag_writes_to_drafts_dir_only(tmp_path: Path, capsys):
    build_signal_repo(tmp_path)
    save_signal_dossier(signal_dossier_path(tmp_path, ENRICHMENT_ID, "saas-01"), _ads_dossier("saas-01"))
    sealed = tmp_path / BENCHMARK_DIR / "ground-truth"
    before = {path.name: path.read_bytes() for path in sealed.glob("*.yaml")}

    assert main(["--draft-ground-truth", "--company", "saas-01"], repo_root=tmp_path) == 0
    result = json.loads(capsys.readouterr().out)
    target = drafts_dir(tmp_path) / "saas-01.yaml"
    assert result["targets"] == [str(target)] and result["approval"] is False
    draft = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert draft["channels"]["meta"]["observed_offer"] == TODO_HUMAN
    assert draft["channels"]["google"] == {"status": "active", "evidence_ids": ["ev-saas-01-google"]}
    assert {path.name: path.read_bytes() for path in sealed.glob("*.yaml")} == before
    assert load_signal_dossier(signal_dossier_path(tmp_path, ENRICHMENT_ID, "saas-01")).assertions

    assert main(["--draft-ground-truth", "--company", "saas-99"], repo_root=tmp_path) == 2
    assert "unknown company IDs" in json.loads(capsys.readouterr().out)["message"]


class FakeAdsClient:
    def request_fingerprint(self, request: ExperimentInput) -> str:
        return sha256(canonical_json(request).encode("utf-8")).hexdigest()

    def estimate(self, requests, track) -> str:
        assert track is ExecutionTrack.SYNCHRONOUS
        return str(Decimal("0.001") * len(requests))

    def execute(self, requests, track) -> tuple[ModelExecution, ...]:
        results = []
        for request in requests:
            schema = request.output_contract
            assert list(schema["required"]) == ["ads", "unknowns"]
            evidence_id = f"ev-{request.company_id}-google"
            assert evidence_id in schema["properties"]["ads"]["properties"]["channels"]["items"][
                "properties"]["evidence_ids"]["items"]["enum"]
            value = {"channels": [{
                "channel": "google", "status": "active", "angle": None, "offer": None,
                "call_to_action": None, "landing_page": None, "evidence_ids": [evidence_id],
            }]}
            results.append(ModelExecution(
                request.company_id,
                (FieldAssertion("ads", value, (evidence_id,), 1.0, Visibility.MESSAGE_SAFE),),
                (), "gpt-4.1-mini-2025-04-14", 10, "0.001",
            ))
        return tuple(results)


def test_evaluate_runs_the_ads_spec_end_to_end(tmp_path: Path, capsys):
    build_signal_repo(tmp_path)
    code = main(["--evaluate", "--lineage", "ads-1", "--allow-paid"], repo_root=tmp_path,
                model_client_factory=lambda **_: FakeAdsClient())
    assert code == 0
    result = json.loads(capsys.readouterr().out)
    assert result["gate"]["human_review_eligible"] is True and result["gate"]["approval"] is False
    run_root = tmp_path / "runs/company-enrichment" / ENRICHMENT_ID / "ads-1"
    holdout = json.loads((run_root / "scores/holdout/baseline.json").read_text(encoding="utf-8"))
    assert holdout["mean_score"] == "1.0000" and holdout["hard_failures"] == []
    assert holdout["cases"][0]["component_scores"] == {"status": "1"}
