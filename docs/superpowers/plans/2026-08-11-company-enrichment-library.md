# Company Enrichment Library Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a manifest-driven company enrichment library, a cited 60-company benchmark corpus with a 15-company shared core, and a cost-bounded experiment/review system for eight P0 enrichments.

**Architecture:** A deep `company_enrichment` Python package owns strict contracts, capability discovery, provider routing, evidence/cache persistence, aggregate budgets, dossier construction, benchmarking, and approval transitions. Versioned YAML manifests describe enrichments and companies; injected adapters reuse GTM Orchestrator, Nexus, Parallel Search, ads tools, and TechSight without leaking provider-specific payloads into public results.

**Tech Stack:** Python 3.11+, stdlib dataclasses/enum/hashlib/json/pathlib/subprocess, PyYAML 6.x, pytest, JSONL/content-addressed artifacts, YAML manifests, Markdown operator documentation.

## Global Constraints

- The benchmark contains exactly 60 unique companies: 10 each in Google Maps/local, SaaS, recently funded within the prior 12 months, well-known, agencies, and ecommerce/CPG.
- Exactly 15 of the 60 companies belong to the shared cross-category core; cohort membership is primary and mutually exclusive, while secondary tags may overlap.
- Initial dossier construction has one aggregate paid-API ceiling of `$2.00` across all 60 companies; each enrichment experiment has one aggregate paid-API ceiling of `$1.00` across its selected fixture set.
- Research is free-first and continues to source saturation: required facts are cited, one first-party plus two independent sources are checked where available, and two new search angles yield no material facts.
- Every enrichment run records GTM Orchestrator discovery and a Nexus search result or a precise Nexus authentication/error record before selecting a new adapter.
- Parallel is search-only. Known URLs route through GTM Orchestrator `web-scraping` v2.1.0: HTTP/html2text, Crawl4AI, Firecrawl standard, Firecrawl JS.
- Google and Meta ads apply where relevant, LinkedIn ads apply to B2B, and TikTok applies to ecommerce/CPG; active, inactive, and unknown are distinct.
- Social activity is filter-only and cannot appear in outbound copy.
- Status is `proposed -> experiment -> candidate -> approved | rejected`. Automated checks may create `candidate`; only an explicit human verdict may create `approved`.
- No secret value enters YAML, Git, logs, evidence, benchmark records, or command output. Paid calls reserve budget before execution and stop at the cap.
- Live paid-provider smoke tests use 1â€“3 fixtures. Missing authentication remains a visible `authentication_required` result and never becomes a fabricated success.
- Preserve unrelated work. If execution needs a worktree, follow `AGENTS.md` and use `<repo>/.worktrees/<task-id>` on `wt/<task-id>`.

---

## File map

- `scripts/company_enrichment/contracts.py`: immutable public request/result, dossier, evidence, seller-context, experiment, and review values.
- `scripts/company_enrichment/definitions.py`: strict YAML loading, schema/version checks, and manifest registry.
- `scripts/company_enrichment/discovery.py`: mandatory GTM/Nexus preflight and reusable-capability selection.
- `scripts/company_enrichment/evidence.py`: content-addressed evidence/cache store and source-saturation state.
- `scripts/company_enrichment/budgets.py`: atomic aggregate reservations and append-only paid-cost ledger.
- `scripts/company_enrichment/providers.py`: provider protocols, normalized errors, and route records.
- `scripts/company_enrichment/adapters/*.py`: Parallel search, GTM waterfall, ads, TechSight, and test-double bridges.
- `scripts/company_enrichment/runner.py`: `EnrichmentRunner.run(EnrichmentRequest) -> EnrichmentResult`.
- `scripts/company_enrichment/corpus.py`: corpus invariant validation and dossier-building orchestration.
- `scripts/company_enrichment/benchmark.py`: deterministic scoring, model comparisons, and blind-review packs.
- `scripts/company_enrichment/review.py`: transition rules and explicit human verdict persistence.
- `scripts/company_enrichment/cli.py`: capability, corpus, run, benchmark, review, and report commands.
- `enrichments/p0/*.yaml`: eight versioned enrichment definitions.
- `benchmarks/companies.yaml`: the 60-company population and 15-company shared core.
- `benchmarks/dossiers/<company-id>.yaml`: field-level ground truth and evidence references.
- `docs/providers/capability-registry.md`: verified GTM/Nexus/provider inventory and operator fallbacks.
- `tests/company_enrichment/*.py`: contracts, discovery, budget, evidence, adapter, runner, corpus, benchmark, review, CLI, and policy tests.

