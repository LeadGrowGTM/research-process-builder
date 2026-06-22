# Triage Labels

Five canonical triage states for issues in this repository.

## Labels

### needs-triage
**When to apply:** Automatically on every new issue. No one has looked at it yet; severity and scope are unknown.
**Who acts next:** Repo maintainer (Mitch) reviews within 24 hours, classifies, and moves to another state.
**Example:** "Pipeline run output 0 companies today" — opened automatically, cause unknown.

---

### needs-info
**When to apply:** The issue is real but the reporter hasn't provided enough context to diagnose it. Missing: eval output, run date, stage number, or a reproducible example.
**Who acts next:** Reporter — they provide the missing data. Issue sits here until they do.
**Example:** "Domain resolution seems off" — needs: which company, which bad domain was assigned, which eval case failed.

---

### ready-for-agent
**When to apply:** The issue is scoped, reproducible, and mechanical — no judgment call required. A Claude Code agent can attempt a fix with a clear success criterion.
**Who acts next:** Spawn an agent with the issue body + `py scripts/eval_pipeline.py --offline` as the verification step.
**Example:** "Confidence scorer marks 'Stripe Raises $X' as LOW because 'Raises' matches HEADLINE_FRAMING — fix the regex to exclude company names that precede the verb."

---

### ready-for-human
**When to apply:** The fix requires judgment, production data access, or external service credentials. Cannot be safely delegated to an agent.
**Who acts next:** Mitch — needs hands-on attention.
**Example:** "GT validation shows 3 conflicts where agent domain disagrees with stored domain — need manual review to decide which is correct before promoting."

---

### wontfix
**When to apply:** The issue is valid but intentionally out of scope — either too niche, too costly relative to impact, or by design.
**Who acts next:** Maintainer closes with a reason comment so future reporters understand the decision.
**Example:** "Add `site:reddit.com` support" — intentionally killed. Known to return zero results universally. Kill list entry in process files documents the reason.

---

## Triage Decision Tree

```
New issue
  |
  +--> Can reproduce with eval_pipeline.py? 
       |
       YES --> Is the fix mechanical (regex, filter rule, blocklist entry)?
               YES --> ready-for-agent
               NO  --> ready-for-human
       |
       NO  --> Does the reporter have enough context?
               NO  --> needs-info
               YES --> needs-triage (further investigation needed)
```
