# Luna signal review sheet: news-v11-luna and comp-v11-luna

**Date:** 2026-08-19  
**Lineages:** news-v11-luna (gpt-5.6-luna, prompt news-v3-kind-rules, dev 0.974 / holdout 0.997) and comp-v11-luna (gpt-5.6-luna, prompt comp-v3-category-sanity, dev 0.933 / holdout 0.960), no hard failures.  
**Decision needed:** approve / revise / reject each Experiment as a reusable enrichment.

Every entry below is the grounded output actually shipped by the loop: the deterministic postprocess already dropped uncited dates, evergreen pages, duplicates, hallucinated or self competitors, and re-bucketed named/inferred from the Evidence. Scores compare these entries to the sealed ground truth; a reviewer should judge whether the entries are true and useful, and spot anything both the model and the ground truth missed.

## Where the information comes from

Collection is Serper (Google search + news endpoints) plus a free scrape waterfall over first-party paths (newsroom, blog, press pages); no paid sources. What the shipped entries cite:

**News/launches (65 entries, 101 citations):** 50% PR wires (prnewswire, businesswire, globenewswire), 23% first-party newsroom/blog/changelog pages, 9% other third-party pages (partner newsrooms like mozaic.net, investor pages like canapi.com), 5% trade press (TechCrunch, CRN, Bloomberg, bizjournals), 4% Google search-result snippets, the rest company databases and one social post.

