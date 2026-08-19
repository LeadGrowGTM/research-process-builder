# Serper vs Parallel A/B - news and competitor signal enrichments

- **Date:** 2026-08-19
- **Branch:** `wt/signal-enrichments`
- **Model:** `gpt-5.6-luna` (approved set), winning anneal prompts held fixed:
  news `news-v3-kind-rules`, competitors `comp-v3-category-sanity`
- **Ground truth:** the sealed corpus (`benchmarks/signals/<enrichment>/ground-truth/`),
  untouched. Parallel dossiers were collected into
  `benchmarks/signals-parallel/<enrichment>/` via `--benchmark-dir`; the sealed
  Serper corpus was never overwritten.
- **Citation component excluded cross-collector by construction:** ground-truth
  Evidence IDs are content-addressed to the sealed Serper collection, so a new
  collection can never contain them (`check_citations=False` wiring, commit
  `142e25d`). Citation numbers below are reported but not comparable; the A/B
  metrics are `events` (news F1) and `named_set` (competitor F1).

## Setup

| | Serper | Parallel |
|---|---|---|
| API | `google.serper.dev` `/search`, `/news` | `api.parallel.ai` `/v1/search`, mode `advanced` |
| Queries per company | 3 (same `SearchPlan`) | 3 (same `SearchPlan`) |
| Freshness | `tbs` windows | `tbs` mapped to `source_policy.after_date` |
| Cost per query | $0.001 | $0.005 (advanced; turbo/fast is $0.001) |
| Collection cost per enrichment (10 companies) | $0.030 | $0.150 |
| Lineages | `news-v11-luna`, `comp-v11-luna` | `news-v12-parallel`, `comp-v12-parallel` |

Collected 2026-08-19 with `--search-provider parallel`; per-company collection
logs sit beside each dossier (`*.collection.json`, all `provider: parallel`,
zero search failures, 8-10 results per query).

## Results - component means

### News (`events` F1 is the A/B metric)

| Split | events (Parallel) | events (Serper) | kind (P) | kind (S) | mean (P) | mean (S) |
|---|---|---|---|---|---|---|
| dev | **0.306** | **0.976** | 0.833 | 1.000 | 0.317 | 0.974 |
| holdout | **0.516** | **1.000** | 0.958 | 0.981 | 0.513 | 0.997 |

### Competitors (`named_set` F1 is the A/B metric)

| Split | named_set (P) | named_set (S) | labeling (P) | labeling (S) | mean (P) | mean (S) |
|---|---|---|---|---|---|---|
| dev | **0.272** | **0.878** | 0.490 | 0.967 | 0.384 | 0.933 |
| holdout | **0.546** | **0.943** | 0.920 | 0.940 | 0.757 | 0.960 |

### Per-company A/B metric

| Company | news events P / S | comp named_set P / S |
|---|---|---|
| saas-01 (dev) | 0.000 / 1.000 | 0.667 / 0.947 |
| saas-02 (dev) | 0.000 / 1.000 | 0.000* / 0.846 |
| saas-03 (holdout) | 0.667 / 1.000 | 0.839 / 0.970 |
| saas-04 (dev) | 0.571 / 1.000 | 0.421 / 0.710 |
| saas-05 (dev) | 0.400 / 0.933 | 0.000* / 0.941 |
| saas-06 (holdout) | 0.476 / 1.000 | 0.605 / 0.936 |
| saas-07 (dev) | 0.667 / 0.923 | 0.000* / 0.898 |
| saas-08 (holdout) | 0.364 / 1.000 | 0.591 / 0.944 |
| saas-09 (dev) | 0.200 / 1.000 | 0.542 / 0.927 |
| saas-10 (holdout) | 0.556 / 1.000 | 0.150 / 0.923 |

\* `invalid_output` hard failure: luna returned invalid structured JSON on
these three Parallel-corpus dev cases (`provider returned invalid structured
JSON`); scored 0 by contract. Zero hard failures on the Serper corpus. The
Parallel dossiers are ~10% larger (375/379 Evidence items vs 340/363), and the
evidence-ID enum in the output contract grows with them.

## Why Parallel lost

