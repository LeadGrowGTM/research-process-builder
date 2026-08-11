# Company Enrichment Library Harness

## PLANNER_BRIEF

Read first:

1. `docs/superpowers/specs/2026-08-11-company-enrichment-library-design.md`
2. `docs/superpowers/plans/2026-08-11-company-enrichment-library.md`
3. `docs/enrichment-library-backlog.md`
4. `docs/providers/parallel-search-mcp.md`
5. GTM Orchestrator `C:\Users\mitch\Everything_CC\pipelines\gtm-orchestrator\.claude\skills\web-scraping\SKILL.md`

Write `PLAN.md` before task artifacts. Use the plan's nine task boundaries and record execution shape `goal-loop with dynamic corpus/research phase`. Preserve dependency order: contracts -> discovery -> evidence/budgets -> adapters -> runner -> corpus -> benchmark/review -> CLI -> proof. Allocate 80 turns: Planner 1-5, Maker 6-62, Prover/Checker cycles 63-74, report 75-80. Human approval of enrichment output is outside this harness.

Acceptance reward is deterministic: `accepted_checks / required_checks * 100`. PASS requires exactly 100%, focused/full tests green, successful CLI proof, and no critical invariant failure. Maximum three cycles.

Paid ceilings are aggregate, never per company: `$2.00` total for all 60 dossier builds and `$1.00` total per enrichment experiment. Direct people/contact discovery and P1 technology/pricing/reviews/standalone funding work are excluded.

## MAKER_ROUTING

- Phase 1: `codebase-design` then `tdd` and `implement` -- contracts and eight strict P0 manifests.
- Phase 2: `codebase-design` then `tdd` and `implement` -- mandatory GTM/Nexus discovery and provider registry.
- Phase 3: `tdd` then `implement` -- content-addressed evidence, saturation, cache/resume, aggregate ledgers.
- Phase 4: `tdd` then `implement`, plus GTM `web-scraping` -- provider adapters and waterfall routing.
- Phase 5: `codebase-design`, `tdd`, `implement` -- deep runner and P0 executors.
- Phase 6: `tdd` then `implement` -- 60 companies, 10 per cohort, 15 shared core, cited dossiers.
- Phase 7: `tdd` then `implement` -- benchmark, model ladder, blind packs, human-only approval.
- Phase 8: `tdd` then `implement` -- CLI and operator documentation.
- Phase 9: `superpowers:verification-before-completion` -- bounded live smoke, full proof, report.

Known URLs must use GTM waterfall v2.1.0. Query Nexus every run; current missing `NEXUS_BOUNDARY_TOKEN` is a visible, nonfatal `authentication_required` record. Parallel is search-only. TechSight needs an import smoke. Meta actor `ZQyDz7154hrOfrDMK` requires 1-3 URL schema/cost validation; absent current TikTok capability stays unknown. Commit and append literal command proof to `PROGRESS.md` at every phase boundary.

## PROVER_BRIEF

Feature intent: the internal CLI safely builds, runs, benchmarks, reviews, and reports company enrichments while enforcing discovery, evidence, and aggregate-cost policy.

How to exercise after Maker creates `scripts/company_enrichment_cli.py`:

```powershell
py scripts/company_enrichment_cli.py --help
py scripts/company_enrichment_cli.py capabilities scan --json --dry-run
py scripts/company_enrichment_cli.py corpus validate --json
py scripts/company_enrichment_cli.py run --enrichment company-description --company-id <fixture-id> --dry-run --json
py scripts/company_enrichment_cli.py run --enrichment company-description --company-id <fixture-id> --allow-paid --max-paid-cost 2.01 --json
py scripts/company_enrichment_cli.py review --approve --verdict-file <invalid-generated-file> --json
```

Auth: dry-run requires none. Never reveal credentials or make an unbounded paid call.

Accept criteria: help and dry-run exit 0; corpus reports 60/10/15; JSON exposes artifact paths but no secrets/raw payloads; excess cap and invalid/generated approval exit nonzero with typed errors; missing Nexus auth is visible and nonfatal. Paste raw output and return `Feature: works` only if all behaviors match.