---

### Task 1: Strict contracts and eight P0 definitions

**Files:**
- Create: `requirements.txt`
- Create: `scripts/company_enrichment/__init__.py`
- Create: `scripts/company_enrichment/contracts.py`
- Create: `scripts/company_enrichment/definitions.py`
- Create: `enrichments/p0/*.yaml`
- Create: `tests/company_enrichment/test_contracts.py`
- Create: `tests/company_enrichment/test_definitions.py`

**Interfaces:**
- Produces: immutable requests, results, evidence, dossiers, seller context, experiments, verdicts, and strict YAML definitions.
- Consumes: no provider clients; values serialize canonically without secret-bearing fields.

- [ ] **Step 1: Red-test contracts**

Test frozen schema `1.0` values, canonical serialization, absolute evidence URLs, bounded collections, forbidden secret-bearing keys, and every seller field: target market, personas, capabilities, named offer, timeline, promised outcome, proof, de-risking, exclusions, and current-investment/worldview.

- [ ] **Step 2: Run red**

Run: `py -m pytest tests/company_enrichment/test_contracts.py -q`  
Expected: FAIL because `scripts.company_enrichment` is absent.

- [ ] **Step 3: Implement contracts**

Use frozen slotted dataclasses. Define result statuses `complete|partial|failed`; failures `retryable|terminal|budget_exhausted|authentication_required|contract_invalid|insufficient_evidence`; visibility `message_safe|filter_only`; and review statuses `proposed|experiment|candidate|approved|rejected`.

- [ ] **Step 4: Red-test and implement manifests**

Require eight unique P0 IDs, semantic version `1.0.0`, execution mode, providers/fallbacks, freshness, source rules, caps, early stops, output visibility, dataset version, and both gates. Add `PyYAML>=6.0,<7`; use `yaml.safe_load`; reject unknown keys and environment interpolation.

- [ ] **Step 5: Run green and commit**

Run: `py -m pytest tests/company_enrichment/test_contracts.py tests/company_enrichment/test_definitions.py -q`  
Expected: PASS. Commit as `feat: define company enrichment contracts`.

---

### Task 2: Mandatory GTM Orchestrator and Nexus discovery

**Files:** Create `discovery.py`, `test_discovery.py`, and `docs/providers/capability-registry.md`; modify `.codex/config.toml`, `docs/providers/parallel-search-mcp.md`, and `tests/test_mcp_configuration.py`.

**Interfaces:** `CapabilityDiscovery.discover(enrichment_id) -> DiscoveryRecord`; injected `GtmProbe` and `NexusProbe`; `CapabilityRegistry.select(requirement) -> CapabilityMatch | None`.

- [ ] **Step 1: Red-test the preflight**

Assert every run calls `gtm -> nexus -> select`, records plugin version/path, Nexus query/outcome, reuse choice or verified gap, and converts missing `NEXUS_BOUNDARY_TOKEN` to nonfatal `authentication_required`. Skipping either probe fails closed.

- [ ] **Step 2: Restrict Parallel**

Change the MCP contract to require `enabled_tools == [web_search]`; known-URL retrieval belongs to the GTM waterfall. Run both tests and observe failures before implementation.

- [ ] **Step 3: Implement discovery and registry**

Locate the active GTM plugin deterministically, read current skill/executor metadata, inspect named local tools and relevant Git history, then invoke the injected Nexus probe. Registry entries cover GTM waterfall v2.1.0, `lg_free` Google Ads, LinkedIn Ads, historical Meta actor `ZQyDz7154hrOfrDMK`, TikTok status, TechSight status, and Parallel search-only with provenance, cost class, validation state, and eligible enrichments.

- [ ] **Step 4: Run green and commit**

Run: `py -m pytest tests/company_enrichment/test_discovery.py tests/test_mcp_configuration.py -q`  
Expected: PASS with no network. Commit as `feat: require enrichment capability discovery`.

---

### Task 3: Evidence, cache, saturation, and aggregate budgets

**Files:** Create `evidence.py`, `budgets.py`, `test_evidence.py`, and `test_budgets.py`.

