from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
import json
import os
from pathlib import Path
import secrets
from types import MappingProxyType
from typing import Mapping, Protocol, Sequence

from ._locking import file_lock
from .benchmark import (
    BenchmarkCase,
    BenchmarkRunner,
    ExecutionTrack,
    ExperimentPlan,
)
from .budgets import BudgetLedger, ReservationSettled, ReservationState
from .contracts import (
    CompanyDossier,
    EnrichmentResult,
    FailureKind,
    FieldAssertion,
    ResultStatus,
    Visibility,
    canonical_json,
)
from .executors import P0_ENRICHMENTS


FIXED_SAAS_CORE = ("saas-01", "saas-04", "saas-07")
EXPERIMENT_ENRICHMENTS = (
    "company-description",
    "icp-persona-analysis",
    "growth-signals",
)
# Approved models only (Mitch, 2026-08-18): never the full gpt-4.1 tier or the
# gpt-4o family.
EXPERIMENT_MODELS = (
    "gpt-5-nano",
    "gpt-4.1-mini",
    "gpt-5.6-luna",
)
EXPERIMENT_TRACKS = (ExecutionTrack.SYNCHRONOUS, ExecutionTrack.BATCH)
EXPERIMENT_CAP_USD = Decimal("1.00")


@dataclass(frozen=True, slots=True)
class ExperimentInput:
    enrichment_id: str
    company_id: str
    requested_model_id: str
    dossier: CompanyDossier
    prompt_id: str = ""
    prompt_text: str = ""
    output_contract: Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.prompt_id, str):
            raise ValueError("prompt_id must be text")
        if not isinstance(self.prompt_text, str):
            raise ValueError("prompt_text must be text")
        if self.output_contract is not None:
            if not isinstance(self.output_contract, Mapping):
                raise ValueError("output_contract must be a mapping")
            canonical_json(self.output_contract)
            object.__setattr__(
                self, "output_contract", self._freeze(self.output_contract),
            )

    @classmethod
    def _freeze(cls, value):
        if isinstance(value, Mapping):
            return MappingProxyType({
                key: cls._freeze(item) for key, item in value.items()
            })
        if isinstance(value, (list, tuple)):
            return tuple(cls._freeze(item) for item in value)
        return value


@dataclass(frozen=True, slots=True)
class ModelExecution:
    company_id: str
    assertions: tuple[FieldAssertion, ...]
    unknowns: tuple[str, ...]
    resolved_model_id: str | None
    latency_ms: int
    actual_cost_usd: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "assertions", tuple(self.assertions))
        object.__setattr__(self, "unknowns", tuple(self.unknowns))
        if self.latency_ms < 0:
            raise ValueError("latency must be non-negative")
        cost = Decimal(self.actual_cost_usd)
        if not cost.is_finite() or cost < 0:
            raise ValueError("actual model cost must be finite and non-negative")


class ModelClient(Protocol):
    def estimate(
        self, requests: Sequence[ExperimentInput], track: ExecutionTrack,
    ) -> str: ...

    def execute(
        self, requests: Sequence[ExperimentInput], track: ExecutionTrack,
    ) -> tuple[ModelExecution, ...]: ...


class PendingModelClient(ModelClient, Protocol):
    """Optional capability for resuming a durably recorded provider job."""

    def has_pending(
        self, requests: Sequence[ExperimentInput], track: ExecutionTrack,
    ) -> bool: ...


@dataclass(frozen=True, slots=True)
class ExperimentSummary:
    enrichment_id: str
    status: str
    approved: bool
    completed_cases: int
    resumed_cases: int
    planned_cases: int
    source_cache_hits: int
    source_purchases: int
    model_cost_usd: Decimal
    cap_usd: Decimal
    authentication_gap: str | None = None
    programmed_gate_score: float | None = None
    gate_artifact_path: str | None = None
    gate_threshold: float = 0.90
    blind_outputs: tuple[Mapping[str, object], ...] = ()


