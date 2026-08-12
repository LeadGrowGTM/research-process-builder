# research-process-builder operator guide

This repository develops and evaluates repeatable web-research workflows. Its
canonical domain language is [CONTEXT.md](CONTEXT.md): use **Research Flow**,
**Search Flow**, **Site Extraction Flow**, **Source Adapter**, **Experiment**,
**Evidence**, and **Approval** as defined there.

## Layout and boundaries

- `scripts/` contains local CLIs. `pattern_tester.py` expands and scores search
  patterns; `gt_evaluator.py` deterministically evaluates stored results against
  `ground-truth/`; `validate.py` compares automated scores with ground truth;
  `autoresearch.py` records and compares baseline snapshots.
- `processes/` and `prompts/` hold reusable research instructions. `searches/`,
  `ground-truth/`, and `baselines/` contain the checked-in evidence and baseline
  inputs used by the local evaluators.
- `docs/domain/adr/0003-resumable-autoresearch-orchestration.md` records the
  planned orchestration seam. `trigger/` is the separately documented scheduled
  pipeline; see [trigger/README.md](trigger/README.md).

## Verified local commands

Run these from the repository root on Windows. They parse arguments and do not
run a research job or write a remote service:

```powershell
py scripts/pattern_tester.py --help
py scripts/gt_evaluator.py --help
py scripts/validate.py --help
py scripts/autoresearch.py --help
py -m pytest tests/test_repository_policy.py -q
```

The repository has no package manifest. These four help commands require Python;
`pattern_tester.py` imports `python-dotenv` and only loads the optional
`serper_search` adapter when it actually runs queries. Configure that adapter
through `SHARED_SCRIPTS_PATH` only when executing a query run. Other scripts may
have their own API dependencies; inspect their imports and `--help` before use.

## Safety and artifacts

Never commit `.env*`, API keys, or other secrets. Do not run a networked CLI,
write Supabase, or promote an artifact without an explicit approved task. The
default pattern-tester output is the tracked historical fixture
`searches/raw-results.json`; use `--output output/<name>.json` for a new local
run. `output/`, `runs/`, caches, `.worktrees/`, and `.quarantine/` are ignored.

Recovery evidence is retained in
`docs/recovery/repo-cleanup-full-update/inventory.csv` and
`docs/recovery/repo-cleanup-full-update/quarantine-map.csv`. Do not bulk-restore
the quarantined campaign or generated payloads, and never apply, pop, or drop a
recovery stash or ref. Restore a reusable item only after review and record its
object ID, hash evidence, destination, and decision in the recovery manifest.

## Validation and approval

An Experiment becomes an Approval only after programmed ground-truth validation
at **>= 90%**, followed by explicit human review. Treat the programmed result as
evidence, not automatic promotion: a reviewer must check source attribution,
scope, safety, and intended destination before approving a reusable Research
Flow or scheduled change.
