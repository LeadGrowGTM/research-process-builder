# Progress

## 2026-08-10 — Recovery inventory preservation

- RED: `py -m pytest tests/test_recovery_inventory.py -q` failed because `scripts.recovery_inventory` did not exist.
- GREEN: `py -m pytest tests/test_recovery_inventory.py -q` passed (`7 passed`).
- Generated and verified: `enumerated=3561 recorded=3558 difference=+3 unexplained=0`.
- Phase inventory commit: `88be1585c55af61411490783a327f50c81f1ba8f` (`chore: preserve preflight repository inventory`).
- Full suite remains blocked during collection by the pre-existing missing `serper_search` import from `scripts/pipeline_base.py`.
## 2026-08-10 - Canonical resumable-autoresearch domain

- RED: `py -m pytest tests/test_domain_contract_docs.py -q` failed as expected because root `CONTEXT.md` and ADR 0003 were absent (`3 failed`).
- GREEN: `py -m pytest tests/test_domain_contract_docs.py -q` passed (`3 passed`).
- Added the canonical domain terms and ADR 0003; reviewed with `git diff --check`.
- Phase commit: `def8588ae8ed9c3755f3b785d0620485c2301da2` (`docs: define resumable autoresearch domain`).
- The repository pre-commit hook remains blocked by its existing `tests/test_recovery_inventory.py` collection error: `ModuleNotFoundError: No module named 'scripts'`.

## 2026-08-10 — Repository policy and verified guidance

- RED: `py -m pytest tests/test_repository_policy.py -q` failed as expected: `pattern_tester.py --help` eagerly required the optional `serper_search` adapter, and `CLAUDE.md` lacked the canonical-context/approval lifecycle.
- GREEN: `py -m pytest tests/test_repository_policy.py -q` passed (`5 passed`).
- Verified help exits zero: `py scripts/pattern_tester.py --help`, `py scripts/gt_evaluator.py --help`, `py scripts/validate.py --help`, and `py scripts/autoresearch.py --help`.
- Added ignore coverage for local Python/tool caches and `runs/`; documented safe local commands, artifact boundaries, recovery handling, and the programmed >=90% then explicit-human-review lifecycle in `CLAUDE.md`.
- Recovery decision: no restoration and no removal. The manifest records the immutable recovery commit and existing inventory/quarantine evidence; no current-tree candidate had an explicit removal disposition.