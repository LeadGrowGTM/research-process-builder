from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

from ._locking import file_lock
from .contracts import canonical_json


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


ROLLOUT_STAGES = (
    "saas_shared_core",
    "remaining_saas",
    "recently_funded_b2b",
    "b2b_agencies",
    "well_known_b2b",
    "b2b_commerce_suppliers",
    "local_b2b_services",
)


class BenchmarkRollout:
    def __init__(
        self,
        companies: Sequence[Mapping[str, Any]],
        *,
        journal: Path | None = None,
    ) -> None:
        self._companies = tuple(companies)
        self._journal = Path(journal) if journal is not None else None
        self._batches = self._build_batches()
        self._completed = self._load_completed()

    @property
    def current_stage(self) -> str | None:
        return (
            ROLLOUT_STAGES[len(self._completed)]
            if len(self._completed) < len(ROLLOUT_STAGES)
            else None
        )

    @property
    def current_company_ids(self) -> tuple[str, ...]:
        stage = self.current_stage
        return () if stage is None else self._batches[stage]

    def complete(self, company_ids: Sequence[str]) -> None:
        stage = self.current_stage
        if stage is None:
            raise ValueError("rollout is already complete")
        supplied = tuple(company_ids)
        expected = self.current_company_ids
        if len(supplied) != len(set(supplied)) or set(supplied) != set(expected):
            raise ValueError(f"completion must match the current rollout stage: {stage}")
        if self._journal is not None:
            lock_path = self._journal.with_suffix(self._journal.suffix + ".lock")
            with file_lock(lock_path):
                completed = self._read_journal()
                if len(completed) != len(self._completed):
                    raise RuntimeError("rollout journal changed; resume before completing")
                self._journal.parent.mkdir(parents=True, exist_ok=True)
                with self._journal.open("a", encoding="utf-8", newline="\n") as stream:
                    stream.write(
                        canonical_json(
                            {"company_ids": sorted(supplied), "stage": stage}
                        )
                        + "\n"
                    )
                    stream.flush()
                    os.fsync(stream.fileno())
        self._completed = (*self._completed, stage)

    def _build_batches(self) -> dict[str, tuple[str, ...]]:
        by_cohort: dict[str, list[Mapping[str, Any]]] = {}
        for company in self._companies:
            by_cohort.setdefault(str(company["primary_cohort"]), []).append(company)
        saas = sorted(by_cohort.get("b2b_saas", ()), key=lambda item: item["id"])
        return {
            "saas_shared_core": tuple(
                str(item["id"]) for item in saas if item["shared_core"]
            ),
            "remaining_saas": tuple(str(item["id"]) for item in saas if not item["shared_core"]),
            **{
                cohort: tuple(
                    str(item["id"])
                    for item in sorted(by_cohort.get(cohort, ()), key=lambda item: item["id"])
                )
                for cohort in ROLLOUT_STAGES[2:]
            },
        }

    def _load_completed(self) -> tuple[str, ...]:
        if self._journal is None:
            return ()
        lock_path = self._journal.with_suffix(self._journal.suffix + ".lock")
        with file_lock(lock_path):
            return self._read_journal()

    def _read_journal(self) -> tuple[str, ...]:
        if self._journal is None or not self._journal.exists():
            return ()
        completed: list[str] = []
        for index, line in enumerate(
            self._journal.read_text(encoding="utf-8").splitlines()
        ):
            try:
                event = json.loads(line)
                stage = event["stage"]
                expected_stage = ROLLOUT_STAGES[index]
                expected_ids = sorted(self._batches[expected_stage])
                if stage != expected_stage or event["company_ids"] != expected_ids:
                    raise ValueError("rollout sequence mismatch")
            except (IndexError, KeyError, TypeError, json.JSONDecodeError, ValueError) as error:
                raise ValueError(f"invalid rollout event on line {index + 1}") from error
            completed.append(stage)
        return tuple(completed)
