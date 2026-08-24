"""Thin, cached-only ICP/persona prompt loop over the sealed SaaS benchmark."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

import yaml

from scripts.company_enrichment.benchmark import ExecutionTrack
from scripts.company_enrichment.contracts import (
    CompanyDossier, EvidenceRef, FieldAssertion, Visibility, canonical_json,
)
from scripts.company_enrichment.experiment_runner import ExperimentInput, ModelClient
from scripts.company_enrichment.icp_persona_contracts import (
    IcpPersonaOutput, parse_icp_output, render_segment,
)
from scripts.company_enrichment.icp_persona_ground_truth import (
    IcpGroundTruthRecord, issue_evaluator_dataset_capability, load_icp_dataset,
    load_icp_dataset_for_evaluator,
)
from scripts.company_enrichment.openai_model_client import build_openai_model_client
from scripts.research_orchestration.artifacts import ArtifactStore
from scripts.research_orchestration.budgets import BudgetExceeded, BudgetLimits
from scripts.research_orchestration.contracts import (
    BudgetCharge, CheckerResult, EvaluationResult, Evidence, Experiment,
    Role, RunRequest, SCHEMA_VERSION,
)
from scripts.research_orchestration.gate import GateInput, decide_gate
from scripts.research_orchestration.orchestrator import (
    AutoresearchOrchestrator, RoleRunners,
)


MODEL_ID = os.environ.get("ICP_LOOP_MODEL", "gpt-4.1-mini")
CAP_USD = Decimal("1.00")
DEV_IDS = ("saas-01", "saas-02", "saas-04", "saas-05", "saas-07", "saas-09")
HOLDOUT_IDS = ("saas-03", "saas-06", "saas-08", "saas-10")
ALL_IDS = tuple(f"saas-{index:02d}" for index in range(1, 11))
_LINEAGE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,79}$")
_WEIGHTS = {
    "one_focus": Decimal(".25"), "word_limit": Decimal(".20"),
    "grammar": Decimal(".20"), "citation": Decimal(".20"),
    "buyer": Decimal(".10"), "plain_language": Decimal(".05"),
}


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


def prompt_candidates(repo_root: Path) -> tuple[PromptCandidate, ...]:
    baseline = (repo_root / "prompts/company-enrichment/icp-persona-analysis.md").read_text(
        encoding="utf-8",
    ).strip()
    return (
        PromptCandidate(
            "conversational-nonredundant",
            baseline,
            sha256(baseline.encode("utf-8")).hexdigest(),
        ),
    )


def load_cached_dossiers(repo_root: Path) -> dict[str, CompanyDossier]:
    dossiers: dict[str, CompanyDossier] = {}
    for company_id in ALL_IDS:
        path = repo_root / "benchmarks/dossiers" / f"{company_id}.yaml"
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
        evidence = tuple(EvidenceRef(
            item["evidence_id"], item["url"], datetime.fromisoformat(item["retrieved_at"]),
            item["content_hash"], item["excerpt"],
        ) for item in value["evidence"])
        assertions = tuple(FieldAssertion(
            item["field"], item["value"], tuple(item["evidence_ids"]),
            float(item["confidence"]), Visibility(item["visibility"]),
        ) for item in value["assertions"])
        dossiers[company_id] = CompanyDossier(
            company_id, value["schema_version"], assertions, evidence,
            tuple(value.get("unknowns", ())),
        )
    return dossiers


def _output_contract(dossier: CompanyDossier) -> dict[str, Any]:
    ids = [item.evidence_id for item in dossier.evidence]
    evidence_ids = {
        "type": "array", "items": {"type": "string", "enum": ids},
        "minItems": 1,
    }
    def obj(properties, required):
        return {"type": "object", "properties": properties, "required": required,
                "additionalProperties": False}
    segment = obj({
        "buyer": {"type": "string"},
        "relationship": {
            "type": "string",
            "enum": ["looking for", "who need", "in the market for"],
        },
        "outcome": {"type": "string"},
        "evidence_ids": evidence_ids,
    }, ["buyer", "relationship", "outcome", "evidence_ids"])
    return obj({
        "primary_icp": segment,
        "secondary_icps": {"type": "array", "items": segment, "maxItems": 2},
        "outcomes": {"type": "array", "items": obj({
            "text": {"type": "string"}, "evidence_ids": evidence_ids,
        }, ["text", "evidence_ids"])},
        "observed_personas": {"type": "array", "items": obj({
            "role": {"type": "string"}, "evidence_ids": evidence_ids,
        }, ["role", "evidence_ids"])},
        "inferred_personas": {"type": "array", "items": obj({
            "role": {"type": "string"}, "based_on_evidence_ids": evidence_ids,
        }, ["role", "based_on_evidence_ids"])},
    }, ["primary_icp", "secondary_icps", "outcomes", "observed_personas",
        "inferred_personas"])


def _tokens(value: str) -> tuple[str, ...]:
    return tuple(re.findall(r"[a-z0-9]+", value.casefold()))


def _matches(value: str, accepted: Sequence[str]) -> bool:
    candidate = _tokens(value)
    return any(candidate == _tokens(item) for item in accepted)


def _role_score(actual: Sequence[str], expected: Sequence[str]) -> Decimal:
    wanted = {_tokens(item) for item in expected}
    got = {_tokens(item) for item in actual}
    if not wanted:
        return Decimal("1") if not got else Decimal("0")
    return Decimal(len(wanted & got)) / Decimal(len(wanted))


def _supported_claim(text: str, evidence_ids: Sequence[str], dossier: CompanyDossier) -> bool:
    excerpts = " ".join(
        item.excerpt for item in dossier.evidence if item.evidence_id in set(evidence_ids)
    )
    claim = {token for token in _tokens(text) if len(token) > 2}
    observed = set(_tokens(excerpts))
    return not claim or bool(claim & observed)


def score_payload(
    payload: Mapping[str, Any], record: IcpGroundTruthRecord, dossier: CompanyDossier,
) -> CaseScore:
    primary_value = payload.get("primary_icp")
    if isinstance(primary_value, Mapping) and "outcome" in primary_value:
        buyer = str(primary_value.get("buyer", "")).strip()
        relationship = str(primary_value.get("relationship", "")).strip()
        outcome = str(primary_value.get("outcome", "")).strip()
        evidence_ids = primary_value.get("evidence_ids", ())
        retained_ids = {item.evidence_id for item in dossier.evidence}
        sentence = " ".join((buyer, relationship, outcome)).strip()
        words = _tokens(sentence)
        outcome_words = _tokens(outcome)
        forbidden = bool(re.search(r"(?:,|\band\b)", sentence, re.IGNORECASE))
        bare_verbs = {
            "save", "know", "reduce", "improve", "make", "get", "prove",
            "prevent", "manage", "track", "run", "govern",
        }
        supported = (
            isinstance(evidence_ids, list)
            and bool(evidence_ids)
            and set(evidence_ids) <= retained_ids
        )
        secondaries = payload.get("secondary_icps", ())
        secondary_buyers = [
            _tokens(str(item.get("buyer", "")))
            for item in secondaries if isinstance(item, Mapping)
        ] if isinstance(secondaries, list) else []
        secondary_valid = (
            isinstance(secondaries, list)
            and len(secondary_buyers) == len(secondaries)
            and all(item != _tokens(buyer) for item in secondary_buyers)
            and len(set(secondary_buyers)) == len(secondary_buyers)
        )
        one_focus = (
            bool(outcome_words)
            and len(outcome_words) <= 5
            and not forbidden
            and len(secondaries) <= 2
            and secondary_valid
        )
        components = {
            "one_focus": Decimal(one_focus),
            "word_limit": Decimal(bool(words) and len(words) <= 10),
            "grammar": Decimal(
                relationship in {"looking for", "who need", "in the market for"}
                and bool(outcome_words)
                and not outcome_words[0].endswith("ing")
                and outcome_words[0] not in bare_verbs
                and not forbidden
            ),
            "citation": Decimal(supported),
            "buyer": Decimal(bool(_tokens(buyer))),
            "plain_language": Decimal(
                not any(token in {
                    "orchestration", "transformation", "enablement", "platform",
                    "workspace", "workflow", "solution", "management",
                } for token in outcome_words)
            ),
        }
        failures = () if all(components.values()) else tuple(
            name for name, value in components.items() if not value
        )
        score = sum((_WEIGHTS[key] * components[key] for key in _WEIGHTS), Decimal("0"))
        return CaseScore(record.company_id, components, score, failures)
    try:
        output = parse_icp_output(dict(payload), {item.evidence_id for item in dossier.evidence})
    except ValueError as error:
        reason = (
            "evidence_outside_retained_dossier"
            if "retained Evidence" in str(error) else "contract_invalid"
        )
        return CaseScore(record.company_id, {key: Decimal("0") for key in _WEIGHTS},
                         Decimal("0"), (reason,))

    primary = output.primary_icp
    expected = record.primary_icp
    aliases = record.acceptable_aliases
    components = {
        "buyer": Decimal(_matches(primary.buyer, aliases["primary.buyer"])),
        "need": Decimal(_matches(primary.need, aliases["primary.need"])),
        "object": Decimal(_matches(primary.object, aliases["primary.object"])),
    }
    citations = []
    for name in ("buyer", "need", "object"):
        required = set(record.evidence_by_component[f"primary.{name}"])
        citations.append(required <= set(primary.evidence_ids))
    components["citation"] = Decimal(sum(citations)) / Decimal("3")

    actual_roles = [item.role for item in output.observed_personas]
    actual_roles += [item.role for item in output.inferred_personas]
    expected_roles = [item.role for item in record.observed_personas]
    expected_roles += [item.role for item in record.inferred_personas]
    components["persona"] = _role_score(actual_roles, expected_roles)
    rendered = render_segment(primary)
    readable = len(rendered) <= 300 and all(1 <= len(_tokens(getattr(primary, name))) <= 20
                                             for name in ("buyer", "need", "object"))
    components["readability"] = Decimal(readable)

    failures: list[str] = []
    for name in ("buyer", "need", "object"):
        if not components[name] and not _supported_claim(
            getattr(primary, name), primary.evidence_ids, dossier,
        ):
            failures.append(f"unsupported_primary_{name}")
    expected_secondary = {
        (_tokens(item.buyer), _tokens(item.need), _tokens(item.object))
        for item in record.secondary_icps
    }
    for item in output.secondary_icps:
        if (_tokens(item.buyer), _tokens(item.need), _tokens(item.object)) not in expected_secondary:
            failures.append("unsupported_secondary")
    expected_outcomes = {_tokens(item.text) for item in record.outcomes}
    if any(_tokens(item.text) not in expected_outcomes for item in output.outcomes):
        failures.append("unsupported_outcome")
    expected_observed = {_tokens(item.role) for item in record.observed_personas}
    expected_inferred = {_tokens(item.role) for item in record.inferred_personas}
    if any(_tokens(item.role) not in expected_observed for item in output.observed_personas):
        failures.append("unsupported_observed_persona")
    if any(_tokens(item.role) not in expected_inferred for item in output.inferred_personas):
        failures.append("unsupported_inferred_persona")
    score = sum((_WEIGHTS[key] * components[key] for key in _WEIGHTS), Decimal("0"))
    if failures:
        score = Decimal("0")
    return CaseScore(record.company_id, components, score, tuple(dict.fromkeys(failures)))


def _payload_from_execution(execution) -> dict[str, Any]:
    values = {item.field: _primitive(item.value) for item in execution.assertions}
    return {
        **values["icp"],
        **values["personas"],
    }


def _reserve_cost(path: Path, attempt_id: str, estimate: Decimal) -> None:
    state = _read_json(path) or {"cap_usd": str(CAP_USD), "reservations": {}}
    reservations = state["reservations"]
    if attempt_id in reservations:
        return
    committed = sum((Decimal(item.get("actual_usd", item["estimate_usd"]))
                     for item in reservations.values()), Decimal("0"))
    if committed + estimate > CAP_USD:
        _atomic_json(path.parent / "cap-blocked.json", {
            "attempt_id": attempt_id, "committed_usd": str(committed),
            "estimate_usd": str(estimate), "cap_usd": str(CAP_USD),
        })
        raise BudgetExceeded("aggregate ICP/persona cap exhausted")
    reservations[attempt_id] = {"estimate_usd": str(estimate), "status": "reserved"}
    _atomic_json(path, state)


def _reconcile_cost(path: Path, attempt_id: str, actual: Decimal) -> None:
    state = _read_json(path)
    if state is None or attempt_id not in state["reservations"]:
        raise ValueError("missing cost reservation")
    reservations = state["reservations"]
    other = sum((Decimal(item.get("actual_usd", item["estimate_usd"]))
                 for key, item in reservations.items() if key != attempt_id), Decimal("0"))
    if other + actual > CAP_USD:
        raise BudgetExceeded("actual aggregate ICP/persona cost exceeded cap")
    reservations[attempt_id] = {
        **reservations[attempt_id], "actual_usd": str(actual), "status": "completed",
    }
    _atomic_json(path, state)


def _total_cost(path: Path) -> Decimal:
    state = _read_json(path) or {"reservations": {}}
    return sum((Decimal(item.get("actual_usd", "0"))
                for item in state["reservations"].values()), Decimal("0"))


def _score_attempt(
    output_dir: Path, ids: Sequence[str], records: Mapping[str, IcpGroundTruthRecord],
    dossiers: Mapping[str, CompanyDossier],
) -> tuple[tuple[CaseScore, ...], Decimal, tuple[str, ...]]:
    cases_list: list[CaseScore] = []
    for company_id in ids:
        artifact = _read_json(output_dir / f"{company_id}.json") or {}
        if artifact.get("invalid_output"):
            cases_list.append(CaseScore(
                company_id,
                {key: Decimal("0") for key in _WEIGHTS},
                Decimal("0"),
                ("invalid_output",),
            ))
            continue
        cases_list.append(score_payload(
            artifact["output"], records[company_id], dossiers[company_id],
        ))
    cases = tuple(cases_list)
    mean = sum((item.score for item in cases), Decimal("0")) / Decimal(len(cases))
    failures = tuple(f"{item.company_id}:{reason}" for item in cases
                     for reason in item.hard_failures)
    return cases, mean, failures


def _run_attempt(
    *, run_root: Path, attempt_id: str, split: str, ids: Sequence[str],
    candidate: PromptCandidate, records: Mapping[str, IcpGroundTruthRecord],
    dossiers: Mapping[str, CompanyDossier], dataset_hash: str,
    model_client: ModelClient,
) -> dict[str, Any] | None:
    requests = tuple(ExperimentInput(
        "icp-persona-analysis", company_id, MODEL_ID, dossiers[company_id],
        candidate.candidate_id, candidate.text, _output_contract(dossiers[company_id]),
    ) for company_id in ids)
    estimate = Decimal(model_client.estimate(requests, ExecutionTrack.SYNCHRONOUS))
    output_dir = run_root / "outputs" / split / candidate.candidate_id
    score_path = run_root / "scores" / split / f"{candidate.candidate_id}.json"
    cost_path = run_root / "cost.json"

    def inventor(_envelope):
        return Experiment(SCHEMA_VERSION, "icp-persona-analysis",
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
                execution = model_client.execute(
                    (request,), ExecutionTrack.SYNCHRONOUS,
                )[0]
            except Exception as error:
                invalid_ids.append(request.company_id)
                _atomic_json(output_dir / f"{request.company_id}.json", {
                    "company_id": request.company_id,
                    "error": type(error).__name__,
                    "input_hash": sha256(canonical_json({
                        "dossier": _primitive(dossiers[request.company_id]),
                        "prompt_hash": candidate.prompt_hash,
                    }).encode("utf-8")).hexdigest(),
                    "invalid_output": True,
                    "requested_model": MODEL_ID,
                    "source_cache_reused": True,
                    "source_purchases": 0,
                })
                continue
            executions.append(execution)
            _atomic_json(output_dir / f"{execution.company_id}.json", {
                "company_id": execution.company_id,
                "input_hash": sha256(canonical_json({
                    "dossier": _primitive(dossiers[execution.company_id]),
                    "prompt_hash": candidate.prompt_hash,
                }).encode("utf-8")).hexdigest(),
                "latency_ms": execution.latency_ms,
                "model_cost_usd": execution.actual_cost_usd,
                "output": _payload_from_execution(execution),
                "requested_model": MODEL_ID,
                "resolved_model": execution.resolved_model_id,
                "source_cache_reused": True,
                "source_purchases": 0,
            })
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
        cases, mean, failures = _score_attempt(output_dir, ids, records, dossiers)
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
    roles = RoleRunners(
        inventor, checker("in_bounds"), checker("novelty"), executor, evaluator,
        {Role.INVENTOR: zero, Role.IN_BOUNDS_CHECKER: zero,
         Role.NOVELTY_CHECKER: zero,
         Role.EXECUTOR: BudgetCharge(calls=1, llm_calls=len(ids), cost=float(estimate)),
         Role.EVALUATOR: zero},
    )
    request = RunRequest(
        SCHEMA_VERSION, attempt_id, f"ICP/persona {split} prompt attempt",
        ("cached_dossier_evidence_only", "zero_source_purchases", "sync_only"), {},
        BudgetLimits(max_calls=1, max_llm_calls=len(ids), max_cost=float(CAP_USD)),
        .90,
        execution_inputs={"company_ids": tuple(ids), "dataset_hash": dataset_hash,
                          "prompt_hash": candidate.prompt_hash, "split": split},
        rubric="buyer:.25,need:.20,object:.20,citation:.20,persona:.10,readability:.05",
    )
    executor(None)
    evaluator(None)
    return _read_json(score_path)


def run_loop(
    *, repo_root: Path, run_root: Path, model_client: ModelClient, resume: bool,
) -> dict[str, Any]:
    if run_root.exists() and any(run_root.iterdir()) and not resume:
        raise ValueError("lineage already exists; pass --resume")
    run_root.mkdir(parents=True, exist_ok=True)
    dossiers = load_cached_dossiers(repo_root)
    public = load_icp_dataset(repo_root, dossiers)
    candidates = prompt_candidates(repo_root)
    _atomic_json(run_root / "inputs.json", {
        "all_ids": list(public.all_ids), "dataset_hash": public.dataset_hash,
        "development_ids": list(public.development_ids),
        "dossier_hashes": {company_id: sha256(
            (repo_root / "benchmarks/dossiers" / f"{company_id}.yaml").read_bytes()
        ).hexdigest() for company_id in ALL_IDS},
        "holdout_ids": list(public.holdout_ids), "source_purchases": 0,
    })
    _atomic_json(run_root / "candidates.json", {
        "candidates": [{"candidate_id": item.candidate_id, "prompt_hash": item.prompt_hash,
                        "prompt_text": item.text} for item in candidates],
        "max_candidates": len(candidates),
    })

    dev_scores = []
    for index, candidate in enumerate(candidates):
        score = _run_attempt(
            run_root=run_root, attempt_id=f"dev-{index}-{candidate.candidate_id}",
            split="dev", ids=DEV_IDS, candidate=candidate, records=public.records,
            dossiers=dossiers, dataset_hash=public.dataset_hash, model_client=model_client,
        )
        if score is None:
            break
        dev_scores.append((Decimal(score["mean_score"]), -len(score["hard_failures"]),
                           -index, candidate))
    if len(dev_scores) != len(candidates):
        gate = {"action": "halt_for_review", "approval": False,
                "human_review_eligible": False, "reason_code": "budget_exhausted",
                "threshold": "0.90"}
        result = {"gate": gate, "model": MODEL_ID,
                  "total_cost_usd": str(_total_cost(run_root / "cost.json"))}
        _atomic_json(run_root / "gate.json", gate)
        _atomic_json(run_root / "result.json", result)
        return result

    winner = max(dev_scores, key=lambda item: item[:3])[3]
    winner_value = {"candidate_id": winner.candidate_id, "prompt_hash": winner.prompt_hash}
    _atomic_json(run_root / "winner.json", winner_value)

    capability = issue_evaluator_dataset_capability()
    evaluator_dataset = load_icp_dataset_for_evaluator(
        repo_root, dossiers, capability=capability,
    )
    holdout = _run_attempt(
        run_root=run_root, attempt_id=f"holdout-{winner.candidate_id}",
        split="holdout", ids=HOLDOUT_IDS, candidate=winner,
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
    eligible = mean >= Decimal(".90") and not failures
    gate = {
        "action": decision.action.value, "approval": False,
        "human_review_eligible": eligible, "reason_code": decision.reason_code,
        "threshold": "0.90",
    }
    result = {
        "gate": gate, "model": MODEL_ID, "source_purchases": 0,
        "total_cost_usd": str(_total_cost(run_root / "cost.json")),
        "winner": winner_value,
    }
    _atomic_json(run_root / "gate.json", gate)
    _atomic_json(run_root / "result.json", result)
    return result


def _dry_plan(lineage: str, repo_root: Path) -> dict[str, Any]:
    return {
        "all_ids": list(ALL_IDS), "approval": False, "cached_only": True,
        "candidate_prompt_hashes": [item.prompt_hash for item in prompt_candidates(repo_root)],
        "cost_cap_usd": str(CAP_USD), "development_ids": list(DEV_IDS),
        "holdout_ids": list(HOLDOUT_IDS), "lineage": lineage, "model": MODEL_ID,
        "source_purchases": 0, "track": "synchronous",
    }


def main(
    argv: Sequence[str] | None = None, *, repo_root: Path | None = None,
    artifact_root: Path | None = None, model_client_factory=build_openai_model_client,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lineage", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--allow-paid", action="store_true")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args(argv)
    if not _LINEAGE.fullmatch(args.lineage):
        parser.error("--lineage must be a safe task-scoped name")
    root = Path(repo_root or Path(__file__).resolve().parents[1])
    base = Path(artifact_root or root / "runs/company-enrichment/icp-persona")
    if args.dry_run:
        print(canonical_json(_dry_plan(args.lineage, root)))
        return 0
    if not args.allow_paid:
        print(canonical_json({"approval": False, "error": "--allow-paid is required"}))
        return 2
    run_root = base / args.lineage
    client = model_client_factory(artifact_root=run_root)
    try:
        result = run_loop(repo_root=root, run_root=run_root,
                          model_client=client, resume=args.resume)
    except (BudgetExceeded, ValueError) as error:
        print(canonical_json({"approval": False, "error": type(error).__name__,
                              "message": str(error)}))
        return 2
    print(canonical_json(result))
    return 0 if result["gate"]["human_review_eligible"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
