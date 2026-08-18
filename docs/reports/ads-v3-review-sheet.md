# ads-v3 human review sheet (running-ads-offer-intelligence)

**Date:** 2026-08-18  
**Lineage:** ads-v3, gpt-4.1-mini, dev 1.00 / holdout 1.00, gate `human_review_required`  
**Decision needed:** approve / revise / reject the ads Experiment (prompt + ground truth) as a reusable enrichment.

## What the 1.00 does and does not prove

- 8 of 10 companies score on `status` only (no Meta copy in GT), so the score is mostly "did the model repeat the adapter statuses faithfully". It did.
- Ground truth was drafted from the same collection the model reads. A wrong provider result is wrong in both GT and output and still scores 1.00. Provider correctness is the thing only a human live check can catch.
- Meta `unknown` on 5 of 10 = no verified FB/IG handle found on the website, not "no ads". That is a handle-discovery gap, scored as correct by design.
- Meta `not_found` on a matched page (saas-02, saas-07) is scored `inactive` at 0.7 confidence; Meta rate limiting produces the same empty result. Both need a live look.

## Findings already caught while building this sheet

1. **saas-01 GT `call_to_action: Learn more` looks wrong.** The cited Meta Evidence samples 4 active ads: 3 x `Contact us`, 1 x `Learn more`; landing `/p/enterprise` x3, `/p/free-trial` x1. Model said `Contact us`. Passed only because `offer` token recall hit, so the CTA mismatch was invisible to the score. Recommend GT -> `Contact us` (or store both).
2. **Meta Evidence excerpt carries only 4-5 sampled ads** out of 90-122 active. Offer/angle summaries are drawn from that sample. Fine for a signal, but say so in the contract docs; a reviewer comparing to the full Ad Library will see ads not in the excerpt.
3. **saas-08 landing page is the bare homepage** (`https://www.bigpanda.io/`). Correct per Evidence, low downstream value. Not a defect, but worth knowing the field is often uninformative.

## Suggested review procedure (about 20 minutes)

1. Open each Google link below, confirm active/inactive matches the live Transparency Center (spot check at least saas-02 and saas-04, the two `inactive`).
2. Open each Meta link. For saas-02 and saas-07 (`not_found` on matched page) confirm the page truly has no ads. For the 5 `unknown`, search the brand name in Ad Library and note whether a page with ads exists (that measures the handle-discovery miss rate).
3. For saas-01 and saas-08 read the offer/angle against the live ads and mark keep / rewrite.
4. Fill the Verdict column, then decide: approve as-is, approve after GT fixes (rerun ads-v4 to confirm still >= 0.90), or reject and specify.

## Per-company sheet

### saas-01 (dev) - agencyanalytics.com

| Field | Provider Evidence | Ground truth | ads-v3 output | Verdict |
|---|---|---|---|---|
| Google status | active; 200 creatives; advertiser AgencyAnalytics; last_seen 2026-08-18 | active | active | |
| Meta status | done via handle_fb page 120680864620158 (AgencyAnalytics); active 90 / inactive 15; last_ad 2026-08-10; sampled CTAs {'Contact us': 3, 'Learn more': 1} | active | active | |
| Meta landing page | see sampled ads | https://agencyanalytics.com/p/enterprise | https://agencyanalytics.com/p/enterprise | |
| Meta CTA | see sampled CTAs above | Learn more | Contact us | |
| Meta offer | ad copy | enterprise agency reporting plan and free trial | enterprise reporting plan and free trial | |
| Meta angle (model only) | ad copy | - | automate marketing reports to save time and scale agencies | |

