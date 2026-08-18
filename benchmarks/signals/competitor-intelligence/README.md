# competitor-intelligence benchmark

Rubric version 1.0. `rubric.yaml` carries only `weights` and `threshold`
because the shared loader (`scripts/company_enrichment/signal_ground_truth.py`)
accepts exactly those keys; scoring lives in
`scripts/company_enrichment/competitor_evaluator.py`.

## Layout

- `split.yaml` - locked development/holdout split shared with the ICP loop
- `rubric.yaml` - weights (`named_set` 0.5, `citation` 0.3, `labeling` 0.2) and threshold 0.90
- `saas-XX.yaml` - collected signal dossiers (`--collect`)
- `saas-XX.collection.json` - per-company collection log (queries, scrapes, normalized failures, paid cost)
- `ground-truth/saas-XX.yaml` - sealed, human-authored ground truth
- `ground-truth-drafts/saas-XX.yaml` - machine pre-filled drafts (`--draft-ground-truth`); never read by the loop

## Commands

```powershell
py scripts/company_enrichment_competitor_loop.py --collect --dry-run
lg run -- py scripts/company_enrichment_competitor_loop.py --collect [--company saas-01] [--overwrite]
py scripts/company_enrichment_competitor_loop.py --draft-ground-truth [--company saas-01] [--overwrite]
py scripts/company_enrichment_competitor_loop.py --evaluate --lineage <name> --dry-run
lg run -- py scripts/company_enrichment_competitor_loop.py --evaluate --lineage <name> --allow-paid
```

## Ground truth shape

```yaml
company_id: saas-01
named:                            # explicitly positioned against the subject
- {name: DashThis, aliases: [Dash This], domain: dashthis.com}
inferred:                         # same buyer problem, never explicitly compared
- {name: Looker Studio, aliases: [Google Data Studio], domain: null}
evidence_ids: [ev-...]            # Evidence the reviewer used to seal the set
```

Empty `named` and `inferred` lists are valid; the payload then scores 1.0
only when both buckets are empty and `competitors` is declared unknown.

## Hard failure rules

- `contract_violation` - payload violates the competitors output contract
- `hallucinated_competitor` - name and domain absent from every cited excerpt
- `self_competitor` - the subject company (identity assertion / first base Evidence domain) listed as a competitor
- `invented_competitor` - ground truth has no competitors but the payload reports some
