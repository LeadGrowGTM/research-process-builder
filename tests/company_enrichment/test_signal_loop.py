from __future__ import annotations

from decimal import Decimal
import json
from pathlib import Path
from typing import Any, Mapping

import pytest

from scripts.company_enrichment.benchmark import ExecutionTrack
from scripts.company_enrichment.contracts import (
    CompanyDossier, FieldAssertion, Visibility,
)
from scripts.company_enrichment.experiment_runner import ExperimentInput, ModelExecution
from scripts.company_enrichment.signal_evidence import load_signal_dossier, signal_dossier
from scripts.company_enrichment.signal_ground_truth import (
    ALL_IDS, DEVELOPMENT_IDS, HOLDOUT_IDS, SignalGroundTruthRecord, dataset_loader,
)
from scripts.company_enrichment import signal_loop
from scripts.company_enrichment.signal_loop import (
    CaseScore, CollectRequest, SignalSpec, run_loop,
)
from tests.company_enrichment.test_signal_ground_truth import (
    ENRICHMENT, WEIGHTS, _ref, build_signal_repo,
)


def _output_contract(dossier: CompanyDossier) -> dict[str, Any]:
    ids = [item.evidence_id for item in dossier.evidence]
    return {
        "type": "object",
        "properties": {"ads": {"type": "object", "properties": {"google": {
            "type": "object", "properties": {
                "status": {"type": "string"},
                "evidence_ids": {"type": "array", "items": {"type": "string", "enum": ids}},
            }, "required": ["status", "evidence_ids"], "additionalProperties": False,
        }}, "required": ["google"], "additionalProperties": False}},
        "required": ["ads"], "additionalProperties": False,
    }


def _score(payload: Mapping[str, Any], record: SignalGroundTruthRecord,
           dossier: CompanyDossier) -> CaseScore:
    expected = record.body["channels"]["google"]
    actual = payload.get("ads", {}).get("google", {})
    status = Decimal(actual.get("status") == expected["status"])
    cited = Decimal(set(expected["evidence_ids"]) <= set(actual.get("evidence_ids", ())))
    components = {"status": status, "landing_page": cited, "offer": Decimal("1")}
    score = sum((WEIGHTS[key] * value for key, value in components.items()), Decimal("0"))
    failures = () if status else ("status_mismatch",)
    return CaseScore(record.company_id, components, score, failures)


def make_spec(collect=None, **overrides) -> SignalSpec:
    values = {
        "enrichment_id": ENRICHMENT, "fields": ("ads",),
        "benchmark_dir": Path("benchmarks/signals") / ENRICHMENT,
        "output_contract": _output_contract,
        "load_ground_truth": dataset_loader(ENRICHMENT, WEIGHTS),
        "score": _score,
        "prompt_path": Path("prompts/company-enrichment") / f"{ENRICHMENT}.md",
        "weights": WEIGHTS, "collect": collect,
    }
    values.update(overrides)
    return SignalSpec(**values)


class FakeModelClient:
    def __init__(self, status: str = "active", cost: str = "0.001") -> None:
        self.status = status
        self.cost = cost
        self.executed: list[str] = []
        self.estimates: list[tuple[str, ...]] = []

    def estimate(self, requests, track) -> str:
        assert track is ExecutionTrack.SYNCHRONOUS
        self.estimates.append(tuple(item.company_id for item in requests))
        return str(Decimal(self.cost) * len(requests))

    def execute(self, requests, track) -> tuple[ModelExecution, ...]:
        assert track is ExecutionTrack.SYNCHRONOUS
        results = []
        for request in requests:
            assert isinstance(request, ExperimentInput)
            assert request.enrichment_id == ENRICHMENT
            assert request.output_contract["properties"]["ads"]
            evidence_id = f"ev-{request.company_id}-google"
            assert evidence_id in {item.evidence_id for item in request.dossier.evidence}
            self.executed.append(request.company_id)
            results.append(ModelExecution(
                request.company_id,
                (FieldAssertion(
                    "ads", {"google": {"status": self.status, "evidence_ids": [evidence_id]}},
                    (evidence_id,), 1.0, Visibility.MESSAGE_SAFE,
                ),),
                (), "gpt-4.1-mini-2025-04-14", 12, self.cost,
            ))
        return tuple(results)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    build_signal_repo(tmp_path)
    return tmp_path


