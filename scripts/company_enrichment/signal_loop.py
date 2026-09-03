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
from functools import partial
from hashlib import sha256
import inspect
import json
import marshal
import os
from pathlib import Path
import re
from typing import Any, Callable, Mapping, Sequence

from .benchmark import ExecutionTrack
from .contracts import CompanyDossier, canonical_json
from .executors import P0_ENRICHMENTS
from .experiment_runner import ExperimentInput, ModelClient
from .openai_model_client import MODEL_PRICES, build_openai_model_client
from .packages import candidate_prompt_text, resolve_prompt_path
from .signal_evidence import (
    load_cached_dossiers, load_signal_dossier, save_signal_dossier,
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
# Comma-separated search provider order consumed by the entrypoints' lazy
# search factory (for example "parallel" or "serper,parallel").
SEARCH_PROVIDER_ENV = "SIGNAL_SEARCH_PROVIDER"
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
    # Repo-relative dossier directory; ``None`` keeps the canonical
    # ``benchmarks/signals/<enrichment_id>`` location.
    benchmark_dir: Path | None = None


ScoreFn = Callable[[Mapping[str, Any], SignalGroundTruthRecord, CompanyDossier], CaseScore]
CollectFn = Callable[[CollectRequest], CompanyDossier]
# Deterministic output hygiene applied to the model payload before it is
# written, scored, or shipped: returns (grounded_payload, report).
PostprocessFn = Callable[[Mapping[str, Any], CompanyDossier], tuple[dict[str, Any], dict[str, Any]]]


def _require_repo_relative(path: Path, name: str) -> None:
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"{name} must be repo-relative without '..' segments")


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
    evaluation_dependencies: tuple[Callable[..., Any], ...] = ()
    resolve_prompt_layout: bool = True
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
        _require_repo_relative(self.benchmark_dir, "benchmark_dir")
        _require_repo_relative(self.prompt_path, "prompt_path")
        for name in ("output_contract", "load_ground_truth", "score"):
            if not callable(getattr(self, name)):
                raise ValueError(f"{name} must be callable")
        if self.collect is not None and not callable(self.collect):
            raise ValueError("collect must be callable or None")
        if self.postprocess is not None and not callable(self.postprocess):
            raise ValueError("postprocess must be callable or None")
        evaluation_dependencies = tuple(self.evaluation_dependencies)
        if not all(callable(item) for item in evaluation_dependencies):
            raise ValueError("evaluation_dependencies must contain only callables")
        object.__setattr__(self, "evaluation_dependencies", evaluation_dependencies)
        if not isinstance(self.resolve_prompt_layout, bool):
            raise ValueError("resolve_prompt_layout must be boolean")
        candidate_paths = tuple(Path(item) for item in self.candidate_paths)
        for item in candidate_paths:
            _require_repo_relative(item, "candidate_paths")
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


def _callable_fingerprint(value: Callable[..., Any] | None) -> str | None:
    if value is None:
        return None
    code = getattr(value, "__code__", None)
    if code is None:
        raise ValueError("evaluation callable cannot be deterministically fingerprinted")
    try:
        closure = [
            _primitive(cell.cell_contents) for cell in (value.__closure__ or ())
        ]
        defaults = _primitive(value.__defaults__ or ())
        keyword_defaults = _primitive(value.__kwdefaults__ or {})
    except (TypeError, ValueError) as error:
        raise ValueError(
            "evaluation callable closes over a non-deterministic value"
        ) from error
    source_file = inspect.getsourcefile(value)
    source_hash = (
        sha256(Path(source_file).read_bytes()).hexdigest()
        if source_file and Path(source_file).is_file()
        else None
    )
    material = {
        "closure": closure,
        "code_hash": sha256(marshal.dumps(code)).hexdigest(),
        "defaults": defaults,
        "keyword_defaults": keyword_defaults,
        "module": getattr(value, "__module__", None),
        "qualname": getattr(value, "__qualname__", None),
        "source_hash": source_hash,
    }
    return sha256(canonical_json(material).encode("utf-8")).hexdigest()


