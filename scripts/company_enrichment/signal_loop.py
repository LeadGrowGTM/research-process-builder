"""Generic, cached-only prompt loop for signal enrichments over the SaaS benchmark.

One ``SignalSpec`` parameterizes the loop for an enrichment (running ads,
news/launches, competitors). Two phases:

- ``--collect``: network allowed; ``spec.collect`` builds a signal dossier per
  company (base Evidence plus signal Evidence) and writes it to
  ``benchmarks/signals/<enrichment_id>/<company>.yaml``.
- ``--evaluate``: cached only; the prompt candidate runs over the development
  split, the winner runs over the sealed holdout, and the gate decides. Costs
  are reserved before every attempt against a $1.00 aggregate cap.

Artifacts land under ``runs/company-enrichment/<enrichment_id>/<lineage>/``
with the same layout as the ICP loop: ``outputs/{split}/{candidate}/{company}.json``,
``scores/{split}/{candidate}.json``, ``cost.json``, ``inputs.json``.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field, replace
from decimal import Decimal
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from typing import Any, Callable, Mapping, Sequence

from .benchmark import ExecutionTrack
from .contracts import CompanyDossier, canonical_json
from .executors import P0_ENRICHMENTS
from .experiment_runner import ExperimentInput, ModelClient
from .openai_model_client import build_openai_model_client
from .signal_evidence import (
    load_cached_dossiers, load_signal_dossier, save_signal_dossier, signal_dossier_path,
)
from .signal_ground_truth import (
    ALL_IDS, DEVELOPMENT_IDS, HOLDOUT_IDS, EvaluatorSignalDataset,
    GroundTruthLoader, SignalDataset, SignalGroundTruthRecord,
    issue_evaluator_dataset_capability, validate_weights,
)
from scripts.research_orchestration.budgets import BudgetExceeded, BudgetLimits
from scripts.research_orchestration.contracts import (
    BudgetCharge, CheckerResult, EvaluationResult, Evidence, Experiment,
    Role, RunRequest, SCHEMA_VERSION,
)
from scripts.research_orchestration.gate import GateInput, decide_gate
from scripts.research_orchestration.orchestrator import RoleRunners


MODEL_ID = os.environ.get("SIGNAL_LOOP_MODEL", "gpt-4.1-mini")
CAP_USD = Decimal("1.00")
THRESHOLD = Decimal(".90")
_LINEAGE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,79}$")
_STARTED_MARKERS = ("cost.json", "outputs", "scores", "gate.json", "result.json")


@dataclass(frozen=True, slots=True)
class PromptCandidate:
    candidate_id: str
    text: str
    prompt_hash: str


@dataclass(frozen=True, slots=True)
class CaseScore:
    company_id: str
    components: Mapping[str, Decimal]
    score: Decimal
    hard_failures: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CollectRequest:
    """Inputs handed to ``SignalSpec.collect`` for one company."""

    company_id: str
    base: CompanyDossier
    repo_root: Path


ScoreFn = Callable[[Mapping[str, Any], SignalGroundTruthRecord, CompanyDossier], CaseScore]
CollectFn = Callable[[CollectRequest], CompanyDossier]
# Deterministic output hygiene applied to the model payload before it is
# written, scored, or shipped: returns (grounded_payload, report).
PostprocessFn = Callable[[Mapping[str, Any], CompanyDossier], tuple[dict[str, Any], dict[str, Any]]]


@dataclass(frozen=True)
class SignalSpec:
    """Everything that differs between two signal-enrichment prompt loops."""

    enrichment_id: str
    fields: tuple[str, ...]
    benchmark_dir: Path
    output_contract: Callable[[CompanyDossier], dict[str, Any]]
    load_ground_truth: GroundTruthLoader
    score: ScoreFn
    prompt_path: Path
    weights: Mapping[str, Decimal]
    collect: CollectFn | None = None
    candidate_id: str = field(default="baseline")
    postprocess: PostprocessFn | None = None
    # Extra prompt files evaluated as candidates alongside ``prompt_path``;
    # each candidate is identified by its file stem.
    candidate_paths: tuple[Path, ...] = ()

    def __post_init__(self) -> None:
        expected = P0_ENRICHMENTS.get(self.enrichment_id)
        if expected is None:
            raise ValueError(f"unknown P0 enrichment: {self.enrichment_id}")
        if tuple(self.fields) != expected:
            raise ValueError(f"fields must equal {expected} for {self.enrichment_id}")
        object.__setattr__(self, "fields", tuple(self.fields))
        object.__setattr__(self, "benchmark_dir", Path(self.benchmark_dir))
        object.__setattr__(self, "prompt_path", Path(self.prompt_path))
        if self.benchmark_dir.is_absolute() or self.prompt_path.is_absolute():
            raise ValueError("benchmark_dir and prompt_path must be repo-relative")
        for name in ("output_contract", "load_ground_truth", "score"):
            if not callable(getattr(self, name)):
                raise ValueError(f"{name} must be callable")
        if self.collect is not None and not callable(self.collect):
            raise ValueError("collect must be callable or None")
        if self.postprocess is not None and not callable(self.postprocess):
            raise ValueError("postprocess must be callable or None")
        candidate_paths = tuple(Path(item) for item in self.candidate_paths)
        if any(item.is_absolute() for item in candidate_paths):
            raise ValueError("candidate_paths must be repo-relative")
        object.__setattr__(self, "candidate_paths", candidate_paths)
        object.__setattr__(self, "weights", validate_weights(self.weights))
        if not isinstance(self.candidate_id, str) or not self.candidate_id.strip():
            raise ValueError("candidate_id must be non-empty text")

    @property
    def rubric(self) -> str:
        return ",".join(f"{key}:{value}" for key, value in self.weights.items())


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    temporary.write_text(canonical_json(value) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON artifact must be an object: {path}")
    return value


def _primitive(value: Any) -> Any:
    return json.loads(canonical_json(value))


def prompt_candidates(spec: SignalSpec, repo_root: Path) -> tuple[PromptCandidate, ...]:
    """The baseline prompt plus every ``candidate_paths`` file, keyed by stem."""
    candidates: list[PromptCandidate] = []
    seen: set[str] = set()
    for candidate_id, path in (
        (spec.candidate_id, spec.prompt_path),
        *((item.stem, item) for item in spec.candidate_paths),
    ):
        if candidate_id in seen or not _LINEAGE.match(candidate_id):
            raise ValueError(f"invalid or duplicate prompt candidate id: {candidate_id}")
        seen.add(candidate_id)
        text = (repo_root / path).read_text(encoding="utf-8").strip()
        candidates.append(PromptCandidate(
            candidate_id, text, sha256(text.encode("utf-8")).hexdigest(),
        ))
    return tuple(candidates)


def load_signal_dossiers(spec: SignalSpec, repo_root: Path) -> dict[str, CompanyDossier]:
    return {
        company_id: load_signal_dossier(repo_root / spec.benchmark_dir / f"{company_id}.yaml")
        for company_id in ALL_IDS
    }


def _payload_from_execution(execution) -> dict[str, Any]:
    payload = {item.field: _primitive(item.value) for item in execution.assertions}
    payload["unknowns"] = list(execution.unknowns)
    return payload


def _reserve_cost(path: Path, attempt_id: str, estimate: Decimal, cap: Decimal = CAP_USD) -> None:
    state = _read_json(path) or {"cap_usd": str(cap), "reservations": {}}
    reservations = state["reservations"]
    if attempt_id in reservations:
        return
    committed = sum((Decimal(item.get("actual_usd", item["estimate_usd"]))
                     for item in reservations.values()), Decimal("0"))
    if committed + estimate > cap:
        _atomic_json(path.parent / "cap-blocked.json", {
            "attempt_id": attempt_id, "committed_usd": str(committed),
            "estimate_usd": str(estimate), "cap_usd": str(cap),
        })
        raise BudgetExceeded("aggregate signal loop cap exhausted")
    reservations[attempt_id] = {"estimate_usd": str(estimate), "status": "reserved"}
    _atomic_json(path, state)


def _reconcile_cost(path: Path, attempt_id: str, actual: Decimal, cap: Decimal = CAP_USD) -> None:
    state = _read_json(path)
    if state is None or attempt_id not in state["reservations"]:
        raise ValueError("missing cost reservation")
    reservations = state["reservations"]
    other = sum((Decimal(item.get("actual_usd", item["estimate_usd"]))
                 for key, item in reservations.items() if key != attempt_id), Decimal("0"))
    if other + actual > cap:
        raise BudgetExceeded("actual aggregate signal loop cost exceeded cap")
    reservations[attempt_id] = {
        **reservations[attempt_id], "actual_usd": str(actual), "status": "completed",
    }
    _atomic_json(path, state)


def _total_cost(path: Path) -> Decimal:
    state = _read_json(path) or {"reservations": {}}
    return sum((Decimal(item.get("actual_usd", "0"))
                for item in state["reservations"].values()), Decimal("0"))


def _score_attempt(
    spec: SignalSpec, output_dir: Path, ids: Sequence[str],
    records: Mapping[str, SignalGroundTruthRecord], dossiers: Mapping[str, CompanyDossier],
) -> tuple[tuple[CaseScore, ...], Decimal, tuple[str, ...]]:
    cases_list: list[CaseScore] = []
    for company_id in ids:
        artifact = _read_json(output_dir / f"{company_id}.json") or {}
        if artifact.get("invalid_output"):
            cases_list.append(CaseScore(
                company_id, {key: Decimal("0") for key in spec.weights},
                Decimal("0"), ("invalid_output",),
            ))
            continue
        cases_list.append(spec.score(
            artifact["output"], records[company_id], dossiers[company_id],
        ))
    cases = tuple(cases_list)
    mean = sum((item.score for item in cases), Decimal("0")) / Decimal(len(cases))
    failures = tuple(f"{item.company_id}:{reason}" for item in cases
                     for reason in item.hard_failures)
    return cases, mean, failures


def _run_attempt(
    *, spec: SignalSpec, run_root: Path, attempt_id: str, split: str, ids: Sequence[str],
    candidate: PromptCandidate, records: Mapping[str, SignalGroundTruthRecord],
    dossiers: Mapping[str, CompanyDossier], dataset_hash: str, model_client: ModelClient,
) -> dict[str, Any] | None:
    requests = tuple(ExperimentInput(
        spec.enrichment_id, company_id, MODEL_ID, dossiers[company_id],
        candidate.candidate_id, candidate.text, spec.output_contract(dossiers[company_id]),
    ) for company_id in ids)
    estimate = Decimal(model_client.estimate(requests, ExecutionTrack.SYNCHRONOUS))
    output_dir = run_root / "outputs" / split / candidate.candidate_id
    score_path = run_root / "scores" / split / f"{candidate.candidate_id}.json"
    cost_path = run_root / "cost.json"

    def input_hash(company_id: str) -> str:
        return sha256(canonical_json({
            "dossier": _primitive(dossiers[company_id]), "prompt_hash": candidate.prompt_hash,
        }).encode("utf-8")).hexdigest()

    def inventor(_envelope):
        return Experiment(SCHEMA_VERSION, spec.enrichment_id,
                          f"Evaluate {candidate.candidate_id} on {split}",
                          candidate.prompt_hash)

    def checker(name):
        return lambda _envelope: CheckerResult(SCHEMA_VERSION, name, True, "bounded")

    def executor(_envelope):
        _reserve_cost(cost_path, attempt_id, estimate)
        executions = []
        invalid_ids: list[str] = []
        for request in requests:
            try:
                execution = model_client.execute((request,), ExecutionTrack.SYNCHRONOUS)[0]
            except Exception as error:
                invalid_ids.append(request.company_id)
                _atomic_json(output_dir / f"{request.company_id}.json", {
                    "company_id": request.company_id,
                    "error": type(error).__name__,
                    # The message never contains prompt text or secrets; it is
                    # the validator/provider reason, kept so a failed case can
                    # be diagnosed without re-running the model.
                    "error_message": str(error)[:500],
                    "input_hash": input_hash(request.company_id),
                    "invalid_output": True,
                    "requested_model": MODEL_ID,
                    "source_cache_reused": True,
                    "source_purchases": 0,
                })
                continue
            executions.append(execution)
            payload = _payload_from_execution(execution)
            artifact = {
                "company_id": execution.company_id,
                "input_hash": input_hash(execution.company_id),
                "latency_ms": execution.latency_ms,
                "model_cost_usd": execution.actual_cost_usd,
                "output": payload,
                "requested_model": MODEL_ID,
                "resolved_model": execution.resolved_model_id,
                "source_cache_reused": True,
                "source_purchases": 0,
            }
            if spec.postprocess is not None:
                grounded, report = spec.postprocess(payload, dossiers[execution.company_id])
                artifact.update({
                    "model_output": payload, "output": grounded, "postprocess": report,
                })
            _atomic_json(output_dir / f"{execution.company_id}.json", artifact)
        actual = sum((Decimal(item.actual_cost_usd) for item in executions), Decimal("0"))
        _reconcile_cost(cost_path, attempt_id, estimate if invalid_ids else actual)
        return tuple(Evidence(
            SCHEMA_VERSION, dossiers[company_id].evidence[0].url,
            canonical_json({
                "artifact": str((output_dir / f"{company_id}.json").relative_to(run_root)),
                "company_id": company_id,
            }), dossiers[company_id].evidence[0].retrieved_at.isoformat(),
        ) for company_id in ids)

    def evaluator(_envelope):
        cases, mean, failures = _score_attempt(spec, output_dir, ids, records, dossiers)
        _atomic_json(score_path, {
            "candidate_id": candidate.candidate_id,
            "cases": [{
                "company_id": item.company_id,
                "component_scores": {key: str(value) for key, value in item.components.items()},
                "hard_failures": list(item.hard_failures), "score": str(item.score),
            } for item in cases],
            "hard_failures": list(failures), "mean_score": str(mean), "split": split,
        })
        return EvaluationResult(SCHEMA_VERSION, not failures, float(mean),
                                "scored" if not failures else "hard_failure")

    zero = BudgetCharge()
    RoleRunners(
        inventor, checker("in_bounds"), checker("novelty"), executor, evaluator,
        {Role.INVENTOR: zero, Role.IN_BOUNDS_CHECKER: zero, Role.NOVELTY_CHECKER: zero,
         Role.EXECUTOR: BudgetCharge(calls=1, llm_calls=len(ids), cost=float(estimate)),
         Role.EVALUATOR: zero},
    )
    RunRequest(
        SCHEMA_VERSION, attempt_id, f"{spec.enrichment_id} {split} prompt attempt",
        ("cached_dossier_evidence_only", "zero_source_purchases", "sync_only"), {},
        BudgetLimits(max_calls=1, max_llm_calls=len(ids), max_cost=float(CAP_USD)),
        .90,
        execution_inputs={"company_ids": tuple(ids), "dataset_hash": dataset_hash,
                          "prompt_hash": candidate.prompt_hash, "split": split},
        rubric=spec.rubric,
    )
    executor(None)
    evaluator(None)
    return _read_json(score_path)


def _started(run_root: Path) -> bool:
    return any((run_root / name).exists() for name in _STARTED_MARKERS)


def _write_inputs(spec: SignalSpec, repo_root: Path, run_root: Path, public: SignalDataset) -> None:
    def digest(path: Path) -> str:
        return sha256(path.read_bytes()).hexdigest()
    _atomic_json(run_root / "inputs.json", {
        "all_ids": list(public.all_ids), "dataset_hash": public.dataset_hash,
        "development_ids": list(public.development_ids),
        "dossier_hashes": {company_id: digest(
            repo_root / "benchmarks/dossiers" / f"{company_id}.yaml",
        ) for company_id in ALL_IDS},
        "enrichment_id": spec.enrichment_id,
        "holdout_ids": list(public.holdout_ids), "source_purchases": 0,
        "signal_hashes": {company_id: digest(
            repo_root / spec.benchmark_dir / f"{company_id}.yaml",
        ) for company_id in ALL_IDS},
    })


def run_loop(
    spec: SignalSpec, *, repo_root: Path, run_root: Path, model_client: ModelClient,
    resume: bool,
) -> dict[str, Any]:
    if _started(run_root) and not resume:
        raise ValueError("lineage already exists; pass --resume")
    run_root.mkdir(parents=True, exist_ok=True)
    dossiers = load_signal_dossiers(spec, repo_root)
    public = spec.load_ground_truth(repo_root, dossiers)
    if not isinstance(public, SignalDataset):
        raise ValueError("load_ground_truth must return a public SignalDataset")
    candidates = prompt_candidates(spec, repo_root)
    _write_inputs(spec, repo_root, run_root, public)
    _atomic_json(run_root / "candidates.json", {
        "candidates": [{"candidate_id": item.candidate_id, "prompt_hash": item.prompt_hash,
                        "prompt_text": item.text} for item in candidates],
        "max_candidates": len(candidates),
    })

    dev_scores = []
    for index, candidate in enumerate(candidates):
        score = _run_attempt(
            spec=spec, run_root=run_root, attempt_id=f"dev-{index}-{candidate.candidate_id}",
            split="dev", ids=public.development_ids, candidate=candidate,
            records=public.records, dossiers=dossiers, dataset_hash=public.dataset_hash,
            model_client=model_client,
        )
        if score is None:
            break
        dev_scores.append((Decimal(score["mean_score"]), -len(score["hard_failures"]),
                           -index, candidate))
    if len(dev_scores) != len(candidates):
        gate = {"action": "halt_for_review", "approval": False,
                "human_review_eligible": False, "reason_code": "budget_exhausted",
                "threshold": str(THRESHOLD)}
        result = {"enrichment_id": spec.enrichment_id, "gate": gate, "model": MODEL_ID,
                  "total_cost_usd": str(_total_cost(run_root / "cost.json"))}
        _atomic_json(run_root / "gate.json", gate)
        _atomic_json(run_root / "result.json", result)
        return result

    winner = max(dev_scores, key=lambda item: item[:3])[3]
    winner_value = {"candidate_id": winner.candidate_id, "prompt_hash": winner.prompt_hash}
    _atomic_json(run_root / "winner.json", winner_value)

    evaluator_dataset = spec.load_ground_truth(
        repo_root, dossiers, capability=issue_evaluator_dataset_capability(),
    )
    if not isinstance(evaluator_dataset, EvaluatorSignalDataset):
        raise ValueError("load_ground_truth must honour the evaluator capability")
    holdout = _run_attempt(
        spec=spec, run_root=run_root, attempt_id=f"holdout-{winner.candidate_id}",
        split="holdout", ids=public.holdout_ids, candidate=winner,
        records=evaluator_dataset.records, dossiers=dossiers,
        dataset_hash=evaluator_dataset.public.dataset_hash, model_client=model_client,
    )
    if holdout is None:
        mean, failures = Decimal("0"), ("budget_exhausted",)
    else:
        mean = Decimal(holdout["mean_score"])
        failures = tuple(holdout["hard_failures"])
    decision = decide_gate(GateInput(
        checks_accepted=True, evaluation_passed=not failures,
        candidate_score=float(mean), safe_to_proceed=not failures,
        budget_exhausted=holdout is None,
    ))
    eligible = mean >= THRESHOLD and not failures
    gate = {
        "action": decision.action.value, "approval": False,
        "human_review_eligible": eligible, "reason_code": decision.reason_code,
        "threshold": str(THRESHOLD),
    }
    result = {
        "enrichment_id": spec.enrichment_id, "gate": gate, "model": MODEL_ID,
        "source_purchases": 0, "total_cost_usd": str(_total_cost(run_root / "cost.json")),
        "winner": winner_value,
    }
    _atomic_json(run_root / "gate.json", gate)
    _atomic_json(run_root / "result.json", result)
    return result


def _dry_plan(spec: SignalSpec, lineage: str, repo_root: Path) -> dict[str, Any]:
    return {
        "all_ids": list(ALL_IDS), "approval": False, "cached_only": True,
        "candidate_prompt_hashes": [
            item.prompt_hash for item in prompt_candidates(spec, repo_root)
        ],
        "cost_cap_usd": str(CAP_USD), "development_ids": list(DEVELOPMENT_IDS),
        "enrichment_id": spec.enrichment_id, "holdout_ids": list(HOLDOUT_IDS),
        "lineage": lineage, "model": MODEL_ID, "source_purchases": 0,
        "track": "synchronous",
    }


def collect_signals(
    spec: SignalSpec, *, repo_root: Path, company_ids: Sequence[str], overwrite: bool,
    dry_run: bool,
) -> dict[str, Any]:
    """Run ``spec.collect`` per company and write signal dossiers to the benchmark dir."""
    if spec.collect is None:
        raise ValueError(f"{spec.enrichment_id} has no collect stage")
    unknown = sorted(set(company_ids) - set(ALL_IDS))
    if unknown:
        raise ValueError(f"unknown company IDs: {unknown}")
    bases = load_cached_dossiers(repo_root, company_ids)
    written: list[str] = []
    skipped: list[str] = []
    for company_id in company_ids:
        target = signal_dossier_path(repo_root, spec.enrichment_id, company_id)
        if target.exists() and not overwrite:
            skipped.append(company_id)
            continue
        if dry_run:
            written.append(company_id)
            continue
        dossier = spec.collect(CollectRequest(company_id, bases[company_id], repo_root))
        if not isinstance(dossier, CompanyDossier) or dossier.company_id != company_id:
            raise ValueError(f"collect must return the signal dossier for {company_id}")
        base_ids = {item.evidence_id for item in bases[company_id].evidence}
        if not base_ids <= {item.evidence_id for item in dossier.evidence}:
            raise ValueError(f"signal dossier for {company_id} dropped base Evidence")
        save_signal_dossier(target, dossier)
        written.append(company_id)
    return {
        "approval": False, "dry_run": dry_run, "enrichment_id": spec.enrichment_id,
        "phase": "collect", "skipped_existing": skipped,
        "targets": [str(signal_dossier_path(repo_root, spec.enrichment_id, company_id))
                    for company_id in written],
        "written" if not dry_run else "planned": written,
    }


def main(
    spec: SignalSpec, argv: Sequence[str] | None = None, *, repo_root: Path | None = None,
    artifact_root: Path | None = None, model_client_factory=build_openai_model_client,
) -> int:
    parser = argparse.ArgumentParser(
        description=f"{spec.enrichment_id} signal loop",
        usage="%(prog)s (--collect [--company ID ...] [--overwrite] | --evaluate --lineage NAME "
              "[--allow-paid] [--resume]) [--dry-run]",
    )
    phase = parser.add_mutually_exclusive_group(required=True)
    phase.add_argument("--collect", action="store_true",
                       help="collect signal Evidence per company (network allowed)")
    phase.add_argument("--evaluate", action="store_true",
                       help="run the cached-only prompt loop for a lineage")
    parser.add_argument("--lineage", help="artifact lineage name (required for --evaluate)")
    parser.add_argument("--company", action="append", default=None,
                        help="restrict --collect to these company IDs (repeatable)")
    parser.add_argument("--overwrite", action="store_true",
                        help="let --collect replace an existing signal dossier")
    parser.add_argument("--prompt", default=None,
                        help="repo-relative prompt file that replaces the baseline prompt "
                             "(the candidate id is the file stem)")
    parser.add_argument("--candidate", action="append", default=None,
                        help="repo-relative prompt file evaluated as an extra candidate "
                             "(repeatable; the candidate id is the file stem)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--allow-paid", action="store_true")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args(argv)
    root = Path(repo_root or Path(__file__).resolve().parents[2])
    if args.prompt or args.candidate:
        overrides: dict[str, Any] = {}
        if args.prompt:
            overrides.update(prompt_path=Path(args.prompt), candidate_id=Path(args.prompt).stem)
        if args.candidate:
            overrides["candidate_paths"] = (
                *spec.candidate_paths, *(Path(item) for item in args.candidate),
            )
        try:
            spec = replace(spec, **overrides)
        except ValueError as error:
            parser.error(str(error))

    if args.collect:
        try:
            result = collect_signals(
                spec, repo_root=root, company_ids=tuple(args.company or ALL_IDS),
                overwrite=args.overwrite, dry_run=args.dry_run,
            )
        except (BudgetExceeded, ValueError) as error:
            print(canonical_json({"approval": False, "error": type(error).__name__,
                                  "message": str(error)}))
            return 2
        print(canonical_json(result))
        return 0

    if not args.lineage or not _LINEAGE.fullmatch(args.lineage):
        parser.error("--evaluate requires --lineage as a safe task-scoped name")
    base = Path(artifact_root or root / "runs/company-enrichment" / spec.enrichment_id)
    run_root = base / args.lineage
    if args.dry_run:
        dossiers = load_signal_dossiers(spec, root)
        public = spec.load_ground_truth(root, dossiers)
        _write_inputs(spec, root, run_root, public)
        print(canonical_json(_dry_plan(spec, args.lineage, root)))
        return 0
    if not args.allow_paid:
        print(canonical_json({"approval": False, "error": "--allow-paid is required"}))
        return 2
    client = model_client_factory(artifact_root=run_root)
    try:
        result = run_loop(spec, repo_root=root, run_root=run_root,
                          model_client=client, resume=args.resume)
    except (BudgetExceeded, ValueError) as error:
        print(canonical_json({"approval": False, "error": type(error).__name__,
                              "message": str(error)}))
        return 2
    print(canonical_json(result))
    return 0 if result["gate"]["human_review_eligible"] else 2