**Interfaces:** `EvidenceStore.put(SourceRecord) -> EvidenceRef`, `EvidenceStore.get(hash)`, `SaturationTracker.observe(SearchAngleResult) -> SaturationState`, and `BudgetLedger.reserve(scope_id, charge) -> Reservation`.

- [ ] **Step 1: Red-test persistence**

Assert content hashes deduplicate identical material, source records are append-only, cache keys include URL/provider/freshness, bounded excerpts point to artifacts, and tampering is detected. Saturation requires cited required fields, first-party plus two independent sources where available, and two consecutive material-fact-free search angles.

- [ ] **Step 2: Red-test aggregate caps**

Parallel reservation attempts must never push `corpus-build` above `$2.00` or any `experiment:<id>` above `$1.00`. Denied work emits `budget_exhausted`; retries are charged; cache hits are not repurchased; resume reuses idempotency keys.

- [ ] **Step 3: Implement stores and atomic ledger**

Write canonical objects under `runs/company-enrichment/objects/<sha256>.json`, append source and charge events to JSONL, and use temp-file plus atomic replace for projections. Reserve estimated maximum cost before a paid call, reconcile actual cost afterward, and reject negative/nonfinite charges.

- [ ] **Step 4: Run green and commit**

Run: `py -m pytest tests/company_enrichment/test_evidence.py tests/company_enrichment/test_budgets.py -q`  
Expected: PASS including concurrent cap tests. Commit as `feat: add bounded enrichment evidence store`.

---

### Task 4: Provider-neutral routing and reusable adapters

**Files:** Create `providers.py`, `adapters/{parallel,gtm_waterfall,ads,techsight}.py`, and `test_providers.py`.

**Interfaces:** `SearchProvider.search(SearchRequest)`, `ScrapeProvider.scrape(KnownUrlRequest)`, `AdsProvider.inspect(AdsRequest)`, `TechnologyProvider.detect(TechnologyRequest)`, and `ProviderRouter.route(definition, discovery) -> RoutePlan`.

- [ ] **Step 1: Red-test route policy**

Recording doubles prove Parallel exposes search only; known URLs call GTM's `firecrawl_waterfall.py`; levels 1â€“2 are free and levels 3â€“4 require reservation; provider failures normalize to the six contract categories; provider payloads never leak into public results.

- [ ] **Step 2: Red-test channel-aware ads**

Applicable companies route Google plus Meta, B2B adds LinkedIn, and ecommerce/CPG adds TikTok. Results preserve `active|inactive|unknown`, dates, geography, angle, offer, CTA, landing page, evidence, and confidence. Meta requires a 1â€“3 URL schema/cost validation before batch eligibility.

- [ ] **Step 3: Implement bridges**

Wrap the current GTM executor rather than copying it. Adapt `lg_free` Google Ads, current LinkedIn Ads, the historical Meta actor only after validation, and TikTok only when the registry proves a current capability. Repair or reinstall the TechSight launcher in its own repository if its import check still fails; record its commit/path/version, otherwise return `authentication_required` or `terminal` with evidence.

- [ ] **Step 4: Run green and commit**

Run: `py -m pytest tests/company_enrichment/test_providers.py -q`  
Expected: PASS entirely on doubles and temporary files. Commit as `feat: route enrichment providers safely`.

---

### Task 5: Deep enrichment runner and P0 executors

**Files:** Create `runner.py`, `executors.py`, `prompts/*.md`, `test_runner.py`, and `test_p0_executors.py`.

**Interfaces:** `EnrichmentRunner.run(request: EnrichmentRequest) -> EnrichmentResult`; executors consume only validated definitions, dossier/evidence views, seller context, and provider protocols.

- [ ] **Step 1: Red-test orchestration**

Assert exact order: validate -> discover GTM/Nexus -> load cache -> reserve -> collect evidence -> execute -> validate output -> append result. Cover cache/resume, bounded retry, early stop, partial evidence, exact requested/resolved model IDs, deterministic latency/cost accounting, and all normalized failures.

- [ ] **Step 2: Red-test safety and seller context**

Parameterized tests cover all eight P0 definitions. Filter-only social facts are rejected from message-safe text. Job-opportunity and analogy/value outputs must cite the shared `SellerContext` instead of inventing an offer, proof, promise, timeline, or guarantee.

