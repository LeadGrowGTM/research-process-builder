---
name: research-process-builder
description: Build validated web research processes through self-annealing loops. Takes a research goal, generates search steps, tests against sample companies, scores accuracy, and iterates until 90%+. Use when creating new research workflows, building claygent/agent prompts, or systematizing any web research task.
---

# Research Process Builder

Build validated, step-by-step web research processes through iterative testing. Takes any research goal, generates search patterns, tests them against real companies, scores accuracy, and loops until the process hits 90%+ reliability.

This is the factory that produces research agent prompts. The output is a portable .md file with step-by-step instructions that any agent (Claude Code, Clay, custom GPT, browser agent) can follow to reliably surface specific intelligence.

## When To Use

- Building a new research workflow for any topic (company intel, market sizing, hiring signals, tech stack detection)
- Creating claygent or web research agent prompts that need to work reliably
- Systematizing any manual web research you do repeatedly
- Someone asks "how do I research X about companies?"

## When NOT To Use

- Running an existing research process (load the process .md directly)
- One-off research where you just need the answer
- Data enrichment at scale (use a dedicated enrichment tool)

---

## Interactive Flow

> Reference: leadgrow-hq/company/methodology/interactive-skill-pattern.md

### Intake

1. **Ask:** "What do you want to research about companies?" (state the research goal in one sentence)
   **Default:** none — REQUIRED
   **Why:** The goal sentence drives pattern generation, scoring criteria, and output template. A vague goal produces vague patterns.

2. **Ask:** "What does a 'good result' look like? What should the output contain?" (3-5 bullet points)
   **Default:** none — REQUIRED
   **Why:** Defines the extraction spec for every search pattern. Without this, Claude can't score Quality — it doesn't know what "good" means for YOUR use case.

3. **Ask:** "Do you have ground truth examples? Companies where you already KNOW the answer, so we can validate accuracy."
   **Default:** no, but strongly encouraged. If yes, collect: company name, domain, and the known-good answer for each (3-5 companies ideal).
   **Why:** Ground truth turns the annealing loop from "does this look right?" to "did we find what we KNOW is there?" Without it, accuracy is subjective. With it, accuracy is measurable.

4. **Ask:** "What accuracy target?"
   **Default:** 90%
   **Why:** Determines when the iteration loop stops. Lower targets finish faster but produce less reliable processes.

5. **Ask:** "Do you have sample companies across size tiers? (enterprise / mid-market / startup)"
   **Default:** suggest 6-10 from existing client list + well-known companies, ensuring Tier 1 (known), Tier 2 (mid), and Tier 3 (obscure) are represented
   **Why:** Patterns that work for SpaceX break for startups. Testing across tiers is what makes the process reliable.

6. **Ask:** "Is this time-sensitive research? (e.g., recent news vs evergreen profiles)"
   **Default:** no (evergreen)
   **Why:** Time-sensitive goals add a Freshness (F) scoring dimension and require `{{current_year}}` variables in all patterns.

7. **Ask:** "Where will this process run? (Claude Code / Clay claygent / browser agent / custom)"
   **Default:** Claude Code
   **Why:** Output format differs — Clay claygents need specific field mappings, browser agents need URL patterns, Claude Code processes are freeform markdown.

### Gap Detection

| Check | Where to Look | If Missing | Severity |
|-------|--------------|------------|----------|
| Research goal is specific enough (not "learn about companies" or "find info") | User input analysis | Ask clarifying questions until goal is one-sentence specific with a clear target | BLOCKING |
| Desired output is concrete (not "useful info") | User's output description | Show examples from existing processes (e.g., find-competitors output spec), ask user to match that specificity | BLOCKING |
| Ground truth variables provided | User input | DEGRADED — can still build, but accuracy validation will be weaker. Suggest: "Can you name 3-5 companies where you already know the answer? This dramatically improves the process." | DEGRADED |
| Sample companies span 3 tiers | User input + existing client list | Auto-suggest from clients and well-known companies. Include at least one ambiguous-name company (Clay, Keep, Harvey). | Auto-resolve |
| Existing process already covers this goal | `research-process-builder/processes/` | Show the existing process, ask: "This already exists. Extend it, or build a new angle?" | BLOCKING |
| Ambiguous-name company included in samples | Sample company list | Add one automatically — ambiguous names stress-test disambiguation logic | Auto-resolve |

### Checkpoints

#### CHECKPOINT 1: Goal + Samples Confirmed

