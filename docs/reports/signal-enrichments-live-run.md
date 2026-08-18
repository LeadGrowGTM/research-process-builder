# Signal enrichments live run: running ads, news/launches, competitors

**Date:** 2026-08-18
**Branch:** `wt/signal-enrichments` (worktree `.worktrees/signal-enrichments`, cut from `wt/icp-persona-loop` at c319fad)
**Corpus:** saas-01..10 (development 01,02,04,05,07,09; holdout 03,06,08,10)
**Model:** gpt-4.1-mini (resolved gpt-4.1-mini-2025-04-14), synchronous, one prompt candidate per lineage
**Plan:** `C:\Users\mitch\.claude\plans\hashed-snuggling-fountain.md`

## What was built

One generic prompt loop, `scripts/company_enrichment/signal_loop.py`, parameterized
by a `SignalSpec`, replaces copying the ICP loop three times. Each enrichment
supplies a typed contract, an evaluator, a collect stage, and a prompt:

| Enrichment | Collect stage | Contract / evaluator | Entrypoint |
|---|---|---|---|
| running-ads-offer-intelligence | `adapters/google_ads.py` (lg-free Google Ads Transparency, key `LG_FREE_ENRICHMENTS_API_KEY`) + `adapters/meta_ads.py` (local `meta-ads-scraper` on :3001, Playwright, no key) via `ads_collect.py` | `ads_contracts.py`, `ads_evaluator.py` | `scripts/company_enrichment_ads_loop.py` |
| news-product-launches | `signal_collection.py`: Serper (`adapters/serper.py`, news + web queries) with Parallel as typed fallback, free-only known-URL scrape through the GTM waterfall (`adapters/known_url_scrape.py`, L1/L2), first-party path probes | `news_contracts.py`, `news_evaluator.py` | `scripts/company_enrichment_news_loop.py` |
| competitor-intelligence | same collector, competitor query plan | `competitor_contracts.py`, `competitor_evaluator.py` | `scripts/company_enrichment_competitor_loop.py` |

Signal dossiers (base dossier Evidence plus new content-addressed Evidence) live
in `benchmarks/signals/<enrichment>/saas-XX.yaml`; base dossiers in
`benchmarks/dossiers/` are untouched, so ICP dossier hashes are stable. Sealed
ground truth is in `benchmarks/signals/<enrichment>/ground-truth/`, drafts in
`ground-truth-drafts/` (never read by the loop). `openai_model_client.py` gained
a field-keyed structured-output validator for non-ICP enrichments; ICP behaviour
is unchanged (all pre-existing tests green).

## Provider verification (live, saas-01 AgencyAnalytics)

- Google Ads Transparency: `running_ads: true`, 197 creatives, first_seen 2022-08-13, last_seen 2026-08-18. Free.
- Meta Ad Library: `handle_fb` match on page 120680864620158, 90 active / 15 inactive ads, per-ad copy, CTA, landing page from the scraper's SQLite. ~60 s per company, `workers: 1` (Meta rate-limits). Free.
- Serper: competitor champion query returns the subject's own `/competitors` page plus alternatives articles; `{{domain}} news` on the news endpoint returns dated PR-wire items only without a time filter (`qdr:m`/`qdr:y` returned 0 for smaller SaaS), so the news query runs unfiltered and recency is scored, not filtered.
- Parallel: no key exists in any secret project; the adapter lands as a normalized failure, never a crash.

## Results by lineage

Scores are the loop's programmed rubric (threshold 0.90). Automation halts for
review; nothing here is an Approval.

### running-ads-offer-intelligence

| Lineage | Dev | Holdout | Hard failures | Cost | Gate |
|---|---|---|---|---|---|
| ads-v1 | 0.00 | 0.00 | schema rejected by OpenAI (`uniqueItems`) | 0.074 (reserved) | unsafe_ambiguity |
| ads-v2 | 0.50 | 0.625 | 3 invalid_output, 3 status_overclaim:meta | 0.052 | unsafe_ambiguity |
| ads-v3 | **1.00** | **1.00** | none | 0.014 | **human_review_required** |

Fixes between lineages: drop `uniqueItems`; restrict the `evidence_ids` enum to
ad-library Evidence, add the `cross_channel_citation` hard failure, and state in
the prompt that website/LinkedIn Evidence never creates or activates a channel
(the v2 failure mode: Meta "active" from a homepage "Book a demo").

Caveat: status/landing-page/CTA come from the deterministic adapters and the
ground truth was drafted from the same collection, so 1.00 measures faithful
reporting of the Evidence plus offer summarization on the two Meta-active
companies (saas-01, saas-08), not independent discovery. Meta outcomes across
the ten: 2 active, 3 inactive (page matched, no ads), 5 unknown (no verified
social handle on the website).