- [ ] **Step 3: Implement the runner and executors**

Keep routing, cache, budgets, retries, evidence, model resolution, schema validation, and failure normalization inside `EnrichmentRunner`. Executors only assemble queries/extraction instructions and produce typed field assertions. Use the model ladder GPT-5 nano, GPT-4o mini, GPT-4.1 mini, and GPT-5.6 Luna through injected LLM clients.

- [ ] **Step 4: Run green and commit**

Run: `py -m pytest tests/company_enrichment/test_runner.py tests/company_enrichment/test_p0_executors.py -q`  
Expected: PASS with fake providers/models. Commit as `feat: execute reusable company enrichments`.

---

### Task 6: Select the 60-company corpus and build dossiers

**Files:** Create `corpus.py`, `benchmarks/companies.yaml`, `benchmarks/dossiers/*.yaml`, `docs/benchmarks/company-selection-policy.md`, `test_corpus.py`, and `test_dossiers.py`.

**Interfaces:** `Corpus.load(path) -> tuple[CompanyFixture, ...]`, `Corpus.validate(as_of)`, and `DossierBuilder.build(fixture, scope) -> CompanyDossier`.

- [ ] **Step 1: Red-test population invariants**

Require 60 unique IDs/domains, exactly 10 per primary cohort, exactly 15 `shared_core: true`, no duplicate primary membership, and difficulty coverage `easy|ambiguous|hard` in every cohort. Recently funded entries require a dated primary funding source within the 12 months preceding the recorded `as_of` date; local entries require a Maps place/listing reference.

- [ ] **Step 2: Select and document fixtures**

Reuse suitable existing ground-truth companies, then fill cohort gaps with diverse legal entities and business models. Record selection reason, identity/domain, primary cohort, secondary tags, difficulty, expected ad channels, and qualifying evidence. Avoid subsidiaries/brands that duplicate another fixture's research surface.

- [ ] **Step 3: Build core then full dossiers**

Build all 15 shared-core dossiers first, validate them, then build the other 45. Each dossier contains field-level assertions for identity, description, offers, ICP/personas, news/launches, growth, ads, hiring, competitors, technology, pricing, sources, unknowns, and human corrections. Continue free-first to saturation; one ledger scope `corpus-build` enforces `$2.00` total.

- [ ] **Step 4: Verify and commit**

Run: `py -m pytest tests/company_enrichment/test_corpus.py tests/company_enrichment/test_dossiers.py -q`  
Then run the corpus validator and assert `companies=60 cohorts=6 each=10 core=15 paid_cost<=2.00`. Commit as `data: add company benchmark corpus` without claiming incomplete fields are known.

---

### Task 7: Benchmark scoring, blind review, and human-only approval

**Files:** Create `benchmark.py`, `review.py`, `tests/company_enrichment/test_benchmark.py`, and `test_review.py`.

**Interfaces:** `BenchmarkRunner.run(ExperimentPlan) -> BenchmarkReport`, `score_result(result, dossier) -> ScoreCard`, `ReviewStore.create_pack(report)`, and `ReviewStore.record_human_verdict(verdict)`.

- [ ] **Step 1: Red-test deterministic scoring**

Use fixed fixtures/prompts/settings and score field correctness, citation validity/completeness/freshness, cost, and latency. Store synchronous and Batch API tracks separately with exact requested/resolved model IDs. Re-running against cached evidence may spend on models but never repurchases sources.

- [ ] **Step 2: Red-test transitions**

Table-test every legal/illegal transition. Mechanical gates may move `experiment` to `candidate`; no automated API can emit `approved`. Approval requires a blind human verdict scoring readability, specificity, usefulness, casualness, and non-creepiness with reviewer ID and timestamp.

- [ ] **Step 3: Implement append-only reports**

Every experiment has one `$1.00` aggregate ledger. Compare GPT-5 nano, GPT-4o mini, GPT-4.1 mini, and GPT-5.6 Luna per enrichment; promotion is per enrichment. Blind packs hide model/provider identity until verdict capture and retain failures rather than dropping them.

- [ ] **Step 4: Run green and commit**

Run: `py -m pytest tests/company_enrichment/test_benchmark.py tests/company_enrichment/test_review.py -q`  
Expected: PASS, including proof automation cannot approve. Commit as `feat: benchmark and review enrichments`.

