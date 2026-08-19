"""Deep, deterministic orchestration behind the two command-line adapters."""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Callable, Any, Mapping

from .artifacts import ArtifactHaltForReview, ArtifactStore
from .budgets import BudgetExceeded, BudgetLedger, BudgetUsage
from .contracts import (
    BudgetCharge, CompletedRoleRecord, Role, RoleEnvelope, RunRequest, RunSummary,
    SCHEMA_VERSION, SchemaError,
)
from .gate import GateAction, GateInput, decide_gate

RoleRunner = Callable[[RoleEnvelope], Any]


@dataclass(frozen=True, slots=True)
class RoleRunners:
    inventor: RoleRunner
    in_bounds_checker: RoleRunner
    novelty_checker: RoleRunner
    executor: RoleRunner
    evaluator: RoleRunner
    charges: Mapping[Role, BudgetCharge]

    def __post_init__(self) -> None:
        if any(not callable(getattr(self, role.value)) for role in Role):
            raise SchemaError("role runners must be callable")
        if not isinstance(self.charges, Mapping) or set(self.charges) != set(Role):
            raise SchemaError("charges must declare every role exactly once")
        if not all(isinstance(charge, BudgetCharge) for charge in self.charges.values()):
            raise SchemaError("charges must contain BudgetCharge values")
        object.__setattr__(self, "charges", MappingProxyType(dict(self.charges)))

    def for_role(self, role: Role) -> RoleRunner:
        return getattr(self, role.value)


