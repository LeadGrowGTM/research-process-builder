# Harness — Repository cleanup and full update

PLANNER_BRIEF

Plan a safe modernization of `research-process-builder` without replacing its existing purpose or approval methodology. Read the root README, SKILL.md, current/root/old CLAUDE guidance, `scripts/autoresearch_agent.py`, `scripts/autocontext_runner.py`, pattern testing/evaluation/baseline scripts, process STATUS files, tests, and the agent-harness benchmark-climb reference.

Before implementation, inventory every tracked modification, deletion, and untracked path. Classify source, tests, durable research knowledge, generated output, secrets/config, caches, campaign-specific artifacts, and obsolete duplicates. Preserve reusable knowledge and a concise inventory. Move disposable/generated material into an ignored, recoverable quarantine before removal; never silently lose unique user work. Record recovery paths.

After the current workspace is safe and clean enough for isolation, resolve the Git root, ensure `.worktrees/` is ignored while preserving unrelated `.gitignore` edits, and create or reuse only `<repo>/.worktrees/repo-cleanup-full-update` on `wt/repo-cleanup-full-update`. Record that authoritative path in tasks-axi. Never use an external Treehouse/worktree path.

Plan small, dependency-aware phases for: inventory and preservation; cleanup/ignore policy; an accurate root CLAUDE.md; repository-scoped Parallel Search MCP configuration with no literal credentials; test-first orchestration redesign; provider seams; verification; and handoff. Preserve the programmed >=90% ground-truth validation plus human approval. Do not build the future research-flow catalog in this goal. Treat GTM MCP as read-only source discovery until its actual interface is verified.

MAKER_ROUTING

Phase 1: direct — inventory, classify, and create a reversible quarantine/recovery map before destructive cleanup.
Phase 2: codebase-design + domain-modeling — define deep modules and canonical terms for Research Flow, Search Flow, Site Extraction Flow, Source Adapter, Experiment, Evidence, and Approval.
Phase 3: direct — clean generated/obsolete material, update ignore policy, and refresh root CLAUDE.md from verified behavior.
Phase 4: direct — add project-scoped Parallel Search MCP configuration from current official docs, using OAuth or environment references only. Discover/document the existing GTM MCP read interface; do not perform remote writes.
Phase 5: test-driven-development — redesign autoresearch around a fresh experiment inventor per cycle, independent in-bounds and novelty checks, executor, independent evaluator, deterministic advance/retry/rollback/halt gate, compact schema-validated artifacts, budgets, and resumability. Adapt agent-harness patterns; do not import its injected workflow runtime as a library.
Phase 6: verification-before-completion — run focused and regression checks, deterministic stubbed end-to-end dry runs, secret scans, documentation checks, and cleanup integrity checks.

For every phase, inspect overlapping user changes first, make the smallest coherent edits, and commit at the phase boundary. Do not silently normalize unrelated files. Provider-specific details stay behind Source Adapter seams. Site Extraction Flows must prefer fetch/scrape, selectors, regex, and deterministic patterns before optional LLM extraction. Do not implement the new flow catalog, full GTM provider, or remote writes.

PROVER_BRIEF

Exercise the affected Python CLIs non-interactively. Record command, exit code, relevant output, and artifact paths. Verify repository/worktree/branch identity; no literal secrets; inventory and recovery references; ignored generated outputs; authoritative tests; focused orchestration state-transition tests; CLI help and invalid-input behavior; and a stubbed dry run proving fresh inventor context, pre-execution rejection, accepted execution, independent evaluation, every gate outcome, retry exhaustion, rollback, and resume without duplicate paid work. Confirm Parallel configuration parses without secrets and fails clearly when auth is unavailable. Contract-test Site Extraction and GTM seams without live remote writes. If network verification is unavailable, use deterministic doubles and record the limitation.

REDTEAM_BRIEF: N/A — internal repository goal. Add adversarial verification only if credential handling, destructive automation, or remote writes enter scope.

