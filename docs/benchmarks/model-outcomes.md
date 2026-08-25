# Model outcomes across enrichments

**Updated:** 2026-08-25 (luna rerun for description/growth; both benchmarks
found invalid - see their sections). Consolidated view of every model tried per
enrichment and where it landed. Detail lives in the per-track reports linked
below; this page is the summary of record. Scores are dev / holdout mean
against ground truth (gate >= 0.90 on both, plus human review) unless marked
human-judged.

**Model policy** (Mitch, 2026-08-18 + 2026-08-20): production-approved models
are **gpt-4.1-mini, gpt-5-nano, gpt-5.6-luna**. **gpt-4o-mini is
benchmark-only** (the cheap-tier floor of the experiment matrix). The full
gpt-4.1 tier and the rest of the gpt-4o family are never used. Luna prices at
2026-08-20 list rates (0.20 in / 0.02 cache read / 0.25 cache write / 1.20
out per 1M) - cheaper per token than gpt-4.1-mini.

## Outcomes by enrichment and model

### news-product-launches (report: docs/reports/signal-enrichments-anneal.md)

| Model | Dev | Holdout | Hard failures | Outcome |
|---|---|---|---|---|
| gpt-5.6-luna | **0.974** | **0.997** | none | **approved + graduated 2026-08-21; production** |
| gpt-4.1-mini | 0.887 | 1.000 | none | below dev gate; run-to-run spread ~+-0.03 |
| gpt-4o-mini | 0.779 | 0.764 | none | benchmark-only; far below gate |
| gpt-5-nano | 0.669 | 0.606 | none | far below gate |

### competitor-intelligence (report: docs/reports/signal-enrichments-anneal.md)

| Model | Dev | Holdout | Hard failures | Outcome |
|---|---|---|---|---|
| gpt-5.6-luna | **0.933** | **0.960** | none | **approved + graduated 2026-08-21; production** |
| gpt-4.1-mini | 0.885 | 0.965 | none | below dev gate |
| gpt-4o-mini | 0.804 | 0.879 | none | benchmark-only; below gate |
| gpt-5-nano | 0.686 | 0.642 | hallucinated_competitor (saas-10) | far below gate |

### icp-persona-analysis (report: docs/reports/icp-persona-anneal.md)

| Model | Dev | Holdout | Hard failures | Outcome |
|---|---|---|---|---|
| gpt-5.6-luna | **1.00** | **1.00** | none | **approved 2026-08-24; shipping lineage v27-luna, ~USD 0.0014/company** |
| gpt-4.1-mini | 0.808 | 0.888 | 6 dev + 2 holdout | stalled 0.78-0.81 across 20+ lineages |
| gpt-5-nano | 0.742 | 0.713 | 7 dev + 5 holdout | far below gate (v23-gpt5nano) |

### running-ads-offer-intelligence (report: docs/reports/ads-v3-review-sheet.md)

| Model | Dev | Holdout | Hard failures | Outcome |
|---|---|---|---|---|
| gpt-4.1-mini | 1.00 | 1.00 | none | **approved 2026-08-18 after GT fix; production** |

Only mini was run; the task is mostly faithful adapter-status transcription,
so no luna attempt was needed.

### buying-trigger-analysis - human-judged, no scorer (report: docs/reports/buying-trigger-anneal.md)

| Model | Outcome |
|---|---|
| gpt-5.6-luna | **accepted 2026-08-25 at v6-luna (prompt 0.5.0); production.** ~USD 0.0012/company |
| gpt-4.1-mini | v1/v2 output rejected on review: generic, internal-state phrasing; Mitch switched to luna at v3 |

### company-description - benchmark invalid, do not compare models on it

| Model | Score | Outcome |
|---|---|---|
| 4-model matrix aggregate | 0.774 | case-weighted across all 4 models x 2 tracks (company-corpus live matrix, docs/reports/company-corpus-live-run.md) - NOT a per-model score |
| gpt-5.6-luna | 0.75 | luna-only rerun 2026-08-25 (`--model` flag), USD 0.0037; correctness 0.0 on all 6 cases, citations perfect |

The 2026-08-25 luna rerun exposed why this track cannot clear the gate:
`_correctness` in `scripts/company_enrichment/benchmark.py` requires an exact
canonical-JSON string match between the model's free-text value and the
dossier assertion. A paraphrase scores 0; only verbatim copying of dossier
text scores. The score measures parroting, not description quality. Ceiling
without verbatim copying is 0.75 (the three citation dimensions).
**Do not anneal or compare models against this gate until the track gets a
semantic scorer and real ground truth like the ICP loop had.**

### growth-signals - benchmark invalid, do not compare models on it

| Model | Score | Outcome |
|---|---|---|
| 4-model matrix aggregate | 0.719 | case-weighted across all 4 models x 2 tracks - NOT a per-model score |
| gpt-5.6-luna | 0.50 | luna-only rerun 2026-08-25, USD 0.0027 |

Worse than description: all three fixed dossiers (saas-01/04/07) list
`growth` under `unknowns` - there is **zero growth ground truth** in the
corpus. A model that asserts growth (wrong per GT) still scores 0.75 through
the citation dimensions; a model that correctly answers `unknown` scores
**0.0** because an assertion-free output zeroes every citation dimension.
Luna's 0.50 reflects that it answered `unknown` for saas-07 (the correct
answer) and was punished for it. The gate is inverted on this corpus.
**Needs growth ground truth (dated, observable signals) before any model
work.** The deterministic page-signals check (careers/blog presence) is the
first source of real observable hiring/growth evidence for that GT.

## Pattern

Every enrichment with a **valid semantic scorer** that stalled just under the
gate on gpt-4.1-mini cleared it on gpt-5.6-luna with the same or near-same
prompt (news 0.887 -> 0.974, comp 0.885 -> 0.933, ICP 0.808 -> 1.00). At
current rates luna is also the near-cheapest option: news + competitors
combined ~USD 11/1k companies synchronous, ~6/1k batch; ICP ~1.4/1k;
buying-trigger ~1.2/1k. gpt-5-nano has never come close to a gate (0.61-0.74)
and produced hard failures on two of three scored tracks; it stays a pricing
floor, not a candidate. company-description and growth-signals are excluded
from the pattern: their benchmark is exact-string matching against dossier
text (and for growth, no GT at all), so their sub-gate scores say nothing
about any model.
