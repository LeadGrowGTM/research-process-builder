# Independent Prover Evidence

Date: 2026-08-10  
Worktree: `C:/Users/mitch/Everything_CC/tools/data/research-process-builder/.worktrees/repo-cleanup-full-update`  
Constraint: deterministic local execution only; no live network, authentication, remote mutation, or paid provider call was attempted.

## Repository and preservation

Command:

```powershell
git rev-parse --show-toplevel
git branch --show-current
git status --short
git show-ref refs/recovery/repo-cleanup-full-update/initial-dirty refs/recovery/repo-cleanup-full-update/dashboard-metadata
git stash list
py scripts/recovery_inventory.py verify --manifest docs/recovery/repo-cleanup-full-update/inventory.csv --expected-recorded-count 3558 --expected-ref refs/recovery/repo-cleanup-full-update/initial-dirty --quarantine-map docs/recovery/repo-cleanup-full-update/quarantine-map.csv --quarantine-root .quarantine/repo-cleanup-full-update
```

Exit: `0`. Relevant output:

```text
C:/Users/mitch/Everything_CC/tools/data/research-process-builder/.worktrees/repo-cleanup-full-update
wt/repo-cleanup-full-update
7bca5038f31a9427f1781e823a388d0dcf2ac33d refs/recovery/repo-cleanup-full-update/dashboard-metadata
e3932d55217c29ac28eca16fdc7e6f6c5c3e3337 refs/recovery/repo-cleanup-full-update/initial-dirty
stash@{0}: On chore/repo-cleanup-preflight: pre-goal-dashboard-metadata-2026-08-10
stash@{1}: On chore/repo-cleanup-preflight: pre-goal-repo-cleanup-2026-08-10
enumerated=3561 recorded=3558 difference=+3 unexplained=0
```

The initial status was empty. Recovery evidence is tracked at `docs/recovery/repo-cleanup-full-update/inventory.csv`, `manifest.md`, `quarantine-map.csv`, and `action-decisions.csv`. `git check-ignore -v` confirmed `.quarantine/` and `.artifacts/` are ignored. Inventory verification includes quarantine payload hashes and recovery-object checks.

## Automated verification

Commands and results:

```text
py -m pytest tests/test_recovery_inventory.py tests/test_domain_contract_docs.py tests/test_repository_policy.py tests/test_mcp_configuration.py -q
exit 0: 39 passed in 9.38s

py -m pytest tests/test_autoresearch_contracts.py tests/test_autoresearch_gate.py tests/test_autoresearch_artifacts.py tests/test_source_adapters.py tests/test_autoresearch_orchestrator.py tests/test_autoresearch_clis.py scripts/test_autoresearch_agent.py -q
exit 0: 163 passed in 8.13s

py -m pytest -q
exit 0: 242 passed in 15.14s
```

No skip or xfail was reported. The focused cases provide automated evidence for fresh bounded role contexts; both pre-execution checker rejections; accepted execution and independent evaluation; exact `advance`, `retry`, `rollback`, and `halt_for_review` Gate actions; retry exhaustion; runner failures; pre-run budget denial; persisted rollback/retry; corruption, incompatible version, reordering, truncation, and tamper rejection; process-restart resume; known-URL extraction; deterministic fetch/scrape, selector, regex, pattern, optional-LLM ordering; and a read-only Source Adapter surface. Representative cases are `test_roles_receive_fresh_exact_context_and_threshold_halts_for_review`, `test_rejected_checker_stops_before_executor`, `test_duplicate_candidate_stops_before_executor`, `test_evaluator_receives_the_exact_executor_evidence`, `test_gate_emits_each_safe_deterministic_action`, `test_exhausted_runner_failure_uses_gate_and_finishes`, `test_declared_role_charge_exhaustion_blocks_before_runner`, `test_retry_count_and_fresh_cycle_survive_new_orchestrator_process`, artifact tamper/version tests, and the source-adapter contract tests.

## CLI exercises

Both `py scripts/autoresearch_agent.py --help` and `py scripts/autocontext_runner.py --help` exited `0`. Both CLIs rejected `--definitely-invalid` with exit `2`. With no arguments, both exited `0` and printed:

```json
{"mode": "dry_run", "paid_cost_ceiling": 0.0}
```

Primary proof artifacts: `.artifacts/autoresearch/prover-20260810-primary/`. Primary stub and resume both exited `0`; `journal.jsonl` remained 5 lines and SHA-256 `A60C48022E20CEC191866D105EFEB53CE7D38C31B50D23A5E084EFF50A83BF6D` before and after resume.

Compatibility proof artifacts: `.artifacts/autoresearch/prover-20260810-compat/`. Compatibility stub and resume both exited `0`; `journal.jsonl` remained 5 lines and SHA-256 `FD17B397CDF5B04890760FC44807282951A46370026499F42C4EE160E2D430CC` before and after resume.

Both summaries reported one completed cycle, `halt_for_review`, `human_review_required`, and schema `1.0`. Unchanged journals prove zero duplicate persisted executions/reservations/charges on completed resume.

## Configuration, validation, and failure

Parsing `.codex/config.toml` with Python `tomllib` exited `0`. It contains the repository-scoped `parallel-search` OAuth endpoint, only `web_search`/`web_fetch`, and prompt approval; it contains no auth secret. The deterministic no-auth contract is covered by `test_missing_oauth_is_locally_actionable_without_a_remote_connection`; live OAuth was intentionally not attempted.

`py scripts/validate.py --summary` exited `0` and reported 35 companies with ground truth, 37 schema categories, and 11 categories with GT data. `git diff --check` exited `0`.

Required credential command:

```powershell
py scripts/credential_scan.py
```

Exit: `1`. Output:

```text
tests/test_credential_scan.py:16: literal value assigned to password
CREDENTIAL_SCAN=FAIL violations=1
```

The initial scan failure was caused by a tracked synthetic negative-test fixture, not a real credential. Commit `5f63102` preserves the negative test while constructing its fake value at runtime; the fresh fix round below proves the authoritative scan now passes without weakening detection.

## Fix round — credential fixture

- Inspected commit `5f63102`: only `tests/test_credential_scan.py` changed; runtime concatenation preserves the negative detection test without storing a scanner-matching literal.
- `py scripts/credential_scan.py` → exit `0`, `CREDENTIAL_SCAN=PASS violations=0 allowed_placeholders=2`.
- `py -m pytest tests/test_credential_scan.py -q` → `2 passed`.
- `py -m pytest -q` → `242 passed`, zero skips shown.
- `git diff --check` → exit `0`; status contained only this Prover evidence file.

The sole prior blocker is resolved.

## Limitations

The normal Windows sandbox process launcher failed with `CreateProcessWithLogonW failed: 2`; commands were rerun through approved PowerShell execution in the assigned worktree. Network/authentication verification was intentionally replaced with deterministic doubles under the zero-spend/no-remote-call constraint.

PROOF VERDICT: PASS
