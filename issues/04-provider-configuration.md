# Phase 04 — Parallel Search MCP and GTM read-interface documentation

**Status:** complete ? reviewed; commits `d03da55`, `f87b7a3`, `27e13a3`
**Canonical source:** `.harness/goals/repo-cleanup-full-update/PLAN.md` → `## Phases` → Phase 4
**Routing:** direct configuration/documentation plus TDD contract checks
**Commit:** `feat: configure repository search providers`

## Deliverable

Add current-official, repository-scoped Parallel Search MCP configuration without credential values and document only the actually discoverable GTM MCP read surface.

## Acceptance

- [x] Parallel/host primary-doc URLs, access date, endpoint, transport, auth, and repository scope are recorded.
- [x] Repository MCP TOML parses and uses environment references only.
- [x] Missing auth fails clearly; no paid/live query is required.
- [x] GTM document lists confirmed read tools/contracts and explicitly marks unverified gaps.
- [x] No remote mutation is invoked or exposed as part of this goal.
- [x] `py -m pytest tests/test_mcp_configuration.py -q` passes after a recorded red run; literal-secret scan is empty.
- [x] Proof and phase commit SHA are appended to `PROGRESS.md`.

## Prohibitions

No literal credentials, live writes, full GTM provider, or claims unsupported by current official/local evidence.
