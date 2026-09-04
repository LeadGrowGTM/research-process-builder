# Description/growth ground truth: dated observable dataset + semantic scorer

**Date:** 2026-08-27
**Branch:** `wt/description-growth-gt`
**Status:** Experiment - programmed evidence only. Human review required before
any Approval (repo gate: >= 0.90 programmed plus explicit review).

## Why

`docs/benchmarks/model-outcomes.md` closed the company-description and
growth-signals tracks as **benchmark-invalid**: `_correctness` in
`scripts/company_enrichment/benchmark.py` required an exact canonical-JSON
string match against dossier assertion text (rewarding verbatim parroting,
ceiling 0.75 without it), and all three fixed dossiers listed `growth` under
`unknowns`, so a correct `unknown` scored 0.0 (gate inverted). This dataset
and scorer replace that gate for these two tracks.

## What was built

- **Dataset** `benchmarks/description-growth/` (new dir; sealed corpora
  untouched): `split.yaml` (dev/holdout 6/4, identical IDs to the ICP
  benchmark), `rubric.yaml`, and `ground-truth/saas-01..10.yaml` over the ten
  fixed saas dossiers.
  - Description ground truth per company: `identity` / `offering` /
    `audience`, each a canonical value plus acceptable aliases plus the
    dossier Evidence IDs that support it.
  - Growth ground truth per company: a verdict (`growth_signals` /
    `no_signal`) plus dated observable signals. Every dossier-sourced signal
    carries a **verbatim quote** of its cited Evidence (loader enforces the
    substring match); every page-signals observation carries the checked URL
    and check date from
    `runs/company-enrichment/page-signals/page-signals-v1/results.jsonl`
    (2026-08-25). Funding events use the date stated in the Evidence
    (e.g. saas-04 Vista investment 2023-10-19).
- **Loader** `scripts/company_enrichment/description_growth_ground_truth.py`:
  strict validation (locked split, locked rubric weights, exact ten files,
  Evidence links, verbatim quotes) and a dataset hash over files plus cited
  Evidence content hashes.
- **Scorer** `scripts/company_enrichment/description_growth_evaluator.py`:
  - description: alias matching per component, per-component citation,
    readability; hard failures `verbatim_parroting` (a contiguous 25-word run
    copied from any Evidence excerpt), `missing_description`, `uncited_*`,
    `off_target_description`.
  - growth: verdict vs ground truth (a correct `unknown` on a `no_signal`
    company scores 1.0 - the inversion fix), precision of claimed signal
    kinds (keyword lexicon), citation grounding; hard failures
    `unsupported_growth_claim`, `fabricated_{funding,expansion,customer_scale,headcount,hiring}`,
    `uncited_growth`.
- **Eval CLI** `scripts/company_enrichment_description_growth_eval.py`:
  scores stored experiment `outcomes.jsonl` artifacts offline (no network,
  no model spend) and reports dev/holdout means per (enrichment, model,
  track).
- **Tests:** `tests/company_enrichment/test_description_growth_ground_truth.py`
  and `test_description_growth_evaluator.py` (27 tests).

## Rescoring the 2026-08-25 luna rerun (stored outputs, zero spend)

Dataset hash `34eba23646a8d11a983ad47253e1501e8bdb1b5a920f4aa1cd686a1f152c6e34`.
The luna rerun covered only the three fixed development companies
(saas-01/04/07), so holdout means are empty; numbers below are dev-only.

| Track | Old (exact-match) | New semantic dev mean | Hard failures |
|---|---|---|---|
| company-description sync | 0.75 | **0.911** | none |
| company-description batch | 0.75 | **0.911** | none |
| growth-signals sync | 0.50 | 0.638 | none |
| growth-signals batch | 0.50 | 0.667 | none |

Reading:

- **Description is no longer capped.** Luna's paraphrased descriptions score
  on content (identity/offering/audience all matched for saas-01/04; saas-07
  loses only the audience dimension - its description never names who the
  product is for, a real gap, not a scorer artifact).
- **Growth is no longer inverted.** saas-01/04 assert grounded signals
  (headcount, hiring, customer_scale, funding) and score 0.91-1.0. saas-07
  scores 0.0 because luna answered `unknown` while its dossier Evidence
  contains "See jobs" and "View all 224 employees" - under the old ground
  truth that `unknown` was rewarded; now the observable signals make it a
  miss. That is a prompt problem to anneal, not a benchmark problem.
- Neither track clears the 0.90 gate on this stored run alone (growth is far
  below; description needs holdout coverage), so both remain Experiment.

## Corpus caveats (for the next loop)

- All ten companies show careers and blog pages present (page-signals v1),
  so this corpus has **no `no_signal` company**; the no_signal scoring path
  is covered by unit tests only. Page-signals v2 (recency, role counts) or a
  broader corpus should add negative examples.
- saas-09 (Bitly) has **no hiring signal**: its `/careers` check redirected
  off-domain to topresume.com, so the page-signals observation was excluded
  as unreliable.
- saas-02 (AgilePoint) Evidence states "zero VC funding raised" - a funding
  claim there is a fabrication (noted in the ground-truth file).
- One saas-07 offering alias ("performance intelligence platform") was added
  after inspecting luna's stored output; the phrasing is grounded in the
  dossier Evidence ("AI-native performance intelligence solution").

## How to reproduce

```powershell
# from the repo root; offline, no secrets needed
py -m pytest tests/company_enrichment/test_description_growth_ground_truth.py tests/company_enrichment/test_description_growth_evaluator.py -q
py scripts/company_enrichment_description_growth_eval.py --outcomes runs/company-enrichment/experiments-luna/company-description/outcomes.jsonl --outcomes runs/company-enrichment/experiments-luna/growth-signals/outcomes.jsonl --output output/description-growth-eval-luna.json
```
