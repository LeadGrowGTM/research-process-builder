from pathlib import Path

import pytest
import yaml

from scripts.company_enrichment.benchmark_schedule import (
    BenchmarkRollout, ordered_company_ids,
)


def _companies() -> list[dict]:
    return yaml.safe_load(
        Path('benchmarks/companies.yaml').read_text(encoding='utf-8')
    )['companies']


def test_schedule_runs_saas_core_then_saas_before_other_cohorts() -> None:
    ordered = ordered_company_ids(_companies())
    assert ordered[:3] == ('saas-01', 'saas-04', 'saas-07')
    assert set(ordered[:10]) == {f'saas-{number:02d}' for number in range(1, 11)}
    assert ordered[10:13] == ('funded-01', 'funded-05', 'funded-07')
    assert len(ordered) == 60


def test_rollout_never_repeats_a_company_between_stages(tmp_path) -> None:
    journal = tmp_path / 'rollout.jsonl'
    rollout = BenchmarkRollout(_companies(), journal=journal)
    assert rollout.current_stage == 'saas_shared_core'
    assert rollout.current_company_ids == ('saas-01', 'saas-04', 'saas-07')
    with pytest.raises(ValueError, match='current rollout stage'):
        rollout.complete(('funded-01',))
    first = set(rollout.current_company_ids)
    rollout.complete(rollout.current_company_ids)
    assert rollout.current_stage == 'remaining_saas'
    assert len(rollout.current_company_ids) == 7
    assert first.isdisjoint(rollout.current_company_ids)
    assert set(rollout.current_company_ids) == {
        'saas-02', 'saas-03', 'saas-05', 'saas-06',
        'saas-08', 'saas-09', 'saas-10',
    }
    resumed = BenchmarkRollout(_companies(), journal=journal)
    assert resumed.current_stage == 'remaining_saas'
    resumed.complete(resumed.current_company_ids)
    assert resumed.current_stage == 'recently_funded_b2b'
    assert all(item.startswith('funded-') for item in resumed.current_company_ids)
