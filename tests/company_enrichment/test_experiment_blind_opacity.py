from hashlib import sha256
import json
from pathlib import Path

from scripts.company_enrichment.contracts import canonical_json
from scripts.company_enrichment.experiment_runner import (
    EXPERIMENT_MODELS,
    EXPERIMENT_TRACKS,
    FIXED_SAAS_CORE,
)
from tests.company_enrichment.test_experiment_crash_recovery import (
    CountingClient,
    _runner,
)


def test_blind_ids_are_not_enumerable_matrix_hashes_and_order_is_persisted(
    tmp_path: Path,
) -> None:
    client = CountingClient()
    first = _runner(tmp_path, client).run(
        "company-description", allow_paid=True,
    )
    enumerable = {
        "output-" + sha256(canonical_json({
            "company_id": company_id,
            "execution_track": track.value,
            "requested_model_id": model,
        }).encode("utf-8")).hexdigest()[:16]
        for model in EXPERIMENT_MODELS
        for track in EXPERIMENT_TRACKS
        for company_id in FIXED_SAAS_CORE
    }
    ids = tuple(item["output_id"] for item in first.blind_outputs)
    mapping = (
        tmp_path / "company-description" / "blind-output-map.json"
    )
    original = mapping.read_bytes()

    resumed = _runner(tmp_path, client).run(
        "company-description", allow_paid=True, resume=True,
    )

    assert not (set(ids) & enumerable)
    assert len(ids) == len(set(ids)) == 18
    assert tuple(resumed.blind_outputs) == tuple(first.blind_outputs)
    assert mapping.read_bytes() == original
    serialized = json.dumps(first.blind_outputs)
    assert "gpt-" not in serialized
    assert "synchronous" not in serialized
    assert "batch" not in serialized
