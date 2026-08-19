# Live Company Corpus Execution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build cited, research-complete dossiers for all 60 approved B2B fixtures through the existing resumable autoresearch lifecycle, then run the first enrichment experiments.

**Architecture:** `AutoresearchOrchestrator` remains the outer Experiment lifecycle. A deep `EnrichmentRunner` composes the existing capability registry, eligible routes, evidence cache, saturation tracker, and aggregate budget ledger; `CorpusResearchRunner` applies it to the durable SaaS-first rollout and persists one dossier per company.

**Tech Stack:** Python 3.11+, frozen dataclasses, JSONL/content-addressed artifacts, YAML dossiers, pytest, injected Source Adapters, existing local web capabilities.

## Global Constraints

- Exactly 60 unique B2B fixtures, 10 per cohort and 15 shared-core.
- Execution order is three shared-core SaaS, seven remaining SaaS, funded B2B, agencies, well-known B2B, suppliers, then local B2B services.
- Every required selection and dossier field is cited or explicitly unknown after source saturation.
- Source saturation requires required fields cited, one first-party and two distinct independent sources where available, and two distinct dry search angles.
- Known URLs use the free GTM waterfall first. Only approved Firecrawl escalation may spend from the aggregate `corpus-build` USD 2.00 cap.
- Parallel remains search-only. No people/contact discovery and no unapproved paid provider.
- Resume never repurchases cached evidence or re-executes a completed fixture.
- Experiment scopes use USD 1.00 aggregate caps. Automated validation may create a candidate but never Approval.

---

### Task 1: Research-complete corpus contracts and non-repeating rollout

**Files:**
- Create: `scripts/company_enrichment/corpus.py`
- Create: `tests/company_enrichment/test_corpus.py`
- Create: `tests/company_enrichment/test_dossiers.py`
- Modify: `scripts/company_enrichment/contracts.py`
- Modify: `scripts/company_enrichment/benchmark_schedule.py`
- Modify: `tests/company_enrichment/test_benchmark_schedule.py`

**Interfaces:**
- Consumes: `benchmarks/companies.yaml`, `CompanyDossier`, `EvidenceRef`, `FieldAssertion`.
- Produces: `Corpus.load(path)`, `Corpus.validate(as_of)`, `validate_research_complete(fixture, dossier)`, and a rollout whose second batch contains only the seven unprocessed SaaS fixtures.

- [ ] Write failing tests proving an empty dossier fails; every required field is cited or named in `unknowns`; funded fixtures require a primary funding URL/date within 12 months; local fixtures require a listing URL; and no company repeats between rollout stages.
- [ ] Run `py -m pytest tests/company_enrichment/test_corpus.py tests/company_enrichment/test_dossiers.py tests/company_enrichment/test_benchmark_schedule.py -q` and confirm behavioral failures.
- [ ] Implement immutable fixture values, strict YAML loading, the research-complete predicate, cohort-specific qualification rules, and seven-company second SaaS batch.
- [ ] Run the focused suite and `git diff --check`; commit `feat: enforce research complete company dossiers`.

### Task 2: Deep enrichment and dossier runner

**Files:**
- Create: `scripts/company_enrichment/runner.py`
- Create: `scripts/company_enrichment/executors.py`
- Create: `scripts/company_enrichment/dossier_runner.py`
- Create: `tests/company_enrichment/test_runner.py`
- Create: `tests/company_enrichment/test_p0_executors.py`
- Create: `tests/company_enrichment/test_dossier_runner.py`
- Create: `prompts/company-enrichment/*.md`

**Interfaces:**
- Consumes: validated definitions, `CapabilityDiscovery`, `ProviderRouter`, `EvidenceStore.resolve`, `BudgetLedger`, `SaturationTracker`, and provider-neutral Source Adapters.
- Produces: `EnrichmentRunner.run(request) -> EnrichmentResult` and `DossierBuilder.build(fixture, scope) -> CompanyDossier`.

