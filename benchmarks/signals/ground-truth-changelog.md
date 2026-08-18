# Signal ground truth changelog

Sealed ground truth under `benchmarks/signals/<enrichment>/ground-truth/` is
reference data. Every change after sealing is recorded here with the Evidence
that justifies it, so a reviewer can tell a correction from a fit-to-model.

Rule for a change: the corrected value must be literally supported by Evidence
already in the company's signal dossier (excerpt text or Evidence URL), and the
change must be recorded before any lineage is scored against it.

| Date | Enrichment | Company | Change | Evidence | Why |
|---|---|---|---|---|---|
| 2026-08-18 | running-ads-offer-intelligence | saas-01 | meta `call_to_action`: `Learn more` -> `Contact us` | ev-6a20365a736c144d (3 of 4 sampled active ads carry `Contact us`) | Drafting error; caught in the ads-v3 human review. |
| 2026-08-18 | news-product-launches | saas-03 | added news/funding event `2026-07-01` "Closed a $60M Series B", source globenewswire.com | ev-564f66db29e563a6, ev-05e4c4ad55380e84 (excerpt says "1 month ago"; the cited URL path is `/news-release/2026/07/01/`) | The authoring rule required the date literally in the excerpt and missed a URL-path date. `date_is_cited` now accepts a cited Evidence URL path date, so the rule and the evaluator agree. Largest recent event for the company; recorded before news-v6 was scored. |