**Show:**
- Formatted research goal (one sentence)
- Desired output spec (bullet points)
- Sample companies organized by tier (Tier 1 / Tier 2 / Tier 3)
- Ground truth variables (if provided) — company name, domain, known answer
- Accuracy target
- Scoring dimensions: Quality + Consistency (+ Freshness if time-sensitive) (+ Accuracy if ground truth provided)

**Ask:** "Does this capture what you want? Any companies to swap or ground truth to add?"

**On Approve:** Proceed to pattern generation (Phase 2)
**On Reject:** Adjust goal, samples, or ground truth based on feedback

#### CHECKPOINT 2: Pattern Candidates Generated

**Show:**
15-20 generated search patterns grouped by type:
- Direct intent queries (e.g., `[name] competitors`)
- OR-combined queries (e.g., `[name] alternatives OR competitors OR "vs"`)
- Platform-specific (e.g., `site:g2.com [name]`)
- Natural language (e.g., `who competes with [name]`)
- Category-derived (e.g., `best [category] tools {{current_year}}`)
- Domain-anchored (e.g., `site:[domain] blog OR pricing`)

**Ask:** "Any patterns you know work well that I should add? Any you want to kill before testing?"

**On Approve:** Proceed to testing (Phase 3)
**On Reject:** Add/remove patterns, then proceed

#### CHECKPOINT 3: Test Results

**Show:**
Pattern-by-pattern results table:
| Pattern | Company Tested | Tier | Q | C | F? | A? | Classification |
|---------|---------------|------|---|---|----|----|---------------|
| `[name] competitors` | Clay | T2 | 5 | 4 | - | 5 | PRIMARY |
| `site:reddit.com [name]` | SpaceX | T1 | 1 | 1 | - | - | KILL |

Summary: X patterns tested, Y classified PRIMARY, Z classified KILL
Current accuracy: X%
If ground truth provided: "Found the known answer for 4/5 ground truth companies (80%)"

**Ask:** "Accuracy is X%. Target is Y%. Should I iterate with fix patterns, or is this good enough?"

**On Approve (if at target):** Proceed to assembly (Phase 6)
**On Approve (if below target):** Proceed to iteration (Phase 5)
**On Reject:** Adjust scoring or reclassify specific patterns

#### CHECKPOINT 4: Iteration Results (if needed)

**Show:**
- New fix patterns tested (targeting specific failure modes)
- Updated scores for revised patterns
- Accuracy delta: "Was X%, now Y% (improved Z%)"
- Remaining failure modes (if any)

**Ask:** "Accuracy now X%. Continue iterating, or assemble the process?"

**On Approve (assemble):** Proceed to Phase 6
**On Approve (iterate):** Generate more fix patterns, loop back

#### CHECKPOINT 5: Process File Preview

**Show:**
Complete process file structure:
- Step sequence (ordered by consistency, then quality)
- Conditional steps (Tier 1-2 only, Tier 3 fallbacks)
- Kill list with reasons
- Output template (what the agent should produce)
- Stop conditions per step
- Ground truth accuracy (if applicable): "Process found the known answer for X/Y ground truth companies"

**Ask:** "This is the final process. Save to `processes/[name].md`?"

**On Approve:** Save and output final process file
**On Reject:** Adjust steps, ordering, or output template

### Ground Truth Training Pattern

When ground truth variables are provided, the build loop gains a measurable accuracy dimension:

**Phase 3 Enhancement:** After running each pattern against a ground truth company, compare extracted results to the known answer. Score an additional dimension:
- **Accuracy (A):** 5 = found exact known answer, 3 = found partial/adjacent info, 1 = missed entirely

**Phase 4 Enhancement:** Classification factors in A score — PRIMARY requires A >= 4 in addition to Q >= 4, C >= 4

**Phase 5 Enhancement:** Iteration specifically targets ground truth misses — "Pattern X missed the known answer for Company Y because [failure mode]. Fix pattern: [new pattern targeting that failure]"

**Final Metric:** `ground_truth_accuracy = (companies where process found known answer) / (total ground truth companies) * 100`

This is the key differentiator. Without ground truth, accuracy is "Claude thinks these results look good." With ground truth, accuracy is "the process found 4/5 things we KNOW exist." The latter is what makes a process worth shipping.

## Prompt Output Review Loop

Before building or annealing an LLM prompt, read
[references/prompt-annealing.md](references/prompt-annealing.md). It defines the
controlled experiment, negative-boundary design, live validation, reporting,
and graduation rules.