- [ ] Write failing tests for exact orchestration order, discovery recording on cache hits and failures, eligible-only routes, cache/resume, owned paid reservations, bounded retries, saturation/partial results, output validation, append-only outcomes, and exact requested/resolved model identities.
- [ ] Write failing parameterized tests for meaningful outputs from all eight P0 enrichments; require cited evidence and supplied `SellerContext`; reject filter-only material from message-safe output.
- [ ] Write failing dossier tests proving all required categories are merged, conflicts and unknowns survive, and only a research-complete dossier is persisted.
- [ ] Implement the minimal runner, per-enrichment query/extraction specifications, typed output validators, and dossier builder using injected clients only.
- [ ] Run `py -m pytest tests/company_enrichment/test_runner.py tests/company_enrichment/test_p0_executors.py tests/company_enrichment/test_dossier_runner.py -q`; commit `feat: run resumable company research`.

### Task 3: Live composition root and three-company SaaS proof

**Files:**
- Create: `scripts/company_enrichment/cli.py`
- Create: `scripts/company_enrichment_cli.py`
- Create: `tests/company_enrichment/test_cli.py`
- Create: `docs/reports/company-corpus-live-run.md`
- Modify: `scripts/research_orchestration/orchestrator.py`
- Modify: `scripts/research_orchestration/contracts.py`
- Modify: `tests/test_autoresearch_orchestrator.py`

**Interfaces:**
- Consumes: the existing outer orchestrator and company runner.
- Produces: `research-corpus --stage saas_shared_core --resume --paid-cap-usd 2.00`, durable run artifacts, and a machine-readable stage report.

- [ ] Write failing tests for injected execution inputs/rubric, dry-run constructing no clients, exact stage selection, resume, zero duplicate IDs, explicit paid opt-in, fixed aggregate cap, authentication gaps, and JSON summary output.
- [ ] Implement the typed outer-loop bridge and CLI composition without adding another orchestrator.
- [ ] Run CLI tests and all autoresearch tests.
- [ ] Execute the live `saas_shared_core` stage using free routes first and approved Firecrawl only after an owned reservation; persist raw Evidence and three validated dossiers.
- [ ] Resume the same run and prove zero source repurchases; record calls, cost, gaps, and validation in `docs/reports/company-corpus-live-run.md`.
- [ ] Commit `data: prove live saas company research`.

### Task 4: Complete all 60 company dossiers

**Files:**
- Create: `benchmarks/dossiers/<company-id>.yaml` for every fixture.
- Modify: `benchmarks/companies.yaml`
- Modify: `docs/reports/company-corpus-live-run.md`
- Create: `docs/benchmarks/company-selection-policy.md`

**Interfaces:**
- Consumes: the validated live CLI, rollout state, cache, and aggregate ledger.
- Produces: 60 research-complete dossiers and a completed rollout journal.

- [ ] Run the seven remaining SaaS fixtures and validate all 10 SaaS dossiers.
- [ ] Run and validate funded B2B, recording a dated primary funding source within the preceding 12 months for all 10.
- [ ] Run and validate agencies, well-known B2B, and suppliers.
- [ ] Run and validate local B2B services, recording a local listing reference for all 10.
- [ ] Run the corpus validator and assert `companies=60 cohorts=6 each=10 core=15 dossiers=60 paid_cost_usd<=2.00`, with every unresolved field explicit in `unknowns`.
- [ ] Resume the completed rollout and prove no fixture or source is re-executed; commit `data: complete b2b company benchmark dossiers`.

### Task 5: Begin enrichment experiments

**Files:**
- Create: `scripts/company_enrichment/benchmark.py`
- Create: `scripts/company_enrichment/review.py`
- Create: `tests/company_enrichment/test_benchmark.py`
- Create: `tests/company_enrichment/test_review.py`
- Create: `runs/company-enrichment/experiments/<enrichment-id>/...`
- Modify: `docs/reports/company-corpus-live-run.md`

**Interfaces:**
- Consumes: fixed dossiers and cached Evidence.
- Produces: deterministic experiment reports, blind review packs, and candidate-only gate outcomes.

- [ ] Write failing tests for correctness, citation validity/completeness/freshness, latency/cost, separate synchronous/batch tracks, exact model IDs, and cache reuse.
- [ ] Write failing transition tests proving automation cannot emit `approved` and blind human verdicts require reviewer identity and timestamp.
- [ ] Implement benchmark scoring and append-only review packs.
- [ ] Run company-description, ICP/persona, and growth-signal experiments on the three SaaS core fixtures using the USD 1.00 per-enrichment caps.
- [ ] Record scores, costs, failures, and candidate status without Approval; run the full test suite and credential scanner.
- [ ] Commit `feat: benchmark first company enrichments`.