**Competitors (211 entries, 403 citations):** 39% third-party comparison/alternatives blog pages (often a competitor's own "alternative to X" page, e.g. tallyfy.com, okrstool.com), 25% review sites and software directories (Gartner Peer Insights 43, TrustRadius 17, G2 14, Capterra 14, Slashdot 9), 19% Google search-result snippets that quote those lists, 7% company databases (CB Insights 14, Craft, 6sense, Sumble), 5% the subject's own competitor pages, 2% community threads (feed the `inferred` bucket).

Practical read: news quality rides on wire coverage; a company with no PR wire footprint yields first-party blog events or nothing. Competitor quality rides on review-site and "alternatives" listicle coverage; niche companies (Built) get thinner, database-driven sets.

## news-v11-luna, per company

### saas-01 (dev) - AgencyAnalytics (agencyanalytics.com)

| Kind | Date | Type | Event | Source | Verdict |
|---|---|---|---|---|---|
| news | 2024-04-11 | leadership | Expanded its senior leadership team to support growth and innovation ([source](https://www.prnewswire.com/news-releases/agencyanalytics-announces-senior-leadership-team-expansion-signifying-its-commitment-to-growth-and-innovation-302114035.html), prnewswire.com) | prnewswire.com | |
| launches | 2023-12-12 | feature | Unveiled time-saving features for marketing agencies ([source](https://www.prnewswire.com/news-releases/agencyanalytics-unveils-raft-of-time-saving-features-for-marketing-agencies-302012543.html), prnewswire.com) | prnewswire.com | |
| launches | 2024-01-30 | product | Launched 11-Second Smart Reports for faster client reporting ([source](https://www.prnewswire.com/news-releases/agencyanalytics-unveils-11-second-smart-reports--marketing-agencies-get-comprehensive-client-reports-faster-than-tying-a-shoelace-302047734.html), prnewswire.com) | prnewswire.com | |
| launches | 2026-03-09 | feature | Added a LinkedIn followers-over-time metric to its integration ([source](https://updates.agencyanalytics.com/changelog/linkedin-followers-over-time-metric-now-available), updates.agencyanalytics.com) | updates.agencyanalytics.com | |

### saas-02 (dev) - AgilePoint (agilepoint.com)

| Kind | Date | Type | Event | Source | Verdict |
|---|---|---|---|---|---|
| launches | 2026-04-01 | integration | Introduced an AgilePoint NX connector with Qwen process activities and AI Control Towers ([source](https://blog.agilepoint.com/agilepoint_nx_connector-for-qwen/), blog.agilepoint.com) | blog.agilepoint.com | |
| launches | 2026-04-25 | product | Launched a redesigned SharePoint On-Premises solution with Workflow Manager support ([source](https://blog.agilepoint.com/introducing-agilepoint-for-sharepoint-onpremises-subscription-edition-with-sharepoint-workflow-manager-support/), blog.agilepoint.com) | blog.agilepoint.com | |

### saas-03 (holdout) - Aligned (alignedup.com)

| Kind | Date | Type | Event | Source | Verdict |
|---|---|---|---|---|---|
| news | 2026-07-01 | funding | Raised $60 million in Series B funding ([source](https://www.globenewswire.com/news-release/2026/07/01/3320495/0/en/aligned-closes-60m-series-b-to-solidify-leadership-position-as-the-system-of-action-for-b2b-sales.html), globenewswire.com) | globenewswire.com | |
| news | 2025-02-19 | award | Earned a place on G2’s 2025 Best Software Awards for sales software ([source](https://www.prnewswire.com/news-releases/aligned-earns-spot-on-g2s-2025-best-software-awards-for-top-sales-software-302380406.html), prnewswire.com) | prnewswire.com | |
| news | 2023-05 | funding | Raised $5.8 million in a seed funding round ([source](https://www.nfx.com/post/why-nfx-invested-in-aligned), nfx.com) | nfx.com | |

### saas-04 (dev) - aPriori Technologies (apriori.com)

| Kind | Date | Type | Event | Source | Verdict |
|---|---|---|---|---|---|
| news | 2023-10-19 | funding | Received growth investment from Vista Credit Partners ([source](https://www.vistaequitypartners.com/news/apriori-receives-growth-investment-from-vista-credit-partners-for-its-manufacturing-insights-platform/), vistaequitypartners.com) | vistaequitypartners.com | |
| news | 2021-07-20 | award | CEO Stephanie Feraday was accepted into the Forbes Technology Council ([source](https://www.businesswire.com/news/home/20210720005324/en/Stephanie-Feraday-President-CEO-of-aPriori-Accepted-into-Forbes-Technology-Council), businesswire.com) | businesswire.com | |
| launches | 2026-05-26 | product | Launched aiSource, an AI sourcing solution for procurement teams ([source](https://www.businesswire.com/news/home/20260526130779/en/aPriori-Launches-aiSource-an-AI-Sourcing-Solution-Giving-Procurement-Teams-the-Manufacturing-and-Cost-Intelligence-to-Win-More-Supplier-Negotiations), businesswire.com) | businesswire.com | |

### saas-05 (dev) - Archive360 (archive360.com)

| Kind | Date | Type | Event | Source | Verdict |
|---|---|---|---|---|---|
| news | 2026-08-11 | award | Was named to the 2026 Inc. 5000 list for the second consecutive year ([source](https://www.morningstar.com/news/pr-newswire/20260811ny23510/archive360-named-to-the-inc-5000-list-of-fastest-growing-private-companies-in-america-2026), morningstar.com) | morningstar.com | |
| news | 2026-06-10 | other | Achieved FedRAMP authorization for its platform ([source](https://www.prnewswire.com/news-releases/archive360-achieves-fedramp-authorization-302796181.html), prnewswire.com) | prnewswire.com | |
| news | 2026-01-27 | positioning | Positioned its platform as an AI entry point beyond traditional archiving ([source](https://www.crn.com/news/storage/2026/archive360-transforms-from-archive-tool-to-ai-entry-point-ceo), crn.com) | crn.com | |
| news | 2025-10-15 | award | Was named a Leader in Gartner’s 2025 Magic Quadrant for the second consecutive year ([source](https://www.archive360.com/a360-news-blog/archive360-named-a-leader-in-the-2025-gartner-magic-quadrant-for-the-second-consecutive-year), archive360.com) | archive360.com | |
| news | 2025-10-14 | partnership | Collaborated with Microsoft on agentic AI for detecting and preserving policy violations ([source](https://www.archive360.com/a360-news-blog/archive360-collaborates-with-microsoft-to-deliver-agentic-ai-that-detects-investigates-and-preserves-policy-violations), archive360.com) | archive360.com | |
| news | 2025-08-21 | partnership | Joined forces with Neev Data on an enterprise fleet solution ([source](https://www.facebook.com/neevdata/posts/were-proud-to-join-forces-with-archive360-to-deliver-a-breakthrough-in-enterpris/122185184000337235/), facebook.com) | facebook.com | |
| news | 2019-05-30 | expansion | Reported 331 percent growth while continuing its expansion trajectory ([source](https://www.prnewswire.com/news-releases/archive360-achieves-331-percent-growth-and-continues-expansion-trajectory-300858985.html), prnewswire.com) | prnewswire.com | |
| launches | 2025-05-13 | product | Launched a governed AI-ready data cloud for modern enterprises ([source](https://www.prnewswire.com/news-releases/archive360-launches-a-governed-ai-ready-data-cloud-to-meet-future-needs-of-the-modern-enterprise-302453204.html), prnewswire.com) | prnewswire.com | |

### saas-06 (holdout) - Arkestro (arkestro.com)

| Kind | Date | Type | Event | Source | Verdict |
|---|---|---|---|---|---|
| news | 2025-05-14 | funding | Secured $36 million in strategic investment to accelerate predictive procurement innovation ([source](https://www.prnewswire.com/news-releases/arkestro-secures-36m-in-strategic-investment-to-accelerate-predictive-procurement-innovation-302454539.html), prnewswire.com) | prnewswire.com | |
| news | 2025-10-15 | expansion | Expanded its Chevron customer relationship across global markets ([source](https://arkestro.com/press-releases/arkestro-announces-customer-expansion-with-chevron-driving-predictive-procurement-transformation-across-global-markets/), arkestro.com) | arkestro.com | |
| news | 2025-12-19 | positioning | Reported significant customer momentum and cross-industry growth heading into 2026 ([source](https://www.prnewswire.com/news-releases/arkestro-drives-significant-customer-momentum-growth-across-industries-heading-into-2026-302646472.html), prnewswire.com) | prnewswire.com | |
| news | 2026-02-25 | partnership | Collaborated with Nissan Americas on predictive procurement transformation ([source](https://www.prnewswire.com/news-releases/arkestro-announces-collaboration-with-nissan-to-support-predictive-procurement-transformation-302697203.html), prnewswire.com) | prnewswire.com | |
| news | 2026-04-30 | award | Earned recognition in The Hackett Group’s Spring 2026 procurement technology assessment ([source](https://www.prnewswire.com/news-releases/arkestro-earns-validation-in-the-hackett-groups-spring-2026-solutionmap-procurement-technology-assessment-302759116.html), prnewswire.com) | prnewswire.com | |
| news | 2026-06-30 | award | Was listed as a Gartner sample vendor for procurement and sourcing solutions ([source](https://www.prnewswire.com/news-releases/arkestro-listed-as-a-sample-vendor-in-gartner-hype-cycle-for-procurement-and-sourcing-solutions-for-four-consecutive-years-302814412.html), prnewswire.com) | prnewswire.com | |
| news | 2026-08-06 | leadership | Added experienced SaaS leaders as Ben Leiken transitioned from CTO ([source](https://www.prnewswire.com/news-releases/arkestro-momentum-drives-leadership-growth-and-appoints-industry-powerhouse-veterans-302844608.html), prnewswire.com) | prnewswire.com | |
| launches | 2025-10-29 | product | Unveiled Arkestro Labs and new AI-powered predictive procurement offerings ([source](https://www.prnewswire.com/news-releases/arkestro-announces-arkestro-labs-powerful-new-procurement-ai-offerings-at-optimal-25-302598472.html), prnewswire.com) | prnewswire.com | |

### saas-07 (dev) - Betterworks (betterworks.com)

| Kind | Date | Type | Event | Source | Verdict |
|---|---|---|---|---|---|
| news | 2026-06-02 | acquisition | Acquired Rypple to advance manager effectiveness and AI-native performance management ([source](https://www.businesswire.com/news/home/20260602407140/en/Betterworks-Acquires-Rypple-to-Advance-Manager-Effectiveness-and-AI-Native-Performance-Management), businesswire.com) | businesswire.com | |
| news | 2020-02-20 | acquisition | Acquired Hyphen to connect employee engagement with business impact ([source](https://www.prnewswire.com/news-releases/betterworks-acquires-hyphen-enabling-every-enterprise-to-better-connect-employee-engagement-to-business-impact-301008194.html), prnewswire.com) | prnewswire.com | |
| news | 2017-07-26 | leadership | CEO stepped down amid a former employee’s sexual harassment lawsuit ([source](https://www.bloomberg.com/news/articles/2017-07-26/betterworks-ceo-duggan-resigns-amid-sexual-harassment-lawsuit), bloomberg.com) | bloomberg.com | |
| news | 2019-10-10 | other | Released its first report on continuous performance management outcomes ([source](https://www.prnewswire.com/news-releases/betterworks-research-indicates-adopting-continuous-performance-management-delivers-improved-business-outcomes-300934527.html), prnewswire.com) | prnewswire.com | |
| launches | 2026-05-06 | product | Unveiled AI-powered Talent Intelligence for stronger business execution ([source](https://www.businesswire.com/news/home/20260506230172/en/Betterworks-Unveils-AI-Powered-Talent-Intelligence-to-Turn-Performance-Into-a-Business-Execution-Engine), businesswire.com) | businesswire.com | |
| launches | 2026-07-14 | integration | Launched AI capabilities connecting performance data to AI assistants ([source](https://www.businesswire.com/news/home/20260714162785/en/Betterworks-Launches-New-AI-Capabilities-That-Connect-Performance-Data-to-AI-Assistants), businesswire.com) | businesswire.com | |
| launches | 2019-10-15 | feature | Released multiple features bringing continuous performance management into daily workflows ([source](https://www.prnewswire.com/news-releases/betterworks-announces-multiple-new-product-features-that-bring-continuous-performance-management-into-the-flow-of-work-300938624.html), prnewswire.com) | prnewswire.com | |

### saas-08 (holdout) - BigPanda (bigpanda.io)

| Kind | Date | Type | Event | Source | Verdict |
|---|---|---|---|---|---|
| news | 2026-08-03 | partnership | Partnered with Mozaic to bring AI-powered operational intelligence to service management ([source](https://mozaic.net/newsroom/mozaic-announces-partnership-with-bigpanda-to-bring-ai-powered-operational-intelligence-to-enterprise-service-management/), mozaic.net) | mozaic.net | |
| news | 2026-04-01 | partnership | Partnered with ServiceNow on advanced event intelligence and incident automation ([source](https://www.businesswire.com/news/home/20260401511917/en/BigPanda-Partners-with-ServiceNow-to-Extend-ServiceNow-with-Advanced-Event-Intelligence-and-Incident-Automation), businesswire.com) | businesswire.com | |
| news | 2025-12-16 | partnership | Partnered with Downdetector by Ookla on external observability ([source](https://www.bigpanda.io/press-release/downdetector-partnership/), bigpanda.io) | bigpanda.io | |
| news | 2025-11-10 | acquisition | Acquired Velocity to advance agentic IT operations ([source](https://www.bigpanda.io/blog/bigpanda-acquires-velocity/), bigpanda.io) | bigpanda.io | |
| news | 2024-03-18 | leadership | Appointed Tom Melzl as chief revenue officer ([source](https://www.businesswire.com/news/home/20240318601210/en/BigPanda-Appoints-Tom-Melzl-as-Chief-Revenue-Officer), businesswire.com) | businesswire.com | |
| news | 2022-08-17 | funding | Raised a $20 million Series E extension ([source](https://techcrunch.com/2022/08/17/aiops-startup-bigpanda-raises-series-e-extension-bringing-its-total-capital-to-340m/), techcrunch.com) | techcrunch.com | |
| news | 2022-04-07 | leadership | Named Rick Underwood chief revenue officer ([source](https://www.globenewswire.com/news-release/2022/04/07/2418568/0/en/bigpanda-names-rick-underwood-chief-revenue-officer.html), globenewswire.com) | globenewswire.com | |
| news | 2022-01-12 | funding | Raised $190 million at a $1.2 billion valuation ([source](https://www.globenewswire.com/news-release/2022/01/12/2365641/0/en/bigpanda-raises-190-million-in-funding-at-1-2-billion-valuation.html), globenewswire.com) | globenewswire.com | |
| launches | 2026-04-13 | product | Introduced the BigPanda L1 Agent for autonomous IT operations ([source](https://www.bigpanda.io/blog/bigpanda-l1-agent/), bigpanda.io) | bigpanda.io | |
| launches | 2026-04-08 | integration | Made the BigPanda for ServiceNow application available in the ServiceNow Store ([source](https://www.bigpanda.io/blog/bigpanda-servicenow-snow-knowledge-2026/), bigpanda.io) | bigpanda.io | |
| launches | 2025-12-05 | feature | Introduced the BigPanda Triage Agent for agentic L1 operations ([source](https://www.bigpanda.io/blog/triage-agent/), bigpanda.io) | bigpanda.io | |
| launches | 2025-05-28 | product | Launched its agentic IT operations platform ([source](https://www.businesswire.com/news/home/20250528507389/en/BigPanda-Launches-Agentic-IT-Operations-to-Bring-Intelligent-Automation-to-the-%24200-billion-ITOps-Market), businesswire.com) | businesswire.com | |
| launches | 2015-06-25 | integration | Integrated BigPanda with the Librato monitoring platform ([source](https://www.prnewswire.com/news-releases/bigpanda-now-integrated-with-librato-platform-300104790.html), prnewswire.com) | prnewswire.com | |

### saas-09 (dev) - Bitly (bitly.com)

| Kind | Date | Type | Event | Source | Verdict |
|---|---|---|---|---|---|
| news | 2026-03-19 | leadership | Appointed Matt Young as chief technology officer ([source](https://www.google.com/goto?url=CAESygEB7keqTRLitJncaKJFjGQCEqbM_z49ECqC6xTQ-gb6hlu3ffKex7j_ZvuLFVBNwmZ_Z9oGs1T5c9xpvbVqiIEsLRrfd3qibiSbrZq6w2TodeINC_Bl3tAxpnP8lucBXyLzjEHhuqJ5TPLrpP2CBFAX-VGBU_-l9QPXEySp8UUTNe8q-cr_KF_RJFH-YA7cMX-OpHZcReKGFUrKm7QzfoZrPhtXGDEQAmfX_wlnfacW29ibf40wMMVoKctlNu9er1RY_-eTzuaBrAjZ), google.com) | google.com | |
| news | 2026-02-03 | positioning | Positioned Bitly as embedded infrastructure for links and QR codes ([source](https://www.prnewswire.com/news-releases/bitly-enters-2026-as-the-embedded-connection-layer-powering-links-and-qr-codes-worldwide-302676857.html), prnewswire.com) | prnewswire.com | |
| news | 2026-04-20 | positioning | Rebranded QR Code Generator as QRCG by Bitly ([source](https://www.qr-code-generator.com/blog/introducing-qrcg-by-bitly/), qr-code-generator.com) | qr-code-generator.com | |
| launches | 2026-04-02 | feature | Released Bitly Assist and Weekly Insights for faster marketing analysis ([source](https://bitly.com/blog/introducing-bitly-assist-weekly-insights/), bitly.com) | bitly.com | |
| launches | 2026-03-09 | integration | Introduced integrations with ChatGPT, Claude, and Microsoft Copilot ([source](https://bitly.com/blog/introducing-bitly-llm-integrations/), bitly.com) | bitly.com | |

### saas-10 (holdout) - Built (getbuilt.com)

| Kind | Date | Type | Event | Source | Verdict |
|---|---|---|---|---|---|
| news | 2017-05-23 | partnership | Expanded its lender platform integration through Trinity Real Estate Solutions ([source](https://www.prnewswire.com/news-releases/trinity-real-estate-solutions-expands-portfolio-with-built-technologies-draw-management-platform-integration-300462313.html), prnewswire.com) | prnewswire.com | |
| news | 2018-04-04 | funding | Raised $21 million as founder Chase Gilbert led the company’s growth ([source](https://www.bizjournals.com/nashville/news/2018/04/04/the-boss-builtschase-gilbert-raised-millions-lives.html), bizjournals.com) | bizjournals.com | |
| news | 2021-09-30 | funding | Raised $125 million in Series D funding at a $1.5 billion valuation ([source](https://www.businesswire.com/news/home/20210930005263/en/Built-Technologies-Announces-%24125M-Series-D-Financing-Round-Led-by-New-Investor-TCV), businesswire.com) | businesswire.com | |
| news | 2022-02-07 | positioning | Surpassed $200 billion in managed construction value and reached unicorn status ([source](https://www.canapi.com/investment/built), canapi.com) | canapi.com | |
| news | 2023-01-05 | leadership | Added Bora Chung and Matt Marenghi as company advisors ([source](https://www.businesswire.com/news/home/20230105005660/en/Built-Technologies-Names-Digital-Payment-Expert-Bora-Chung-and-Engineering-Veteran-Matt-Marenghi-as-Advisors), businesswire.com) | businesswire.com | |
| news | 2023-04-13 | funding | Secured an investment from Citi ([source](https://www.canapi.com/investment/built), canapi.com) | canapi.com | |
| news | 2024-01-22 | leadership | Named Carnell Elliott senior vice president of sales ([source](https://www.businesswire.com/news/home/20240122780686/en/Built-Technologies-Names-Tech-Veteran-SVP-of-Sales), businesswire.com) | businesswire.com | |
| news | 2025-09-15 | partnership | Powered Regions Bank’s digital portal for real estate banking clients ([source](https://ir.regions.com/news-events/press-releases/news-details/2025/Industry-First-Regions-Bank-Launches-Convenient-Seamless-Digital-Portal-for-Real-Estate-Banking-Clients/default.aspx), ir.regions.com) | ir.regions.com | |
| launches | 2021-12-14 | product | Introduced Built Pay to simplify construction payments ([source](https://financialpost.com/pmn/press-releases-pmn/business-wire-news-releases-pmn/built-technologies-introduces-the-future-of-construction-payments-built-pay), financialpost.com) | financialpost.com | |
| launches | 2024-03-11 | product | Expanded into owner and developer finance with a new management and payments product ([source](https://www.businesswire.com/news/home/20240311655232/en/Built-Expands-Platform-to-Real-Estate-Owners-and-Developers-Unveils-Next-Generation-Financial-Management-and-Payments-Product), businesswire.com) | businesswire.com | |
| launches | 2024-06-10 | feature | Unveiled unified commercial real estate financing and portfolio management capabilities ([source](https://www.businesswire.com/news/home/20240610211292/en/Built-Unveils-Unified-CRE-Financing-and-Asset-Portfolio-Management-Enhancing-Lender-Efficiency-and-Performance), businesswire.com) | businesswire.com | |
| launches | 2025-11-04 | product | Launched Draw Agent to approve construction-loan draws in minutes ([source](https://getbuilt.com/news/built-launches-draw-agent-to-approve-construction-loan-draws-in-minutes/), getbuilt.com) | getbuilt.com | |

## comp-v11-luna, per company

### saas-01 (dev) - AgencyAnalytics (agencyanalytics.com)

| Bucket | Competitor | Domain | Rel. | Why (model) | Cited from | Verdict |
|---|---|---|---|---|---|---|
| named | Swydo | swydo.com | direct | Described as a direct like-for-like alternative and compared with AgencyAnalytics. | seranking.com, swydo.com | |
| named | DashThis | - | direct | Listed as an AgencyAnalytics alternative for automated marketing reporting. | reddit.com, seranking.com | |
| named | Whatagraph | - | direct | Presented as a replacement for AgencyAnalytics with faster setup and stronger visuals. | funnel.io, reportingninja.com | |
| named | Funnel | funnel.io | direct | Listed among tools that can replace AgencyAnalytics for client reporting. | funnel.io | |
| named | Databox | databox.com | direct | Described as an AgencyAnalytics alternative for reporting and automated insights. | databox.com, funnel.io | |
| named | Klipfolio | - | direct | Listed as a client-reporting replacement and automated reporting option. | agencyanalytics.com, funnel.io | |
| named | Porter Metrics | portermetrics.com | direct | Listed among the top tested AgencyAnalytics alternatives. | portermetrics.com | |
| named | Supermetrics | - | adjacent | Compared with AgencyAnalytics as a marketing data and reporting alternative. | agencyanalytics.com, portermetrics.com | |
| named | Dataslayer | - | adjacent | Listed among the top AgencyAnalytics alternatives for marketing reporting. | portermetrics.com | |
| named | Windsor.ai | windsor.ai | direct | Presented as an AgencyAnalytics alternative with comparable platform quality. | portermetrics.com, windsor.ai | |
| named | ReportGarden | - | direct | Included in AgencyAnalytics’s side-by-side automated reporting comparisons. | agencyanalytics.com | |
| named | TapClicks | tapclicks.com | direct | Compared directly with AgencyAnalytics on pricing, campaign limits, and SEO reporting. | agencyanalytics.com, tapclicks.com | |
| named | Looker Studio | - | alternative | Included in AgencyAnalytics’s comparison of automated reporting options. | agencyanalytics.com | |
| named | Octoboard | - | direct | Included in AgencyAnalytics’s side-by-side marketing analytics platform comparison. | agencyanalytics.com | |
| named | SE Ranking | seranking.com | adjacent | Included among AgencyAnalytics alternatives spanning reporting and SEO platforms. | agencyanalytics.com, seranking.com | |
| named | Reporting Ninja | reportingninja.com | direct | Listed as the best overall AgencyAnalytics alternative for agencies. | reportingninja.com | |
| named | Geckoboard | - | direct | Listed among other AgencyAnalytics alternatives for reporting and dashboards. | blog.coupler.io | |
| named | SegMetrics | segmetrics.io | adjacent | Positioned as an AgencyAnalytics alternative for easier, more accurate lifetime reporting. | segmetrics.io | |
| inferred | Reportei | - | direct | Named by an agency user evaluating alternatives to AgencyAnalytics. | reddit.com | |
| inferred | Oviond | - | direct | Named among tools tried as alternatives to AgencyAnalytics. | reddit.com | |
| inferred | ZapDigits | - | direct | Named as the reporting alternative ultimately selected instead of AgencyAnalytics. | reddit.com | |

Grounding: 3 bucket(s) reassigned from the Evidence; 1 unverifiable domain(s) nulled

### saas-02 (dev) - AgilePoint (agilepoint.com)

| Bucket | Competitor | Domain | Rel. | Why (model) | Cited from | Verdict |
|---|---|---|---|---|---|---|
| named | Nintex | - | direct | Listed as an AgilePoint alternative across Gartner, G2, and Tallyfy comparison pages. | g2.com, gartner.com, google.com | |
| named | Appian | - | direct | Listed as an AgilePoint alternative across Gartner, G2, and Tallyfy comparison pages. | g2.com, gartner.com, google.com | |
| named | Bizagi | - | direct | Listed as an AgilePoint alternative across Gartner, G2, and Tallyfy comparison pages. | gartner.com, google.com, tallyfy.com | |
| named | OpenText | - | direct | Gartner lists OpenText among AgilePoint alternatives for business process management platforms. | gartner.com, google.com | |
| named | Oracle BPM | - | direct | Tallyfy lists Oracle BPM among alternatives to AgilePoint. | tallyfy.com | |
| named | ProcessMaker | - | direct | Gartner lists ProcessMaker among AgilePoint alternatives. | gartner.com, google.com | |
| named | Agiloft | - | adjacent | Gartner lists Agiloft among AgilePoint business process automation alternatives. | gartner.com, google.com | |
| named | Pegasystems | - | direct | Named in a BPM comparison with AgilePoint and listed by Gartner as an alternative. | gartner.com, tablesprint.com | |
| named | ABBYY | - | adjacent | Gartner lists ABBYY among AgilePoint business process automation alternatives. | gartner.com, google.com | |
| named | Tallyfy | tallyfy.com | direct | Tallyfy describes itself as a proven alternative to AgilePoint. | google.com, tallyfy.com | |
| named | Microsoft Power Automate | - | direct | G2 names Microsoft Power Automate among the best AgilePoint NX alternatives. | g2.com, google.com | |
| named | Informatica Intelligent Data Management Cloud (IDMC) | - | adjacent | PeerSpot lists Informatica IDMC among the top AgilePoint alternative solutions. | google.com, peerspot.com | |
| named | Automation Anywhere | - | adjacent | Compared directly with AgilePoint in BPM and robotic process automation comparisons. | peerspot.com, slashdot.org | |
| named | Camunda | - | direct | PeerSpot and Tallyfy list Camunda among AgilePoint alternatives. | peerspot.com, tallyfy.com | |
| named | Square 9 Softworks | - | direct | TrustRadius lists Square 9 Softworks among AgilePoint NX alternatives. | trustradius.com | |
| named | Quixy | - | direct | TrustRadius lists Quixy among AgilePoint NX alternatives. | trustradius.com | |
| named | CMW Platform | - | direct | TrustRadius and SoftwareWorld list CMW Platform among AgilePoint alternatives. | softwareworld.co, trustradius.com | |
| named | Creatio | - | direct | TrustRadius lists Creatio among AgilePoint NX alternatives. | trustradius.com | |
| named | IBM Cloud Pak for Business Automation | - | direct | TrustRadius lists IBM Cloud Pak for Business Automation among AgilePoint NX alternatives. | trustradius.com | |
| named | TIBCO® BPM | - | direct | TrustRadius lists TIBCO BPM among AgilePoint NX alternatives. | trustradius.com | |
| named | NICE Robotic Process Automation | - | adjacent | 6sense compares NICE Robotic Process Automation directly with AgilePoint. | 6sense.com | |
| named | Ultimus Digital Process Automation Suite | - | direct | SourceForge provides a direct AgilePoint NX versus Ultimus comparison. | sourceforge.net | |
| named | ServiceNow | - | adjacent | Tallyfy lists ServiceNow among alternatives to AgilePoint. | tallyfy.com | |
| named | K2 | - | direct | Tallyfy lists K2 among alternatives to AgilePoint. | tallyfy.com | |
| named | Mendix | - | direct | Tallyfy lists Mendix among alternatives to AgilePoint. | tallyfy.com | |
| named | IBM Blueworks | - | adjacent | Tallyfy lists IBM Blueworks among alternatives to AgilePoint. | tallyfy.com | |
| named | Control-M | - | adjacent | 6sense identifies Control-M as a leading alternative in workflow automation. | 6sense.com | |
| named | Retool | - | adjacent | Findstack lists Retool among AgilePoint NX alternatives and describes its low-code internal-tool platform. | findstack.com | |
| named | SoftExpert Suite | - | direct | SoftwareWorld lists SoftExpert Suite among AgilePoint NX alternatives. | softwareworld.co | |

### saas-03 (holdout) - Aligned (alignedup.com)

| Bucket | Competitor | Domain | Rel. | Why (model) | Cited from | Verdict |
|---|---|---|---|---|---|---|
| named | Highspot | - | adjacent | Gartner shows Highspot in an Aligned comparison within digital sales rooms. | gartner.com | |
| named | Seismic Enablement Cloud | - | adjacent | Gartner shows Seismic Enablement Cloud in an Aligned comparison. | gartner.com | |
| named | Omedym | - | direct | Gartner shows Omedym in an Aligned comparison within digital sales rooms. | gartner.com | |
| named | Mindtickle | - | adjacent | Gartner shows Mindtickle in an Aligned comparison. | gartner.com | |
| named | FollowUp | gofollowup.ai | direct | Listed as an Aligned digital sales room alternative for startups. | gofollowup.ai | |
| named | Distribute | distribute.so | direct | Listed as an Aligned alternative and compared directly with Aligned. | distribute.so, gofollowup.ai | |
| named | Paage | - | direct | Listed as an Aligned digital sales room alternative for startups. | gofollowup.ai | |
| named | Trumpet | - | direct | Listed as an Aligned alternative and digital sales room competitor. | dock.us, g2.com, gofollowup.ai | |
| named | Dock | dock.us | direct | Listed as an Aligned alternative and directly compared with Aligned. | dock.us, flowla.com, g2.com | |
| named | Arrows | - | direct | Listed as an Aligned digital sales room alternative for startups. | gofollowup.ai | |
| named | Flowla | - | direct | Compared with Aligned for cross-lifecycle automation and sales execution. | hummingdeck.com | |
| named | GetAccept | - | direct | Listed among digital sales room software products for sales teams. | dock.us | |
| named | Accord | - | direct | Listed among digital sales room software products for sales teams. | dock.us | |
| named | Allego | - | adjacent | Listed among digital sales room software products and sales enablement tools. | dock.us | |
| named | DealHub | - | direct | Listed among digital sales room software products for sales teams. | dock.us | |
| named | Spekit | - | adjacent | Listed among digital sales room software products and sales enablement tools. | dock.us | |
| named | Vidyard | - | adjacent | Listed among digital sales room software products used by sales teams. | dock.us | |

Grounding: 6 bucket(s) reassigned from the Evidence; 1 unverifiable domain(s) nulled

### saas-04 (dev) - aPriori Technologies (apriori.com)

| Bucket | Competitor | Domain | Rel. | Why (model) | Cited from | Verdict |
|---|---|---|---|---|---|---|
| named | DFMA by Boothroyd Dewhurst | dfma.com | direct | Presented as an alternative for transparent, traceable should-cost estimates and product simplification. | dfma.com, google.com | |
| named | Costimator | - | direct | Described as cost-estimation software similar to aPriori and a direct competitor. | google.com, sumble.com | |
| named | EasyKost | - | direct | TrustRadius directly compares EasyKost with aPriori Technologies. | trustradius.com | |
| named | Aras Corporation | - | adjacent | Listed among aPriori Technologies’ top competitors. | growjo.com | |
| named | DfR Solutions | - | adjacent | Listed among aPriori Technologies’ top competitors. | growjo.com | |
| named | Neilsoft Solutions | - | adjacent | Listed among aPriori Technologies’ top competitors. | growjo.com | |
| named | SOLIDWORKS | - | adjacent | Listed as an aPriori alternative and directly compared with aPriori Technologies. | google.com, trustradius.com | |
| named | Simile | - | adjacent | Listed among aPriori’s top competitors. | cbinsights.com, google.com | |
| named | Makersite | - | direct | Listed among aPriori’s top competitors in product and manufacturing intelligence. | cbinsights.com, google.com | |
| named | Assent | - | adjacent | Listed among aPriori’s top competitors. | cbinsights.com, google.com | |
| named | Cosmo Tech | - | adjacent | Listed among aPriori’s top competitors. | cbinsights.com, google.com | |
| named | Eugenie AI | - | adjacent | Listed among aPriori’s top competitors. | cbinsights.com, google.com | |
| named | Fero Labs | - | adjacent | Listed among aPriori’s top competitors. | cbinsights.com, google.com | |
| named | FactoryMind | - | adjacent | Listed among aPriori’s top competitors. | cbinsights.com, google.com | |
| named | Facton | - | direct | Listed among aPriori’s top competitors in product cost and manufacturing intelligence. | cbinsights.com, google.com | |
| named | Unison Cost Engineering | - | direct | Listed as a main competitor of aPriori Technologies. | craft.co, google.com | |
| named | Coupa | - | alternative | Listed as an enterprise product cost management alternative to aPriori. | gartner.com | |
| named | Oracle Cloud EPM Profitability and Cost Management | - | alternative | Listed as an enterprise product cost management alternative to aPriori. | gartner.com | |
| named | CostPerform | - | direct | Listed as an enterprise product cost management alternative to aPriori. | gartner.com | |
| named | CadDo Platform | - | direct | Listed as an enterprise product cost management alternative to aPriori. | gartner.com | |

### saas-05 (dev) - Archive360 (archive360.com)

| Bucket | Competitor | Domain | Rel. | Why (model) | Cited from | Verdict |
|---|---|---|---|---|---|---|
| named | Google Vault | - | direct | Listed as an Archive360 alternative for enterprise email archiving. | archondatastore.com, gartner.com, google.com | |
| named | Enterprise Vault | - | direct | Compared with Archive360 as an enterprise archiving alternative and migration target. | archive360.com, gartner.com, google.com | |
| named | Cloud Archive | - | direct | Listed as an Archive360 alternative for cloud-based archiving. | gartner.com, google.com | |
| named | Proofpoint Archive | - | direct | Listed as an Archive360 alternative for enterprise email archiving. | gartner.com, google.com | |
| named | Barracuda Message Archiver | - | direct | Listed as an Archive360 alternative for message archiving. | gartner.com, google.com | |
| named | Smarsh | - | direct | Listed across Archive360 alternatives pages for regulated communications archiving. | gartner.com, google.com, slashdot.org | |
| named | Archon | archondatastore.com | direct | Named as an Archive360 competitor offering enterprise archiving, analysis, and governance. | archondatastore.com, google.com | |
| named | Mimecast | - | direct | Listed as an Archive360 alternative for enterprise communications archiving. | archondatastore.com, google.com | |
| named | ArcTitan | - | direct | Listed as an Archive360 alternative for email archiving. | google.com, slashdot.org | |
| named | Intradyn | - | direct | Listed as an Archive360 alternative for email archiving. | google.com, slashdot.org | |
| named | Aid4Mail | - | direct | Listed as an Archive360 alternative for email archiving and migration. | google.com, slashdot.org | |
| named | ArchiverFS | - | direct | Listed as an Archive360 alternative for email archiving. | google.com, slashdot.org | |
| named | OneVault | - | direct | Listed as an Archive360 alternative for archiving. | google.com, slashdot.org | |
| named | Onna | - | direct | Listed as an Archive360 alternative for information and communications archiving. | google.com, slashdot.org | |
| named | MessageSolution | - | direct | Listed as an Archive360 alternative for message archiving. | google.com, slashdot.org | |
| named | TransVault | - | direct | Identified as a top Archive360 competitor in archive migration and management. | growjo.com | |
| named | SAS Data Management | - | direct | Directly compared with Archive360 on data-management market share and user recommendations. | peerspot.com | |
| named | ZL Unified Archive | - | direct | Archive360's migration guide compares its open archive with ZL Unified Archive and SaaS archives. | archive360.com, google.com | |

### saas-06 (holdout) - Arkestro (arkestro.com)

| Bucket | Competitor | Domain | Rel. | Why (model) | Cited from | Verdict |
|---|---|---|---|---|---|---|
| named | ORO Labs | - | direct | Compared directly with Arkestro as a procurement software solution. | peerspot.com | |
| named | Sievo | - | direct | Listed as one of Arkestro's primary competitors. | owler.com | |
| named | Suplari | - | direct | Listed as one of Arkestro's primary competitors. | owler.com | |
| named | Zycus | - | direct | Listed as one of Arkestro's primary competitors. | owler.com | |
| named | Liberate | - | direct | Listed among products similar to Arkestro. | cbinsights.com, google.com | |
| named | Levelpath | - | direct | Listed among products similar to Arkestro. | cbinsights.com, google.com | |
| named | Promoted | - | direct | Listed among products similar to Arkestro. | cbinsights.com, google.com | |
| named | SAP Ariba | - | direct | Listed as an Arkestro alternative and commonly compared procurement platform. | capterra.com, g2.com, gartner.com | |
| named | Workday Strategic Sourcing | - | direct | Listed as an Arkestro alternative. | gartner.com | |
| named | JAGGAER | - | direct | Listed as an Arkestro alternative and commonly compared procurement platform. | gartner.com, trustradius.com | |
| named | Coupa | - | direct | Listed as an Arkestro alternative and commonly compared procurement platform. | gartner.com, trustradius.com | |
| named | GEP SMART | - | direct | Listed as an Arkestro alternative. | gartner.com | |
| named | Order.co | - | direct | Listed among alternatives to Arkestro. | capterra.com | |
| named | Tradogram | - | direct | Listed among alternatives to Arkestro. | capterra.com | |
| named | ProcurementExpress.com | - | direct | Listed among alternatives to Arkestro. | capterra.com | |
| named | Field Materials AI | - | direct | Listed among alternatives to Arkestro. | capterra.com | |
| named | wherex | - | direct | Listed among alternatives to Arkestro. | capterra.com | |
| named | Pivot | - | direct | Listed among alternatives to Arkestro. | capterra.com | |
| named | GEP Quantum Intelligence | - | direct | Listed among products most commonly compared to Arkestro. | trustradius.com | |
| named | Zip Intake-to-Procure | - | direct | Listed among products most commonly compared to Arkestro. | trustradius.com | |
| named | Precoro | - | direct | Listed among products most commonly compared to Arkestro. | trustradius.com | |
| named | PairSoft | - | direct | Listed among products most commonly compared to Arkestro. | trustradius.com | |
| named | Basware Procure-to-Pay | - | direct | Listed among products most commonly compared to Arkestro. | trustradius.com | |
| named | Fairmarkit | - | direct | Compared directly with Arkestro in procurement software. | arkestro.com, google.com | |
| named | e-Procurement Technologies | - | direct | Compared side by side with Arkestro in strategic sourcing application suites. | gartner.com, google.com | |

### saas-07 (dev) - Betterworks (betterworks.com)

| Bucket | Competitor | Domain | Rel. | Why (model) | Cited from | Verdict |
|---|---|---|---|---|---|---|
| named | Culture Amp | - | direct | Listed in a Betterworks comparison for performance management. | betterworks.com | |
| named | UKG | - | direct | Listed in a Betterworks comparison for performance management. | betterworks.com | |
| named | Cornerstone | - | direct | Listed in a Betterworks comparison for performance management. | betterworks.com | |
| named | Lattice | - | direct | Frequently listed as a Betterworks alternative for performance management. | betterworks.com, g2.com, google.com | |
| named | Peoplelogic | peoplelogic.ai | direct | Article explicitly presents Peoplelogic among Betterworks competitors and alternatives. | peoplelogic.ai | |
| named | Tability | - | direct | Compared with Betterworks as an OKR and goal-tracking platform. | okrstool.com | |
| named | 15Five | - | direct | Listed in multiple Betterworks alternatives and comparison lists. | mooncamp.com, okrstool.com, tability.io | |
| named | Cascade | - | direct | Compared with Betterworks in an alternatives list. | mooncamp.com, tability.io | |
| named | Jira Align | - | adjacent | Compared with Betterworks in a performance-management alternatives list. | tability.io | |
| named | JOP | - | direct | Compared with Betterworks in an alternatives list. | tability.io | |
| named | Leapsome | - | direct | Repeatedly listed as a Betterworks alternative for performance and talent management. | mooncamp.com, okrstool.com, tability.io | |
| named | Mooncamp | - | direct | Listed and compared as a Betterworks alternative for strategic goals and OKRs. | mooncamp.com, okrstool.com, peoplemanagingpeople.com | |
| named | Trakstar | trakstar.com | direct | Presented as a Betterworks alternative for goals, reviews, and 360-degree feedback. | trakstar.com | |
| named | QPR Performance Management | - | direct | Directly compared with BetterWorks in performance-management technology. | 6sense.com | |
| named | OKRs Tool | - | direct | Listed as a Betterworks alternative for OKR management. | okrstool.com | |
| named | Perdoo | - | direct | Listed as a Betterworks alternative for OKR management. | okrstool.com | |
| named | Synergita | - | direct | Listed as a Betterworks alternative for HR and performance management. | okrstool.com | |
| named | Microsoft | - | direct | Ranked among top Betterworks alternatives by Gartner Peer Insights. | external.pi.gpi.aws.gartner.com | |
| named | Quantive | - | direct | Ranked among top Betterworks alternatives by Gartner Peer Insights. | external.pi.gpi.aws.gartner.com | |
| named | Profit.co | - | direct | Listed among Betterworks alternatives for OKRs and strategic performance. | external.pi.gpi.aws.gartner.com, mooncamp.com, peoplemanagingpeople.com | |
| named | TraineryHCM | traineryhcm.com | adjacent | Presented as a replacement for Betterworks across performance and broader HCM functions. | traineryhcm.com | |
| named | Rippling | - | adjacent | Listed as a Betterworks alternative within broader HR management. | traineryhcm.com | |
| named | Workday HCM | - | adjacent | Listed as a Betterworks alternative within broader HCM. | traineryhcm.com | |
| named | WorkBoard | - | direct | Listed as a Betterworks alternative for goals and OKRs. | mooncamp.com | |
| named | MangoApps | - | adjacent | Identified as a BetterWorks competitor in employee recognition. | 6sense.com | |
| named | Namely | - | adjacent | Identified as a BetterWorks competitor in employee recognition. | 6sense.com | |
| named | Kudos | - | adjacent | Identified as a BetterWorks competitor in employee recognition. | 6sense.com | |

### saas-08 (holdout) - BigPanda (bigpanda.io)

| Bucket | Competitor | Domain | Rel. | Why (model) | Cited from | Verdict |
|---|---|---|---|---|---|---|
| named | PagerDuty | pagerduty.com | direct | Listed as a BigPanda alternative and compared directly on incident lifecycle, detection, resolution, and automation. | g2.com, gartner.com, google.com | |
| named | Splunk IT Service Intelligence (ITSI) | - | direct | Listed as a BigPanda alternative and compared in the IT alerting and incident management category. | gartner.com, google.com | |
| named | ScienceLogic AI Platform | - | direct | Listed among Gartner's BigPanda alternatives and competitors. | gartner.com, google.com | |
| named | BMC Helix Observability & AIOps | - | direct | Listed among Gartner's BigPanda alternatives and competitors. | gartner.com, google.com | |
| named | ignio | - | direct | Listed among Gartner's BigPanda alternatives and competitors. | gartner.com, google.com | |
| named | Better Stack | - | direct | Listed among Gartner's BigPanda alternatives and competitors. | gartner.com | |
| named | Edwin AI | - | direct | Listed among Gartner's BigPanda alternatives and highlighted in an AIOps comparison page. | gartner.com, logicmonitor.com | |
| named | LogicMonitor | logicmonitor.com | direct | Compared with BigPanda as an AIOps platform for alert context, incident intelligence, and event correlation. | google.com, logicmonitor.com, technologymatch.com | |
| named | Splunk AppDynamics | - | direct | Listed as a BigPanda alternative and rated better for transparency, integration, and implementation. | google.com, softwarereviews.com | |
| named | Sherlocks.ai | sherlocks.ai | adjacent | Positioned as an alternative for AI incident investigation, root-cause analysis, and infrastructure-aware debugging. | google.com, sherlocks.ai | |
| named | BitSentry | bitsentry.ai | adjacent | Compared with BigPanda for AI investigation versus enterprise ITOps and responder investigation. | bitsentry.ai | |
| named | Dynatrace | - | direct | Listed as a BigPanda alternative and included in a three-way AIOps platform comparison. | g2.com, google.com, softwarereviews.com | |
| named | Keep | - | direct | Described as an alternative to BigPanda for alert correlation and AIOps workflows. | docs.keephq.dev, google.com | |
| named | Datadog | - | direct | Listed as a BigPanda alternative in AIOps competitor listings. | google.com, softwarereviews.com | |
| named | ServiceNow IT Operations Management | - | direct | Listed as a BigPanda alternative in AIOps competitor listings. | google.com, softwarereviews.com | |
| named | IBM Cloud Pak for AIOps | - | direct | Listed as a BigPanda alternative in AIOps competitor listings. | google.com, softwarereviews.com | |
| named | Moogsoft | - | direct | Listed as a top BigPanda alternative for AIOps tools. | g2.com, google.com | |
| named | OpsGenie | - | direct | Community discussion identifies it among major tools for monitoring integration, alerting, and incident management. | google.com, reddit.com | |
| named | VictorOps | - | direct | Community discussion identifies it among major alerting and incident-management tools. | google.com, reddit.com | |

Grounding: 2 bucket(s) reassigned from the Evidence

### saas-09 (dev) - Bitly (bitly.com)

| Bucket | Competitor | Domain | Rel. | Why (model) | Cited from | Verdict |
|---|---|---|---|---|---|---|
| named | Branch Activation | branch.io | direct | Presented as an alternative for unified link and QR management. | branch.io | |
| named | Rebrandly | - | direct | Frequently listed or compared as a branded URL-shortening and link-management alternative. | bitly.com, branch.io, capterra.com | |
| named | TinyURL | - | direct | Listed and compared as a simpler, faster, or cheaper URL-shortening alternative. | bitly.com, branch.io, google.com | |
| named | RocketLink | - | direct | Listed as a Bitly alternative in a link-management comparison. | branch.io | |
| named | T2M | - | direct | Listed as an alternative for URL shortening and link management. | branch.io, google.com | |
| named | BL.INK | - | direct | Identified as a leading Bitly alternative and URL-shortening competitor. | bitly.com, g2.com, google.com | |
| named | Uniqode QR Code Generator | - | adjacent | Listed as a Bitly alternative with overlapping QR-code functionality. | g2.com | |
| named | Dub | dub.co | direct | Positions itself as a modern link-management and URL-shortening alternative to Bitly. | bitly.com, dub.co, google.com | |
| named | Sniply | - | adjacent | Compared with Bitly for curated links and call-to-action overlays. | qr-code-generator.com | |
| named | Cuttly | cutt.ly | direct | Presented as a Bitly replacement for URL shortening and analytics. | cutt.ly, google.com, zapier.com | |
| named | T.ly | - | direct | Listed among URL-shortener alternatives to Bitly. | google.com, zapier.com | |
| named | y.gy | - | direct | Listed as a value-oriented Bitly alternative, with fewer features. | zapier.com | |
| named | BetterLinks Pro | - | direct | Recommended as an alternative URL shortener with WordPress and analytics. | google.com, reddit.com | |
| named | Linkly | linklyhq.com | direct | Compared with Bitly on pricing, features, API capabilities, retargeting, and geo-targeting. | bitly.com, capterra.com, google.com | |
| named | Veshort | - | direct | Listed as a Bitly alternative on a software alternatives page. | capterra.com | |
| named | Recut | - | direct | Listed as a Bitly alternative on a software alternatives page. | capterra.com | |
| named | LinkCentral | - | direct | Listed as a Bitly alternative on a software alternatives page. | capterra.com | |
| named | CodeQR | - | adjacent | Listed as a Bitly alternative with overlapping QR-code capabilities. | capterra.com | |
| named | ReSlug | - | direct | Listed as a Bitly alternative for link shortening and management. | capterra.com | |
| named | Short.io | - | direct | Compared with Bitly as an alternative URL-shortening platform. | bitly.com, efficient.app, google.com | |
| named | GoLinks | - | adjacent | Listed among URL-shortener alternatives for users seeking branded links and link management. | bitly.com | |
| inferred | Pxl.to | - | direct | Included among free alternatives offering features similar to Bitly. | medium.com | |

Grounding: 2 bucket(s) reassigned from the Evidence

### saas-10 (holdout) - Built (getbuilt.com)

| Bucket | Competitor | Domain | Rel. | Why (model) | Cited from | Verdict |
|---|---|---|---|---|---|---|
| named | Construction Lending For Windows | - | direct | Directly compared with Built in a construction lending software comparison. | sourceforge.net | |
| named | Taktile | - | adjacent | Listed by Crunchbase as a possible Built alternative or competitor. | crunchbase.com | |
| named | Oscilar | - | adjacent | Listed by Crunchbase as a possible Built alternative or competitor. | crunchbase.com | |
| named | BillingPlatform | - | adjacent | Listed by Crunchbase as a possible Built alternative or competitor. | crunchbase.com | |
| named | Banner | - | direct | Ranked as the leading alternative to Built in a construction finance alternatives list. | withbanner.com | |
| named | Yardi Construction Manager | - | direct | Listed as a Built alternative for construction finance management. | withbanner.com | |
| named | AvidXchange | - | adjacent | Listed as a Built alternative for construction finance and payment workflows. | withbanner.com | |
| named | Nexus Systems (Bottomline) | - | adjacent | Listed as a Built alternative in construction finance software. | withbanner.com | |
| named | Coupa | - | adjacent | Listed as a Built alternative for financial and payment management. | withbanner.com | |
| named | Handle | - | direct | Identified as one of Built's top competitors in construction-related software. | cbinsights.com | |
| named | TrustPoint | - | direct | Identified as one of Built's top competitors in construction-related software. | cbinsights.com | |
| named | Sitewire | - | direct | Identified as one of Built's top competitors in construction-related software. | cbinsights.com | |
| named | Buildertrend | buildertrend.com | direct | Provides construction financial management software connecting estimating, job costing, invoicing, and payments. | buildertrend.com | |

Grounding: 1 bucket(s) reassigned from the Evidence

## Decision

news-v11-luna:

- [ ] Approve as-is
- [ ] Approve after GT fixes (list):
- [ ] Reject (reason):

comp-v11-luna:

- [ ] Approve as-is
- [ ] Approve after GT fixes (list):
- [ ] Reject (reason):

Reviewer: ____  Date: ____