def _evaluation_fingerprint(spec: SignalSpec) -> str:
    return sha256(canonical_json({
        "dependencies": [
            _callable_fingerprint(item) for item in spec.evaluation_dependencies
        ],
        "postprocess": _callable_fingerprint(spec.postprocess),
        "rubric": spec.rubric,
        "score": _callable_fingerprint(spec.score),
    }).encode("utf-8")).hexdigest()


def prompt_candidates(spec: SignalSpec, repo_root: Path) -> tuple[PromptCandidate, ...]:
    """The baseline prompt plus every ``candidate_paths`` file, keyed by stem."""
    candidates: list[PromptCandidate] = []
    seen: set[str] = set()
    conventional = Path("prompts/company-enrichment") / f"{spec.enrichment_id}.md"
    baseline_path = (
        resolve_prompt_path(spec.enrichment_id, repo_root)
        if spec.resolve_prompt_layout and spec.prompt_path == conventional
        else spec.prompt_path
    )
    for candidate_id, path in (
        (spec.candidate_id, baseline_path),
        *((item.stem, item) for item in spec.candidate_paths),
    ):
        if candidate_id in seen or not _LINEAGE.match(candidate_id):
            raise ValueError(f"invalid or duplicate prompt candidate id: {candidate_id}")
        seen.add(candidate_id)
        text = candidate_prompt_text(repo_root / path)
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


def _reserve_cost(
    path: Path, attempt_id: str, estimate: Decimal, company_ids: Sequence[str],
    cap: Decimal = CAP_USD,
) -> None:
    state = _read_json(path) or {"cap_usd": str(cap), "reservations": {}}
    reservations = state["reservations"]
    if attempt_id in reservations:
        raise ValueError(f"cost reservation already exists: {attempt_id}")
    committed = sum((Decimal(item.get("actual_usd", item["estimate_usd"]))
                     for item in reservations.values()), Decimal("0"))
    if committed + estimate > cap:
        _atomic_json(path.parent / "cap-blocked.json", {
            "attempt_id": attempt_id, "committed_usd": str(committed),
            "estimate_usd": str(estimate), "cap_usd": str(cap),
        })
        raise BudgetExceeded("aggregate signal loop cap exhausted")
    reservations[attempt_id] = {
        "company_ids": list(company_ids), "estimate_usd": str(estimate),
        "status": "reserved",
    }
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
    return sum((Decimal(item.get("actual_usd", item["estimate_usd"]))
                for item in state["reservations"].values()), Decimal("0"))


def _reservation_ids(path: Path, attempt_id: str) -> tuple[str, ...]:
    state = _read_json(path) or {"reservations": {}}
    prefix = f"{attempt_id}-retry-"
    matching = (
        key for key in state["reservations"]
        if key == attempt_id or key.startswith(prefix)
    )
    return tuple(sorted(
        matching,
        key=lambda key: 0 if key == attempt_id else int(key.removeprefix(prefix)),
    ))


