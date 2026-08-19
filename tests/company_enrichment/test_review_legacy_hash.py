from datetime import datetime, timezone
from hashlib import sha256

from scripts.company_enrichment.contracts import ReviewStatus, canonical_json
from scripts.company_enrichment.review import ReviewActor, ReviewRecord


def test_legacy_experiment_record_hash_remains_verifiable() -> None:
    occurred_at = datetime(2026, 8, 12, 12, tzinfo=timezone.utc)
    legacy = {
        "sequence": 1,
        "experiment_id": "initial-company-description",
        "from_status": ReviewStatus.PROPOSED,
        "to_status": ReviewStatus.EXPERIMENT,
        "actor": ReviewActor.AUTOMATION,
        "occurred_at": occurred_at,
        "prior_record_hash": None,
        "verdict": None,
        "reviewer_id": None,
        "blind": False,
    }
    legacy_hash = sha256(canonical_json(legacy).encode("utf-8")).hexdigest()

    record = ReviewRecord(**legacy, record_hash=legacy_hash)

    assert record.record_hash == legacy_hash