class ExperimentRunner:
    def __init__(
        self,
        *,
        artifact_root: Path,
        dossiers: Mapping[str, CompanyDossier],
        model_client: ModelClient | None,
        benchmark_runner: BenchmarkRunner,
        as_of: datetime,
        fault_hook=None,
    ) -> None:
        if as_of.tzinfo is None:
            raise ValueError("as_of must include a timezone")
        self._root = Path(artifact_root)
        self._dossiers = dict(dossiers)
        self._client = model_client
        self._benchmarks = benchmark_runner
        self._as_of = as_of
        self._fault_hook = fault_hook or (lambda _step: None)
        missing = set(FIXED_SAAS_CORE) - set(self._dossiers)
        if missing:
            raise ValueError(f"missing fixed SaaS dossiers: {', '.join(sorted(missing))}")
        if any(self._dossiers[item].company_id != item for item in FIXED_SAAS_CORE):
            raise ValueError("dossier company ID does not match fixed fixture")

    def run(
        self,
        enrichment_id: str,
        *,
        allow_paid: bool = False,
        resume: bool = False,
    ) -> ExperimentSummary:
        if enrichment_id not in EXPERIMENT_ENRICHMENTS:
            raise ValueError("unsupported initial experiment enrichment")
        experiment_dir = self._root / enrichment_id
        journal = experiment_dir / "outcomes.jsonl"
        prior = self._read_outcomes(journal)
        expected_keys = tuple(
            self._case_key(enrichment_id, model, track, company_id)
            for model in EXPERIMENT_MODELS
            for track in EXPERIMENT_TRACKS
            for company_id in FIXED_SAAS_CORE
        )
        completed = {
            self._event_key(event) for event in prior
            if event.get("status") == "completed"
        }
        blocked_auth = {
            self._event_key(event) for event in prior
            if event.get("status") == "not_executed"
            and event.get("failure") == "authentication_required"
        }
        if completed and not resume:
            raise FileExistsError("experiment outcomes already exist; use resume")
        remaining = tuple(key for key in expected_keys if key not in completed)

        if self._client is None:
            pending_auth = tuple(
                key for key in remaining
                if not (resume and key in blocked_auth)
            )
            if pending_auth:
                for key in pending_auth:
                    self._append(journal, {
                        "company_id": key[3],
                        "enrichment_id": enrichment_id,
                        "execution_track": key[2],
                        "requested_model_id": key[1],
                        "resolved_model_id": None,
                        "source_cache_reused": True,
                        "source_purchases": 0,
                        "status": "not_executed",
                        "failure": "authentication_required",
                    })
            return ExperimentSummary(
                enrichment_id, "experiment", False, len(completed),
                len(completed) if resume else 0, len(expected_keys),
                len(completed), 0, Decimal("0"), EXPERIMENT_CAP_USD,
                "OPENAI_API_KEY unavailable; live model cases not executed",
            )
        if not allow_paid:
            raise ValueError("model execution requires explicit paid opt-in")

        def group_needs_work(model: str, track: ExecutionTrack) -> bool:
            if any(
                self._case_key(enrichment_id, model, track, company_id)
                in remaining for company_id in FIXED_SAAS_CORE
            ):
                return True
            group_id = f"{enrichment_id}--{model}--{track.value}"
            transaction = self._read_transaction(
                experiment_dir / "transactions" / f"{group_id}.json"
            )
            return transaction is not None and transaction["state"] != "completed"

        grouped = tuple(
            (model, track, tuple(
                ExperimentInput(
                    enrichment_id, company_id, model,
                    self._dossiers[company_id],
                )
                for company_id in FIXED_SAAS_CORE
            ))
            for model in EXPERIMENT_MODELS for track in EXPERIMENT_TRACKS
            if group_needs_work(model, track)
        )
        grouped = tuple(item for item in grouped if item[2])
        estimates = tuple(
            Decimal(self._client.estimate(requests, track))
            for _model, track, requests in grouped
        )
        if any(not value.is_finite() or value < 0 for value in estimates):
            raise ValueError("model estimate must be finite and non-negative")
        if sum(estimates, Decimal("0")) > EXPERIMENT_CAP_USD:
            raise ValueError("estimated spend exceeds aggregate experiment cap")

        scope_id = f"experiment:{enrichment_id}"
        ledger = BudgetLedger(
            experiment_dir / "budget.jsonl", {scope_id: str(EXPERIMENT_CAP_USD)},
        )
        successful_keys = set(completed)
        for (model, track, requests), estimate in zip(grouped, estimates):
            group_id = f"{enrichment_id}--{model}--{track.value}"
            transaction_path = experiment_dir / "transactions" / f"{group_id}.json"
            transaction = self._read_transaction(transaction_path)
            reservation = None
            if transaction is None:
                failed_attempts = self._next_attempt_index(
                    experiment_dir / "budget.jsonl", group_id,
                )
                reservation_key = f"{group_id}--attempt-{failed_attempts}"
                reservation = ledger.reserve(scope_id, reservation_key, estimate)
                has_pending = getattr(self._client, "has_pending", None)
                can_resume_pending = (
                    not reservation.should_execute
                    and callable(has_pending)
                    and bool(has_pending(requests, track))
                )
                if not reservation.should_execute and not can_resume_pending:
                    raise RuntimeError("owned experiment reservation is incomplete")
                try:
                    executions = self._client.execute(requests, track)
                except Exception as error:
                    ledger.reconcile(reservation, estimate)
                    failure_cases = tuple(
                        self._failure_case(
                            request, track, "retryable", type(error).__name__,
                        ) for request in requests
                    )
                    for request, case in zip(requests, failure_cases):
                        self._append(journal, {
                            "company_id": request.company_id,
                            "enrichment_id": enrichment_id,
                            "execution_track": track.value,
                            "requested_model_id": model,
                            "resolved_model_id": None,
                            "result": case.result,
                            "source_cache_reused": True,
                            "source_purchases": 0,
                            "status": "failed",
                            "failure": "retryable",
                            "error_type": type(error).__name__,
                            "reservation_key": reservation_key,
                        })
                    failure_plan = ExperimentPlan(
                        experiment_id=group_id + "--failed",
                        enrichment_id=enrichment_id,
                        execution_track=track,
                        requested_model_id=model,
                        cases=failure_cases,
                        freshness_days=90,
                    )
                    report_path = getattr(self._benchmarks, "report_path", None)
                    if report_path is None or not report_path(failure_plan).exists():
                        self._benchmarks.run(failure_plan)
                    continue
                if len(executions) != len(requests):
                    ledger.reconcile(reservation, estimate)
                    raise ValueError("model client returned the wrong case count")
                by_company = {item.company_id: item for item in executions}
                if set(by_company) != {item.company_id for item in requests}:
                    ledger.reconcile(reservation, estimate)
                    raise ValueError("model client returned the wrong company IDs")
                transaction = {
                    "executions": [self._execution_payload(item) for item in executions],
                    "group_id": group_id,
                    "reservation_key": reservation_key,
                    "state": "collected",
                }
                self._write_transaction(transaction_path, transaction)
            else:
                executions = tuple(
                    self._execution_from_payload(item)
                    for item in transaction["executions"]
                )
                by_company = {item.company_id: item for item in executions}

            actual = sum(
                (Decimal(item.actual_cost_usd) for item in executions),
                Decimal("0"),
            )
            if transaction["state"] == "collected":
                if reservation is None:
                    reservation = ledger.reserve(
                        scope_id, str(transaction["reservation_key"]), estimate,
                    )
                ledger.reconcile(reservation, actual)
                transaction = dict(transaction, state="reconciled")
                self._write_transaction(transaction_path, transaction)
                self._fault_hook("after_reconcile")
            cases = []
            for index, request in enumerate(requests):
                execution = by_company[request.company_id]
                try:
                    result = self._result(request, execution)
                    status = "completed"
                    failure = None
                except Exception as error:
                    result = self._failure_result(
                        request, execution.resolved_model_id,
                        FailureKind.CONTRACT_INVALID,
                    )
                    status = "failed"
                    failure = "contract_invalid"
                case = BenchmarkCase(
                    result=result,
                    dossier=request.dossier,
                    as_of=self._as_of,
                    latency_ms=execution.latency_ms,
                    model_cost_usd=execution.actual_cost_usd,
                    source_cost_usd="0",
                    source_lookups=1,
                    source_cache_hits=1,
                    source_purchases=0,
                )
                cases.append(case)
                self._append_once(journal, {
                    "company_id": request.company_id,
                    "enrichment_id": enrichment_id,
                    "execution_track": track.value,
                    "requested_model_id": model,
                    "resolved_model_id": execution.resolved_model_id,
                    "result": result,
                    "source_cache_reused": True,
                    "source_purchases": 0,
                    "status": status,
                    "failure": failure,
                })
                if status == "completed":
                    successful_keys.add(self._case_key(
                        enrichment_id, model, track, request.company_id,
                    ))
                if index == 0:
                    self._fault_hook("after_partial_journal")
            plan = ExperimentPlan(
                experiment_id=group_id,
                enrichment_id=enrichment_id,
                execution_track=track,
                requested_model_id=model,
                cases=tuple(cases),
                freshness_days=90,
            )
            self._fault_hook("before_report")
            report_path = getattr(self._benchmarks, "report_path", None)
            path = report_path(plan) if report_path is not None else None
            if path is None or not path.exists():
                report = self._benchmarks.run(plan)
            else:
                report = json.loads(path.read_text(encoding="utf-8"))
            report_payload = self._report_payload(report)
            report_material = canonical_json(report_payload).encode("utf-8")
            transaction = dict(
                transaction,
                state="completed",
                gate_score=self._gate_score(report),
                report_path=str(path) if path else f"inline:{group_id}",
                report_hash=(
                    sha256(path.read_bytes()).hexdigest()
                    if path is not None and path.is_file()
                    else sha256(report_material).hexdigest()
                ),
                case_count=len(cases),
            )
            self._write_transaction(transaction_path, transaction)
        total_completed = len(successful_keys)
        gate_score, gate_path = self._aggregate_gate(
            experiment_dir, enrichment_id,
        )
        candidate_ready = (
            total_completed == len(expected_keys)
            and gate_score is not None and gate_score >= 0.90
        )
        return ExperimentSummary(
            enrichment_id,
            "candidate" if candidate_ready else "experiment",
            False,
            total_completed,
            len(completed) if resume else 0,
            len(expected_keys),
            total_completed,
            0,
            ledger.spent(scope_id),
            EXPERIMENT_CAP_USD,
            programmed_gate_score=gate_score,
            gate_artifact_path=gate_path,
            blind_outputs=(
                self._blind_outputs(
                    journal, experiment_dir / "blind-output-map.json",
                ) if candidate_ready else ()
            ),
        )

    @staticmethod
    def _result(
        request: ExperimentInput, execution: ModelExecution,
    ) -> EnrichmentResult:
        if not execution.resolved_model_id:
            raise ValueError("resolved model identity is required")
        allowed = set(P0_ENRICHMENTS[request.enrichment_id])
        covered = {item.field for item in execution.assertions} | set(
            execution.unknowns
        )
        if covered != allowed:
            raise ValueError("model output must cover the exact enrichment fields")
        evidence_ids = {item.evidence_id for item in request.dossier.evidence}
        if any(
            evidence_id not in evidence_ids
            for assertion in execution.assertions
            for evidence_id in assertion.evidence_ids
        ):
            raise ValueError("model output cites evidence outside the fixed dossier")
        return EnrichmentResult(
            request.enrichment_id,
            request.company_id,
            "1.0",
            ResultStatus.COMPLETE,
            {
                "assertions": execution.assertions,
                "evidence": request.dossier.evidence,
                "unknowns": execution.unknowns,
                "requested_model": request.requested_model_id,
                "resolved_model": execution.resolved_model_id,
            },
        )

    @staticmethod
    def _failure_result(
        request: ExperimentInput, resolved_model_id: str | None,
        failure: FailureKind,
    ) -> EnrichmentResult:
        return EnrichmentResult(
            request.enrichment_id,
            request.company_id,
            "1.0",
            ResultStatus.FAILED,
            {
                "assertions": (),
                "evidence": request.dossier.evidence,
                "unknowns": P0_ENRICHMENTS[request.enrichment_id],
                "requested_model": request.requested_model_id,
                "resolved_model": resolved_model_id,
            },
            failure,
        )

    @classmethod
    def _blind_outputs(
        cls, journal: Path, mapping_path: Path,
    ) -> tuple[Mapping[str, object], ...]:
        events = []
        for event in cls._read_outcomes(journal):
            if event.get("status") != "completed":
                continue
            result = event["result"]["output"]
            content = {
                "assertions": tuple(
                    {"field": item["field"], "value": item["value"]}
                    for item in result["assertions"]
                ),
                "unknowns": tuple(result.get("unknowns", ())),
            }
            identity = canonical_json({
                "company_id": event["company_id"],
                "execution_track": event["execution_track"],
                "requested_model_id": event["requested_model_id"],
            })
            events.append((identity, content))
        if not events:
            return ()
        if mapping_path.exists():
            mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
        else:
            shuffled = list(identity for identity, _content in events)
            for index in range(len(shuffled) - 1, 0, -1):
                selected = secrets.randbelow(index + 1)
                shuffled[index], shuffled[selected] = (
                    shuffled[selected], shuffled[index]
                )
            mapping = {
                "entries": [
                    {
                        "internal_key": identity,
                        "output_id": "output-" + secrets.token_hex(16),
                    }
                    for identity in shuffled
                ],
                "schema_version": "1.0",
            }
            cls._write_transaction(mapping_path, mapping)
        entries = mapping.get("entries")
        if not isinstance(entries, list):
            raise ValueError("blind output mapping is invalid")
        by_identity = dict(events)
        existing_keys = {item.get("internal_key") for item in entries}
        if existing_keys != set(by_identity):
            raise ValueError("blind output mapping does not match completed outputs")
        output_ids = [item.get("output_id") for item in entries]
        if (
            any(not isinstance(item, str) or not item.startswith("output-")
                for item in output_ids)
            or len(output_ids) != len(set(output_ids))
        ):
            raise ValueError("blind output IDs must be unique opaque text")
        return tuple(
            {
                "content": by_identity[item["internal_key"]],
                "output_id": item["output_id"],
            }
            for item in entries
        )

    @staticmethod
    def _gate_score(report: object) -> float:
        direct = getattr(report, "mean_quality_score", None)
        if direct is not None:
            return float(direct)
        scorecards = getattr(report, "scorecards", None)
        if scorecards is not None:
            return sum(item.quality_score for item in scorecards) / len(scorecards)
        if isinstance(report, Mapping):
            return float(report["mean_scores"]["quality"])
        raise ValueError("benchmark report lacks a programmed quality score")

    @staticmethod
    def _report_payload(report: object) -> object:
        to_payload = getattr(report, "to_payload", None)
        if callable(to_payload):
            return to_payload()
        if isinstance(report, Mapping):
            return report
        direct = getattr(report, "mean_quality_score", None)
        if direct is not None:
            return {"mean_quality_score": float(direct)}
        raise ValueError("benchmark report cannot be serialized for gate evidence")

    @classmethod
    def _aggregate_gate(
        cls, experiment_dir: Path, enrichment_id: str,
    ) -> tuple[float | None, str | None]:
        entries = []
        for model in EXPERIMENT_MODELS:
            for track in EXPERIMENT_TRACKS:
                group_id = f"{enrichment_id}--{model}--{track.value}"
                transaction = cls._read_transaction(
                    experiment_dir / "transactions" / f"{group_id}.json"
                )
                if transaction is None or transaction["state"] != "completed":
                    return None, None
                report_path = str(transaction["report_path"])
                report_hash = str(transaction["report_hash"])
                if not report_path.startswith("inline:"):
                    path = Path(report_path)
                    if not path.is_file():
                        raise ValueError("programmed gate report artifact is missing")
                    if sha256(path.read_bytes()).hexdigest() != report_hash:
                        raise ValueError("programmed gate report artifact hash mismatch")
                entries.append({
                    "case_count": int(transaction["case_count"]),
                    "execution_track": track.value,
                    "group_id": group_id,
                    "model_id": model,
                    "report_hash": report_hash,
                    "report_path": report_path,
                    "score": float(transaction["gate_score"]),
                })
        total_cases = sum(item["case_count"] for item in entries)
        weighted = sum(
            (
                Decimal(str(item["score"])) * item["case_count"]
                for item in entries
            ),
            Decimal("0"),
        ) / Decimal(total_cases)
        score = float(weighted)
        manifest = {
            "case_count": total_cases,
            "enrichment_id": enrichment_id,
            "groups": entries,
            "programmed_gate_score": score,
            "schema_version": "1.0",
            "threshold": 0.90,
        }
        path = experiment_dir / "aggregate-gate.json"
        material = canonical_json(manifest) + "\n"
        if path.exists():
            if path.read_text(encoding="utf-8") != material:
                raise ValueError("aggregate programmed gate manifest mismatch")
        else:
            cls._write_transaction(path, manifest)
        return score, str(path)

    def _failure_case(
        self, request: ExperimentInput, track: ExecutionTrack,
        failure: str, _error_type: str,
    ) -> BenchmarkCase:
        kind = (
            FailureKind.RETRYABLE if failure == "retryable"
            else FailureKind.CONTRACT_INVALID
        )
        return BenchmarkCase(
            self._failure_result(request, None, kind), request.dossier,
            self._as_of, 0, "0", "0", 1, 1, 0,
        )

    @staticmethod
    def _execution_payload(execution: ModelExecution) -> dict[str, object]:
        return {
            "actual_cost_usd": execution.actual_cost_usd,
            "assertions": execution.assertions,
            "company_id": execution.company_id,
            "latency_ms": execution.latency_ms,
            "resolved_model_id": execution.resolved_model_id,
            "unknowns": execution.unknowns,
        }

    @staticmethod
    def _execution_from_payload(value: Mapping[str, object]) -> ModelExecution:
        assertions = tuple(
            FieldAssertion(
                item["field"], item["value"], tuple(item["evidence_ids"]),
                item["confidence"], Visibility(item["visibility"]),
            )
            for item in value["assertions"]
        )
        return ModelExecution(
            str(value["company_id"]), assertions,
            tuple(value["unknowns"]),
            value.get("resolved_model_id"),
            int(value["latency_ms"]),
            str(value["actual_cost_usd"]),
        )

    @staticmethod
    def _read_transaction(path: Path) -> dict[str, object] | None:
        if not path.exists():
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
        if value.get("state") not in {"collected", "reconciled", "completed"}:
            raise ValueError("invalid experiment transaction state")
        if not isinstance(value.get("executions"), list):
            raise ValueError("experiment transaction lacks executions")
        return value

    @staticmethod
    def _write_transaction(path: Path, value: Mapping[str, object]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(canonical_json(value) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)

    @classmethod
    def _append_once(cls, path: Path, event: Mapping[str, object]) -> None:
        lock_path = path.with_suffix(path.suffix + ".lock")
        with file_lock(lock_path):
            existing = cls._read_outcomes(path)
            identity = cls._event_key(event)
            if any(
                cls._event_key(item) == identity
                and item.get("status") == event.get("status")
                for item in existing
            ):
                return
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(canonical_json(event) + "\n")
                stream.flush()
                os.fsync(stream.fileno())

    @staticmethod
    def _case_key(
        enrichment_id: str, model: str, track: ExecutionTrack, company_id: str,
    ) -> tuple[str, str, str, str]:
        return enrichment_id, model, track.value, company_id

    @staticmethod
    def _event_key(event: Mapping[str, object]) -> tuple[str, str, str, str]:
        return (
            str(event["enrichment_id"]),
            str(event["requested_model_id"]),
            str(event["execution_track"]),
            str(event["company_id"]),
        )

    @staticmethod
    def _read_outcomes(path: Path) -> tuple[dict[str, object], ...]:
        if not path.exists():
            return ()
        return tuple(
            json.loads(line) for line in path.read_text(
                encoding="utf-8",
            ).splitlines()
        )

    @staticmethod
    def _next_attempt_index(path: Path, group_id: str) -> int:
        if not path.exists():
            return 0
        prefix = group_id + "--attempt-"
        attempts: dict[int, str] = {}
        settled: set[str] = set()
        for line in path.read_text(encoding="utf-8").splitlines():
            event = json.loads(line)
            key = event.get("idempotency_key")
            if event.get("kind") == "reserve" and isinstance(key, str) and key.startswith(prefix):
                suffix = key[len(prefix):]
                if not suffix.isdigit():
                    raise ValueError("invalid experiment attempt key")
                attempts[int(suffix)] = str(event["reservation_id"])
            elif event.get("kind") in {"release", "reconcile"}:
                settled.add(str(event["reservation_id"]))
        outstanding = [
            attempt for attempt, reservation_id in attempts.items()
            if reservation_id not in settled
        ]
        if len(outstanding) > 1:
            raise ValueError("experiment group has multiple active reservations")
        if outstanding:
            return outstanding[0]
        return max(attempts, default=-1) + 1

    @staticmethod
    def _append(path: Path, event: Mapping[str, object]) -> None:
        lock_path = path.with_suffix(path.suffix + ".lock")
        with file_lock(lock_path):
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(canonical_json(event) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
