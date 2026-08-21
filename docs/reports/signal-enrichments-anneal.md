# Signal enrichments anneal: news/launches and competitors to >= 0.90

**Date:** 2026-08-18
**Branch:** `wt/signal-enrichments` (worktree `.worktrees/signal-enrichments`)
**Starting point:** `docs/reports/signal-enrichments-live-run.md` (news-v5 0.71 / 0.71,
comp-v5 0.68 / 0.67 on gpt-4.1-mini, hard failures on both)
**Result:** both enrichments clear the 0.90 threshold on development and holdout
with no hard failures on **gpt-5.6-luna** (approved flagship). On gpt-4.1-mini
hard failures are gone and holdout clears, but development lands just under
(news 0.887, competitors 0.885). Gate is `human_review_required`; nothing here
is an Approval until Mitch reviews.

**Approved models (Mitch, 2026-08-18): gpt-4.1-mini, gpt-5-nano, gpt-5.6-luna
only.** The full gpt-4.1 tier and the gpt-4o family are never used. Both were
removed from `MODEL_PRICES` and `EXPERIMENT_MODELS` (the ICP experiment
matrix is now 3 models x 2 tracks); the client rejects them before any spend.

**Withdrawn:** five lineages (news-v8-gpt41, news-v10-gpt41, comp-v8-gpt41,
comp-v9-gpt41, comp-v10-gpt41) ran on gpt-4.1 before that policy was stated.
Their artifacts stay under `runs/` for the record, their scores are not
evidence for any decision, and their USD 2.11 is counted below as waste.

| Enrichment | Lineage | Model | Prompt | Dev | Holdout | Hard failures | Cost |
|---|---|---|---|---|---|---|---|
| news-product-launches | news-v11-luna | gpt-5.6-luna | news-v3-kind-rules | **0.974** | **0.997** | none | 0.21 |
| news-product-launches | news-v7 | gpt-4.1-mini | news-v3-kind-rules | 0.887 | 1.000 | none | 0.14 (3 candidates) |
| news-product-launches | news-v11-nano | gpt-5-nano | news-v3-kind-rules | 0.669 | 0.606 | none | 0.01 |
| competitor-intelligence | comp-v11-luna | gpt-5.6-luna | comp-v3-category-sanity | **0.933** | **0.960** | none | 0.29 |
| competitor-intelligence | comp-v9 | gpt-4.1-mini | comp-v3-category-sanity | 0.885 | 0.965 | none | 0.17 (3 candidates) |
| competitor-intelligence | comp-v11-nano | gpt-5-nano | comp-v3-category-sanity | 0.686 | 0.642 | hallucinated_competitor (saas-10) | 0.01 |

Per-company cost: luna about USD 0.02 (news) and 0.03 (competitors); mini
about 0.008; nano about 0.001 but far below the gate. gpt-4.1-mini sits
within noise of the line (run-to-run spread on identical prompts about +-0.03
even at temperature 0: news-v3 0.887 in v7, 0.869 in v9). Recommendation:
ship luna for both; if mini is preferred for cost, add an n-sample median to
the loop first.

## What changed

### Deterministic grounding (code, not model judgment)

`SignalSpec.postprocess` runs on every model payload before it is written,
scored, or shipped. The artifact keeps `model_output` next to the grounded
`output` with a per-case `postprocess` report, so a reviewer can see what was
dropped and why.

News (`ground_news_payload` in `news_evaluator.py`):

- `invalid_event`: a malformed entry (year-only date, event type outside its
  collection, unretained Evidence) is dropped alone instead of failing the case.
- `uncited_date`: the date must be stated by the cited Evidence, written in
  the excerpt or carried in a cited Evidence URL path (`/2026/07/01/`).
- `evergreen_page`: an event supported only by feature, solutions, product,
  platform, pricing, help, docs, or support pages (search-result pages are
  neutral) is dropped; a crawl date on such a page is not an event date.
  Listing pages (author, tag, category) are not evergreen because the collector
  files dated post snippets under them.
- `duplicate_event`: a shared Evidence ID on the same date, or the same cited
  page with headline overlap >= 0.5, is one event.
- An emptied collection is declared unknown.

Competitors (`ground_competitor_payload` in `competitor_evaluator.py`):

- Citations are narrowed to Evidence whose excerpt mentions the competitor;
  when the model cited the wrong item and the name lives elsewhere in the
  dossier, the citation is repaired; an entry no Evidence mentions is dropped
  (`hallucinated_competitor`), as is the subject itself (`self_competitor`).
- A domain the cited Evidence does not literally show is nulled (the v5
  `QPR -> betterworks.com` self-competitor came from a guessed domain).
- The bucket is decided by the Evidence: `named` when a cited excerpt names
  the subject (identity, first word, or domain label) and talks about
  alternatives, competitors, comparison, or "vs", and the page is not a
  community thread; otherwise `inferred`. This is the ground-truth authoring
  rule; the model's bucket is advisory and the report counts `relabeled`.

