# news-product-launches benchmark

Rubric version 1.0. `rubric.yaml` carries only `weights` and `threshold`
because the shared loader (`scripts/company_enrichment/signal_ground_truth.py`)
accepts exactly those keys; scoring lives in
`scripts/company_enrichment/news_evaluator.py`.

## Layout

- `split.yaml` - locked development/holdout split shared with the ICP loop
- `rubric.yaml` - weights (`events` 0.6, `citation` 0.25, `kind` 0.15) and threshold 0.90
- `saas-XX.yaml` - collected signal dossiers (`--collect`)
- `saas-XX.collection.json` - per-company collection log (queries, scrapes, normalized failures, paid cost)
- `ground-truth/saas-XX.yaml` - sealed, human-authored ground truth
- `ground-truth-drafts/saas-XX.yaml` - machine pre-filled drafts (`--draft-ground-truth`); never read by the loop

## Commands

```powershell
py scripts/company_enrichment_news_loop.py --collect --dry-run
lg run -- py scripts/company_enrichment_news_loop.py --collect [--company saas-01] [--overwrite]
py scripts/company_enrichment_news_loop.py --draft-ground-truth [--company saas-01] [--overwrite]
py scripts/company_enrichment_news_loop.py --evaluate --lineage <name> --dry-run
lg run -- py scripts/company_enrichment_news_loop.py --evaluate --lineage <name> --allow-paid
```

## Ground truth shape

```yaml
company_id: saas-01
as_of: '2026-08-18'
recent_window_days: 180          # events older than this are optional for recall
events:
- date: '2024-04-11'             # YYYY-MM-DD or YYYY-MM, quoted
  headline_aliases: [Expanded its senior leadership team]
  source_domain: prnewswire.com  # registrable domain of the reporting page
  kind: news                     # news | launch
  event_type: leadership         # per prompt enums for the kind
  evidence_ids: [ev-...]         # signal-dossier Evidence proving the event
```

`events: []` is valid for a quiet company; the payload then scores 1.0 only
when it reports nothing and declares both `news` and `launches` unknown.

## Hard failure rules

- `contract_violation` - payload violates the news output contract (dates, enums, retained IDs)
- `uncited_date` - an event's date text does not appear in the excerpts it cites
- `invented_event` - ground truth has no events but the payload reports some
