from pathlib import Path

import yaml

from scripts.company_enrichment.benchmark_schedule import ordered_company_ids


def test_schedule_runs_saas_core_then_remaining_saas_before_other_cohorts() -> None:
    companies = yaml.safe_load(
        Path("benchmarks/companies.yaml").read_text(encoding="utf-8")
    )["companies"]

    ordered = ordered_company_ids(companies)

    assert ordered[:3] == ("saas-01", "saas-04", "saas-07")
    assert set(ordered[:10]) == {f"saas-{number:02d}" for number in range(1, 11)}
    assert ordered[10:13] == ("funded-01", "funded-05", "funded-07")
    assert len(ordered) == 60