CHECKER_BRIEF

Read BRIEF.md, PLAN.md, the final diff, inventory/recovery map, tests, and Prover evidence; do not read Maker self-assessment. Score 1–5 with file:line or command evidence:

1. Preservation safety — 5 means complete classification, reversible cleanup, and no unexplained loss.
2. Repository clarity — 5 means CLAUDE.md, commands, structure, methodology, and artifact policy match observed behavior.
3. Orchestration correctness — 5 means isolated inventor, independent checks/evaluator, deterministic gates, bounded artifacts, and resumability are demonstrated.
4. Validation rigor — 5 means rejection, execution, evaluation, all transitions, failure recovery, resume, and context isolation have automated evidence.
5. Provider architecture — 5 means Parallel works behind clean contracts and future site/GTM sources fit without core changes.
6. Security/operations — 5 means no literal secrets, bounded budgets, dry-run defaults, clear failures, and reversible actions.
7. Scope discipline — 5 means no new flow catalog, live writes, unauthorized shipping, or unrelated rewrites.

PASS requires every dimension >=4 and mean >=4.5. Automatic failure: lost unique work, committed credential, external worktree, inherited/unbounded inventor context, missing independent evaluator/checks, failed authoritative tests, undocumented destructive cleanup, or out-of-scope flow-catalog work.

SHIP_BRIEF

Shipping is not approved. After Checker PASS, prepare only a local handoff with worktree/branch, change summary, preservation manifest, verification matrix, limitations, review commands, and proposed commit/PR structure. Do not push, open a PR, merge, deploy, rotate credentials, perform remote writes, or delete the original workspace/worktree without separate explicit approval.

ORCHESTRATION NOTE

Outer build loop: Planner -> Maker -> Prover -> fresh Checker -> local handoff. Autoresearch inner loop: compact artifacts -> fresh Inventor -> In-bounds Checker -> Novelty Checker -> Executor -> independent Evaluator -> deterministic Gate -> advance/retry/rollback/halt. Only versioned, schema-validated artifacts cross seams; raw transcripts and inherited chat context do not.

LOOP_TRACKER

## Loop Tracker
> Update this file as you complete each step. Check off items in order.

### Planner
- [x] HARNESS.md read
- [x] routing resolution consumed
- [x] selected routing file read: `C:/Users/mitch/Everything_CC/tools/agent/agent-harness/skills/write-goal-prompt/references/skill-routing.md`
- [x] BRIEF.md and PLAN.md written
- [x] PLAN phases mirrored to `issues/NN-<slug>.md`

### Cycle 1
- [x] Maker: inventory/preservation — artifacts `docs/recovery/repo-cleanup-full-update/`; commits `88be158`, `307d34c`, `6853794`
- [x] Maker: cleanup/docs/MCP — artifacts `CONTEXT.md`, `docs/domain/adr/0003-resumable-autoresearch-orchestration.md`, `CLAUDE.md`, `.codex/config.toml`, `docs/providers/`; commits `def8588`, `0a2d9f7`, `d03da55` (review closures `3374909`, `9b96e38`, `3d02ca8`)
- [x] Maker: orchestration/provider seams — commits `00d20e0`..`c6a6528`; focused `163 passed`; stub/resume proofs retained under ignored `.artifacts/autoresearch/`
- [ ] Mechanical gate passed
- [ ] Prover verdict received
- [ ] Checker wrote CYCLE_LOG.md
- [ ] Reward signal: __/5.0 (threshold: every dimension >=4; mean >=4.5)
- [ ] Verdict: PASS / ITERATE / PLATEAU

### Cycle 2 (if ITERATE)
- [ ] Lowest-scoring dimension fixed
- [ ] Mechanical gate and Prover passed
- [ ] Checker updated CYCLE_LOG.md
- [ ] Verdict: PASS / ITERATE / PLATEAU

