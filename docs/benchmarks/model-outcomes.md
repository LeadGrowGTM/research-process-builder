# Model outcomes across enrichments

**Updated:** 2026-08-25. Consolidated view of every model tried per
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

### company-description - experiment, below gate

| Model | Score | Outcome |
|---|---|---|
| gpt-4.1-mini | 0.774 | Experiment; below gate (company-corpus live matrix, docs/reports/company-corpus-live-run.md) |
| gpt-5.6-luna | not yet run | queued: the obvious cheap retry given the ICP result |

### growth-signals - experiment, below gate

| Model | Score | Outcome |
|---|---|---|
| gpt-4.1-mini | 0.719 | Experiment; below gate (company-corpus live matrix) |
| gpt-5.6-luna | not yet run | queued: same luna retry |

## Pattern

Every enrichment that stalled just under the gate on gpt-4.1-mini cleared it
on gpt-5.6-luna with the same or near-same prompt (news 0.887 -> 0.974, comp
0.885 -> 0.933, ICP 0.808 -> 1.00). At current rates luna is also the
near-cheapest option: news + competitors combined ~USD 11/1k companies
synchronous, ~6/1k batch; ICP ~1.4/1k; buying-trigger ~1.2/1k. gpt-5-nano has
never come close to a gate (0.61-0.74) and produced hard failures on two of
three scored tracks; it stays a pricing floor, not a candidate.