def test_signal_spec_validation():
    spec = make_spec()
    assert spec.fields == ("ads",)
    assert spec.rubric == "status:0.6,landing_page:0.2,offer:0.2"
    with pytest.raises(ValueError, match="unknown P0 enrichment"):
        make_spec(enrichment_id="not-an-enrichment")
    with pytest.raises(ValueError, match="fields must equal"):
        make_spec(fields=("ads", "news"))
    with pytest.raises(ValueError, match="total 1.0"):
        make_spec(weights={"status": Decimal(".5")})
    with pytest.raises(ValueError, match="repo-relative"):
        make_spec(prompt_path=Path("C:/absolute/prompt.md"))
    with pytest.raises(ValueError, match="score must be callable"):
        make_spec(score="not callable")


def test_dry_run_writes_inputs_and_no_cost(repo: Path, capsys):
    code = signal_loop.main(make_spec(), [
        "--evaluate", "--lineage", "dry-1", "--dry-run",
    ], repo_root=repo, model_client_factory=lambda **_: pytest.fail("no client"))

    assert code == 0
    run_root = repo / "runs/company-enrichment" / ENRICHMENT / "dry-1"
    inputs = json.loads((run_root / "inputs.json").read_text(encoding="utf-8"))
    assert inputs["development_ids"] == list(DEVELOPMENT_IDS)
    assert inputs["holdout_ids"] == list(HOLDOUT_IDS)
    assert set(inputs["signal_hashes"]) == set(ALL_IDS)
    assert set(inputs["dossier_hashes"]) == set(ALL_IDS)
    assert not (run_root / "cost.json").exists()
    assert not (run_root / "outputs").exists()
    plan = json.loads(capsys.readouterr().out)
    assert plan["cached_only"] is True and plan["source_purchases"] == 0
    assert plan["enrichment_id"] == ENRICHMENT and plan["cost_cap_usd"] == "1.00"


def test_evaluate_requires_allow_paid(repo: Path, capsys):
    code = signal_loop.main(make_spec(), ["--evaluate", "--lineage", "x"], repo_root=repo)
    assert code == 2
    assert json.loads(capsys.readouterr().out)["error"] == "--allow-paid is required"


def test_evaluate_scores_dev_then_holdout_with_fake_client(repo: Path):
    client = FakeModelClient()
    run_root = repo / "runs/company-enrichment" / ENRICHMENT / "lineage-1"

    result = run_loop(make_spec(), repo_root=repo, run_root=run_root,
                      model_client=client, resume=False)

    assert client.executed == [*DEVELOPMENT_IDS, *HOLDOUT_IDS]
    assert result["gate"]["human_review_eligible"] is True
    assert result["gate"]["approval"] is False
    assert result["gate"]["action"] == "halt_for_review"
    assert result["gate"]["reason_code"] == "human_review_required"
    assert result["winner"]["candidate_id"] == "baseline"
    dev = json.loads((run_root / "scores/dev/baseline.json").read_text(encoding="utf-8"))
    holdout = json.loads((run_root / "scores/holdout/baseline.json").read_text(encoding="utf-8"))
    assert dev["mean_score"] == "1.0" and holdout["mean_score"] == "1.0"
    assert [item["company_id"] for item in holdout["cases"]] == list(HOLDOUT_IDS)
    output = json.loads((run_root / "outputs/dev/baseline/saas-01.json").read_text(encoding="utf-8"))
    assert output["output"] == {
        "ads": {"google": {"status": "active", "evidence_ids": ["ev-saas-01-google"]}},
        "unknowns": [],
    }
    assert output["source_purchases"] == 0 and output["source_cache_reused"] is True
    cost = json.loads((run_root / "cost.json").read_text(encoding="utf-8"))
    assert set(cost["reservations"]) == {"dev-0-baseline", "holdout-baseline"}
    assert all(item["status"] == "completed" for item in cost["reservations"].values())
    assert result["total_cost_usd"] == "0.010"
    assert (run_root / "gate.json").exists() and (run_root / "winner.json").exists()


def test_hard_failures_zero_out_review_eligibility(repo: Path):
    run_root = repo / "runs/company-enrichment" / ENRICHMENT / "lineage-bad"
    result = run_loop(make_spec(), repo_root=repo, run_root=run_root,
                      model_client=FakeModelClient(status="inactive"), resume=False)
    assert result["gate"]["human_review_eligible"] is False
    assert result["gate"]["action"] == "halt_for_review"
    holdout = json.loads((run_root / "scores/holdout/baseline.json").read_text(encoding="utf-8"))
    assert "saas-03:status_mismatch" in holdout["hard_failures"]