Live check: [Google Ads Transparency](https://adstransparency.google.com/advertiser/AR10909288365736067073?region=anywhere) · [Meta Ad Library](https://www.facebook.com/ads/library/?active_status=all&ad_type=all&view_all_page_id=120680864620158)

### saas-02 (dev) - agilepoint.com

| Field | Provider Evidence | Ground truth | ads-v3 output | Verdict |
|---|---|---|---|---|
| Google status | inactive; 0 creatives; advertiser -; last_seen  | inactive | inactive | |
| Meta status | not_found via handle_fb page 177024768973 (AgilePoint); active 0 / inactive 0; last_ad -; sampled CTAs {} | inactive | inactive | |

Live check: [Google Ads Transparency](https://adstransparency.google.com/?domain=agilepoint.com) · [Meta Ad Library](https://www.facebook.com/ads/library/?active_status=all&ad_type=all&view_all_page_id=177024768973)

### saas-03 (holdout) - alignedup.com

| Field | Provider Evidence | Ground truth | ads-v3 output | Verdict |
|---|---|---|---|---|
| Google status | active; 66 creatives; advertiser Team Aligned Inc.; last_seen 2026-08-18 | active | active | |
| Meta status | no verified FB/IG handle on website -> not queried | unknown | absent | |

Live check: [Google Ads Transparency](https://adstransparency.google.com/advertiser/AR02856337359908110337?region=anywhere) · [Meta Ad Library](https://www.facebook.com/ads/library/?active_status=all&ad_type=all&country=ALL&q=alignedup)

### saas-04 (dev) - apriori.com

| Field | Provider Evidence | Ground truth | ads-v3 output | Verdict |
|---|---|---|---|---|
| Google status | inactive; 0 creatives; advertiser -; last_seen  | inactive | inactive | |
| Meta status | no verified FB/IG handle on website -> not queried | unknown | absent | |

Live check: [Google Ads Transparency](https://adstransparency.google.com/?domain=apriori.com) · [Meta Ad Library](https://www.facebook.com/ads/library/?active_status=all&ad_type=all&country=ALL&q=apriori)

### saas-05 (dev) - archive360.com

| Field | Provider Evidence | Ground truth | ads-v3 output | Verdict |
|---|---|---|---|---|
| Google status | active; 31 creatives; advertiser ARCHIVE360, LLC; last_seen 2026-08-18 | active | active | |
| Meta status | no verified FB/IG handle on website -> not queried | unknown | absent | |

Live check: [Google Ads Transparency](https://adstransparency.google.com/advertiser/AR02088763061287518209?region=anywhere) · [Meta Ad Library](https://www.facebook.com/ads/library/?active_status=all&ad_type=all&country=ALL&q=archive360)

### saas-06 (holdout) - arkestro.com

| Field | Provider Evidence | Ground truth | ads-v3 output | Verdict |
|---|---|---|---|---|
| Google status | active; 5 creatives; advertiser Arkestro; last_seen 2026-08-17 | active | active | |
| Meta status | no verified FB/IG handle on website -> not queried | unknown | absent | |

Live check: [Google Ads Transparency](https://adstransparency.google.com/advertiser/AR11915849041436475393?region=anywhere) · [Meta Ad Library](https://www.facebook.com/ads/library/?active_status=all&ad_type=all&country=ALL&q=arkestro)

### saas-07 (dev) - betterworks.com

| Field | Provider Evidence | Ground truth | ads-v3 output | Verdict |
|---|---|---|---|---|
| Google status | active; 65 creatives; advertiser Betterworks Systems INC; last_seen 2026-08-18 | active | active | |
| Meta status | not_found via handle_ig page 119824318076252 (Betterworks); active 0 / inactive 0; last_ad -; sampled CTAs {} | inactive | inactive | |

Live check: [Google Ads Transparency](https://adstransparency.google.com/advertiser/AR05644020440683773953?region=anywhere) · [Meta Ad Library](https://www.facebook.com/ads/library/?active_status=all&ad_type=all&view_all_page_id=119824318076252)

### saas-08 (holdout) - bigpanda.io

| Field | Provider Evidence | Ground truth | ads-v3 output | Verdict |
|---|---|---|---|---|
| Google status | active; 89 creatives; advertiser BigPanda Inc; last_seen 2026-08-18 | active | active | |
| Meta status | done via handle_fb page 281226961919874 (BigPanda); active 122 / inactive 7; last_ad 2026-08-17; sampled CTAs {'Learn more': 5} | active | active | |
| Meta landing page | see sampled ads | https://www.bigpanda.io/ | https://www.bigpanda.io/ | |
| Meta CTA | see sampled CTAs above | Learn more | Learn more | |
| Meta offer | ad copy | agentic ITOps platform | Agentic ITOps platform | |
| Meta angle (model only) | ad copy | - | Bring shared context, faster action, and fewer disruptions to IT operations with BigPanda’s Agentic ITOps platform. | |

Live check: [Google Ads Transparency](https://adstransparency.google.com/advertiser/AR13267933737246523393?region=anywhere) · [Meta Ad Library](https://www.facebook.com/ads/library/?active_status=all&ad_type=all&view_all_page_id=281226961919874)

### saas-09 (dev) - bitly.com

| Field | Provider Evidence | Ground truth | ads-v3 output | Verdict |
|---|---|---|---|---|
| Google status | active; 200 creatives; advertiser Bitly, Inc; last_seen 2026-08-18 | active | active | |
| Meta status | done via handle_fb page 111454522278222 (Bitly); active 0 / inactive 200; last_ad 2026-06-26; sampled CTAs {'Learn more': 3, 'Sign up': 1} | inactive | inactive | |
| Meta landing page | see sampled ads | None | https://bitly.com/pages/landing/bringing-us-all-a-bit-closer?utm_source=facebook&utm_medium=paid_social&utm_campaign=retargeting_website_visitors_image_fulldynamic | |
| Meta CTA | see sampled CTAs above | None | Learn more | |
| Meta offer | ad copy | None | Bitly's Connection Platform | |
| Meta angle (model only) | ad copy | - | Unlock the full power of your links. Explore Bitly's Connection Platform today. | |

Live check: [Google Ads Transparency](https://adstransparency.google.com/advertiser/AR03806981014568304641?region=anywhere) · [Meta Ad Library](https://www.facebook.com/ads/library/?active_status=all&ad_type=all&view_all_page_id=111454522278222)

### saas-10 (holdout) - getbuilt.com

| Field | Provider Evidence | Ground truth | ads-v3 output | Verdict |
|---|---|---|---|---|
| Google status | active; 28 creatives; advertiser Built Technologies, Inc.; last_seen 2026-08-18 | active | active | |
| Meta status | no verified FB/IG handle on website -> not queried | unknown | absent | |

Live check: [Google Ads Transparency](https://adstransparency.google.com/advertiser/AR01149563939693002753?region=anywhere) · [Meta Ad Library](https://www.facebook.com/ads/library/?active_status=all&ad_type=all&country=ALL&q=getbuilt)

## Decision

- [x] Approve after GT fixes: saas-01 `call_to_action` corrected `Learn more` -> `Contact us` (3 of 4 sampled ads); ads-v3 saas-01 output rescored deterministically at 1.00, no model rerun needed.
- [ ] Approve ads-v3 as-is
- [ ] Reject (reason):

Reviewer: Mitch (via session, "should be good")  Date: 2026-08-18

Standing caveats carried into the Approval: score measures faithful reporting of adapter output plus offer summarization; Meta `unknown` on 5 of 10 is a handle-discovery gap; Meta `not_found` on a matched page may be rate limiting. Live spot checks of provider truth were not performed.
