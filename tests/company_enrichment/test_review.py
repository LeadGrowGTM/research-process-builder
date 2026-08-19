from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone

import pytest

from scripts.company_enrichment.contracts import ReviewStatus
from scripts.company_enrichment import review as review_module
from scripts.company_enrichment.review import (
    ReviewActor,
    ReviewHistory,
    ReviewVerdict,
    automated_transition,
    record_blind_verdict,
)


NOW = datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)


def test_blind_review_pack_exposes_only_anonymous_outputs_and_fixed_dimensions() -> None:
    pack = review_module.BlindReviewPack.create(
        "company-description:gusto",
        outputs=(
            review_module.BlindReviewOutput(
                output_id="output-a",
                content={"description": "Payroll and HR software for growing teams."},
            ),
        ),
        created_at=NOW,
    )

    assert pack.dimensions == (
        "readability",
        "specificity",
        "usefulness",
        "casualness",
        "non-creepiness",
    )
    assert pack.dimensions == review_module.BLIND_REVIEW_DIMENSIONS
    assert pack.outputs[0].content == {
        "description": "Payroll and HR software for growing teams."
    }
    assert not hasattr(pack.outputs[0], "provider")
    assert not hasattr(pack.outputs[0], "requested_model")
    assert not hasattr(pack.outputs[0], "resolved_model")


@pytest.mark.parametrize(
    "identity_key",
    ("provider", "requested_model", "resolved-model", "model_id"),
)
def test_blind_review_pack_rejects_identity_bearing_output_fields(
    identity_key: str,
) -> None:
    with pytest.raises(ValueError, match="identity"):
        review_module.BlindReviewPack.create(
            "company-description:gusto",
            outputs=(
                review_module.BlindReviewOutput(
                    output_id="output-a",
                    content={"nested": {identity_key: "secret-until-verdict"}},
                ),
            ),
            created_at=NOW,
        )

def test_candidate_requires_a_passing_programmed_ground_truth_gate() -> None:
    history = automated_transition(
        ReviewHistory.start("company-description:gusto"),
        ReviewStatus.EXPERIMENT,
        occurred_at=NOW,
    )

    with pytest.raises(ValueError, match="ground-truth gate"):
        automated_transition(
            history,
            ReviewStatus.CANDIDATE,
            occurred_at=NOW,
            gate_evidence=_gate(score=0.89),
            blind_review_pack=_blind_pack(),
        )


def test_candidate_appends_gate_evidence_and_anonymous_pack_to_audit_chain() -> None:
    history = automated_transition(
        ReviewHistory.start("company-description:gusto"),
        ReviewStatus.EXPERIMENT,
        occurred_at=NOW,
    )

    candidate = automated_transition(
        history,
        ReviewStatus.CANDIDATE,
        occurred_at=NOW,
        gate_evidence=_gate(),
        blind_review_pack=_blind_pack(),
    )

    record = candidate.records[-1]
    assert record.gate_evidence == _gate()
    assert record.blind_review_pack == _blind_pack()
    assert record.blind_review_pack.experiment_id == candidate.experiment_id
    assert record.blind_review_pack.outputs[0].content == {
        "description": "Payroll and HR software for growing teams."
    }


def test_approval_requires_a_verdict_linked_to_the_candidate_pack_and_scorecard() -> None:
    candidate = _candidate_history()

    with pytest.raises(ValueError, match="pack_id"):
        record_blind_verdict(
            candidate,
            ReviewVerdict.APPROVE,
            reviewer_id="reviewer-42",
            reviewed_at=NOW,
            pack_id="wrong-pack",
            scorecard=_scorecard(),
        )

    approved = record_blind_verdict(
        candidate,
        ReviewVerdict.APPROVE,
        reviewer_id="reviewer-42",
        reviewed_at=NOW,
        pack_id=_blind_pack().pack_id,
        scorecard=_scorecard(),
    )

    assert approved.status is ReviewStatus.APPROVED
    assert approved.records[-1].blind_review_pack_id == _blind_pack().pack_id
    assert approved.records[-1].scorecard == _scorecard()

def test_automation_can_create_a_candidate_but_never_approval() -> None:
    history = ReviewHistory.start("company-description:gusto")

    history = automated_transition(history, ReviewStatus.EXPERIMENT, occurred_at=NOW)
    history = automated_transition(
        history,
        ReviewStatus.CANDIDATE,
        occurred_at=NOW,
        gate_evidence=_gate(),
        blind_review_pack=_blind_pack(),
    )

    assert history.status is ReviewStatus.CANDIDATE
    assert [record.actor for record in history.records] == [
        ReviewActor.AUTOMATION,
        ReviewActor.AUTOMATION,
    ]
    with pytest.raises(ValueError, match="automation cannot create Approval"):
        automated_transition(history, ReviewStatus.APPROVED, occurred_at=NOW)


@pytest.mark.parametrize("reviewer_id", ("", "   ", None))
def test_blind_human_verdict_requires_reviewer_identity(reviewer_id: str | None) -> None:
    history = _candidate_history()

    with pytest.raises(ValueError, match="reviewer_id"):
        record_blind_verdict(
            history,
            ReviewVerdict.APPROVE,
            reviewer_id=reviewer_id,
            reviewed_at=NOW,
        )


