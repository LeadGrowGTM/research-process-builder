# research process builder

**→ For the scheduled pipeline (Product Hunt monitor, Supabase setup, SQL, deploy): [trigger/README.md](trigger/README.md)**

---

build validated web research through self-annealing loops. take a research goal, generate an approach, test it against real companies with sealed ground truth, score it deterministically, and iterate until it clears 90%+ - then a human reviews before anything is reused.

**an Experiment becomes an Approval only after programmed validation at >= 90% AND explicit human review.** that gate applies to everything here.

the repo has two layers that share the methodology:

### layer 1: company enrichment loops (code-graded, per-company signals)

`scripts/company_enrichment/` runs collect -> extract -> ground -> score loops that produce structured, cited signals for a company (news and product launches, competitors, running ads / offer intelligence). each enrichment is specified in `enrichments/p0/*.yaml`, prompted from `prompts/company-enrichment/`, and evaluated against sealed corpora in `benchmarks/`.

what makes the loop trustworthy:

- **deterministic grounding** - code, not model judgment, drops uncited dates, evergreen pages, duplicates, hallucinated or self competitors before anything is scored or shipped. the artifact keeps `model_output` next to the grounded `output` with a per-case report of what was dropped and why.
- **budget-guarded model client** - only approved models can run (gpt-4.1-mini, gpt-5-nano, gpt-5.6-luna; the client rejects anything else before spend), with a durable ledger, Decimal pricing incl. batch rates, and a hard per-run cost cap.
- **provider transports** - Serper (search + news) as primary, Parallel Search as typed fallback, plus a budget-guarded Parallel Task API client and a free scrape waterfall over first-party paths. provider choice is evidence-backed: see [docs/reports/serper-vs-parallel.md](docs/reports/serper-vs-parallel.md).
- **anneal reports** - every lineage (prompt x model x code version) is recorded with dev/holdout scores, hard failures, and cost: see [docs/reports/signal-enrichments-anneal.md](docs/reports/signal-enrichments-anneal.md).

measured cost at current champion lineages (gpt-5.6-luna at 2026-08-20 list rates): ~$0.005-0.007 per company per enrichment, half that via batch API, plus ~$0.006/company of Serper collection - about $11/1k companies for news + competitors combined, ~$6/1k batched.

entrypoints (all support `--help` without running a job):

```powershell
py scripts/company_enrichment_cli.py --help
py scripts/company_enrichment_news_loop.py --help
py scripts/company_enrichment_competitor_loop.py --help
py scripts/company_enrichment_ads_loop.py --help
```

### layer 2: the process factory (portable search-pattern prompts)

the original layer: a self-annealing loop that produces portable `.md` process files with step-by-step search instructions that any agent (Claude Code, Clay/Claygent, custom GPT, browser agent, OpenAI Agents) can follow to reliably surface specific intelligence about companies.

## what's inside

```
├── SKILL.md                          # the methodology — how to build research processes
├── enrichments/p0/                   # enrichment specs (inputs, caps, gates, providers)
├── prompts/company-enrichment/       # graduated + candidate enrichment prompts
├── scripts/company_enrichment/       # collect/evaluate/anneal pipeline + adapters + budget ledger
├── benchmarks/                       # sealed corpora + ground truth (immutable)
├── docs/reports/                     # anneal reports, provider A/Bs, review sheets
└── processes/
    ├── find-profiles.md              # 6 steps · 100% accuracy
    ├── find-competitors.md           # 7 steps · 93% accuracy
    ├── find-reviews.md               # 6 steps · 95% accuracy
    ├── find-news.md                  # 7 steps · 90% accuracy
    ├── find-pr-releases.md           # 5 steps · 90% accuracy
    ├── find-hiring.md                # 5 steps · 93% accuracy
    ├── find-job-role-insights.md     # 5 steps · 90% accuracy (companion to find-hiring)
    ├── find-growth-signals.md        # 7 steps · 93% accuracy
    └── find-negativity.md            # 6 steps · 90% accuracy
```

