# Accumulated Learnings (from 190+ pattern tests)

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

# Worked Example: How "Find Competitors" Was Built

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