def _next_reservation_id(path: Path, attempt_id: str) -> str:
    existing = set(_reservation_ids(path, attempt_id))
    if attempt_id not in existing:
        return attempt_id
    retry = 1
    while f"{attempt_id}-retry-{retry}" in existing:
        retry += 1
    return f"{attempt_id}-retry-{retry}"


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
    model_id: str, requests_by_company: Mapping[str, ExperimentInput],
    evaluation_fingerprint: str, allow_provider_execution: bool,
) -> dict[str, Any] | None:
    output_dir = run_root / "outputs" / split / candidate.candidate_id
    score_path = run_root / "scores" / split / f"{candidate.candidate_id}.json"
    cost_path = run_root / "cost.json"

    def input_hash(company_id: str) -> str:
        return sha256(canonical_json({
            "dossier": _primitive(dossiers[company_id]), "model_id": model_id,
            "prompt_hash": candidate.prompt_hash,
        }).encode("utf-8")).hexdigest()

    def inventor(_envelope):
        return Experiment(SCHEMA_VERSION, spec.enrichment_id,
                          f"Evaluate {candidate.candidate_id} on {split}",
                          candidate.prompt_hash)

    def checker(name):
        return lambda _envelope: CheckerResult(SCHEMA_VERSION, name, True, "bounded")

    def executor(_envelope):
        _reserve_cost(cost_path, reservation_id, estimate, pending_ids)
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
                    "requested_model": model_id,
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
                "requested_model": model_id,
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
        _reconcile_cost(cost_path, reservation_id, estimate if invalid_ids else actual)
        return tuple(Evidence(
            SCHEMA_VERSION, dossiers[company_id].evidence[0].url,
            canonical_json({
                "artifact": str((output_dir / f"{company_id}.json").relative_to(run_root)),
                "company_id": company_id,
            }), dossiers[company_id].evidence[0].retrieved_at.isoformat(),
        ) for company_id in ids)

    def refresh_outputs() -> None:
        for company_id in ids:
            path = output_dir / f"{company_id}.json"
            artifact = _read_json(path)
            if artifact is None or artifact.get("invalid_output") is True:
                continue
            model_output = artifact.get("model_output", artifact["output"])
            if spec.postprocess is None:
                artifact["output"] = model_output
                artifact.pop("model_output", None)
                artifact.pop("postprocess", None)
            else:
                grounded, report = spec.postprocess(model_output, dossiers[company_id])
                artifact.update({
                    "model_output": model_output, "output": grounded,
                    "postprocess": report,
                })
            _atomic_json(path, artifact)

    def evaluator(_envelope):
        cases, mean, failures = _score_attempt(spec, output_dir, ids, records, dossiers)
        _atomic_json(score_path, {
            "candidate_id": candidate.candidate_id,
            "cases": [{
                "company_id": item.company_id,
                "component_scores": {key: str(value) for key, value in item.components.items()},
                "hard_failures": list(item.hard_failures), "score": str(item.score),
            } for item in cases],
            "evaluation_fingerprint": evaluation_fingerprint,
            "hard_failures": list(failures), "mean_score": str(mean), "split": split,
        })
        return EvaluationResult(SCHEMA_VERSION, not failures, float(mean),
                                "scored" if not failures else "hard_failure")

    artifacts = {
        company_id: _read_json(output_dir / f"{company_id}.json")
        for company_id in ids
    }
    complete_outputs = all(
        artifact is not None and (
            artifact.get("invalid_output") is True or "output" in artifact
        )
        for artifact in artifacts.values()
    )
    existing_reservations = _reservation_ids(cost_path, attempt_id)
    if complete_outputs:
        if not existing_reservations:
            raise ValueError(f"completed outputs lack a cost reservation: {attempt_id}")
        state = _read_json(cost_path)
        reservations = state["reservations"]
        completed = [
            key for key in existing_reservations
            if reservations[key].get("status") == "completed"
        ]
        if not completed:
            reservation_id = existing_reservations[-1]
            reservation_company_ids = tuple(
                reservations[reservation_id].get("company_ids", ids)
            )
            invalid = any(
                artifacts[company_id].get("invalid_output") is True
                for company_id in reservation_company_ids
            )
            actual = (
                Decimal(reservations[reservation_id]["estimate_usd"])
                if invalid
                else sum(
                    (
                        Decimal(artifacts[company_id]["model_cost_usd"])
                        for company_id in reservation_company_ids
                    ),
                    Decimal("0"),
                )
            )
            _reconcile_cost(cost_path, reservation_id, actual)
        stored_score = _read_json(score_path)
        if (
            stored_score is None
            or stored_score.get("evaluation_fingerprint") != evaluation_fingerprint
        ):
            refresh_outputs()
            evaluator(None)
        return _read_json(score_path)

    pending_ids = tuple(
        company_id for company_id, artifact in artifacts.items()
        if artifact is None or (
            artifact.get("invalid_output") is not True and "output" not in artifact
        )
    )
    if not allow_provider_execution:
        missing = ", ".join(
            str((output_dir / f"{company_id}.json").relative_to(run_root))
            for company_id in pending_ids
        )
        raise ValueError(
            f"evaluation-only resume is missing stored {split} artifacts for candidate "
            f"{candidate.candidate_id}: {missing}; run the {split} for that candidate "
            "deliberately"
        )
    requests = tuple(requests_by_company[company_id] for company_id in pending_ids)
    estimate = Decimal(model_client.estimate(requests, ExecutionTrack.SYNCHRONOUS))
    reservation_id = _next_reservation_id(cost_path, attempt_id)

    zero = BudgetCharge()
    RoleRunners(
        inventor, checker("in_bounds"), checker("novelty"), executor, evaluator,
        {Role.INVENTOR: zero, Role.IN_BOUNDS_CHECKER: zero, Role.NOVELTY_CHECKER: zero,
         Role.EXECUTOR: BudgetCharge(calls=1, llm_calls=len(requests), cost=float(estimate)),
         Role.EVALUATOR: zero},
    )
    RunRequest(
        SCHEMA_VERSION, reservation_id, f"{spec.enrichment_id} {split} prompt attempt",
        ("cached_dossier_evidence_only", "zero_source_purchases", "sync_only"), {},
        BudgetLimits(max_calls=1, max_llm_calls=len(requests), max_cost=float(CAP_USD)),
        .90,
        execution_inputs={"company_ids": pending_ids, "dataset_hash": dataset_hash,
                          "model_id": model_id, "prompt_hash": candidate.prompt_hash,
                          "split": split},
        rubric=spec.rubric,
    )
    executor(None)
    evaluator(None)
    return _read_json(score_path)


