# Phase 04 — Parallel Search MCP and GTM read-interface documentation

**Status:** pending  
**Canonical source:** `.harness/goals/repo-cleanup-full-update/PLAN.md` → `## Phases` → Phase 4  
**Routing:** direct configuration/documentation plus TDD contract checks  
**Commit:** `feat: configure repository search providers`

## Deliverable

Add current-official, repository-scoped Parallel Search MCP configuration without credential values and document only the actually discoverable GTM MCP read surface.

## Acceptance

- [ ] Parallel/host primary-doc URLs, access date, endpoint, transport, auth, and repository scope are recorded.
- [ ] Repository MCP JSON parses and uses OAuth or environment references only.
- [ ] Missing auth fails clearly; no paid/live query is required.
- [ ] GTM document lists confirmed read tools/contracts and explicitly marks unverified gaps.
- [ ] No remote mutation is invoked or exposed as part of this goal.
- [ ] `py -m pytest tests/test_mcp_configuration.py -q` passes after a recorded red run; literal-secret scan is empty.
- [ ] Proof and phase commit SHA are appended to `PROGRESS.md`.

## Prohibitions

No literal credentials, live writes, full GTM provider, or claims unsupported by current official/local evidence.
