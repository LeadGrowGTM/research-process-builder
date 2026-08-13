# Company corpus live run

Authoritative research date: 2026-08-12. V9 is the publication proof. Earlier
v1-v8 source attempts and reconstruction runs remain append-only audit history;
v6/v7 were rejected because generic dry searches did not establish
category-specific saturation.

## Result

- 60 published research-complete dossiers across six cohorts of ten; shared
  core: 15.
- Seven rollout stages retained SaaS-first order with no repeated company ID.
- 201 immutable Evidence objects, 963 redacted source/search calls, 60
  qualification records, and 60 dossiers citing 197 distinct content objects.
- No final source, authentication, duplicate-ID, or artifact gaps.
- Paid routes disabled; aggregate cost USD 0 of the immutable USD 2 cap.
- No v9 source repurchases. All seven completed resumes and publication performed no
  research execution or repurchase.

Every dossier cites identity, description, offer, ICP, and personas. All ten
funded dossiers cite growth from an exact dated primary source in the
2025-08-12 through 2026-08-12 window. V9 also resolved four material searches
into three cited assertions using exact retained first-party Evidence:

- Baseten publishes model API and dedicated-deployment pricing;
- Supabase publishes a direct Firebase competitor comparison; and
- Carney announced its redesigned site and new Daily Carnage hub on
  2025-12-22.

Those fields are assertions, not unknowns. All other unresolved categories are
explicit, category-specific unknowns after their two exact research angles
were closed. A material angle closes only when its typed field assertion cites
the exact retained planned source; it is never relabeled as an empty result.

## Execution, validation, and resume

The run composed the existing outer AutoresearchOrchestrator and Task 2
EnrichmentRunner for all eight P0 enrichments. Discovery, eligible routes,
requested/resolved models, saturation state, typed output, budget state, and
append-only outcomes are retained. Free source/search clients were injected;
paid provider construction and spending were not enabled.

The corpus validator reported companies=60, cohorts=6, ten in every cohort,
core=15, dossiers=60, and paid cost USD 0. Completed-stage resumes rehydrated
3, 7, 10, 10, 10, 10, and 10 dossiers with zero execution, Evidence
persistence, repurchase, or cost. Resume validation checks typed YAML, company identity, authoritative
date, exact Evidence observation/hash, and research completeness. It rejects
truncated, malformed, unrelated-provider, and true-orphan artifacts.

Publication is recoverably transactional: all 60 dossier files and the updated
corpus are staged before replacement, a durable marker records swap phase, and
the next start restores backups after process interruption. Ordinary failure
also rolls back. The checked-in benchmarks/companies.yaml is
`research_complete`; `benchmarks/dossiers/` contains exactly 60 files.
Prepared and dossiers-swapped interruptions restore the old paired generation;
a durable committed state preserves the new paired generation even if cleanup
is interrupted.

## Qualification corrections and caveats

- `funded-09` replaces ineligible Virtual Peaker with DualEntry. Virtual
  Peaker's supported event fell outside the window; DualEntry retains a dated
  2025-10-02 primary announcement.
- `agency-04` replaces software-led Audiense with service-led Walker Sands.
- `agency-02`, `agency-08`, and `agency-10` are normalized to AbelsonTaylor,
  Capacity, and Chartis Interactive respectively.
- `local-07` uses an explicitly reviewed exact-name plus Atlanta-locality
  exception between LinkedIn and Cobb County because the official service page
  publishes no street address. No professional-engineer license is inferred.
- `local-08` uses a lower-authority B2BHint registry mirror because the Georgia
  registry endpoint was inaccessible; retained first-party Evidence
  corroborates its address.
- `local-10` is bound to the hyphenated Woodland, Washington domain and OSHA
  record, excluding the unrelated San Diego entity.
- The AgencyAnalytics SourceForge profile is substantive but lower-confidence
  and potentially stale; it provides source diversity, not broad
  corroboration.

This corpus is validation evidence, not an Approval. Promotion still requires
programmed ground-truth validation at 90 percent or higher and explicit human
review of attribution, scope, safety, and destination.

## Initial enrichment experiments

Task 5 added deterministic benchmark scoring, separate synchronous and Batch
tracks, exact requested/resolved model identity, cached-source accounting, and
hash-chained review transitions. Automation is limited to `experiment` and
`candidate`; only an attributed blind human verdict can create Approval.

A mechanical validation used the published cached Evidence for `saas-01`,
`saas-04`, and `saas-07`. Company description and ICP/persona each scored 1.0
for correctness, citation validity, citation completeness, and freshness.
Growth signals scored 0.0 because all three fixed dossiers explicitly retain
growth as unknown; no growth output was invented. Cache reuse was 1.0, model
and source cost were USD 0, source purchases were zero, and the mechanical
validation was neither Candidate nor Approval. Its artifacts are under
`runs/company-enrichment/mechanical-v2`.

The live comparison matrix planned 72 cases: three enrichments, three fixed
companies, four exact requested model IDs (`gpt-5-nano`, `gpt-4o-mini`,
`gpt-4.1-mini`, and `gpt-5.6-luna`), and separate synchronous and Batch tracks.
Neither the development nor production secret registry contained an
`OPENAI_API_KEY`, so all 72 cases are durably recorded as
`not_executed/authentication_required` with a null resolved model. No live
model output was fabricated. Completed live cases are zero, paid cost is USD
0, source purchases are zero, and each enrichment remains `experiment` rather
than Candidate or Approval. The append-only artifacts are under
`runs/company-enrichment/experiments-v2`; a resume retained exactly 72 rows
before and after with zero execution.

Candidate promotion now fails closed unless every planned case completes and
the programmed ground-truth score is at least 0.90 across all eight model/track
reports. A case-weighted aggregate manifest binds every report path and byte
hash; restart revalidates the full set. Growth therefore remains `experiment`
at its current 0.0 mechanical score. A Candidate record must also carry a
content-hashed blind review pack built only from all 24 actual experiment
outputs. Partial runs create no reviewer artifact. Pack content uses persisted
random output IDs and randomized presentation order, recursively rejects
model/provider identity, and fixes the reviewer dimensions to readability,
specificity, usefulness, casualness, and non-creepiness.
Approval additionally requires an attributed, timezone-aware human verdict,
the matching pack ID, and a complete five-dimension scorecard.

Paid model groups use durable collected/reconciled/completed transaction
records. Crash tests cover interruption after reconciliation, after one
outcome row, and after every outcome row but before the benchmark report.
Resume reuses the paid result without repurchase, deduplicates outcomes, and
regenerates missing reports. Invalid model output is retained as
`contract_invalid`; client exceptions are retained as `retryable` failures.
