from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest

from scripts.company_enrichment.budgets import BudgetExhausted, BudgetLedger


def test_budget_reserves_before_spend_and_reconciles_actual_cost(tmp_path) -> None:
    ledger = BudgetLedger(tmp_path / "costs.jsonl", {"corpus-build": "2.00"})

    reservation = ledger.reserve("corpus-build", "firecrawl:acme", "0.50")
    assert ledger.reserved("corpus-build") == Decimal("0.50")
    assert ledger.spent("corpus-build") == Decimal("0")

    ledger.reconcile(reservation, "0.18")
    assert ledger.reserved("corpus-build") == Decimal("0")
    assert ledger.spent("corpus-build") == Decimal("0.18")


def test_budget_reuses_idempotency_key_without_double_reserving(tmp_path) -> None:
    ledger = BudgetLedger(tmp_path / "costs.jsonl", {"experiment:description": "1.00"})
    first = ledger.reserve("experiment:description", "same-run", "0.40")
    second = ledger.reserve("experiment:description", "same-run", "0.40")

    assert first == second
    assert ledger.reserved("experiment:description") == Decimal("0.40")


def test_budget_denies_work_that_would_cross_aggregate_cap(tmp_path) -> None:
    ledger = BudgetLedger(tmp_path / "costs.jsonl", {"corpus-build": "2.00"})
    ledger.reserve("corpus-build", "one", "1.80")

    with pytest.raises(BudgetExhausted):
        ledger.reserve("corpus-build", "two", "0.21")


def test_parallel_reservations_never_cross_cap(tmp_path) -> None:
    ledger = BudgetLedger(tmp_path / "costs.jsonl", {"corpus-build": "2.00"})

    def attempt(index: int) -> bool:
        try:
            ledger.reserve("corpus-build", f"run-{index}", "0.30")
            return True
        except BudgetExhausted:
            return False

    with ThreadPoolExecutor(max_workers=10) as executor:
        accepted = list(executor.map(attempt, range(10)))

    assert sum(accepted) == 6
    assert ledger.reserved("corpus-build") == Decimal("1.80")
    assert ledger.reserved("corpus-build") <= Decimal("2.00")
