"""Human-gated, append-only review transitions for enrichment Experiments."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from hashlib import sha256
import math
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from .contracts import ReviewStatus, canonical_json


BLIND_REVIEW_DIMENSIONS = (
    "readability",
    "specificity",
    "usefulness",
    "casualness",
    "non-creepiness",
)
_IDENTITY_KEYS = {
    "provider",
    "provider_id",
    "model",
    "model_id",
    "requested_model",
    "requested_model_id",
    "resolved_model",
    "resolved_model_id",
}


def _freeze_review_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        frozen: dict[str, Any] = {}
        for key, item in value.items():
            normalized = str(key).strip().lower().replace("-", "_")
            if normalized in _IDENTITY_KEYS:
                raise ValueError(f"blind review output contains model/provider identity: {key}")
            frozen[str(key)] = _freeze_review_value(item)
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_review_value(item) for item in value)
    return value


@dataclass(frozen=True, slots=True)
class BlindReviewOutput:
    output_id: str
    content: Mapping[str, Any]

    def __post_init__(self) -> None:
        _require_text("output_id", self.output_id)
        if not isinstance(self.content, Mapping):
            raise ValueError("content must be a mapping")
        object.__setattr__(self, "content", _freeze_review_value(self.content))


@dataclass(frozen=True, slots=True)
class BlindReviewPack:
    pack_id: str
    experiment_id: str
    created_at: datetime
    outputs: tuple[BlindReviewOutput, ...]
    dimensions: tuple[str, ...] = BLIND_REVIEW_DIMENSIONS

    def __post_init__(self) -> None:
        _require_text("pack_id", self.pack_id)
        _require_text("experiment_id", self.experiment_id)
        _require_aware("created_at", self.created_at)
        object.__setattr__(self, "outputs", tuple(self.outputs))
        if not self.outputs:
            raise ValueError("blind review pack requires at least one output")
        if any(not isinstance(item, BlindReviewOutput) for item in self.outputs):
            raise ValueError("outputs must contain BlindReviewOutput values")
        output_ids = [item.output_id for item in self.outputs]
        if len(set(output_ids)) != len(output_ids):
            raise ValueError("blind review output IDs must be unique")
        if tuple(self.dimensions) != BLIND_REVIEW_DIMENSIONS:
            raise ValueError("blind review dimensions are fixed")
        object.__setattr__(self, "dimensions", BLIND_REVIEW_DIMENSIONS)

    @classmethod
    def create(
        cls,
        experiment_id: str,
        *,
        outputs: Sequence[BlindReviewOutput],
        created_at: datetime,
    ) -> "BlindReviewPack":
        values = tuple(outputs)
        payload = {
            "experiment_id": experiment_id,
            "created_at": created_at,
            "outputs": values,
            "dimensions": BLIND_REVIEW_DIMENSIONS,
        }
        pack_id = sha256(canonical_json(payload).encode("utf-8")).hexdigest()
        return cls(pack_id, experiment_id, created_at, values)

@dataclass(frozen=True, slots=True)
class ProgrammedGateEvidence:
    score: float
    threshold: float
    artifact_path: str
    artifact_hash: str
    gate_kind: str = "programmed_ground_truth"

    def __post_init__(self) -> None:
        for name in ("score", "threshold"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{name} must be a finite score between zero and one")
            value = float(value)
            if not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError(f"{name} must be a finite score between zero and one")
            object.__setattr__(self, name, value)
        if self.threshold < 0.90:
            raise ValueError("programmed ground-truth gate threshold must be at least 0.90")
        _require_text("artifact_path", self.artifact_path)
        if (
            not isinstance(self.artifact_hash, str)
            or len(self.artifact_hash) != 64
            or any(char not in "0123456789abcdef" for char in self.artifact_hash.lower())
        ):
            raise ValueError("artifact_hash must be a SHA-256 hex digest")
        if self.gate_kind != "programmed_ground_truth":
            raise ValueError("gate evidence must identify the programmed ground-truth gate")

    @property
    def passed(self) -> bool:
        return self.score >= self.threshold and self.score >= 0.90


@dataclass(frozen=True, slots=True)
class BlindReviewScorecard:
    readability: float
    specificity: float
    usefulness: float
    casualness: float
    non_creepiness: float

    def __post_init__(self) -> None:
        for name in (
            "readability", "specificity", "usefulness", "casualness",
            "non_creepiness",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{name} must be a finite score from 1 to 5")
            value = float(value)
            if not math.isfinite(value) or not 1 <= value <= 5:
                raise ValueError(f"{name} must be a finite score from 1 to 5")
            object.__setattr__(self, name, value)

class ReviewActor(str, Enum):
    AUTOMATION = "automation"
    HUMAN = "human"


class ReviewVerdict(str, Enum):
    APPROVE = "approve"
    REVISE = "revise"
    REJECT = "reject"


_AUTOMATION_TRANSITIONS = {
    (ReviewStatus.PROPOSED, ReviewStatus.EXPERIMENT),
    (ReviewStatus.EXPERIMENT, ReviewStatus.CANDIDATE),
}
_VERDICT_STATUS = {
    ReviewVerdict.APPROVE: ReviewStatus.APPROVED,
    ReviewVerdict.REVISE: ReviewStatus.CANDIDATE,
    ReviewVerdict.REJECT: ReviewStatus.REJECTED,
}


def _require_text(name: str, value: str | None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty text")
    return value


def _require_aware(name: str, value: datetime) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be a timezone-aware timestamp")


@dataclass(frozen=True, slots=True)
class ReviewRecord:
    sequence: int
    experiment_id: str
    from_status: ReviewStatus
    to_status: ReviewStatus
    actor: ReviewActor
    occurred_at: datetime
    prior_record_hash: str | None
    record_hash: str = ""
    verdict: ReviewVerdict | None = None
    reviewer_id: str | None = None
    blind: bool = False
    gate_evidence: ProgrammedGateEvidence | None = None
    blind_review_pack: BlindReviewPack | None = None
    blind_review_pack_id: str | None = None
    scorecard: BlindReviewScorecard | None = None

    def __post_init__(self) -> None:
        _require_text("experiment_id", self.experiment_id)
        if not isinstance(self.sequence, int) or isinstance(self.sequence, bool) or self.sequence < 1:
            raise ValueError("sequence must be a positive integer")
        _require_aware("occurred_at", self.occurred_at)
        if self.record_hash:
            if self.record_hash != _record_hash(self):
                raise ValueError("record_hash does not match the review record")
        else:
            object.__setattr__(self, "record_hash", _record_hash(self))


def _record_hash(record: ReviewRecord) -> str:
    values = {name: getattr(record, name) for name in (
        "sequence", "experiment_id", "from_status", "to_status", "actor",
"occurred_at", "prior_record_hash", "verdict", "reviewer_id", "blind",
        "gate_evidence", "blind_review_pack", "blind_review_pack_id", "scorecard",
    )}
    return sha256(canonical_json(values).encode("utf-8")).hexdigest()


def _validate_transition(record: ReviewRecord) -> None:
    if record.actor is ReviewActor.AUTOMATION:
        if (
            record.verdict is not None
            or record.reviewer_id is not None
            or record.blind
            or record.blind_review_pack_id is not None
            or record.scorecard is not None
        ):
            raise ValueError("automation record cannot contain a human verdict")
        if (record.from_status, record.to_status) not in _AUTOMATION_TRANSITIONS:
            raise ValueError("invalid automation transition")
        if record.to_status is ReviewStatus.CANDIDATE:
            if (
                not isinstance(record.gate_evidence, ProgrammedGateEvidence)
                or not record.gate_evidence.passed
            ):
                raise ValueError(
                    "candidate requires a passing programmed ground-truth gate at or above 0.90"
                )
            if not isinstance(record.blind_review_pack, BlindReviewPack):
                raise ValueError("candidate requires an anonymized blind review pack")
            if record.blind_review_pack.experiment_id != record.experiment_id:
                raise ValueError("blind review pack belongs to another Experiment")
        elif record.gate_evidence is not None or record.blind_review_pack is not None:
            raise ValueError("gate evidence and blind review pack belong only on candidate records")
        return
    if record.actor is not ReviewActor.HUMAN:
        raise ValueError("actor must identify automation or a human")
    if record.from_status is not ReviewStatus.CANDIDATE:
        raise ValueError("human verdict requires candidate status")
    if record.verdict is None or record.to_status is not _VERDICT_STATUS[record.verdict]:
        raise ValueError("human verdict does not match its status outcome")
    _require_text("reviewer_id", record.reviewer_id)
    if not record.blind:
        raise ValueError("human verdict must be blind")
    _require_text("blind_review_pack_id", record.blind_review_pack_id)
    if not isinstance(record.scorecard, BlindReviewScorecard):
        raise ValueError("human verdict requires a complete blind review scorecard")
    if record.gate_evidence is not None or record.blind_review_pack is not None:
        raise ValueError("human verdict cannot replace candidate gate or pack evidence")

@dataclass(frozen=True, slots=True)
class ReviewHistory:
    experiment_id: str
    records: tuple[ReviewRecord, ...] = ()

    def __post_init__(self) -> None:
        _require_text("experiment_id", self.experiment_id)
        object.__setattr__(self, "records", tuple(self.records))
        status = ReviewStatus.PROPOSED
        prior_hash = None
        active_pack_id = None
        for sequence, record in enumerate(self.records, start=1):
            if record.experiment_id != self.experiment_id:
                raise ValueError("review record belongs to another Experiment")
            if record.sequence != sequence or record.prior_record_hash != prior_hash:
                raise ValueError("review records do not form an append-only audit chain")
            if record.from_status is not status:
                raise ValueError("review record status chain is not contiguous")
            _validate_transition(record)
            if record.blind_review_pack is not None:
                active_pack_id = record.blind_review_pack.pack_id
            if (
                record.actor is ReviewActor.HUMAN
                and record.blind_review_pack_id != active_pack_id
            ):
                raise ValueError("human verdict pack_id does not match the candidate blind review pack")
            status, prior_hash = record.to_status, record.record_hash

    @classmethod
    def start(cls, experiment_id: str) -> "ReviewHistory":
        return cls(experiment_id)

    @property
    def status(self) -> ReviewStatus:
        return self.records[-1].to_status if self.records else ReviewStatus.PROPOSED

    def _append(
        self, target: ReviewStatus, actor: ReviewActor, occurred_at: datetime,
        *, verdict: ReviewVerdict | None = None, reviewer_id: str | None = None,
        blind: bool = False,
        gate_evidence: ProgrammedGateEvidence | None = None,
        blind_review_pack: BlindReviewPack | None = None,
        blind_review_pack_id: str | None = None,
        scorecard: BlindReviewScorecard | None = None,
    ) -> "ReviewHistory":
        record = ReviewRecord(
            sequence=len(self.records) + 1,
            experiment_id=self.experiment_id,
            from_status=self.status,
            to_status=target,
            actor=actor,
            occurred_at=occurred_at,
            prior_record_hash=self.records[-1].record_hash if self.records else None,
            verdict=verdict,
            reviewer_id=reviewer_id,
            blind=blind,
            gate_evidence=gate_evidence,
            blind_review_pack=blind_review_pack,
            blind_review_pack_id=blind_review_pack_id,
            scorecard=scorecard,
        )
        _validate_transition(record)
        return ReviewHistory(self.experiment_id, (*self.records, record))

    def record_automation(
        self,
        target: ReviewStatus,
        *,
        occurred_at: datetime,
        gate_evidence: ProgrammedGateEvidence | None = None,
        blind_review_pack: BlindReviewPack | None = None,
    ) -> "ReviewHistory":
        if target is ReviewStatus.APPROVED:
            raise ValueError("automation cannot create Approval")
        if not isinstance(target, ReviewStatus) or (self.status, target) not in _AUTOMATION_TRANSITIONS:
            value = target.value if isinstance(target, ReviewStatus) else repr(target)
            raise ValueError(f"invalid automation transition: {self.status.value} -> {value}")
        _require_aware("occurred_at", occurred_at)
        return self._append(
            target,
            ReviewActor.AUTOMATION,
            occurred_at,
            gate_evidence=gate_evidence,
            blind_review_pack=blind_review_pack,
        )

    def record_human_verdict(
        self,
        verdict: ReviewVerdict,
        *,
        reviewer_id: str | None,
        reviewed_at: datetime,
        pack_id: str | None = None,
        scorecard: BlindReviewScorecard | None = None,
    ) -> "ReviewHistory":
        if self.status is not ReviewStatus.CANDIDATE:
            raise ValueError("blind human verdict requires candidate status")
        if not isinstance(verdict, ReviewVerdict):
            raise ValueError("verdict must be a ReviewVerdict")
        reviewer_id = _require_text("reviewer_id", reviewer_id)
        _require_aware("reviewed_at", reviewed_at)
        pack_id = _require_text("pack_id", pack_id)
        if not isinstance(scorecard, BlindReviewScorecard):
            raise ValueError("blind human verdict requires a complete scorecard")
        return self._append(
            _VERDICT_STATUS[verdict],
            ReviewActor.HUMAN,
            reviewed_at,
            verdict=verdict,
            reviewer_id=reviewer_id,
            blind=True,
            blind_review_pack_id=pack_id,
            scorecard=scorecard,
        )

def automated_transition(
    history: ReviewHistory,
    target: ReviewStatus,
    *,
    occurred_at: datetime,
    gate_evidence: ProgrammedGateEvidence | None = None,
    blind_review_pack: BlindReviewPack | None = None,
) -> ReviewHistory:
    """Append one permitted automated transition without granting Approval."""
    if not isinstance(history, ReviewHistory):
        raise TypeError("history must be a ReviewHistory")
    return history.record_automation(
        target,
        occurred_at=occurred_at,
        gate_evidence=gate_evidence,
        blind_review_pack=blind_review_pack,
    )


def record_blind_verdict(
    history: ReviewHistory,
    verdict: ReviewVerdict,
    *,
    reviewer_id: str | None,
    reviewed_at: datetime,
    pack_id: str | None = None,
    scorecard: BlindReviewScorecard | None = None,
) -> ReviewHistory:
    """Append an attributed, scored blind verdict linked to its review pack."""
    if not isinstance(history, ReviewHistory):
        raise TypeError("history must be a ReviewHistory")
    return history.record_human_verdict(
        verdict,
        reviewer_id=reviewer_id,
        reviewed_at=reviewed_at,
        pack_id=pack_id,
        scorecard=scorecard,
    )

__all__ = [
    "BLIND_REVIEW_DIMENSIONS",
    "BlindReviewOutput",
    "BlindReviewPack",
    "BlindReviewScorecard",
    "ProgrammedGateEvidence",
    "ReviewActor",
    "ReviewHistory",
    "ReviewRecord",
    "ReviewVerdict",
    "automated_transition",
    "record_blind_verdict",
]