### Cycle 3 (if ITERATE again)
- [ ] Lowest-scoring dimension fixed
- [ ] Mechanical gate and Prover passed
- [ ] Checker updated CYCLE_LOG.md
- [ ] Verdict: PASS / PLATEAU

### Final
- [ ] Shipping: `N/A - shipping not approved`
- [ ] HANDOFF.md, HANDOFF.html, and HANDOFF.excalidraw written
- [ ] Report published or export fallback recorded

EXECUTION_PROTOCOL
Five-stage execution. Before stage 1, the goal parent runs the [ROUTING_GUARD] snippet from the
goal condition; a nonzero result stops before Planner. On success, pass exact `ROUTING_EVIDENCE`
stdout to Planner under `[SKILL_ROUTING_RESOLUTION]`; do not parse or reformat it in the parent.
1. Planner (turns 1-5): consume the routing resolution, decompose task -> write PLAN.md (phases,
   exact routing evidence, selected source/fallback, checker rubric), then mirror each phase to a
   durable slice in `issues/NN-<slug>.md` (survives /compact, tracks per-phase Status). PLAN.md
   `## Phases` stays canonical; slices are the durable drive-list. Do not produce task artifacts
   until PLAN.md is written.
2. Maker (turns 6-<N>): execute per PLAN.md, invoke skills per phase, commit at each phase boundary.
3. Prover (running-app goals only): spawn harness-prover with PROVER_BRIEF. Pass feature intent +
   exercise instructions. Get PROOF VERDICT before Checker. Skip entirely for static artifact goals
   (PROVER_BRIEF: N/A).
3b. Red-team (adversarial-verify goals — running app, user-facing flow, or security-sensitive
   code): run the red-team Workflow (`.claude/workflows/red-team.js`) with REDTEAM_BRIEF (target,
   paths, entryPoint). Feed its worst-first holes back to the Maker as fix input BEFORE Checker
   scores. Skip for static/internal artifacts (REDTEAM_BRIEF: N/A).
4. Checker: spawn fresh harness-checker subagent with CHECKER_BRIEF. Pass artifact paths + PROOF
   VERDICT (if running-app goal). Checker opens "I did not write this." Writes scores to CYCLE_LOG.md.
5. Ship (only after Checker PASS plus separate explicit shipping approval for this invocation):
   if approval is absent, do not spawn the Shipper and record `N/A - shipping not approved` as the
   terminal shipping outcome. If approval is present, spawn a fresh `harness-shipper` agent with
   SHIP_BRIEF.intent, project root, branch, and both approval signals. The shipper invokes
   `/no-mistakes`; the goal agent must never drive it inline. `checks-passed` means the PR is ready
   for human review/merge; do not wait for merge. Do not run this stage for ITERATE or PLATEAU.

Work through the task to completion. If you hit a blocker, do not stop. Use mocks, stubs, or
documented assumptions. Record each workaround and continue with everything that does not require
my decision.

EVAL_LOOP
At turn 1, before any other work, write your eval plan in HANDOFF.md under "Eval Loop Design". Do
not start the task until this is written. Pull the reward signal, done condition, and max cycles
from the goal's [PARAMS] block. Include:
  - Reward signal: <from [PARAMS]>
  - Mechanical gate: <fast binary check — runs in seconds, no LLM judgment>
  - Qualitative gate: <scored check — produces the reward signal>
  - Max cycles: <from [PARAMS] — default 3>
  - Done condition: <from [PARAMS]>

