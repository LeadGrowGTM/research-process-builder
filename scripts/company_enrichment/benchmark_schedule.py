from __future__ import annotations

from typing import Any, Mapping, Sequence


COHORT_ORDER = (
    "b2b_saas",
    "recently_funded_b2b",
    "b2b_agencies",
    "well_known_b2b",
    "b2b_commerce_suppliers",
    "local_b2b_services",
)


def ordered_company_ids(companies: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    cohort_rank = {cohort: index for index, cohort in enumerate(COHORT_ORDER)}
    ordered = sorted(
        companies,
        key=lambda item: (
            cohort_rank[item["primary_cohort"]],
            not bool(item["shared_core"]),
            item["id"],
        ),
    )
    return tuple(str(item["id"]) for item in ordered)