Effect on the score components: `citation` is 1.0 by construction for
competitors (every shipped entry is grounded or gone), and `uncited_date`,
`hallucinated_competitor`, and `self_competitor` cannot occur on shipped
output. `named_set` recall and precision, news event F1, and `kind` are still
model performance.

### Model client and loop

- `temperature: 0` and a `Subject company: <identity> (<host>)` line for
  field-keyed signal requests only (ICP and legacy request bodies unchanged,
  byte-for-byte test still green). Price table holds only the approved
  models (gpt-4.1-mini, gpt-5-nano, gpt-5.6-luna); the full gpt-4.1 tier was
  briefly added, used, and removed (see Withdrawn), and gpt-4o-mini was
  removed from the price table and the ICP experiment matrix.
- `--candidate <prompt.md>` (repeatable) evaluates extra prompts next to the
  baseline; `--prompt <prompt.md>` replaces the baseline. Candidate id = file
  stem. `SIGNAL_LOOP_MODEL` picks the model.
- Evaluator: `date_is_cited` accepts a cited Evidence URL path date;
  competitor ground-truth matching accepts vendor-prefixed product names when
  the shorter normalized key is >= 5 characters ("Informatica IDMC" against
  "Informatica"; "Zip" never claims "Zapier").

### Prompt candidates (`prompts/company-enrichment/candidates/`)

- news-v2-announcement-only: subject-entity discipline, `Detected date:` is
  page metadata, evergreen pages are never launches, URL-path dates count.
- news-v3-kind-rules: v2 plus acquisitions/partnerships/funding/awards are
  always `news`, launches reported once. Best on mini.
- news-v4-non-events: v3 plus how-to guides and culture pieces are not events,
  post date beats snippet date. Did not beat v3 on mini (saas-02 regressed).
- comp-v2-enumerate-lists: enumerate every in-list vendor as `named`, name as
  written, domain only when shown, one bucket per company.
- comp-v3-category-sanity: v2 plus skip off-category padding from directories
  and company databases. Best on mini.
- comp-v4-directory-guard: v3 plus category-profile pages list neighbors, not
  competitors. Not better than v3.

news-v3-kind-rules and comp-v3-category-sanity are the best candidates on
mini and were the prompts run on nano and luna.

### Ground truth

One correction, recorded in `benchmarks/signals/ground-truth-changelog.md`:
saas-03 news gains the USD 60M Series B (2026-07-01, globenewswire.com). The
excerpt says "1 month ago"; the cited URL path carries the date. The authoring
rule ("date literally in the excerpt") missed it; the evaluator now agrees with
the corrected rule. Every other unmatched model event in the audit was a model
error (evergreen pages, snippet dates, Apriori Bio for aPriori), not a gap.