def _started(run_root: Path) -> bool:
    return any((run_root / name).exists() for name in _STARTED_MARKERS)


def _lineage_inputs(
    spec: SignalSpec, repo_root: Path, public: SignalDataset,
    dossiers: Mapping[str, CompanyDossier], candidates: Sequence[PromptCandidate],
    model_id: str, *, model_client: ModelClient | None = None,
    requests: Mapping[str, Mapping[str, ExperimentInput]] | None = None,
) -> dict[str, Any]:
    def digest(path: Path) -> str:
        return sha256(path.read_bytes()).hexdigest()

    inputs = {
        "all_ids": list(public.all_ids), "dataset_hash": public.dataset_hash,
        "candidate_prompt_hashes": {
            candidate.candidate_id: candidate.prompt_hash for candidate in candidates
        },
        "development_ids": list(public.development_ids),
        "dossier_hashes": {
            company_id: sha256(
                canonical_json(_primitive(dossiers[company_id])).encode("utf-8")
            ).hexdigest()
            for company_id in public.all_ids
        },
        "enrichment_id": spec.enrichment_id,
        "evaluation_fingerprint": _evaluation_fingerprint(spec),
        "holdout_ids": list(public.holdout_ids), "model": model_id,
        "source_purchases": 0,
        "signal_hashes": {company_id: digest(
            repo_root / spec.benchmark_dir / f"{company_id}.yaml",
        ) for company_id in public.all_ids},
    }
    if model_client is None and requests is None:
        return inputs
    fingerprint_request = getattr(model_client, "request_fingerprint", None)
    if not callable(fingerprint_request) or requests is None:
        raise ValueError("model client cannot deterministically fingerprint requests")
    provider_requests = []
    for candidate in candidates:
        candidate_requests = []
        for company_id in public.all_ids:
            fingerprint = fingerprint_request(requests[candidate.candidate_id][company_id])
            if not isinstance(fingerprint, str) or not re.fullmatch(
                r"[0-9a-f]{64}", fingerprint
            ):
                raise ValueError("model client returned an invalid request fingerprint")
            candidate_requests.append({
                "company_id": company_id,
                "fingerprint": fingerprint,
            })
        provider_requests.append({
            "candidate_id": candidate.candidate_id,
            "requests": candidate_requests,
        })
    inputs["provider_requests"] = provider_requests
    inputs["lineage_fingerprint"] = sha256(canonical_json({
        "dataset_hash": public.dataset_hash,
        "development_ids": list(public.development_ids),
        "holdout_ids": list(public.holdout_ids),
        "provider_requests": provider_requests,
    }).encode("utf-8")).hexdigest()
    return inputs