class AutoresearchOrchestrator:
    """Own sequencing, isolation, persistence, resume, budgets, and gate selection."""

    def __init__(self, store: ArtifactStore, roles: RoleRunners):
        if not isinstance(store, ArtifactStore) or not isinstance(roles, RoleRunners):
            raise SchemaError("orchestrator requires ArtifactStore and RoleRunners")
        self._store, self._roles = store, roles

    def run(self, request: RunRequest) -> RunSummary:
        if not isinstance(request, RunRequest):
            raise SchemaError("request must be RunRequest")
        if self._store.run_exists():
            persisted = self._store.load_and_validate()
            if persisted != request.to_canonical_dict():
                raise ArtifactHaltForReview("run_request_mismatch")
        else:
            self._store.create_run(request)

        projection = self._store.load_state()
        if projection.last_event is not None and projection.last_event.event == "run_completed":
            return self._store.load_summary()
        ledger = BudgetLedger(request.budget_limits)
        self._replay_usage(ledger, projection.budget_usage)
        retry_count = projection.retry_count
        cursor = self._store.resume_cursor()
        cycle = (projection.last_event.cycle + 1
                 if projection.last_event is not None
                 and projection.last_event.event == "gate_decided"
                 and projection.last_event.action == GateAction.RETRY.value
                 else cursor.cycle)

        while True:
            values: dict[Role, Any] = {}
            prior_decisions = tuple(row.reason_code for row in self._store.load_prior_gate_decisions(cycle))
            prior_fingerprints = tuple(
                experiment.key for _, experiment in self._store.load_prior_inventor_results(cycle)
            )
            for role in Role:
                key = self._key(request, cycle, role)
                if key in cursor.completed_idempotency_keys:
                    values[role] = self._store.load_role_result(cycle, role)
                    continue
                if role is Role.NOVELTY_CHECKER and not values[Role.IN_BOUNDS_CHECKER].accepted:
                    outcome = self._retry_or_finish(request, cycle, ledger, retry_count)
                    if isinstance(outcome, RunSummary):
                        return outcome
                    cycle, retry_count, cursor = outcome
                    break
                if role is Role.EXECUTOR and not (
                    values[Role.IN_BOUNDS_CHECKER].accepted and values[Role.NOVELTY_CHECKER].accepted
                ):
                    outcome = self._retry_or_finish(request, cycle, ledger, retry_count)
                    if isinstance(outcome, RunSummary):
                        return outcome
                    cycle, retry_count, cursor = outcome
                    break
                try:
                    charge = self._roles.charges[role]
                    usage = ledger.reserve(
                        calls=charge.calls, queries=charge.queries, scrapes=charge.scrapes,
                        llm_calls=charge.llm_calls,
                        cost=charge.cost, stages=charge.stages,
                    )
                except BudgetExceeded:
                    return self._finish(request, cycle, ledger.usage, retry_count,
                                        GateInput(budget_exhausted=True))
                envelope = self._envelope(
                    role, request, values, ledger, prior_decisions, prior_fingerprints
                )
                self._store.reserve_role_attempt(cycle, role, envelope, key, usage, retry_count)
                try:
                    result = self._roles.for_role(role)(envelope)
                    CompletedRoleRecord.create(envelope, result)
                except SchemaError as error:
                    self._store.append_state_event(
                        cycle, role.value, "role_failed", "invalid_role_result", usage, retry_count,
                        invocation_id=envelope.invocation_id, idempotency_key=key,
                    )
                    raise ArtifactHaltForReview("invalid_role_result") from error
                except Exception:
                    self._store.append_state_event(
                        cycle, role.value, "role_failed", "runner_error", usage, retry_count,
                        invocation_id=envelope.invocation_id, idempotency_key=key,
                    )
                    outcome = self._retry_or_finish(request, cycle, ledger, retry_count)
                    if isinstance(outcome, RunSummary):
                        return outcome
                    cycle, retry_count, cursor = outcome
                    break
                self._store.complete_role_attempt(cycle, role, envelope, result, key, usage, retry_count)
                values[role] = result
            else:
                evaluation = values[Role.EVALUATOR]
                baseline = max(request.baseline.values(), default=None)
                return self._finish(
                    request, cycle, ledger.usage, retry_count,
                    GateInput(checks_accepted=True, evaluation_passed=evaluation.passed,
                              candidate_score=evaluation.score, baseline_score=baseline,
                              rollback_available=baseline is not None),
                )

    def _retry_or_finish(self, request, cycle, ledger, retry_count):
        retries_remaining = request.budget_limits.max_retries - retry_count
        if retries_remaining > 0:
            try:
                usage = ledger.reserve(retries=1)
            except BudgetExceeded:
                return self._finish(request, cycle, ledger.usage, retry_count,
                                    GateInput(budget_exhausted=True))
            retry_count += 1
        else:
            usage = ledger.usage
        decision = self._record_gate(
            cycle, usage, retry_count,
            GateInput(retryable_failure=True, retries_remaining=retries_remaining,
                      rollback_available=bool(request.baseline)),
        )
        if decision.action is not GateAction.RETRY:
            return self._persist_summary(request, cycle, usage, retry_count, decision)
        return cycle + 1, retry_count, self._store.resume_cursor()

    def _finish(self, request, cycle, usage, retry_count, gate_input):
        decision = self._record_gate(cycle, usage, retry_count, gate_input)
        return self._persist_summary(request, cycle, usage, retry_count, decision)

    def _record_gate(self, cycle, usage, retry_count, gate_input):
        decision = decide_gate(gate_input)
        self._store.append_state_event(cycle, "gate", "gate_decided", decision.reason_code,
                                       usage, retry_count, action=decision.action.value)
        return decision

    def _persist_summary(self, request, cycle, usage, retry_count, decision):
        summary = RunSummary(SCHEMA_VERSION, request.run_id, decision.action.value,
                             decision.reason_code, cycle + 1)
        self._store.append_state_event(cycle, "run", "run_completed", decision.reason_code,
                                       usage, retry_count, action=decision.action.value)
        self._store.write_summary(summary)
        return summary

    @staticmethod
    def _replay_usage(ledger: BudgetLedger, usage: BudgetUsage) -> None:
        ledger.reserve(calls=usage.calls, queries=usage.queries, scrapes=usage.scrapes,
                       llm_calls=usage.llm_calls, retries=usage.retries,
                       cost=usage.cost, stages=usage.stages)

    @staticmethod
    def _key(request: RunRequest, cycle: int, role: Role) -> str:
        return f"{request.run_id}:{cycle}:{role.value}"

    @staticmethod
    def _envelope(role, request, values, ledger, prior_decisions, prior_fingerprints):
        remaining = {
            name.removeprefix("max_"): getattr(request.budget_limits, name)
            - getattr(ledger.usage, name.removeprefix("max_"))
            for name in ("max_calls", "max_queries", "max_scrapes", "max_llm_calls",
                         "max_retries", "max_cost", "max_stages")
        }
        experiment = values.get(Role.INVENTOR)
        payloads = {
            Role.INVENTOR: {"run_brief": request.brief, "baseline": request.baseline,
                            "prior_decisions": prior_decisions, "budget_remaining": remaining},
            Role.IN_BOUNDS_CHECKER: {"constraints": request.constraints, "experiment": experiment},
            Role.NOVELTY_CHECKER: {"experiment": experiment, "prior_fingerprints": prior_fingerprints},
            Role.EXECUTOR: {"experiment": experiment,
                            "execution_inputs": request.execution_inputs},
            Role.EVALUATOR: {"rubric": request.rubric, "experiment": experiment,
                             "evidence": values.get(Role.EXECUTOR, ())},
        }
        return RoleEnvelope.create(role, payloads[role])