---

### Task 8: Operator CLI and policy documentation

**Files:** Create `cli.py`, `scripts/company_enrichment_cli.py`, `test_cli.py`, `docs/company-enrichment-library.md`; modify `README.md` and `docs/enrichment-library-backlog.md`.

**Interfaces:** Commands `capabilities scan`, `corpus validate|build`, `run`, `benchmark`, `review`, and `report`; JSON output and nonzero typed failures.

- [ ] **Step 1: Red-test commands**

Subprocess tests cover help, invalid inputs, dry-run with zero client construction, resume, JSON output, explicit paid opt-in, exact aggregate cap flags, Nexus-auth fallback, and review rejection. `review --approve` requires a human verdict file and cannot accept a generated verdict.

- [ ] **Step 2: Implement thin composition**

Default to free-only. Paid execution requires both `--allow-paid` and `--max-paid-cost`; corpus rejects values above `2.00`, experiments reject values above `1.00`. CLI delegates to package interfaces and prints artifact paths, not credentials or raw provider payloads.

- [ ] **Step 3: Document exact operations**

Document capability preflight, Nexus token remediation, GTM waterfall levels, TechSight/ads validation, corpus selection, source saturation, resume, costs, blind review, and why P1 technology/pricing/reviews/funding remain out of this build.

- [ ] **Step 4: Run green and commit**

Run: `py -m pytest tests/company_enrichment/test_cli.py tests/test_mcp_configuration.py -q`; then run every `--help` and a free dry-run. Commit as `docs: add enrichment library operations`.

---

### Task 9: Live smoke, full proof, and handoff

**Files:** Create `docs/reports/company-enrichment-verification.md`; modify benchmark dossiers/results only through the CLI.

**Interfaces:** Consumes all prior commands/artifacts; produces an evidence matrix and blind-review packs, never an automated approval.

- [ ] **Step 1: Run free preflight and corpus proof**

Run capability scan, credential scan, definition validation, and corpus validation. Nexus auth failure is recorded. Verify GTM waterfall `--help`, Parallel search-only config, TechSight import status, and ads route eligibility without spend.

- [ ] **Step 2: Run bounded live construction**

Build the 15-company core first and then remaining dossiers, using cached/free sources before any paid call. The authorized command may use `--allow-paid --max-paid-cost 2.00`; the ledger must prove aggregate paid cost never exceeds `$2.00`. Provider auth gaps produce explicit unknowns and do not stop other sources.

- [ ] **Step 3: Smoke-test paid adapters and experiments**

Use 1â€“3 applicable fixtures for each credentialed paid adapter, including Meta schema/transform validation. Run each P0 benchmark with `--max-paid-cost 1.00`, starting with the 15-company core. Do not widen an adapter after a failed smoke test; preserve its failure artifact.

- [ ] **Step 4: Run complete verification**

Run `py -m pytest -q`, `git diff --check`, `py scripts/credential_scan.py`, all validators, and CLI dry-run/resume checks. Require no new skip/xfail, 60/10/15 invariants, cap proofs, append-only histories, exact model IDs, filter-only safety, and zero automated approvals.

- [ ] **Step 5: Write handoff and commit**

The report lists commands, exit codes, counts, costs, routes, evidence gaps, authentication gaps, candidate enrichments, and human-review paths. Commit as `docs: verify company enrichment library`; require clean status. Do not push, merge, deploy, or mark any candidate approved.

---

## Self-review checklist

- Spec coverage: discovery, evidence/cache, aggregate budgets, 60/10/15 corpus, eight P0 enrichments, ads, seller context, model ladder, benchmark, human approval, live smoke, and safety each map to a task.
- Type consistency: `EnrichmentRunner.run(EnrichmentRequest) -> EnrichmentResult`, `CapabilityDiscovery.discover`, `EvidenceStore.put`, `BudgetLedger.reserve`, and review transitions retain one spelling and direction.
- Scope: technology changes, pricing changes, third-party review mining, and standalone funding/traction remain P1; people/contact discovery remains excluded.
- Cost: `$2.00` is one corpus-wide cap and `$1.00` is one experiment-wide cap, never per company.
- Completion: unavailable credentials create explicit evidence gaps; they do not weaken deterministic acceptance tests or authorize fabricated data.