def _provider_requests(
    spec: SignalSpec, candidates: Sequence[PromptCandidate],
    dossiers: Mapping[str, CompanyDossier], company_ids: Sequence[str], model_id: str,
) -> dict[str, dict[str, ExperimentInput]]:
    return {
        candidate.candidate_id: {
            company_id: ExperimentInput(
                spec.enrichment_id, company_id, model_id, dossiers[company_id],
                candidate.candidate_id, candidate.text,
                spec.output_contract(dossiers[company_id]),
            )
            for company_id in company_ids
        }
        for candidate in candidates
    }


def _write_inputs(run_root: Path, inputs: Mapping[str, Any]) -> None:
    _atomic_json(run_root / "inputs.json", inputs)


def run_loop(
    spec: SignalSpec, *, repo_root: Path, run_root: Path, model_client: ModelClient,
    resume: bool, model_id: str = MODEL_ID,
) -> dict[str, Any]:
    started = _started(run_root)
    if started and not resume:
        raise ValueError("lineage already exists; pass --resume")
    prior_inputs = _read_json(run_root / "inputs.json") if started else None
    if started:
        prior_model = prior_inputs.get("model") if prior_inputs else None
        if prior_model != model_id:
            raise ValueError(
                f"resume model {model_id!r} does not match lineage model {prior_model!r}"
            )
    run_root.mkdir(parents=True, exist_ok=True)
    dossiers = load_signal_dossiers(spec, repo_root)
    public = spec.load_ground_truth(repo_root, dossiers)
    if not isinstance(public, SignalDataset):
        raise ValueError("load_ground_truth must return a public SignalDataset")
    candidates = prompt_candidates(spec, repo_root)
    requests = _provider_requests(
        spec, candidates, dossiers, public.all_ids, model_id,
    )
    inputs = _lineage_inputs(
        spec, repo_root, public, dossiers, candidates, model_id,
        model_client=model_client, requests=requests,
    )
    if started and (
        prior_inputs is None
        or prior_inputs.get("lineage_fingerprint") != inputs["lineage_fingerprint"]
    ):
        raise ValueError("resume inputs do not match the existing lineage")
    evaluation_only_resume = bool(
        started
        and prior_inputs is not None
        and prior_inputs.get("evaluation_fingerprint") != inputs["evaluation_fingerprint"]
    )
    if not evaluation_only_resume:
        _write_inputs(run_root, inputs)
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
            model_client=model_client, model_id=model_id,
            requests_by_company=requests[candidate.candidate_id],
            evaluation_fingerprint=inputs["evaluation_fingerprint"],
            allow_provider_execution=not evaluation_only_resume,
        )
        if score is None:
            break
        dev_scores.append((Decimal(score["mean_score"]), -len(score["hard_failures"]),
                           -index, candidate))
    if len(dev_scores) != len(candidates):
        gate = {"action": "halt_for_review", "approval": False,
                "human_review_eligible": False, "reason_code": "budget_exhausted",
                "threshold": str(THRESHOLD)}
        result = {"enrichment_id": spec.enrichment_id, "gate": gate, "model": model_id,
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
        model_id=model_id, requests_by_company=requests[winner.candidate_id],
        evaluation_fingerprint=inputs["evaluation_fingerprint"],
        allow_provider_execution=not evaluation_only_resume,
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
        "enrichment_id": spec.enrichment_id, "gate": gate, "model": model_id,
        "source_purchases": 0, "total_cost_usd": str(_total_cost(run_root / "cost.json")),
        "winner": winner_value,
    }
    _atomic_json(run_root / "gate.json", gate)
    _atomic_json(run_root / "result.json", result)
    _write_inputs(run_root, inputs)
    return result


