# Live Company Corpus Execution Design

**Status:** Approved for implementation and live execution on 2026-08-12.

## Goal

Build research-complete, cited dossiers for all 60 approved B2B fixtures, then
begin deterministic enrichment experiments. Safety controls constrain the run;
they are not preconditions for further planning.

## Architecture

Reuse `AutoresearchOrchestrator` as the outer Experiment lifecycle. Add one
company-enrichment runner beneath its executor seam rather than creating a
second orchestration system. The runner composes the existing capability
discovery, eligible provider routing, evidence/cache store, aggregate budget
ledger, source saturation, and typed dossier contracts.

The outer loop owns resumable roles, retry gates, artifact persistence, and
evaluation. The company runner owns fixture validation, provider calls,
field-level evidence, dossier persistence, and research-completeness checks.

## Research-complete contract

A fixture is selection-complete only when it records verified identity and
canonical domain, B2B buyer and business-facing offer, selection reason,
primary cohort and qualifying evidence, secondary tags, difficulty, expected
ad channels, and the cohort-specific funding or local-listing evidence.

A dossier is research-complete when identity, description, offers, ICP and
personas, news and launches, growth, ads, hiring, competitors, technology, and
pricing are each represented by cited field assertions or explicit unknowns.
Evidence retains URL, retrieval time, hash, excerpt, provider, and freshness.
Human corrections are append-only. No unresolved field may disappear merely
because a provider failed.

## Execution order

Run exactly once per fixture in this order:

1. SaaS shared core: `saas-01`, `saas-04`, `saas-07`.
2. The seven remaining SaaS fixtures.
3. Recently funded B2B.
4. B2B agencies.
5. Well-known B2B.
6. B2B commerce suppliers.
7. Local B2B services.

Each stage advances only after its fixtures pass the research-complete
validator. Resume skips completed fixtures and cached sources.

## Provider and budget behavior

Known URLs use the GTM homepage waterfall. Free levels run first; approved
Firecrawl escalation may use the existing aggregate `corpus-build` ceiling of
USD 2.00. Structured free sources and targeted search fill remaining gaps.
Parallel remains search-only. Other paid providers are not authorized by this
design. Every paid call requires an owned reservation and actual-cost
reconciliation.

## Testing and launch

Implement with fake Source Adapters first. Tests cover the full runner order,
research-completeness rejection, explicit unknowns, resume, saturation,
budget exhaustion, and the non-repeating SaaS rollout. Then run the three SaaS
core fixtures live, inspect the durable artifacts, and continue automatically
through the remaining stages unless a real authentication, budget, or source
integrity failure prevents progress.

After all 60 dossiers validate, begin fixed-fixture experiments for company
description, ICP/personas, and growth signals. Programmed scores are Evidence;
they do not create Approval without human review.