def test_cost_cap_blocks_before_execution(repo: Path, capsys):
    run_root = repo / "runs/company-enrichment" / ENRICHMENT / "lineage-cap"
    client = FakeModelClient(cost="0.50")
    code = signal_loop.main(
        make_spec(), ["--evaluate", "--lineage", "lineage-cap", "--allow-paid"],
        repo_root=repo, model_client_factory=lambda **_: client,
    )
    assert code == 2
    assert client.executed == []
    assert json.loads(capsys.readouterr().out)["error"] == "BudgetExceeded"
    blocked = json.loads((run_root / "cap-blocked.json").read_text(encoding="utf-8"))
    assert blocked["estimate_usd"] == "3.00" and blocked["cap_usd"] == "1.00"
    assert not (run_root / "outputs").exists()


def test_resume_lineage_guard(repo: Path, capsys):
    spec = make_spec()
    args = ["--evaluate", "--lineage", "lineage-2", "--allow-paid"]
    factory = lambda **_: FakeModelClient()  # noqa: E731

    assert signal_loop.main(spec, args, repo_root=repo, model_client_factory=factory) == 0
    capsys.readouterr()
    assert signal_loop.main(spec, args, repo_root=repo, model_client_factory=factory) == 2
    assert "lineage already exists" in json.loads(capsys.readouterr().out)["message"]
    assert signal_loop.main(
        spec, [*args, "--resume"], repo_root=repo, model_client_factory=factory,
    ) == 0

    dry_root = repo / "runs/company-enrichment" / ENRICHMENT / "lineage-3"
    assert signal_loop.main(spec, ["--evaluate", "--lineage", "lineage-3", "--dry-run"],
                            repo_root=repo) == 0
    assert (dry_root / "inputs.json").exists()
    capsys.readouterr()
    assert signal_loop.main(spec, ["--evaluate", "--lineage", "lineage-3", "--allow-paid"],
                            repo_root=repo, model_client_factory=factory) == 0


def test_lineage_name_is_validated(repo: Path):
    with pytest.raises(SystemExit):
        signal_loop.main(make_spec(), ["--evaluate", "--lineage", "../escape"], repo_root=repo)
    with pytest.raises(SystemExit):
        signal_loop.main(make_spec(), ["--evaluate"], repo_root=repo)


def test_collect_writes_signal_dossiers_and_skips_existing(repo: Path, capsys):
    calls: list[str] = []

    def collect(request: CollectRequest) -> CompanyDossier:
        calls.append(request.company_id)
        assert request.base.company_id == request.company_id
        return signal_dossier(
            request.company_id, request.base,
            (_ref(request.company_id, "google", '{"running_ads": false}'),),
        )

    spec = make_spec(collect=collect)
    target = repo / "benchmarks/signals" / ENRICHMENT / "saas-01.yaml"
    before = target.read_bytes()

    assert signal_loop.main(spec, ["--collect", "--company", "saas-01", "--dry-run"],
                            repo_root=repo) == 0
    assert calls == [] and target.read_bytes() == before
    dry = json.loads(capsys.readouterr().out)
    assert dry["dry_run"] is True and dry["skipped_existing"] == ["saas-01"]

    assert signal_loop.main(spec, ["--collect", "--company", "saas-01"], repo_root=repo) == 0
    assert calls == [] and target.read_bytes() == before
    result = json.loads(capsys.readouterr().out)
    assert result["written"] == [] and result["skipped_existing"] == ["saas-01"]

    assert signal_loop.main(
        spec, ["--collect", "--company", "saas-01", "--overwrite"], repo_root=repo,
    ) == 0
    assert calls == ["saas-01"]
    loaded = load_signal_dossier(target)
    assert loaded.evidence[-1].excerpt == '{"running_ads": false}'
    assert loaded.evidence[0].evidence_id == "ev-saas-01-about"
    result = json.loads(capsys.readouterr().out)
    assert result["written"] == ["saas-01"] and result["approval"] is False