Competitor ground truth is judgment-tight on saas-04 (aPriori): CB Insights
lists Assent, Cosmo Tech, Eugenie AI, Fero Labs, FactoryMind as competitors and
the author kept only the costing tools. Both models still return the full CB
Insights list, so saas-04 sits at 0.75 to 0.78 on every lineage. Left as
authored; a reviewer may decide the rule ("analyst list about the subject =
named") should win.

## Lineage history this session

| Lineage | Model | Candidates | Dev best | Holdout | Cost | Note |
|---|---|---|---|---|---|---|
| news-v6 | mini | baseline, v2 | 0.842 (v2) | 0.965 | 0.104 | first grounding, hard failures gone |
| news-v7 | mini | + v3 | 0.887 (v3) | 1.000 | 0.143 | |
| news-v8-gpt41 | gpt-4.1 (withdrawn) | baseline, v3 | 0.739 stored | 0.980 | 0.551 | disallowed model; saas-04 year-only date exposed the `invalid_event` gap |
| news-v9 | mini | v3, v4 | 0.869 (v3) | 0.962 | 0.144 | mini variance |
| news-v10-gpt41 | gpt-4.1 (withdrawn) | baseline | 0.924 | 0.989 | 0.303 | disallowed model |
| comp-v6 | mini | baseline, v2 | 0.792 (v2) | 0.960 | 0.115 | before Evidence-decided buckets |
| comp-v7 | mini | + v3 | 0.859 (v2) | 0.928 | 0.156 | |
| comp-v8-gpt41 | gpt-4.1 (withdrawn) | baseline, v2, v3 | 0.891 (baseline) | cap-blocked | 0.515 | disallowed model; cap halted the third candidate |
| comp-v9 | mini | v3, v4 | 0.885 (v3) | 0.965 | 0.165 | |
| comp-v9-gpt41 | gpt-4.1 (withdrawn) | baseline | 0.899 | 0.967 | 0.322 | disallowed model |
| comp-v10-gpt41 | gpt-4.1 (withdrawn) | v3 only | 0.902 | 0.950 | 0.419 | disallowed model |
| news-v11-nano | gpt-5-nano | v3 only | 0.669 | 0.606 | 0.008 | far below gate |
| comp-v11-nano | gpt-5-nano | v3 only | 0.686 | 0.642 | 0.010 | far below gate; one hallucinated_competitor |
| news-v11-luna | gpt-5.6-luna | v3 only | **0.974** | **0.997** | 0.208 | final code, approved model |
| comp-v11-luna | gpt-5.6-luna | v3 only | **0.933** | **0.960** | 0.292 | final code, approved model |

Total LLM spend this session USD 3.46 (15 lineages): USD 2.11 on the five
withdrawn gpt-4.1 lineages, 0.83 on six gpt-4.1-mini lineages, 0.02 on two
gpt-5-nano lineages, 0.50 on two gpt-5.6-luna lineages. Every lineage stayed under the USD 1.00 cap (comp-v8 halted at it).
No source purchases, no network collection.

## Residual losses (for the reviewer)

- news saas-02 AgilePoint: the SharePoint launch appears in the dossier under
  a search-snippet date (Sep 18, 2025) and the post's own date (Apr 25, 2026);
  models pick either or both. Ground truth has Apr 25, 2026.
- news saas-05: two same-week events (Microsoft collaboration, Gartner MQ)
  with swapped citations; scored on `citation` only.
- news saas-01/07: a how-to blog post and a benefits/culture article reported
  as events on some runs.
- competitors saas-04: directory padding (see above); saas-02 6sense
  market-share list (Airflow, Control-M).

## How to reproduce

```powershell
# from C:\Users\mitch\Everything_CC\pipelines\gtm-orchestrator (prod secrets)
$env:SIGNAL_LOOP_MODEL='gpt-5.6-luna'
lg run --env prod py <worktree>\scripts\company_enrichment_news_loop.py --evaluate --lineage news-v12 --allow-paid --prompt prompts/company-enrichment/candidates/news-product-launches/news-v3-kind-rules.md
lg run --env prod py <worktree>\scripts\company_enrichment_competitor_loop.py --evaluate --lineage comp-v12 --allow-paid --prompt prompts/company-enrichment/candidates/competitor-intelligence/comp-v3-category-sanity.md
```

## Next steps

1. Human review of news-v11-luna and comp-v11-luna outputs plus the
   `postprocess` reports (what grounding dropped), then approve / revise.
   A review sheet like the ads one can be generated on request.
2. If mini is wanted for cost, add an n-sample median to the loop and rerun
   news-v3 / comp-v3 on gpt-4.1-mini before relying on it.
3. When the prompts graduate, fold the winning candidate into
   `prompts/company-enrichment/<enrichment>.md` and retire the candidate
   files (kept for now so lineage prompt hashes stay reproducible).

## Addendum 2026-08-20: repricing and the gpt-4o-mini benchmark

**Repricing.** gpt-5.6-luna list rates dropped to 0.20 in / 0.02 cache read /
0.25 cache write / 1.20 out per 1M tokens (Mitch, 2026-08-20). `MODEL_PRICES`
was updated and the client now bills cache-write tokens at the model's write
rate on the synchronous track (96% of these runs' input tokens were cache
writes). Costs recorded above for luna lineages were computed at the stale
1.00/6.00 table and overstate spend ~4.3x; repriced from the run ledgers:

| Lineage | Recorded | At current rates | Per company |
|---|---|---|---|
| news-v11-luna | 0.208 | 0.048 | ~0.005 |
| comp-v11-luna | 0.292 | 0.065 | ~0.006 |

At current rates luna is cheaper per token than gpt-4.1-mini and remains the
only model clearing the gate.

**gpt-4o-mini benchmark (Mitch, 2026-08-20).** gpt-4o-mini was priced
(0.15/0.075/0.60) and run on the frozen v3 prompts over the same dossiers;
it is now a standing benchmark model (the cheap-tier floor of the experiment
matrix), while the rest of the gpt-4o family stays banned:

| Lineage | Model | Dev | Holdout | Hard failures | Cost |
|---|---|---|---|---|---|
| news-v12-4omini | gpt-4o-mini | 0.779 | 0.764 | none | 0.024 |
| comp-v12-4omini | gpt-4o-mini | 0.804 | 0.879 | none | 0.025 |

Well below the 0.90 gate on both enrichments (news drops to 0.35-0.38 on two
companies). No hard failures, but not usable for production. It stays priced
in `MODEL_PRICES` and sits in `EXPERIMENT_MODELS` for benchmarking only
(matrix now 4 models x 2 tracks); it is not approved for production runs.

**Cost per 1k companies, news + competitors combined, at current rates:**
luna ~USD 11 synchronous / ~6 batch; gpt-4.1-mini ~17 (below gate);
gpt-4o-mini ~5 (far below gate). Luna recommendation unchanged and now also
the near-cheapest option.