def test_blind_human_verdict_requires_timezone_aware_timestamp() -> None:
    history = _candidate_history()

    with pytest.raises(ValueError, match="timezone-aware"):
        record_blind_verdict(
            history,
            ReviewVerdict.APPROVE,
            reviewer_id="reviewer-42",
            reviewed_at=datetime(2026, 8, 13, 12, 0),
        )


@pytest.mark.parametrize(
    ("verdict", "expected_status"),
    (
        (ReviewVerdict.APPROVE, ReviewStatus.APPROVED),
        (ReviewVerdict.REJECT, ReviewStatus.REJECTED),
        (ReviewVerdict.REVISE, ReviewStatus.CANDIDATE),
    ),
)
def test_blind_human_verdict_records_the_permitted_outcome(
    verdict: ReviewVerdict, expected_status: ReviewStatus
) -> None:
    history = record_blind_verdict(
        _candidate_history(),
        verdict,
        reviewer_id="reviewer-42",
        reviewed_at=NOW,
        pack_id=_blind_pack().pack_id,
        scorecard=_scorecard(),
    )

    record = history.records[-1]
    assert history.status is expected_status
    assert record.actor is ReviewActor.HUMAN
    assert record.blind is True
    assert record.verdict is verdict
    assert record.reviewer_id == "reviewer-42"
    assert record.occurred_at == NOW


@pytest.mark.parametrize(
    ("starting_status", "target"),
    (
        (ReviewStatus.PROPOSED, ReviewStatus.CANDIDATE),
        (ReviewStatus.EXPERIMENT, ReviewStatus.APPROVED),
        (ReviewStatus.CANDIDATE, ReviewStatus.EXPERIMENT),
        (ReviewStatus.APPROVED, ReviewStatus.REJECTED),
        (ReviewStatus.REJECTED, ReviewStatus.EXPERIMENT),
    ),
)
def test_automation_rejects_out_of_order_or_terminal_transitions(
    starting_status: ReviewStatus, target: ReviewStatus
) -> None:
    history = _history_at(starting_status)

    with pytest.raises(ValueError, match="automation"):
        automated_transition(history, target, occurred_at=NOW)


def test_reconstructed_approval_fails_closed_without_candidate_gate_or_pack() -> None:
    experiment = ReviewHistory.start("company-description:gusto")
    experiment = automated_transition(
        experiment, ReviewStatus.EXPERIMENT, occurred_at=NOW
    )
    invalid_candidate = review_module.ReviewRecord(
        sequence=2,
        experiment_id=experiment.experiment_id,
        from_status=ReviewStatus.EXPERIMENT,
        to_status=ReviewStatus.CANDIDATE,
        actor=ReviewActor.AUTOMATION,
        occurred_at=NOW,
        prior_record_hash=experiment.records[-1].record_hash,
    )

    with pytest.raises(ValueError, match="ground-truth gate"):
        ReviewHistory(
            experiment.experiment_id,
            (*experiment.records, invalid_candidate),
        )

def test_review_history_is_immutable_append_only_and_hash_chained() -> None:
    proposed = ReviewHistory.start("company-description:gusto")
    experiment = automated_transition(proposed, ReviewStatus.EXPERIMENT, occurred_at=NOW)
    candidate = automated_transition(
        experiment,
        ReviewStatus.CANDIDATE,
        occurred_at=NOW,
        gate_evidence=_gate(),
        blind_review_pack=_blind_pack(),
    )

    assert proposed.records == ()
    assert len(experiment.records) == 1
    assert len(candidate.records) == 2
    assert candidate.records[1].prior_record_hash == candidate.records[0].record_hash
    with pytest.raises(FrozenInstanceError):
        candidate.records[0].to_status = ReviewStatus.APPROVED  # type: ignore[misc]


def _blind_pack() -> object:
    return review_module.BlindReviewPack.create(
        "company-description:gusto",
        outputs=(
            review_module.BlindReviewOutput(
                output_id="output-a",
                content={"description": "Payroll and HR software for growing teams."},
            ),
        ),
        created_at=NOW,
    )


def _gate(*, score: float = 0.95) -> object:
    return review_module.ProgrammedGateEvidence(
        score=score,
        threshold=0.90,
        artifact_path="company-description/report.json",
        artifact_hash="a" * 64,
    )


def _scorecard() -> object:
    return review_module.BlindReviewScorecard(
        readability=5,
        specificity=4,
        usefulness=5,
        casualness=4,
        non_creepiness=5,
    )

def _candidate_history() -> ReviewHistory:
    return _history_at(ReviewStatus.CANDIDATE)


def _history_at(status: ReviewStatus) -> ReviewHistory:
    history = ReviewHistory.start("company-description:gusto")
    if status is ReviewStatus.PROPOSED:
        return history
    history = automated_transition(history, ReviewStatus.EXPERIMENT, occurred_at=NOW)
    if status is ReviewStatus.EXPERIMENT:
        return history
    history = automated_transition(
        history,
        ReviewStatus.CANDIDATE,
        occurred_at=NOW,
        gate_evidence=_gate(),
        blind_review_pack=_blind_pack(),
    )
    if status is ReviewStatus.CANDIDATE:
        return history
    verdict = ReviewVerdict.APPROVE if status is ReviewStatus.APPROVED else ReviewVerdict.REJECT
    return record_blind_verdict(
        history,
        verdict,
        reviewer_id="reviewer-42",
        reviewed_at=NOW,
        pack_id=_blind_pack().pack_id,
        scorecard=_scorecard(),
    )
