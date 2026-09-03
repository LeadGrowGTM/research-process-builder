# Handoff 2026-08-24: enrichment loops session

**Branch:** `wt/buying-trigger` in worktree
`C:\Users\mitch\Everything_CC\tools\data\research-process-builder\.worktrees\icp-persona-loop`
(worktree dir is named after its previous branch; the checkout is
`wt/buying-trigger`, based on current master `bdad649`).

## Shipped this session (all merged to master via PR #11, commit bdad649)

- **Luna repricing** to 2026-08-20 list rates (0.20 in / 0.02 cache read /
  0.25 cache write / 1.20 out per 1M). `MODEL_PRICES` updated; sync costs
  bill cache-write tokens at the write rate; estimates reserve at
  max(input, write). Old table overstated luna ~4.3x - it is why luna's v1
  ICP attempt cap-blocked at zero spend and 20 mini lineages were annealed
  unnecessarily.
- **gpt-4o-mini** priced and added to `EXPERIMENT_MODELS` as the standing
  benchmark-only cheap-tier floor (matrix 4 models x 2 tracks). Bench on
  frozen v3 signal prompts: news 0.779/0.764, comp 0.804/0.879 - below the
  0.90 gate, not production-approved. Rest of gpt-4o family stays banned.
- **Signal prompts graduated** (Mitch approved 2026-08-21): news-v11-luna
  0.974/0.997 and comp-v11-luna 0.933/0.960; winning candidates are now the
  canonical `prompts/company-enrichment/news-product-launches.md` (moved to
  `enrichments/news-product-launches/news-product-launches.md`) and
  `competitor-intelligence.md`; candidates archived under
  `archive/2026-08-21-graduated-signal-prompt-candidates/`.
- **ICP/persona enrichment approved** (Mitch, 2026-08-24): shipping lineage
  **icp-persona-live-v27-luna, dev 1.00 / holdout 1.00, zero hard failures,
  ~USD 0.0014/company**. Casing normalized deterministically in
  `_payload_from_execution`; specificity rule added; no-mistakes pipeline
  caught and fixed a dropped Personas prompt section (restored as
  "## Persona rules", validated by v27) and a latent legacy-branch KeyError.
  Full story: `docs/reports/icp-persona-anneal.md`.
- **README reframed** (two layers: enrichment loops + process factory);
  cost figures at current rates (~$11/1k companies sync, ~$6/1k batch for
  news+competitors on luna).
- **Ads enrichment backlog closed** - it was already approved 2026-08-18
  ("Approve after GT fixes" on `docs/reports/ads-v3-review-sheet.md`); the
  backlog entry was stale.

## In flight: buying-trigger prompt loop (this branch, unpushed)

Human-guided anneal, no automated scorer. Two campaign-idea signals per
company over 10 cached SaaS dossiers,
`prompts/company-enrichment/buying-trigger-analysis.md` +
`scripts/company_enrichment_buying_trigger_loop.py` (sys.path shim added).

Lineage history (runs/company-enrichment/buying-trigger/, untracked):
- v1 (0.1.0, mini), v2 (0.2.0, mini) - prior session
- v3-luna (0.2.0): Mitch picked luna over mini; exposed **example
  parroting** - saas-01 returned the prompt's AgencyAnalytics-shaped
  good_output_example verbatim
- v4-luna (0.3.0): examples moved to non-benchmark verticals; parroting
  fixed; residual generic buyers (saas-07 "Companies announcing
  acquisitions")
- v5-luna (0.4.0): specific-buyer-archetype rule; generics fixed but two
  regressions: saas-06 semantic mismatch ("Procurement leaders announcing
  new manufacturing facilities" - wrong actor; v4's "Manufacturers
  announcing new plant openings" was right) and saas-03 both ideas are
  hiring signals (near-paraphrase). Each run ~USD 0.011.

**Decision pending from Mitch** (options presented, not yet answered):
1. 0.5.0 micro-round (recommended): add rule "when the observable event is
   produced by a different actor than the buyer, name the actor who
   produces it" - restores v4's saas-06 semantics, keeps the generic ban.
2. Accept v5 as-is.
3. Accept v4.

After the decision: write `docs/reports/buying-trigger-anneal.md`, close the
backlog entry, run `/no-mistakes` on this branch (validate-only), merge PR.

## How to run a lineage

```powershell
# from C:\Users\mitch\Everything_CC\pipelines\gtm-orchestrator (prod secrets)
$env:BUYING_TRIGGER_MODEL='gpt-5.6-luna'
lg run --env prod py <worktree>\scripts\company_enrichment_buying_trigger_loop.py --lineage buying-trigger-live-v6-luna --allow-paid
# ICP loop: same pattern with ICP_LOOP_MODEL and company_enrichment_icp_loop.py
```

## Gotchas

- Shell cwd resets between tool calls in this harness - `cd` explicitly per
  command or use absolute paths.
- Pre-commit hook runs the full pytest suite (~2 min) - give commits a
  10-minute timeout.
- no-mistakes test step needs the mirror repo trusted:
  `projects["C:/Users/mitch/.no-mistakes/repos/e6ff3f73e3d5.git"].hasTrustDialogAccepted: true`
  in `~/.claude.json` (done; backup at `~/.claude.json.bak-nomistakes-trust`).
- `runs/` is untracked and per-worktree; lineage artifacts for
  buying-trigger and icp-persona live in THIS worktree only.
- Approved models: gpt-4.1-mini, gpt-5-nano, gpt-5.6-luna (prod);
  gpt-4o-mini benchmarks only. Never full gpt-4.1 or other gpt-4o.
- The main worktree (`wt/company-enrichment-b2b`) has unrelated uncommitted
  changes (backlog.md edit, `.agents/`, benchmarks-company-corpus-proposal
  doc) - leave them alone.

## Remaining board after buying-trigger

- Company-corpus experiment enrichments (description 0.774, ICP now done,
  growth 0.719) - description and growth-signals still below gate on mini;
  luna retry is the obvious cheap experiment given the ICP result.
- `wt/signal-enrichments` and `wt/icp-persona-loop` branches fully merged -
  worktree/branch cleanup possible (never delete without Mitch's say).