Use this loop whenever the Research Flow includes an LLM prompt:

1. **Declare the goal output first.** Show the exact structured schema and its
   human-readable rendering before writing prompt candidates.
2. **Make one prompt hypothesis.** State what wording or instruction changed
   and which observed failure it should fix.
3. **Run fixed development examples.** Send only the candidate prompt, normal
   inputs, and retained Evidence to the model. Keep ground-truth answers local
   to the scorer; never include them in provider prompt context.
4. **Show outputs, not just scores.** Present every resulting output in the
   declared human-readable format, including secondary outputs and omissions.
   Then show per-example misses, score delta, and cost.
5. **Get human feedback.** Pause before the next mutation. Turn the reviewer's
   concrete reactions into a general prompt rule rather than a company-specific
   answer.
6. **Repeat.** Keep the same development set so deltas are comparable. Run the
   sealed holdout only after selecting the development winner.

### Required iteration handoff

Every loop handoff contains:

- exact prompt change;
- exact requested and resolved model;
- prompt word and character count;
- exact output for every sample;
- secondary outputs or explicit none;
- score and delta from the prior iteration;
- specific failure pattern;
- cumulative model/source cost;
- one direct request for reviewer feedback.

An aggregate score without the underlying outputs is not a completed prompt
iteration.

### Plain-language gate

Research outputs should sound like the intended buyer, not the vendor's product
marketing. Use conversational language at about an eighth-grade reading level.
Prefer short, concrete, familiar words. Remove jargon, slogans, acronyms,
stacked abstractions, and product-category labels unless removing them would
change the meaning. When Evidence names the target buyer archetype, use that
label rather than inventing a synonym.

Ask of every field: **Would the intended buyer naturally say this?** If not,
rewrite it. For example, prefer "automated loan administration" over
"AI-native workflow orchestration for construction finance enablement."

---

## Example Processes (built with this methodology)

| Process                   | File                                  | Steps | Accuracy |
| ------------------------- | ------------------------------------- | ----- | -------- |
| Find competitors          | `processes/find-competitors.md`       | 7     | 93%      |
| Find reviews              | `processes/find-reviews.md`           | 6     | 95%      |
| Find recent news          | `processes/find-news.md`              | 7     | 90%      |
| Find PR/releases          | `processes/find-pr-releases.md`       | 5     | 90%      |
| Find third-party profiles | `processes/find-profiles.md`          | 6     | 100%     |
| Find hiring activity      | `processes/find-hiring.md`            | 5     | 93%      |
| Find job role insights    | `processes/find-job-role-insights.md` | 5     | 90%      |
| Find growth signals       | `processes/find-growth-signals.md`    | 7     | 93%      |
| Find customer negativity  | `processes/find-negativity.md`        | 6     | 90%      |

---

## The Build Loop

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

## Quality Checklist

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
- [ ] Each prompt iteration showed every actual output before the next mutation
- [ ] Reviewer feedback was converted into a general prompt rule
- [ ] Ground-truth answers stayed local and were not sent as prompt context
- [ ] Output passed the plain-language gate and avoids unnecessary jargon

---

## Preprocessing (Shared Across All Processes)

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

## Ungated Data Platforms

These platforms expose valuable structured data in search snippets without requiring login. Consider them for any new process.