## how the factory works

the methodology has 6 phases:

1. **define the goal** — state what you're looking for and what a "good result" looks like
2. **generate 15-20 candidate search patterns** — parameterized queries like `{{company_name}} competitors`
3. **test patterns against real companies** — 6-10 sample companies across 3 size tiers (enterprise to micro startup)
4. **score and classify** — quality (1-5) x consistency (1-5). classify as PRIMARY / ENRICHMENT / FALLBACK / KILL
5. **iterate until 90%+** — identify failure modes, generate fix patterns, retest
6. **assemble the process file** — ordered steps with extract instructions, early stopping, kill list, output template

full methodology is in [SKILL.md](SKILL.md).

## the 9 processes

built using the methodology above. 200+ patterns tested across 11 companies ranging from SpaceX ($400B+) to micro bootstrapped agencies.

| process                                                       | what it finds                                                                          | steps | accuracy |
| ------------------------------------------------------------- | -------------------------------------------------------------------------------------- | ----- | -------- |
| [find-profiles](processes/find-profiles.md)                   | company fact sheet from zoominfo, crunchbase, linkedin, rocketreach, pitchbook, tracxn | 6     | 100%     |
| [find-competitors](processes/find-competitors.md)             | direct competitors with positioning and justification                                  | 7     | 93%      |
| [find-reviews](processes/find-reviews.md)                     | individual reviews tagged positive/negative with three-sentence summaries              | 6     | 95%      |
| [find-news](processes/find-news.md)                           | partnerships, acquisitions, funding, launches, expansions, leadership changes          | 7     | 90%      |
| [find-pr-releases](processes/find-pr-releases.md)             | official announcements, press releases, blog posts, wire distributions                 | 5     | 90%      |
| [find-hiring](processes/find-hiring.md)                       | open roles, departments hiring, ATS platform, hiring velocity                          | 5     | 93%      |
| [find-job-role-insights](processes/find-job-role-insights.md) | tech stack, pain points, strategic signals from specific job descriptions              | 5     | 90%      |
| [find-growth-signals](processes/find-growth-signals.md)       | blog activity, lead magnets, social presence, community, newsletters, pricing maturity | 7     | 93%      |
| [find-negativity](processes/find-negativity.md)               | customer complaints, negative reviews, controversy, churn signals                      | 6     | 90%      |

each process file includes:

- clear `{{input}}` placeholders you fill in before running
- step-by-step search patterns with exact queries
- exact extraction specs (what to pull from each search, three-sentence summaries)
- **stop if** conditions so the workflow stops when it has enough
- a kill list of patterns that look promising but waste searches
- a casual, structured output template

## how to use the processes

### with any AI agent (ChatGPT, Claude, etc.)

paste the process file content as the system prompt or instructions. fill in the `{{inputs}}`. the agent follows the steps, stops when it has enough, and outputs in the specified format.

### with Clay / Claygent

each step maps to a Clay enrichment column. use the **search** query as your Claygent prompt. the **extract** instructions tell you what to pull from results.

### with Claude Code

drop the process files into your `.claude/skills/` directory. reference them when researching companies:

```
"research [company] using the find-competitors process"
```

## how to build your own processes

use [SKILL.md](SKILL.md) to build processes for any research goal:

- tech stack detection
- hiring signal monitoring
- pricing intelligence
- market sizing
- content gap analysis
- anything you can search the web for

## key discoveries

things we learned testing 220+ search patterns:

- **`site:reddit.com` is completely broken** — zero results universally. use `[name] reddit discussion` without the site: operator.
- **year modifiers are the highest-leverage search modifier.** `[name] review 2026` outperforms `[name] review` by a wide margin.
- **zoominfo + linkedin are the only platforms that cover ALL company sizes**, including 6-month-old startups.
- **generic company names (Clay, Keep, Cursor, Harvey) need mandatory disambiguation.** add category qualifier or use domain.
- **kill lists save more time than pattern lists.** knowing which searches to NOT run prevents wasting 30-40% of your search budget.
- **ATS board searches are gold for hiring data.** `site:boards.greenhouse.io [name]` and `site:jobs.ashbyhq.com [name]` return actual role listings with titles and descriptions.
- **`[name] social media twitter youtube` is a trap.** returns product feature content, not the company's actual social accounts. use `site:twitter.com OR site:x.com` with company name instead.
- **OR operators in a single query are powerful.** `[name] alternatives OR competitors OR "vs"` catches 3 result types in one search, tested Q4.75/C4.75.
- **wellfound (formerly angellist) is the T3 lifeline for hiring data.** small startups without greenhouse/lever/ashby pages still have wellfound profiles with employee count, funding, and industry tags.
- **`site:[domain]` with OR operators is the most efficient growth signal detector.** a single query like `site:[domain] blog OR pricing OR newsletter OR demo` catches 4+ signal types in one search.
- **rocketreach is at `rocketreach.co`, NOT `.com`.** `site:rocketreach.com` returns zero results universally. `site:rocketreach.co` returns rich org chart data including employee titles, department breakdown, and key people even for T3 companies.
- **combined platform OR queries are a cheat code.** `site:zoominfo.com OR site:rocketreach.co OR site:crunchbase.com [name]` pulls from 3 ungated platforms in one search. tested with Lovable: returned $200M funding, $1.8B valuation, ARR, founder, and org chart in a single query.
- **churn-signal searches are a trap.** `[name] "switched from" OR "left" OR "cancelled"` returns marketing content about people switching TO the tool, not FROM it. tested Q2/C1.
- **"do not recommend" and "waste of money" searches return nothing.** people don't use these exact phrases in searchable contexts. use `[name] complaints OR problems` instead.
- **never hardcode the year in process files.** use `{{current_year}}` as an input variable so processes stay valid across years. in Clay, populate from `YEAR({Created At})`.
- **ATS-specific JD searches are the best path to full job descriptions.** `site:jobs.ashbyhq.com/[company] [role]` returns exact JD links. combined ATS OR query (`site:jobs.ashbyhq.com OR site:boards.greenhouse.io OR site:jobs.lever.co [company] [role]`) catches roles across all three platforms in one search.
- **`site:linkedin.com/jobs` is broken for web search.** returns generic LinkedIn job search pages, not company-specific listings. for LinkedIn jobs data, use Claygent to visit `linkedin.com/company/[slug]/jobs/` directly.
- **companion processes unlock depth.** find-hiring gives you breadth (all open roles). find-job-role-insights gives you depth (what a specific JD reveals about strategy, tech stack, and pain points). chain them in Clay for the full picture.
- **`site:{{domain}}/blog` beats `[name] blog` for finding owned content.** `[name] [category] blog 2026` returns third-party blogs ABOUT the company (Q3). `site:[domain]/blog` returns the company's own posts (Q5). use the former for buzz, the latter for owned content.
- **`[name] [category] newsletter` is a trap.** returns product feature content about newsletters, not the company's actual newsletter. tested Q2.25 across all tiers. `site:[domain] "subscribe" OR "newsletter"` finds actual signup mechanisms (Q4).
- **community platforms (discord/slack) are the most underrated growth signal.** `[name] discord OR slack OR community` tested Q5 across Clay (Slack 15K+), Lovable (Discord 162K+), Cursor (Discord 15K+). an active community signals product-market fit and organic advocacy.
- **`site:youtube.com [name]` returns zero results.** YouTube is not searchable via `site:` operator. avoid entirely.

## validation

- 220+ patterns tested via live web search
- 11 companies: SpaceX, Cohere, Harvey AI, Cursor, Clay, Lovable, Keep, Cluely, ClickUp, LeadGrow, The Kiln
- companies ranged from $400B+ to micro bootstrapped
- each pattern tested against 3-4 companies minimum
- three iteration rounds with 30+ fix patterns targeting identified failure modes

## license

MIT