### news-product-launches

| Lineage | Dev | Holdout | Hard failures | Cost | Notes |
|---|---|---|---|---|---|
| news-v1 | 0.539 | 0.250 | 3 invalid_output, 3 uncited_date | 0.197 | `news: []` without an `unknowns` declaration was a validator error |
| news-v2 | 0.775 | 0.213 | 2 invalid_output, 3 uncited_date | 0.118 | invalid = JSON truncated at 1,024 output tokens |
| news-v3 | 0.756 | 0.703 | 4 uncited_date | 0.065 | token cap 4,096 for field-keyed contracts |
| news-v4 | 0.735 | 0.720 | 4 uncited_date | 0.065 | year-copy rule added |
| news-v5 | 0.709 | 0.708 | 3 uncited_date | 0.066 | prose length no longer a contract failure |

Remaining failures are model errors: gpt-4.1-mini shifts years (`Feb 19, 2025`
reported as `2026-02-19`) and converts relative dates ("1 month ago") into
absolute ones. Both are hard failures by design (`uncited_date`).

### competitor-intelligence

| Lineage | Dev | Holdout | Hard failures | Cost | Notes |
|---|---|---|---|---|---|
| comp-v1 | 0.130 | 0.804 | 3 invalid_output, 2 contract_violation, 2 hallucinated | 0.136 | truncation; all-inferred payload with redundant `unknowns` |
| comp-v2 | 0.319 | 0.837 | 3 invalid_output, 1 hallucinated | 0.138 | prompt: explicit alternatives lists are `named` |
| comp-v3 | 0.652 | 0.635 | 2 contract_violation, 2 hallucinated | 0.061 | token cap fix |
| comp-v4 | 0.703 | 0.635 | 3 hallucinated, 1 contract_violation | 0.062 | duplicate ids/companies now merged by the parser |
| comp-v5 | 0.678 | 0.668 | 2 hallucinated, 1 self_competitor, 1 contract_violation | 0.063 | malformed `domain` now nulls instead of failing |

Ground truth for competitors is deliberately generous (11 to 23 named per
company, every name literally present in cited Evidence), so `named_set` recall
is the dominant loss. Run-to-run variance on identical prompts is large (saas-05
0.98 -> 0.86 -> 0.56): the client sends no `temperature`, so single-sample
scores are noisy.

## Spend

LLM USD 1.111 across 13 lineages (ads 0.140, news 0.512, competitors 0.460);
Serper USD 0.060 (60 queries at USD 0.001); scrapers and ad libraries USD 0.
No source purchases. Every lineage stayed under the USD 1.00 cap.

## Ground truth provenance

Ads ground truth was drafted from the deterministic collection and the offer
fields authored from Meta ad copy. News and competitor ground truth was authored
per company from the signal-dossier Evidence under strict rules (absolute dates
present in cited excerpts, names present in cited excerpts, category sanity for
competitors) and re-validated with the repo's own loaders. Mitch has not yet
reviewed any of it; treat all three as Experiments pending human review.

## Next steps

1. Human review of ads-v3 outputs and the ads ground truth (gate is
   `human_review_required`); decide approve / revise / reject.
2. Anneal news and competitors: add prompt candidates (the loop already accepts
   several), and compare the model ladder (gpt-5-nano, gpt-4.1, GPT-5.6 Luna)
   because year-shifting and relative-date conversion look like a cheap-model
   weakness rather than a prompt gap.
3. Add a `temperature` (or n-sample median) to the model client so lineage
   scores are comparable; the ICP loop shares this client, so change it under
   its tests.
4. Consider a `date_from_evidence` post-check that rejects an event whose date is
   not in its cited excerpt before scoring, turning a hard failure into a
   dropped event.
5. Meta rate limiting: space bulk jobs; `not_found` on a matched page is scored
   inactive at 0.7 confidence and should be re-run once when the health
   endpoint reports rate limiting.

## How to resume

```powershell
# from C:\Users\mitch\Everything_CC\pipelines\gtm-orchestrator (secrets live there, prod env)
lg run --env prod py <worktree>\scripts\company_enrichment_ads_loop.py --evaluate --lineage ads-v4 --allow-paid
lg run --env prod py <worktree>\scripts\company_enrichment_news_loop.py --evaluate --lineage news-v6 --allow-paid
lg run --env prod py <worktree>\scripts\company_enrichment_competitor_loop.py --evaluate --lineage comp-v6 --allow-paid
# Meta scraper must be running: cd C:\Users\mitch\Everything_CC\tools\meta-ads-scraper; npx next dev -p 3001
```
