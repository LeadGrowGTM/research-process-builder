# running-ads-offer-intelligence benchmark

Rubric version 1.0. `rubric.yaml` carries only `weights` and `threshold`
because the shared loader (`scripts/company_enrichment/signal_ground_truth.py`)
accepts exactly those keys; everything else about scoring lives in
`scripts/company_enrichment/ads_evaluator.py`.

## Layout

- `split.yaml` - locked development/holdout split shared with the ICP loop
- `rubric.yaml` - weights (`status` 0.6, `landing_page` 0.2, `offer` 0.2) and threshold 0.90
- `saas-XX.yaml` - collected signal dossiers (`--collect`)
- `ground-truth/saas-XX.yaml` - sealed, human-authored ground truth
- `ground-truth-drafts/saas-XX.yaml` - machine pre-filled drafts (`--draft-ground-truth`); never read by the loop

## Ground truth shape

```yaml
company_id: saas-01
as_of: '2026-08-18'
channels:
  google:
    status: active            # active | inactive | unknown
    evidence_ids: [ev-...]    # required exactly when status is not unknown
  meta:
    status: active
    landing_page: https://example.com/p/enterprise   # optional
    observed_offer: enterprise agency reporting plan # optional
    offer_aliases: [enterprise plan]                 # optional
    call_to_action: Contact us                       # optional
    evidence_ids: [ev-...]
```

Google entries never carry copy or a landing page. `TODO_HUMAN` placeholders
left over from a draft are rejected by the loader.

## Hard failure rules

- `invalid_output` - payload violates the ads output contract
- `unretained_evidence:<channel>` - a channel cites Evidence outside the signal dossier
- `status_overclaim:<channel>` - payload says `active` where ground truth is `inactive`, `unknown`, or absent
- `google_creative_fields` - angle, offer, call_to_action, or landing_page on the Google channel