## REDTEAM_BRIEF

N/A -- internal CLI/library. Normal gates still reject secret leakage, cap bypass, filter-only outbound use, and automated approval.

## CHECKER_BRIEF

The CLI and `docs/reports/company-enrichment-verification.md` are future artifacts that the Maker must create before Prover/Checker evaluation.

Scoring is deterministic: for each dimension, 5=100% of binary checks pass, 4=90-99%, 3=75-89%, 2=50-74%, and 1=<50%. Every passed check needs literal file/line or command evidence; these bands override subjective interpretation.

Evaluate the implementation, manifests, corpus/dossiers, test artifacts, CLI proof, and `docs/reports/company-enrichment-verification.md`; do not read Maker self-assessment. Start: "I did not write this."

Score these evidence-backed dimensions 1-5: (1) contracts/manifests, (2) GTM/Nexus discovery and provider routing, (3) evidence/cache/saturation and atomic budgets, (4) adapters/runner/policy safety, (5) 60/10/15 corpus and cited dossiers, (6) benchmark/model identity/human-only review, (7) CLI behavior, (8) verification quality. A 5 means every associated acceptance-manifest check has literal file/line or command evidence; a 1 means absent or contradicted.

Reward signal is the programmatically generated acceptance-manifest pass rate. PASS threshold: exactly `100%` and no failed Prover verdict. Any secret leak, cap breach, wrong corpus count, missing GTM/Nexus preflight, filter-only leakage, or automated `approved` blocks PASS. Missing authentication may remain a documented gap when deterministic fallbacks and explicit unknowns are correct.

Write `CYCLE_LOG.md` with dimension scores, evidence, reward, verdict, weakest dimension, and one fix target. If three cycles produce the same reward, return PLATEAU.

## LOOP_TRACKER

## Loop Tracker
> Update this file as you complete each step. Check off items in order.

### Planner
- [ ] HARNESS.md read
- [ ] skill-routing.md read
- [ ] PLAN.md written: `<path>`

### Cycle 1
- [ ] Maker: Phases 1-5 library foundation -- artifact: `<path>` -- commit: `<SHA>`
- [ ] Maker: Phases 6-7 corpus and benchmark -- artifact: `<path>` -- commit: `<SHA>`
- [ ] Maker: Phases 8-9 CLI and verification -- artifact: `<path>` -- commit: `<SHA>`
- [ ] Mechanical gate: passed
- [ ] Prover: PROOF VERDICT received -- Feature: works | broken
- [ ] Checker: CYCLE_LOG.md written: `<path>`
- [ ] Reward signal: __% (threshold: 100%)
- [ ] Verdict: PASS / ITERATE / PLATEAU

### Cycle 2 (if ITERATE)
- [ ] Fix target: <weakest dimension from Cycle 1>
- [ ] Maker: changes applied -- commit: `<SHA>`
- [ ] Mechanical gate: passed
- [ ] Prover: PROOF VERDICT received -- Feature: works | broken
- [ ] Checker: CYCLE_LOG.md updated
- [ ] Reward signal: __%
- [ ] Verdict: PASS / ITERATE / PLATEAU

### Cycle 3 (if ITERATE again)
- [ ] Fix target: <weakest dimension from Cycle 2>
- [ ] Maker: changes applied -- commit: `<SHA>`
- [ ] Mechanical gate: passed
- [ ] Prover: PROOF VERDICT received -- Feature: works | broken
- [ ] Checker: CYCLE_LOG.md updated
- [ ] Reward signal: __%
- [ ] Verdict: PASS / PLATEAU (max cycles reached)

### Final
- [ ] HANDOFF.md written: `<path>`
- [ ] HANDOFF.html written: `<path>`
- [ ] HANDOFF.excalidraw written: `<path>`
- [ ] HANDOFF.html published: `<ht-ml.app URL>` (or export fallback + reason in HANDOFF.md)
