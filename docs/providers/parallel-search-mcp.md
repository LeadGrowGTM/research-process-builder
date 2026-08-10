# Parallel Search MCP for Codex

## Tracked configuration boundary

This repository configures Codex, in a trusted project, through
`.codex/config.toml`. It is TOML, with the server entry under
`[mcp_servers.parallel-search]`; it is not a Claude Code `.mcp.json` file.
The tracked entry uses the Parallel OAuth endpoint
`https://search.parallel.ai/mcp-oauth` over Streamable HTTP. It sets
`auth = "oauth"`, restricts the server to `web_search` and `web_fetch`, and
prompts for approval before either tool is used.

The configuration contains no credential value. It does not authenticate an
operator, initiate a connection, or perform a search. After an operator has
explicitly approved authentication, the required local action is:

```text
codex mcp login parallel-search
```

If OAuth has not been completed, the documented OAuth endpoint requires
authentication; a missing-auth request returns HTTP 401. The local test models
that condition only by producing the command above. Do not authenticate,
connect, or query as part of this repository configuration or documentation
phase.

## Evidence

- [OpenAI Codex MCP documentation](https://developers.openai.com/codex/mcp),
  accessed 2026-08-10: project-scoped `.codex/config.toml`, the
  `[mcp_servers.<name>]` table, remote `url`, OAuth support, and the
  `codex mcp login <server-name>` flow.
- [Parallel Search MCP documentation](https://docs.parallel.ai/integrations/mcp/search-mcp),
  accessed 2026-08-10: Streamable HTTP, the OAuth endpoint, the 401
  unauthenticated behavior, and the advertised `web_search` / `web_fetch`
  tools.
- [Parallel MCP quickstart](https://docs.parallel.ai/integrations/mcp/quickstart),
  accessed 2026-08-10: the separate anonymous Search MCP endpoint and its
  lower-rate-limit posture.

The anonymous `https://search.parallel.ai/mcp` endpoint is a verified vendor
alternative, but is deliberately not the repository default: it permits
anonymous access, so it cannot provide the deterministic missing-auth path
used here.

For clarity, [Claude Code's MCP documentation](https://code.claude.com/docs/en/mcp),
accessed 2026-08-10, describes its own project-scoped `.mcp.json` format. That
is a different host integration and is not configured by this repository.
