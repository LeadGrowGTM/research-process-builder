# Task 4 report: complete B2B company benchmark dossiers

## Outcome

Completed and atomically published the 60-company B2B benchmark corpus from the
authoritative v9 run. The published corpus has six cohorts of ten, 15 shared-core
fixtures, 60 typed research-complete dossiers, and explicit unknowns for every
unresolved field. The seven stages retained SaaS-first order and no repeated
company IDs. Paid routes remained disabled; aggregate paid cost was USD 0.

V6 and v7 remain immutable audit history but are not the publication proof.
Independent review rejected their generic dry-angle saturation and loose
qualification behavior. V9 reran all eight P0 enrichments with category-specific
queries through the existing AutoresearchOrchestrator and EnrichmentRunner,
then published only after typed resume and corpus validation passed.

## RED/GREEN evidence

Task 4 began RED for missing exact-count rollout, live source planning,
conditional cohort evidence, strict resume, and publication. Subsequent live
failures and review findings each received focused regressions. Material cases
included polluted discovery results; distinct-domain/entity gates; primary
funding dates and SEC Form D semantics; browser/PDF/curl transport; local NAP
normalization; seed-free buyer/offer qualification; exact Evidence observation
verification; partial-rollout versus true-orphan handling; category-specific
searches; transactional publication; and dossier-derived unknown summaries.

The final v9 material-search regressions prove that a material result is closed
only when a typed assertion cites the exact retained planned source. This added:

- Baseten pricing from `https://www.baseten.co/pricing/`;
- Supabase competitor comparison from
  `https://supabase.com/alternatives/supabase-vs-firebase`; and
- Carney's 2025-12-22 site/Daily Carnage launch from
  `https://carney.co/daily-carnage/choose-your-adventure/`.

Their asserted fields are absent from final unknowns. Cached material query rows
were reused without repeat search calls. The source additions used versioned
first-party providers and did not change identity qualification sources.

Final verification evidence:

- Full company-enrichment suite: 222 passed in 107.15s.
- Outer autoresearch and repository-policy suites: 148 passed in 19.44s.
- Focused reviewer-blocker gate: 5 passed.
- All-60 reviewed phrase/Evidence audit: 60 valid, zero failures.
- Diff check: clean (line-ending notices only).

## Live, resume, and publication evidence

The final v9 store contains 201 immutable Evidence objects, 963 redacted
source/search call events, 60 qualification records, and 60 dossiers citing 197
distinct content objects. Final successful stage summaries report no source,
authentication, duplicate-ID, or invalid-artifact gaps, no source repurchases,
and USD 0 paid cost.

Completed-stage resumes rehydrated and validated all seven stages (3, 7, 10,
10, 10, 10, and 10 dossiers) with zero execution, persistence, repurchase, or
cost. Publication reported
`companies=60`, `cohorts=6`, `each=[10,10,10,10,10,10]`, `core=15`, and
`dossiers=60`. Publication stages all 60 dossier files and the corpus document,
then swaps them under a durable transaction marker with next-start recovery for
process interruption between swaps. The checked-in corpus
status is `research_complete` and `benchmarks/dossiers/` contains exactly 60
YAML files.

The publication marker has explicit prepared, dossiers-swapped, and committed
states. Pre-commit recovery restores the old dossier/corpus generation from
backups whether interruption occurs after moving the old dossiers or after
installing the new dossiers. Once committed is durable, recovery preserves the
new generation and only removes stale marker/backup artifacts.

Resume reconstructs typed YAML, checks company ID and authoritative 2026-08-12
research-complete semantics, verifies each exact Evidence observation/hash, and
rejects malformed or unrelated-provider orphan objects. During an incomplete
rollout, evidence from not-yet-published cohorts remains valid journal history;
once all 60 dossiers exist, the validator audits the entire Evidence store.

## Files

- `scripts/company_enrichment/cli.py`
- `scripts/company_enrichment/evidence.py`
- `scripts/company_enrichment/runner.py`
- `tests/company_enrichment/test_cli.py`
- `tests/company_enrichment/test_corpus_completion.py`
- `tests/company_enrichment/test_evidence.py`
- `tests/company_enrichment/test_runner.py`
- `benchmarks/company-source-plan.yaml`
- `benchmarks/companies.yaml`
- `benchmarks/dossiers/*.yaml` (60)
- `docs/benchmarks/company-selection-policy.md`
- `docs/reports/company-corpus-live-run.md`

## Corrections and concerns

Policy-backed replacements are Virtual Peaker to DualEntry and Audiense to
Walker Sands. Identity normalizations cover Float, Redo, AbelsonTaylor,
Capacity, and Chartis Interactive. Local authority and NAP exceptions are
documented in the selection policy and live report.

V6-v8 and failed attempts are deliberately retained as append-only
audit history. Public source availability and content can change after the
authoritative 2026-08-12 date. This corpus is benchmark evidence, not an
Approval; programmed validation at 90 percent or higher and explicit human
review remain required before reusable-flow promotion.

Commit: `data: complete b2b company benchmark dossiers` (this changeset).
