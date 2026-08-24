# ICP/persona anneal: approved at v26-luna

**Date:** 2026-08-24
**Branch:** `wt/icp-persona-loop` (worktree `.worktrees/icp-persona-loop`)
**Goal sentence:** `{buyer} {relationship} {outcome}` with relationship one of
`looking for`, `who need`, `in the market for`; rubric one_focus .25,
word_limit .20, grammar .20, citation .20, buyer .10, plain_language .05;
gate >= 0.90 with zero hard failures, then human review.

## Result

| Lineage | Model | Dev | Holdout | Hard failures | Cost |
|---|---|---|---|---|---|
| icp-persona-live-v24 | gpt-4.1-mini | 0.775 | 0.888 | 6 dev + 2 holdout | 0.014 |
| icp-persona-live-v25 | gpt-4.1-mini | 0.808 | 0.888 | 6 dev + 2 holdout | 0.014 |
| icp-persona-live-v25-luna | gpt-5.6-luna | 1.00 | 1.00 | none | 0.014 |
| **icp-persona-live-v26-luna** | **gpt-5.6-luna** | **1.00** | **1.00** | **none** | **0.014** |

**Approved:** Mitch reviewed the full v26-luna output set on 2026-08-24
("looks quite good") after two explicit revision rounds; no further changes
requested. The winning prompt is already the canonical
`prompts/company-enrichment/icp-persona-analysis.md` (the loop's baseline is
that file), so there is no separate graduation fold. Ship model:
**gpt-5.6-luna** at 2026-08-20 list rates - about USD 0.0014 per company,
the same price as gpt-4.1-mini, which stalled at dev 0.78-0.81 across
twenty lineages and never cleared the gate.

## What closed the gap

1. **Luna itself.** v1 tried luna and was refused at USD 0 spend: the stale
   1.00/6.00 price table made the conservative estimator blow the USD 1 cap.
   Twenty mini lineages of prompt annealing followed. After the 2026-08-20
   repricing (0.20/0.02/0.25/1.20), luna ran and cleared both splits on the
   first attempt (v25-luna) with the same prompt that scored 0.808 on mini.
2. **Deterministic casing normalization** (reviewer feedback, code not
   prompt): `_payload_from_execution` lowercases spurious mid-sentence
   capitals in outcomes ("looking for Client retention") while preserving
   acronyms and mixed-case proper nouns, and capitalizes the buyer's first
   letter. Covered by `tests/company_enrichment/test_icp_loop_normalization.py`.
3. **Specificity rule** (reviewer feedback, prompt): name the concrete
   Evidence problem, never a broader business benefit (retention, revenue,
   growth) unless Evidence states it as the buyer's own problem, plus a
   wrong/right example pair. Turned "Client retention" into "faster client
   reporting" and "Proven regulatory compliance" into "audit-ready data
   evidence".

## Approved v26-luna outputs

Dev: Marketing agencies looking for faster client reporting · Enterprise
architecture teams looking for governed AI operations · Manufacturers looking
for faster product development · Regulated enterprises looking for audit-ready
data evidence · HR leaders looking for workforce capability visibility ·
Marketers who need clear campaign performance.

Holdout: Software sales teams looking for faster deal closing · Procurement
teams looking for trapped savings · IT operations teams who need reduced IT
downtime · Construction lenders looking for lower construction loan risk.

Citation spot-check: "trapped savings" (saas-06) is the company's own
language - the cited LinkedIn Evidence reads "unlock trapped savings" - not a
model invention.

## Known nits (accepted, future iteration fodder)

- saas-08 repeats the buyer word "IT" inside the outcome; the prompt forbids
  it but the evaluator does not score it.
- saas-07's secondary outcome "business priority progress" is clunky.

## How to reproduce

```powershell
# from C:\Users\mitch\Everything_CC\pipelines\gtm-orchestrator (prod secrets)
$env:ICP_LOOP_MODEL='gpt-5.6-luna'
lg run --env prod py <worktree>\scripts\company_enrichment_icp_loop.py --lineage <name> --allow-paid
```
