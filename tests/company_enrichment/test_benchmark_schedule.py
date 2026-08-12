from pathlib import Path

import yaml

import pytest

from scripts.company_enrichment.benchmark_schedule import (
    BenchmarkRollout,
    ordered_company_ids,
)


def test_schedule_runs_saas_core_then_remaining_saas_before_other_cohorts() -> None:
    companies = yaml.safe_load(
        Path("benchmarks/companies.yaml").read_text(encoding="utf-8")
    )["companies"]

    ordered = ordered_company_ids(companies)

    assert ordered[:3] == ("saas-01", "saas-04", "saas-07")
    assert set(ordered[:10]) == {f"saas-{number:02d}" for number in range(1, 11)}
    assert ordered[10:13] == ("funded-01", "funded-05", "funded-07")
    assert len(ordered) == 60


def test_rollout_blocks_later_cohorts_until_each_saas_gate_completes(tmp_path) -> None:
    companies = yaml.safe_load(
        Path("benchmarks/companies.yaml").read_text(encoding="utf-8")
    )["companies"]
    journal = tmp_path / "rollout.jsonl"
    rollout = BenchmarkRollout(companies, journal=journal)

    assert rollout.current_stage == "saas_shared_core"
    assert rollout.current_company_ids == ("saas-01", "saas-04", "saas-07")
    with pytest.raises(ValueError, match="current rollout stage"):
        rollout.complete(("funded-01",))

    rollout.complete(("saas-01", "saas-04", "saas-07"))
    assert rollout.current_stage == "all_saas"
    assert set(rollout.current_company_ids) == {
        f"saas-{number:02d}" for number in range(1, 11)
    }

    resumed = BenchmarkRollout(companies, journal=journal)
    assert resumed.current_stage == "all_saas"
    assert resumed.current_company_ids == rollout.current_company_ids

    resumed.complete(resumed.current_company_ids)
    assert resumed.current_stage == "recently_funded_b2b"
    assert all(item.startswith("funded-") for item in resumed.current_company_ids)