def _dry_plan(
    spec: SignalSpec, lineage: str, repo_root: Path, model_id: str = MODEL_ID,
) -> dict[str, Any]:
    return {
        "all_ids": list(ALL_IDS), "approval": False, "cached_only": True,
        "candidate_prompt_hashes": [
            item.prompt_hash for item in prompt_candidates(spec, repo_root)
        ],
        "cost_cap_usd": str(CAP_USD), "development_ids": list(DEVELOPMENT_IDS),
        "enrichment_id": spec.enrichment_id, "holdout_ids": list(HOLDOUT_IDS),
        "lineage": lineage, "model": model_id, "source_purchases": 0,
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
        target = repo_root / spec.benchmark_dir / f"{company_id}.yaml"
        if target.exists() and not overwrite:
            skipped.append(company_id)
            continue
        if dry_run:
            written.append(company_id)
            continue
        dossier = spec.collect(CollectRequest(
            company_id, bases[company_id], repo_root, benchmark_dir=spec.benchmark_dir,
        ))
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
        "targets": [str(repo_root / spec.benchmark_dir / f"{company_id}.yaml")
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
    parser.add_argument("--benchmark-dir", default=None,
                        help="repo-relative dossier directory that replaces the spec's "
                             "benchmark dir (collect writes there; evaluate reads dossiers "
                             "from there while ground truth stays in the sealed location)")
    parser.add_argument("--search-provider", default=None,
                        help=f"comma-separated search provider order for --collect; sets "
                             f"{SEARCH_PROVIDER_ENV} for this process")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--allow-paid", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--model", choices=tuple(MODEL_PRICES), default=MODEL_ID)
    args = parser.parse_args(argv)
    root = Path(repo_root or Path(__file__).resolve().parents[2])
    if args.search_provider:
        os.environ[SEARCH_PROVIDER_ENV] = args.search_provider
    if args.prompt or args.candidate or args.benchmark_dir:
        overrides: dict[str, Any] = {}
        if args.prompt:
            overrides.update(
                prompt_path=Path(args.prompt), candidate_id=Path(args.prompt).stem,
                resolve_prompt_layout=False,
            )
        if args.candidate:
            overrides["candidate_paths"] = (
                *spec.candidate_paths, *(Path(item) for item in args.candidate),
            )
        if args.benchmark_dir:
            overrides["benchmark_dir"] = Path(args.benchmark_dir)
            # Variant corpora are collected by another provider, so ground-truth
            # Evidence IDs (content-addressed to the sealed collection) cannot
            # appear in them; the citation component is excluded cross-collector.
            overrides["load_ground_truth"] = partial(
                spec.load_ground_truth, check_citations=False,
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
        candidates = prompt_candidates(spec, root)
        inputs = _lineage_inputs(spec, root, public, dossiers, candidates, args.model)
        _write_inputs(run_root, inputs)
        print(canonical_json(_dry_plan(spec, args.lineage, root, args.model)))
        return 0
    if not args.allow_paid:
        print(canonical_json({"approval": False, "error": "--allow-paid is required"}))
        return 2
    client = model_client_factory(artifact_root=run_root)
    try:
        result = run_loop(spec, repo_root=root, run_root=run_root,
                          model_client=client, resume=args.resume, model_id=args.model)
    except (BudgetExceeded, ValueError) as error:
        print(canonical_json({"approval": False, "error": type(error).__name__,
                              "message": str(error)}))
        return 2
    print(canonical_json(result))
    return 0 if result["gate"]["human_review_eligible"] else 2
