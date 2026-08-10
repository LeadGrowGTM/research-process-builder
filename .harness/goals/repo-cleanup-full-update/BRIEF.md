# Planner Brief — Repository Cleanup and Resumable Autoresearch

## Outcome

Modernize `research-process-builder` without changing its approval method: a process must reach at least 90% ground-truth validation and then halt for explicit human review. Preserve every path represented by the immutable preflight recovery refs, make generated material recoverable before removal, add a secret-free repository-scoped Parallel Search MCP configuration, and replace inherited-context autoresearch with a provider-neutral, persisted state machine whose roles receive fresh bounded envelopes.

## Authoritative workspace

- Repository: `C:/Users/mitch/Everything_CC/tools/data/research-process-builder`
- Worktree: `C:/Users/mitch/Everything_CC/tools/data/research-process-builder/.worktrees/repo-cleanup-full-update`
- Branch: `wt/repo-cleanup-full-update`
- Initial dirty recovery ref: `refs/recovery/repo-cleanup-full-update/initial-dirty` (`e3932d55217c29ac28eca16fdc7e6f6c5c3e3337`)
- Dashboard metadata recovery ref: `refs/recovery/repo-cleanup-full-update/dashboard-metadata` (`7bca5038f31a9427f1781e823a388d0dcf2ac33d`)
- Named stash is read-only. Never apply, pop, drop, rewrite, or delete it.

## Fixed requirements

1. Inventory all tracked modifications/deletions and all untracked blobs from the recovery commit trees. Every canonical old/new path must have a category, disposition, object ID, recovery ref, and exact recovery command.
2. Reconcile the recorded 3,558-path preflight count with Git's authoritative object-tree enumeration. Current read-only evidence is 42 tracked status entries plus 3,519 untracked blobs = 3,561 unique paths. Preserve all 3,561; document the three-entry measurement discrepancy rather than deleting entries to make the old number fit.
3. Restore reusable source, tests, and durable research knowledge selectively. Copy disposable/generated or campaign-specific material to ignored `.quarantine/repo-cleanup-full-update/` with hashes and a recovery map before removing it from the reviewed tree.
4. Put canonical domain terms in root `CONTEXT.md`; put verified operator guidance in root `CLAUDE.md`. Keep README commands and artifact policy consistent with observed behavior.
5. Add repository-scoped Parallel Search MCP configuration only after checking current official documentation. Use OAuth or environment-variable references; never serialize a credential. Discover and document the installed GTM MCP read surface; perform no remote writes.
6. Make `scripts/autoresearch_agent.py` a thin composition CLI and `scripts/autocontext_runner.py` a compatibility CLI over a deep orchestration module. Each cycle is fresh Inventor → independent In-bounds Checker → independent Novelty Checker → Executor → independent Evaluator → pure deterministic Gate.
7. Persist versioned, schema-validated canonical JSON artifacts and an append-only hash-addressed journal. Resume at the first missing stage and never repeat a completed idempotency key or paid operation.
8. Expose provider-neutral Search Flow, Site Extraction Flow, and Source Adapter contracts. Site extraction requires known URLs and attempts fetch/scrape, selectors, regex, then deterministic patterns before an explicitly enabled optional LLM fallback.
9. Prove all behavior with deterministic doubles and zero paid spend. Do not create the future flow catalog or a full GTM provider.

## Safety and scope boundaries

- Cost ceiling: `$0` paid API spend.
- No production Supabase, Clay/GTM, monitor, sheet, job, credential, remote, push, PR, merge, deploy, or live write.
- Parallel live authentication is optional verification only; deterministic configuration/contract checks are authoritative when network or auth is absent.
- Keep unrelated files unchanged. Inspect overlap with recovered user work before each phase.
- Commit only at phase boundaries during Maker execution; Planner does not commit.

## Acceptance summary

Completion requires a clean task worktree after phase commits, a full recovery inventory with explicit 3,558→3,561 reconciliation, parsed credential-free MCP configuration, verified documentation, authoritative tests with no new skips, and a stubbed end-to-end run demonstrating context isolation, both pre-execution rejections, accepted execution, independent evaluation, all four gate outcomes, budget/retry exhaustion, rollback, corruption/version failure, and resume without duplicate paid work. Checker PASS requires every rubric dimension ≥4 and mean ≥4.5.