def test_collect_without_stage_or_dropped_base_evidence_fails(repo: Path, capsys):
    assert signal_loop.main(make_spec(), ["--collect"], repo_root=repo) == 2
    assert "no collect stage" in json.loads(capsys.readouterr().out)["message"]

    def dropping(request: CollectRequest) -> CompanyDossier:
        return CompanyDossier(request.company_id, "1.0", (), (_ref(request.company_id, "x", "x"),))

    assert signal_loop.main(
        make_spec(collect=dropping), ["--collect", "--company", "saas-02", "--overwrite"],
        repo_root=repo,
    ) == 2
    assert "dropped base Evidence" in json.loads(capsys.readouterr().out)["message"]


def test_postprocess_grounds_output_and_keeps_the_model_payload(repo: Path):
    def ground(payload, dossier):
        assert dossier.company_id.startswith("saas-")
        grounded = {"ads": {"google": {**payload["ads"]["google"], "status": "grounded"}},
                    "unknowns": []}
        return grounded, {"dropped": [], "note": "touched"}

    run_root = repo / "runs/company-enrichment" / ENRICHMENT / "lineage-pp"
    run_loop(make_spec(postprocess=ground), repo_root=repo, run_root=run_root,
             model_client=FakeModelClient(), resume=False)

    artifact = json.loads((run_root / "outputs/dev/baseline/saas-01.json").read_text(encoding="utf-8"))
    assert artifact["model_output"]["ads"]["google"]["status"] == "active"
    assert artifact["output"]["ads"]["google"]["status"] == "grounded"
    assert artifact["postprocess"] == {"dropped": [], "note": "touched"}
    # scoring sees the grounded payload, not the raw model payload
    dev = json.loads((run_root / "scores/dev/baseline.json").read_text(encoding="utf-8"))
    assert dev["hard_failures"] == [f"{cid}:status_mismatch" for cid in DEVELOPMENT_IDS]
    with pytest.raises(ValueError, match="postprocess must be callable"):
        make_spec(postprocess="nope")


def test_extra_prompt_candidates_are_evaluated_and_the_best_wins(repo: Path):
    prompts = repo / "prompts/company-enrichment/candidates"
    prompts.mkdir(parents=True)
    (prompts / "v2-tighter.md").write_text("Tighter prompt.\n", encoding="utf-8")

    class ScoreByPrompt(FakeModelClient):
        def execute(self, requests, track):
            self.status = "active" if "Tighter" in requests[0].prompt_text else "inactive"
            return super().execute(requests, track)

    client = ScoreByPrompt()
    run_root = repo / "runs/company-enrichment" / ENRICHMENT / "lineage-cands"
    result = run_loop(
        make_spec(candidate_paths=(Path("prompts/company-enrichment/candidates/v2-tighter.md"),)),
        repo_root=repo, run_root=run_root, model_client=client, resume=False,
    )

    candidates = json.loads((run_root / "candidates.json").read_text(encoding="utf-8"))
    assert [item["candidate_id"] for item in candidates["candidates"]] == ["baseline", "v2-tighter"]
    assert result["winner"]["candidate_id"] == "v2-tighter"
    assert (run_root / "scores/dev/v2-tighter.json").exists()
    assert (run_root / "scores/holdout/v2-tighter.json").exists()
    assert not (run_root / "scores/holdout/baseline.json").exists()
    assert set(json.loads((run_root / "cost.json").read_text(encoding="utf-8"))["reservations"]) == {
        "dev-0-baseline", "dev-1-v2-tighter", "holdout-v2-tighter",
    }


def test_candidate_flag_adds_prompt_files(repo: Path, capsys):
    prompts = repo / "prompts/company-enrichment/candidates"
    prompts.mkdir(parents=True)
    (prompts / "v2.md").write_text("Alt prompt.\n", encoding="utf-8")
    code = signal_loop.main(make_spec(), [
        "--evaluate", "--lineage", "dry-cands", "--dry-run",
        "--candidate", "prompts/company-enrichment/candidates/v2.md",
    ], repo_root=repo, model_client_factory=lambda **_: pytest.fail("no client"))
    assert code == 0
    plan = json.loads(capsys.readouterr().out)
    assert len(plan["candidate_prompt_hashes"]) == 2
    with pytest.raises(ValueError, match="duplicate prompt candidate id"):
        signal_loop.prompt_candidates(make_spec(candidate_paths=(
            Path("prompts/company-enrichment/candidates/v2.md"),
            Path("prompts/company-enrichment/other/v2.md"),
        )), repo)
