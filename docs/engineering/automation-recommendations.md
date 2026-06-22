# Automation Recommendations

Claude Code automations tailored to the research-process-builder pipeline.

---

## Pre-Commit Hooks

### 1. Eval regression gate
**What:** Run `py scripts/eval_pipeline.py --offline` before every commit that touches `scripts/domain_resolver.py`, `scripts/domain_classifier.py`, or `scripts/confidence_scorer.py`.
**Why it helps:** Domain resolution and confidence scoring are the accuracy-critical modules. A pre-commit gate catches regressions before they land in main and before a production run surfaces them.
**Implementation sketch:**
```bash
# .git/hooks/pre-commit (or via pre-commit framework)
changed=$(git diff --cached --name-only)
if echo "$changed" | grep -qE "scripts/(domain_resolver|domain_classifier|confidence_scorer)"; then
  py scripts/eval_pipeline.py --offline || exit 1
fi
```

### 2. Credential scan
**What:** Block commits that contain API key patterns or reference `.env` directly in shell commands.
**Why it helps:** The workspace already has a `protect-env.js` hook at the CC level. Adding a repo-level scan adds defense in depth for this repo's scripts, which handle OpenAI, Serper, Supabase, and Firecrawl keys.
**Implementation sketch:**
```bash
# Check for hardcoded key patterns
git diff --cached | grep -E "(sk-|SERPER|supabase.*key)" && echo "Possible credential in diff" && exit 1
```

---

## Beneficial Skills

### 3. `tdd` skill — bootstrap pytest harness
**What:** Run `/tdd` to wrap the existing `scripts/test_resolver_unit.py` and `scripts/test_confidence_scorer.py` print-based tests into a proper pytest suite with pass/fail exit codes.
**Why it helps:** The existing test files use `if __name__ == "__main__"` print loops. They don't integrate with CI or pre-commit. Converting to pytest enables `py -m pytest scripts/` as a single regression command.
**Implementation sketch:** Invoke `tdd` skill, point it at `test_resolver_unit.py` and `test_confidence_scorer.py`, ask it to produce `pytest`-compatible versions in a `tests/` directory.

### 4. `code-review` skill — after pipeline changes
**What:** Run `/code-review` after any change to `pipeline_base.py` or `series_a_pipeline.py`.
**Why it helps:** The base class is 1,000+ lines and carries Supabase write logic mixed with pipeline orchestration. Code review catches leaky abstractions and unintended side-effects before they reach production pipeline runs.

---

## MCP Server Connections

### 5. Supabase MCP
**What:** Connect the Supabase MCP server to this project's Claude Code session.
**Why it helps:** Currently, all Supabase operations go through Python scripts. For ad-hoc queries ("how many rows were written yesterday?", "show me the last 5 domain conflict rows"), the Supabase MCP enables direct SQL without writing a new script.
**Implementation sketch:** Add to `.claude/settings.json`:
```json
{
  "mcpServers": {
    "supabase": {
      "command": "npx",
      "args": ["-y", "@supabase/mcp-server-supabase@latest", "--project-ref", "<project-ref>"]
    }
  }
}
```

---

## Custom Agents

### 6. `pipeline-watchdog` agent
**What:** A scheduled agent that runs `py scripts/eval_pipeline.py` daily and posts a summary to a Slack/notification channel.
**Why it helps:** The eval pipeline is the accuracy regression test. Currently it only runs when someone remembers to run it. A daily automated run catches accuracy drift before a human notices wrong data in Supabase.
**Implementation sketch:** Use the `schedule` skill to create a cloud agent running `py scripts/eval_pipeline.py` on a daily cron. On exit code 1, trigger a push notification via PushNotification tool.

### 7. `gt-promoter` agent (post-run)
**What:** An agent that runs `py scripts/gt_validation.py --sample 10 --days 7 --apply` after each weekly pipeline run.
**Why it helps:** GT validation promotes confirmed (company, domain) pairs to `KNOWN_GOOD_DOMAINS`, improving future eval coverage. Currently requires manual invocation. Automating it after each weekly catch-up run keeps GT growing continuously without human overhead.
**Implementation sketch:** Add a post-run hook in the series-a-daily monitor's `config/monitor.json` to trigger the GT validation script on success.

---

## File Watchers / Background Automations

### 8. Domain classifier cache auto-commit
**What:** Watch `data/domain_classifications.json` for changes and auto-stage + commit it with a standard message after each pipeline run.
**Why it helps:** The classifier cache is committed to git for shared learning. In practice, it's easy to forget to commit after a run that classified new domains. An auto-commit keeps the cache current across machines without manual steps.
**Implementation sketch:**
```bash
# Add to post-pipeline script:
git diff --quiet data/domain_classifications.json || \
  git add data/domain_classifications.json && \
  git commit -m "chore: update domain classifier cache"
```

### 9. Process STATUS.md auto-update watcher
**What:** When a `processes/find-*/process.md` file is edited, prompt (or auto-update) the corresponding `STATUS.md` with today's date.
**Why it helps:** STATUS.md files track process maturity and last-validated date. They drift out of date when the process file is edited without updating the status. A hook prevents stale status signals from misleading future agents.
**Implementation sketch:** Pre-commit hook: if `processes/*/process.md` is staged, check that the corresponding `STATUS.md` last-modified date is within 7 days; warn if not.