| Platform    | site: Domain                | What You Get (Ungated)                                                                                                                                  | Coverage                                      | Gotchas                                                                                                  |
| ----------- | --------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------- | -------------------------------------------------------------------------------------------------------- |
| ZoomInfo    | `site:zoominfo.com`         | Employee count, revenue estimate, industry, funding, key people. Also `pipeline.zoominfo.com` has editorial content (reviews, comparisons, tool lists). | T1-T3 (covers startups < 1 year old)          | None — works reliably                                                                                    |
| RocketReach | `site:rocketreach.co`       | Employee profiles with titles, org charts, department breakdown, company overview                                                                       | T1-T3 (found Hoo.be's CEO with 5-9 employees) | Domain is `.co` NOT `.com`. `site:rocketreach.com` returns zero results.                                 |
| Crunchbase  | `site:crunchbase.com`       | Funding rounds, investors, total raised, company description, signals/news                                                                              | T1-T2 (thin for T3)                           | Competitor data from Crunchbase is inaccurate (description matching). Only use for funding/profile data. |
| LinkedIn    | `site:linkedin.com/company` | Employee count (most current), about section, specialties                                                                                               | T1-T3                                         | Name pollution for common names                                                                          |
| Wellfound   | `site:wellfound.com`        | Employee count, funding stage, industry tags, team members                                                                                              | T2-T3 (the T3 lifeline)                       | Formerly AngelList. Best for startups without traditional ATS.                                           |

**Combined platform query:** `site:zoominfo.com OR site:rocketreach.co OR site:crunchbase.com [name]` pulls from all three in one search. Tested with Lovable: returned $200M Series A, $1.8B valuation, $50M ARR, founder name, and org chart in a single query.

---

## Accumulated Learnings (from 190+ pattern tests)

Hard-won lessons from building 8 processes. Apply these when building new ones.

**What works:**

- **OR operators are the highest-leverage technique.** Combine synonyms into one query before testing individually. `[name] complaints OR "negative reviews" OR problems OR issues` catches 4 angles in one search.
- **`site:[domain]` with OR operators** detects multiple signals in one query. `site:[domain] blog OR pricing OR newsletter OR demo` catches 4+ signal types.
- **Year modifiers are the second highest-leverage modifier.** `[name] review {{current_year}}` outperforms `[name] review` by a wide margin.
- **"No data found" is a valid signal, not a failure.** For T3 companies, thin results ARE the signal. The process should explicitly say this in the output template.

**What doesn't work:**

- **`site:reddit.com` is completely broken.** Zero results universally. Use `[name] reddit discussion` instead.
- **Churn-signal searches return marketing content.** `[name] "switched from" OR "left" OR "cancelled"` surfaces content about people switching TO the tool, not FROM it.
- **Exact negative phrases return nothing.** `[name] "do not recommend"` and `[name] "waste of money"` have zero results. People don't use these phrases in searchable contexts.
- **`[name] social media twitter youtube` is a trap.** Returns product feature content, not the company's actual social accounts. Use `site:twitter.com OR site:x.com` with company name instead.
- **Generic "market landscape" and "competitive intelligence" searches** return industry research papers and CI vendor marketing, not company-specific data.
- **`site:rocketreach.com`** (with `.com`) returns zero results. The correct domain is `rocketreach.co`.

**Process file best practices (learned by iteration):**

- Every recency-based process MUST include `{{current_year}}` as an input. In Clay, populate from `YEAR({Created At})`.
- Every step should have explicit "what to extract" instructions with three-sentence summaries.
- Include `**stop if:**` conditions so the workflow exits when it has enough data.
- Kill lists save more searches than pattern lists. Knowing what NOT to search prevents wasting 30-40% of your search budget.
- The output template should be specific enough that two different agents would produce similar reports.
- "casual, structured" beats "formal, verbose" for output templates. Use markdown code blocks.

---

## Worked Example: How "Find Competitors" Was Built

This traces the exact methodology used to build `processes/find-competitors.md`.

**Phase 1 — Goal:** "Given a company name and domain, find their top 5 competitors with 90%+ reliability."

**Phase 2 — 15 candidate patterns generated:**
`[name] competitors`, `[name] alternatives`, `best [category] tools 2026`, `who competes with [name]`, `site:g2.com [name] alternatives`, `[name] vs`, `[name] market landscape`, `[name] competitive intelligence`, `site:crunchbase.com [name] competitors`, `[domain] competitors site:similarweb.com`, `[name] rival companies`, `[name] similar to`, `[category] market map 2026`, `[name] [category] competitors`, `[domain] competitors`

**Phase 3 — Tested across:** SpaceX, Clay, Harvey AI, Cursor, Cohere, Lovable, Keep, Cluely, Hoo.be (11 companies, 3 tiers)

**Phase 4 — Classification:**

- PRIMARY (5): competitors, alternatives, best tools 2026, who competes with, site:g2.com
- ENRICHMENT (2): [name] vs [competitor], market map 2026
- FALLBACK (3): [name] [category] competitors, [domain] competitors, [name] similar to
- KILL (5): market landscape, competitive intelligence, site:crunchbase.com, site:similarweb.com, rival companies

**Initial accuracy:** 71% (5/7 primary+enrichment at Q4+/C4+)

**Phase 5 — Iteration 1:** Added disambiguation variants for Clay, Keep, Harvey. Retested. 6/7 patterns now Q4+/C4+. Accuracy: 86%.

**Phase 5 — Iteration 2:** Added `[name] [category] competitors` as primary for ambiguous names. Retested. 93%.

**Phase 6 — Assembled into 8-step process.** See `processes/find-competitors.md`.
