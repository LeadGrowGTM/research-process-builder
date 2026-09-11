# The Build Loop

### Phase 1: Define the Research Goal

Before generating any patterns, nail down exactly what "success" looks like.

**Step 1: State the goal in one sentence.**

> "Given a company name and domain, find [WHAT] with [ACCURACY TARGET]% reliability."

Examples:

- "Given a company name and domain, find their top 5 competitors with 90%+ reliability."
- "Given a company name and domain, find recent news from the last 6 months with 90%+ reliability."
- "Given a company name and domain, find their tech stack with 85%+ reliability."

**Step 2: Define what a "good result" looks like.**

Write 3-5 bullet points describing what a successful output contains. Be specific.

> For competitors:
>
> - At least 3 named competitors (not just categories)
> - Competitors are in the same market segment (not adjacent industries)
> - At least one source is a structured platform (G2, Capterra, Tracxn)
> - Head-to-head positioning is surfaced (how they differ)

**Step 3: Pick 6-10 sample companies across size tiers.**

You MUST test across company sizes. Patterns that work for SpaceX break for startups.

| Tier             | Description                            | Pick 2-3                                       |
| ---------------- | -------------------------------------- | ---------------------------------------------- |
| Tier 1 (Known)   | Fortune 500, unicorns, household names | SpaceX, Stripe, Salesforce                     |
| Tier 2 (Mid)     | Growth-stage, funded, some press       | Cohere, Harvey AI, Lovable                     |
| Tier 3 (Obscure) | Micro, bootstrapped, early-stage       | Your company, a friend's startup, a niche tool |

**Include at least one company with an ambiguous name** (Clay, Keep, Cursor, Harvey) to stress-test disambiguation.

### Phase 2: Generate Initial Pattern Candidates

Generate 15-20 search pattern candidates. Each pattern is a parameterized search query.

**Pattern anatomy:**

```
[disambiguated_name] competitors
  ^variable             ^fixed search intent
```

**Generation rules:**

1. **Start with OR-combined queries** — the highest-leverage pattern. `[name] alternatives OR competitors OR "vs"` catches 3+ result types in one search. Always try combining synonyms with OR before testing them individually. Tested Q4.75/C4.75 across all tiers.
2. Start with the obvious: `[name] [goal keyword]` (e.g., `[name] competitors`)
3. Add synonym variants: `[name] alternatives`, `[name] rivals`
4. Add platform-specific: `site:g2.com [name]`, `site:zoominfo.com [name]`, `site:rocketreach.co [name]` (note: `.co` not `.com`), `site:crunchbase.com [name]`
5. Add natural language: `who competes with [name]`, `what is [name] known for`
6. Add category-derived: `best [category] tools {{current_year}}`
7. Add year-anchored: `[name] [keyword] {{current_year}}` — never hardcode the year
8. Add domain-anchored: `[domain] [keyword]` or `site:[domain] [keyword]`
9. Add negation variants: `[name] vs`, `[name] compared to`
10. Add combined platform queries: `site:zoominfo.com OR site:rocketreach.co OR site:crunchbase.com [name]` — pulls from 3 ungated platforms in one search

**Generate at least 15.** You'll kill half of them. That's the point.

### Phase 3: Test Patterns (The Anneal Loop)

This is where the methodology earns its accuracy. Test every pattern against real companies and score the results.

**For each pattern, test against 3-4 sample companies (mix of tiers).**

Run the search. Score each result on two dimensions:

| Dimension       | Score | Meaning                                                                                      |
| --------------- | ----- | -------------------------------------------------------------------------------------------- |
| Quality (Q)     | 1-5   | How useful/specific are the results? 5 = exactly what we need. 1 = irrelevant noise.         |
| Consistency (C) | 1-5   | Does it work across big AND small companies? 5 = works for all. 1 = only works for one tier. |

**Optional third dimension for time-sensitive goals:**

| Dimension     | Score | Meaning                                                          |
| ------------- | ----- | ---------------------------------------------------------------- |
| Freshness (F) | 1-5   | How recent are the results? 5 = last 3 months. 1 = 3+ years old. |

**Record everything.** For each pattern + company test:

```
Pattern: [name] competitors
Company: Clay (Tier 2, disambiguated as "Clay GTM")
Results: G2 comparison page, CBInsights competitor list, 2 blog roundups
Quality: 5 — Direct competitor names with positioning
Consistency: 4 — Works for known companies, weaker for Tier 3
Verdict: PRIMARY STACK
```

### Phase 4: Score and Classify

After testing all patterns, classify each one:

| Classification | Criteria                          | Action                                      |
| -------------- | --------------------------------- | ------------------------------------------- |
| PRIMARY        | Q >= 4 AND C >= 4                 | Include in the core process                 |
| ENRICHMENT     | Q >= 4 AND C >= 3                 | Include as conditional step (Tier 1-2 only) |
| SITUATIONAL    | Q >= 4 AND C <= 2                 | Include with explicit "when to use" guard   |
| FALLBACK       | Q >= 3, useful when primary fails | Include in Tier 3 fallback section          |
| KILL           | Q <= 2 OR consistently irrelevant | Add to kill list with reason                |

**Calculate stack accuracy:**

```
accuracy = (PRIMARY + ENRICHMENT patterns scoring Q4+C4+) / (total patterns tested) * 100
```

### Phase 5: Iterate Until 90%+

If accuracy < 90%, identify the failure modes:

| Failure Mode                    | Fix                                                           |
| ------------------------------- | ------------------------------------------------------------- |
| Ambiguous name pollution        | Add disambiguation variants (name + category, domain anchor)  |
| Tier 3 companies return nothing | Add fallback patterns (domain search, wellfound, rocketreach) |
| Results are stale               | Add `{{current_year}}` modifier to queries                    |
| Wrong type of results           | Add more specific intent words, try site: operators           |
| Platform-specific gaps          | Add platform variants (B2B → G2, B2C → Trustpilot)            |
| Too many separate searches      | Combine synonyms with OR operators into single queries        |
| Marketing content not real data | Add negation or more specific intent keywords                 |
| site: operator returns nothing  | Try the query without site: — broader queries often win       |

**Generate 5-10 fix patterns targeting the specific failure modes.** Test them the same way. Recalculate accuracy.

**Repeat until all classifications combined yield 90%+ accuracy.**

Typical iterations needed:

- Simple goals (profiles, ratings): 1 iteration
- Medium goals (competitors, reviews): 2 iterations
- Hard goals (news, PR for small companies): 2-3 iterations

### Phase 6: Assemble the Process File

Take the surviving patterns and arrange them into a numbered step sequence.

**Process file structure:**

```markdown
# [Research Goal] Process

**Accuracy:** [X]% validated across [N] companies
**Built:** [date]
**Methodology:** research-process-builder, [N] patterns tested

## Preprocessing

[Disambiguation and tier detection steps]

## Steps

### Step 1: [Most reliable pattern — runs for ALL companies]

**Search:** `[pattern]`
**Extract:** [what to pull from results]
**Quality:** [score] | **Consistency:** [score]

### Step 2: [Second most reliable]

...

### Step 7-8: [Tier 1-2 enrichment — conditional]

**When:** Tier 1-2 only
...

### Step 9-10: [Tier 3 fallbacks — conditional]

**When:** Tier 3 only, primary steps returned thin results
...

## Kill List

- `[pattern]` — [why it fails]

## Output Template

[Structured output the agent should produce]
```

**Year references:** Never hardcode the year in search queries. Use `{{current_year}}` as an input variable so the process stays valid across years. In Clay, populate it from a formula column: `YEAR({Created At})`.

**Ordering rules:**

1. Highest consistency patterns first (they work for everyone)
2. Highest quality patterns second (they give the best results)
3. Conditional/enrichment patterns in the middle
4. Fallback patterns at the end
5. Kill list at the bottom

**Step count target:** 5-8 steps is the sweet spot. Each step should earn its place by improving accuracy. More than 10 means your primary stack is too weak. If you can hit 90%+ in 5 steps, stop there.

### Phase 7: Source Review (After 50+ Results)

After assembling the process file, run source analysis to validate your pattern choices against real data.

```bash
py scripts/pattern_tester.py --sources    # generates searches/source-analysis.md
```

This surfaces which domains consistently appear in high-quality (Q3+) results by category. Use it to:

1. **Validate PRIMARY patterns** — If g2.com dominates review results at 60%+, confirm you have a `site:g2.com` pattern in your stack. If not, add one and retest.
2. **Inform new process design** — When starting a new research process, check source-analysis.md first. The dominant sources for your category type are your first-round candidates.
3. **Catch missing coverage** — If a high-value platform (wellfound, pitchbook, tracxn) appears in source analysis but not your process, evaluate whether to add it.

The feedback loop: **test patterns → analyze sources → build patterns targeting dominant sources → test again.**

Skip this phase for your first iteration. Run it after you have 50+ results in a category to get statistically meaningful source distribution.

---

# Quality Checklist

Before calling a process "done":

- [ ] Tested against 6+ companies across 3 tiers
- [ ] At least one ambiguous-name company tested
- [ ] Stack accuracy >= 90%
- [ ] Kill list includes patterns that LOOK promising but fail (saves future agents from wasting searches)
- [ ] Output template is specific enough that two agents would produce similar reports
- [ ] Each step has explicit "what to extract" instructions with three-sentence summaries
- [ ] Conditional steps have clear "when to run" guards
- [ ] Fallback steps have clear "when to trigger" criteria
- [ ] Year references use `{{current_year}}` variable, not hardcoded years
- [ ] Source analysis reviewed — dominant platforms have `site:` patterns in the stack
- [ ] At least one OR-combined query tested (highest-leverage technique)
- [ ] Ungated platform coverage checked (ZoomInfo, RocketReach, Crunchbase, LinkedIn, Wellfound)
- [ ] Each step has a `**stop if:**` condition where applicable
- [ ] "No data found" is explicitly handled as a valid output for T3 companies

---

# Preprocessing (Shared Across All Processes)

Every process built with this methodology should include these two preprocessing steps. They're universal.

### Name Disambiguation

Check if the company name is ambiguous:

- 6 characters or fewer
- Common English word
- Shares name with something famous

If ambiguous: add category qualifier or use domain. If not: use name as-is.

### Company Size Detection

Search: `[name] company overview`

Count third-party profiles in results:

- 5+ profiles → Tier 1 (Known) → full pattern stack
- 2-4 profiles → Tier 2 (Mid) → core stack, skip niche outlets
- 0-1 profiles → Tier 3 (Obscure) → core + fallbacks, thin results are the signal

---

# Ungated Data Platforms

These platforms expose valuable structured data in search snippets without requiring login. Consider them for any new process.

| Platform    | site: Domain                | What You Get (Ungated)                                                                                                                                  | Coverage                                      | Gotchas                                                                                                  |
| ----------- | --------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------- | -------------------------------------------------------------------------------------------------------- |
| ZoomInfo    | `site:zoominfo.com`         | Employee count, revenue estimate, industry, funding, key people. Also `pipeline.zoominfo.com` has editorial content (reviews, comparisons, tool lists). | T1-T3 (covers startups < 1 year old)          | None — works reliably                                                                                    |
| RocketReach | `site:rocketreach.co`       | Employee profiles with titles, org charts, department breakdown, company overview                                                                       | T1-T3 (found Hoo.be's CEO with 5-9 employees) | Domain is `.co` NOT `.com`. `site:rocketreach.com` returns zero results.                                 |
| Crunchbase  | `site:crunchbase.com`       | Funding rounds, investors, total raised, company description, signals/news                                                                              | T1-T2 (thin for T3)                           | Competitor data from Crunchbase is inaccurate (description matching). Only use for funding/profile data. |
| LinkedIn    | `site:linkedin.com/company` | Employee count (most current), about section, specialties                                                                                               | T1-T3                                         | Name pollution for common names                                                                          |
| Wellfound   | `site:wellfound.com`        | Employee count, funding stage, industry tags, team members                                                                                              | T2-T3 (the T3 lifeline)                       | Formerly AngelList. Best for startups without traditional ATS.                                           |

**Combined platform query:** `site:zoominfo.com OR site:rocketreach.co OR site:crunchbase.com [name]` pulls from all three in one search. Tested with Lovable: returned $200M Series A, $1.8B valuation, $50M ARR, founder name, and org chart in a single query.
