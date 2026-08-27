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
- `scripts/company_enrichment/` is the company-enrichment and signal (news,
  competitor, ICP/persona) collect/evaluate/anneal pipeline: provider adapters
  under `adapters/` (Serper, Parallel search, the Parallel Task API client,
  ads platforms), the budget ledger (`budgets.py`), and the CLI (`cli.py`).
  Thin root-level wrappers (`scripts/company_enrichment_cli.py`,
  `scripts/company_enrichment_news_loop.py`,
  `scripts/company_enrichment_competitor_loop.py`,
  `scripts/company_enrichment_ads_loop.py`,
  `scripts/company_enrichment_icp_loop.py`) are the entrypoints. Prompts live
  in `prompts/company-enrichment/`; benchmark corpora and ground truth live in
  `benchmarks/` (the sealed Serper corpus in `benchmarks/signals/` is
  immutable - see [docs/reports/serper-vs-parallel.md](docs/reports/serper-vs-parallel.md)
  for the Serper-vs-Parallel provider decision and
  [docs/reports/signal-enrichments-anneal.md](docs/reports/signal-enrichments-anneal.md)
  for the approved-model policy; ICP/persona gate and approval are recorded in
  [docs/reports/icp-persona-anneal.md](docs/reports/icp-persona-anneal.md)).
  `scripts/company_enrichment_buying_trigger_loop.py` is a separate,
  human-judged loop (no ground-truth scorer) over cached dossiers in
  `benchmarks/dossiers/`; see
  [docs/reports/buying-trigger-anneal.md](docs/reports/buying-trigger-anneal.md).
  `scripts/company_enrichment_page_signals.py` is a deterministic, free
  HTTP check (no model) for careers/blog page presence per company domain;
  per-model outcomes across all enrichments are summarized in
  [docs/benchmarks/model-outcomes.md](docs/benchmarks/model-outcomes.md).
  `scripts/company_enrichment_description_growth_eval.py` scores stored
  company-description / growth-signals outputs offline against the dated
  observable ground truth in `benchmarks/description-growth/`; see
  [docs/reports/description-growth-gt.md](docs/reports/description-growth-gt.md).

## Verified local commands

Run these from the repository root on Windows. They parse arguments and do not
run a research job or write a remote service:

```powershell
py scripts/pattern_tester.py --help
py scripts/gt_evaluator.py --help
py scripts/validate.py --help
py scripts/autoresearch.py --help
py scripts/company_enrichment_cli.py --help
py scripts/company_enrichment_news_loop.py --help
py scripts/company_enrichment_competitor_loop.py --help
py scripts/company_enrichment_icp_loop.py --help
py scripts/company_enrichment_buying_trigger_loop.py --help
py scripts/company_enrichment_page_signals.py --help
py scripts/company_enrichment_description_growth_eval.py --help
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
Flow or scheduled change. A track with no ground-truth scorer is the
documented exception - Approval then rests on recorded human review alone; see
[CONTEXT.md](CONTEXT.md) and [docs/reports/buying-trigger-anneal.md](docs/reports/buying-trigger-anneal.md).

## Prompt improvement review loop

Use [references/prompt-annealing.md](references/prompt-annealing.md) as the
canonical prompt-building and annealing procedure.

Prompt work is an output-review loop, not an infrastructure project. Before the
first run, state the exact goal output and show its human-readable shape.

For every iteration:

1. Change the prompt for one explicit hypothesis.
2. Run the same fixed development examples using only their normal inputs and
   Evidence. Ground-truth answers remain local to the scorer and are never sent
   to the model as prompt context.
3. Show the human reviewer the exact prompt change and every resulting output,
   rendered in the goal-output format. Do not report only an aggregate score.
4. Show the exact requested and resolved model, prompt word/character count,
   secondary outputs, omissions, per-example misses, score delta, and cost.
5. Pause for reviewer feedback. Convert that feedback into a general prompt
   rule, then run the next iteration.

Keep the holdout sealed while choosing prompt variants. Use it only after a
development winner is selected. An iteration is not complete until the reviewer
has seen the actual outputs.

Prefer conversational buyer language at about an eighth-grade reading level.
Remove jargon, product-category slogans, acronyms, and abstract phrases unless
they are necessary to preserve meaning and are used by the intended buyer. If
the Evidence names the target buyer archetype, reuse that label rather than
inventing a synonym. A useful check is: "Would the buyer naturally say this?"
If not, rewrite it in short, concrete words.
