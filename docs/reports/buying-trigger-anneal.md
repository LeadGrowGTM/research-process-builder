# Buying-trigger anneal: accepted at v6-luna (prompt 0.5.0)

**Date:** 2026-08-25
**Branch:** `wt/buying-trigger` (worktree `.worktrees/icp-persona-loop`)
**Loop:** human-guided anneal, no automated scorer. Two campaign-idea buying
signals per company over the 10 cached SaaS dossiers.
`prompts/company-enrichment/buying-trigger-analysis.md` +
`scripts/company_enrichment_buying_trigger_loop.py`.
**Decision:** Mitch accepted the 0.5.0 micro-round on 2026-08-25; v6-luna is
the accepted lineage and 0.5.0 is the canonical prompt.

## Lineage history

| Lineage | Model | Prompt | Cost (USD) | Outcome |
|---|---|---|---|---|
| buying-trigger-live-v1 | gpt-4.1-mini | 0.1.0 | 0.0099 | baseline; generic, internal-state phrasing |
| buying-trigger-live-v2 | gpt-4.1-mini | 0.2.0 | 0.0100 | public-signal rules added; still flat |
| buying-trigger-live-v3-luna | gpt-5.6-luna | 0.2.0 | 0.0113 | Mitch picked luna; exposed example parroting (saas-01 returned the prompt's AgencyAnalytics-shaped good_output_example verbatim) |
| buying-trigger-live-v4-luna | gpt-5.6-luna | 0.3.0 | 0.0107 | examples moved to non-benchmark verticals; parroting fixed; residual generic buyers (saas-07 "Companies announcing acquisitions") |
| buying-trigger-live-v5-luna | gpt-5.6-luna | 0.4.0 | 0.0111 | specific-buyer-archetype rule; generics fixed; two regressions: saas-06 wrong actor, saas-03 two near-paraphrase hiring signals |
| **buying-trigger-live-v6-luna** | **gpt-5.6-luna** | **0.5.0** | **0.0118** | **both v5 regressions fixed; accepted** |

Total model spend across the loop: USD 0.0648. No source purchases.

## The 0.5.0 hypothesis (micro-round)

v5's "start every signal with the specific buyer archetype" rule forced the
buyer into signals whose observable event is produced by someone else. saas-06
(Arkestro, procurement SaaS) regressed from v4's correct "Manufacturers
announcing new plant openings" to "Procurement leaders announcing new
manufacturing facilities" - the wrong actor. 0.5.0 replaces that rule with:

> Start every signal with the actor who produces the observable event. When
> that actor is the buyer use the specific buyer archetype from the Evidence.
> When a different actor produces the event name that actor instead of the
> buyer.

The generic-buyer ban is unchanged.

## v6-luna outputs (full set, for the approval record)

| Company | campaign_idea_1 | campaign_idea_2 |
|---|---|---|
| saas-01 AgencyAnalytics | Marketing agencies posting client reporting manager roles | Marketing agencies announcing multiple new client wins |
| saas-02 AgilePoint | CIOs announcing enterprise AI governance programs | Architecture teams posting agentic AI architect roles |
| saas-03 Aligned | Software sales teams posting enterprise account executive roles | Enterprise software buyers publishing multi-stakeholder procurement requirements |
| saas-04 aPriori | Manufacturers posting design-for-manufacturability engineering roles | Manufacturers announcing new product launches |
| saas-05 Archive360 | Regulated enterprises posting records management roles | Regulated enterprises announcing new AI deployments |
| saas-06 Arkestro | Enterprise procurement teams posting strategic sourcing manager roles | Manufacturers announcing new production facilities |
| saas-07 Betterworks | HR leaders posting performance management job openings | Fast-growing organizations announcing leadership development programs |
| saas-08 BigPanda | IT operations teams posting incident management jobs | IT operations teams publishing outage postmortems |
| saas-09 Bitly | Digital marketers posting campaign analytics roles | Digital marketers announcing new multi-channel campaigns |
| saas-10 Built | Construction lenders posting construction loan operations roles | Construction lenders announcing new construction loan programs |

All 10 companies returned both ideas with citations; `unknowns` empty
throughout; every signal within the 12-word limit; no example parroting.

**Regression checks against v5:**

- saas-06: idea_2 is "Manufacturers announcing new production facilities" -
  v4's correct actor semantics restored while idea_1 keeps the specific buyer
  archetype. Fixed.
- saas-03: hiring signal + procurement-requirements signal - no longer two
  near-paraphrase hiring signals. Fixed.
- saas-07 (v4's generic offender): idea_1 names HR leaders; idea_2 uses
  "Fast-growing organizations", a qualified buyer, not a bare generic. Holds.

**Residual notes (accepted as-is):**

- saas-03 idea_2 "publishing multi-stakeholder procurement requirements" is
  public via RFP portals but harder to research at scale than job posts or
  announcements.
- saas-07 idea_2 "Fast-growing organizations" passes the qualified-buyer rule
  but is the loosest archetype in the set.
- saas-05 idea_2 and saas-06 idea_2 carry a single citation each; the rules
  require citation, not citation count.

## Status

`buying-trigger-analysis.md` 0.5.0 is canonical with `provisional: false`.
This loop is human-judged: there is no ground-truth scorer, so this Approval
rests on the recorded output review above rather than a programmed >= 0.90
gate. If a scored version is wanted later, build ground truth first and rerun
under the standard gate.

## Rerun

```powershell
# from C:\Users\mitch\Everything_CC\pipelines\gtm-orchestrator (prod secrets)
$env:BUYING_TRIGGER_MODEL='gpt-5.6-luna'
lg run --env prod py <worktree>\scripts\company_enrichment_buying_trigger_loop.py --lineage buying-trigger-live-v7-luna --allow-paid
```