Then execute the task using this loop — repeat up to max_cycles times:
  1. Generate output (inputs are fixed — do not change the spec, only the output)
  2. Run mechanical gate — if it fails, fix and re-run before proceeding to step 3
  2b. Adversarial-verify goals only: run the red-team Workflow (REDTEAM_BRIEF). Fix every
     critical/high hole it returns before step 3. Skip if REDTEAM_BRIEF: N/A.
  3. Spawn checker subagent (CHECKER_BRIEF) — pass artifact paths only, not your context. Checker
     opens "I did not write this." Writes dimension scores + reward signal to CYCLE_LOG.md.
  4. If done condition met -> commit, proceed to next phase
  5. If not -> read CYCLE_LOG.md, fix only the lowest-scoring dimension, return to step 1
  6. If 3 consecutive cycles produce the same reward signal -> exit loop (plateau), commit current
     best, note "plateau after N cycles" in HANDOFF.md

Log each cycle to HANDOFF.md: cycle number, mechanical gate result, reward signal score, what
changed. After each cycle, update the LOOP_TRACKER section — check off completed steps, fill in
paths, SHAs, and reward signals. After the first PASS, exit the eval loop. Run the Ship stage
exactly once only when the current invocation also contains separate explicit shipping approval.
Otherwise do not spawn Shipper, record `N/A - shipping not approved` in HANDOFF.md and LOOP_TRACKER,
and terminate successfully. If an approved Ship stage returns `failed` or `cancelled`, report that
terminal outcome; do not describe the change as merge-ready.

CONTEXT_MANAGEMENT
Run /compact when context approaches the compact threshold (default 170k tokens). After compacting,
state your current checkpoint before continuing. Do NOT compact on turn 1.

BLOCKERS
If you hit a hard blocker: mock/stub it, document in HANDOFF.md under "Needs My Decision", and
continue all work that does not depend on the blocked piece. Skill/process failures use tiered
fallbacks — never silently downgrade substance:
- Tier 1: Run the same process manually (same depth, same searches)
- Tier 2: Reduced scope — mark artifact quality: draft in frontmatter
- Tier 3: Skeleton from trained knowledge — mark quality: placeholder, flag in HANDOFF
If a constraint from [PARAMS] would be violated: stop that task, document in HANDOFF.md under
"Constraint Block", and continue with everything that doesn't violate.

PROOF_PROTOCOL
Every completed phase needs proof, not assertion. After each phase append to PROGRESS.md:
  Phase N: <name> — COMPLETE
  Artifact: <absolute-path>
  Proof: <actual command output — paste it, don't describe it>
  e.g. "npm test: 47 passed, 0 failed" not "tests pass"
  e.g. "312 lines" not "file written"
  e.g. "34 cited URLs" not "well-sourced"
  Commit: <SHA>
Never write "Phase N complete" without proof on the line below it.

MORNING_REPORT
By morning, leave me the morning report in the task's working directory:
1. HANDOFF.md — what completed, workarounds, needs my decision, evidence
2. HANDOFF.html — single-page visual summary (see references/morning-report-specs.md)
3. HANDOFF.excalidraw — architecture/flow diagram (see references/morning-report-specs.md)
Then PUBLISH the report so I wake up to a link, not a file on disk:
4. Run `lavish-axi share HANDOFF.html` — publishes to a hosted URL (headless-safe HTTPS POST, no
   browser needed). Publish PUBLIC: do NOT pass --password. The link must open in one click from
   anywhere, including a comment on the no-mistakes PR — a password gate makes the report
   single-player. The trade: anyone with the URL can read it, so keep credentials, tokens, and
   client PII OUT of the report body — gate the value, not the page. Record the hosted URL in a
   "## Published Report" block at the TOP of HANDOFF.md. The update_key is still a secret: write
   it to HANDOFF.secret.local, add that filename to .gitignore immediately — it is
   update/delete-capable and MUST NEVER be committed. If ht-ml.app is unreachable, fall back to
   `lavish-axi export HANDOFF.html --out HANDOFF.export.html` and note why in HANDOFF.md.
   See references/morning-report-specs.md.

TURN_LIMIT
Stop after the turn limit in [PARAMS] (default 80). If not done, write all three morning-report
files anyway, then publish per MORNING_REPORT step 4.