1. **Recall on required recent events.** The news matcher requires date +
   source-domain (or shared Evidence). Example saas-01: the Parallel corpus
   surfaced and the model reported the 2023-12-12 prnewswire.com launch - but
   that ground-truth event is outside the 365-day window (optional), while the
   one required in-window event (2026-03-09, `updates.agencyanalytics.com`
   changelog) never appeared in Parallel's results. Recall 0 makes F1 0 even
   with correct precision. Serper's Google index reliably carries the niche
   first-party changelog/newsroom items and dated wire coverage the ground
   truth demands.
2. **Competitor sets were thinner.** `named_set` recall dropped on every
   company; Parallel's natural-language search returns fewer of the
   "X alternatives / X vs Y" listicle and comparison pages that the competitor
   plan mines.
3. **Structured-output stress.** 3/10 competitor companies failed the JSON
   contract outright on the Parallel corpus (0 on Serper). Retrieval aside,
   the larger noisier excerpts cost reliability on luna.
4. Dates themselves were not the problem: Parallel results carry
   `publish_date`, folded into the same `Date:` excerpt label the evaluators
   read.

## Cost

| | Serper | Parallel |
|---|---|---|
| Collection (both enrichments) | $0.060 | $0.300 |
| Eval spend (luna, both) | $0.500 | $1.040 |
| Cost per A/B point (collection, news dev events) | $0.031/pt | $0.490/pt |

Parallel is 5x the per-query price at `advanced` and scored roughly a third of
Serper's F1 on both signals. All runs stayed under the $1.00 per-lineage cap.

## Round 2 - objective steering (2026-08-19, same day)

Round 1 used Parallel as a SERP clone. Round 2 used it as designed (commit
`daf60b3`): each plan carries a natural-language `objective` (news: dated
first-party changelog/newsroom and wire coverage; competitors: alternatives
and comparison pages) forwarded as `SearchRequest.objective`, plus a
1200-char per-result excerpt cap to relieve the structured-output stress.
Same prompts, model, queries, and cost. Corpora:
`benchmarks/signals-parallel-v2/`; lineages `news-v13-parallel`,
`comp-v13-parallel`.

| A/B metric | Round 1 | Round 2 | Serper |
|---|---|---|---|
| news events dev | 0.306 | 0.246 | **0.976** |
| news events holdout | 0.516 | 0.604 | **1.000** |
| comp named_set dev | 0.272 | 0.516 | **0.878** |
| comp named_set holdout | 0.546 | 0.478 | **0.943** |
| hard failures (comp dev) | 3 | **0** | 0 |

What round 2 proved:

- The **excerpt cap fixed reliability**: all three `invalid_output` failures
  disappeared, and competitor labeling recovered to 0.948 dev / 0.875
  holdout. Worth keeping regardless of provider choice.
- Objective steering roughly **doubled competitor dev recall** (0.272 to
  0.516; part of that is the hard-failure fix) but moved news within noise
  (dev down, holdout up). The gap to Serper is retrieval coverage, not
  prompt-side steering: Parallel's index simply does not surface the niche
  in-window first-party changelog items and comparison listicles the ground
  truth demands, however the request is phrased.

## Verdict

- **Keep Serper as the primary collector** for news and competitor signals -
  confirmed by both rounds, including objective steering in round 2. Keep
  `FallbackSearch(("serper", "parallel"))` order unchanged; Parallel now has
  a real transport, so the fallback is live instead of a stub, and it keeps
  the objective steering plus excerpt cap from round 2.
- Parallel's Search API is not competitive for dated-event and
  comparison-page mining at 5x the cost. If used as fallback only, `turbo`
  mode ($0.001/query) is the right tier.
- The Parallel Task API client (`adapters/parallel_task.py`, capped at the
  `base` processor, $1.00 budget ceiling) remains available for future deep
  research experiments where multi-hop synthesis - not SERP recall - is the
  bottleneck; nothing in this A/B exercises it.

## Artifacts

- Parallel corpora: `benchmarks/signals-parallel/news-product-launches/`,
  `benchmarks/signals-parallel/competitor-intelligence/` (dossiers +
  collection logs)
- Score files: `runs/company-enrichment/news-product-launches/news-v12-parallel/scores/`,
  `runs/company-enrichment/competitor-intelligence/comp-v12-parallel/scores/`
  (local `runs/` is untracked; numbers above are the durable record)
- Adapter + safeguards commits: `a8be81e`, `0f6b527`, `142e25d`
