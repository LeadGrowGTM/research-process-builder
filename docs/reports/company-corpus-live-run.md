# Company corpus live run

Recorded 2026-08-12 for rollout stage saas_shared_core.

## Authoritative v6 execution

- Exact IDs: saas-01, saas-04, saas-07; duplicates: 0.
- Existing outer AutoresearchOrchestrator invoked once with immutable stage
  inputs and the research_complete_company_dossier rubric.
- Existing Task 2 EnrichmentRunner invoked for all eight P0 enrichments for
  each company: 24 append-only outcomes and 24 discovery records.
- Source route per company: one official and two distinct-domain independent
  public sources. Nine content-addressed Evidence objects were retained.
- The injected search client executed two distinct no-material-fact queries per
  company. The redacted call ledger contains 15 rows: nine source calls and six
  search calls, each with URL or query and status.
- Three dossiers passed typed CompanyDossier reconstruction and the strict
  research-complete validator at the authoritative corpus date.
- No authentication, source, artifact, or duplicate-ID gaps.
- No paid route was enabled. Actual paid cost: USD 0.
- Resume rehydrated the YAML dossiers and evidence-derived qualifications,
  validated company IDs and every referenced Evidence object, checked the
  complete object set for orphans, and resumed all three with zero source calls
  or repurchases. Retained dry outcomes are projected from calls.jsonl without
  rerunning searches.

Authoritative ignored artifacts are under runs/company-corpus-live-v6. Earlier
v1-v5 directories remain audit history and are not the authoritative proof.

## Qualification and saturation

Canonical identity and domain, B2B buyer, business offer, and cohort evidence
are derived from retained source bodies. The seed industry and
products_services fields are never promoted to verified qualification.

Saturation is calculated by Task 2 from typed executor coverage, a first-party
source, two distinct independent sources, and two executed consecutive
no-material-fact search outcomes. The CLI does not construct EnrichmentResult
or set saturated itself.

The cited dossier outputs remain intentionally conservative: identity,
description, and offer are asserted; unsupported categories are explicit
unknowns with a recorded reason. The AgencyAnalytics SourceForge profile is
independent and substantial but lower-confidence and potentially stale, so it
provides diversity rather than broad claim corroboration.

This is corpus-construction evidence, not an Approval. Promotion still
requires programmed ground-truth validation at at least 90 percent and
explicit human review.
