# Locally verified GTM MCP read interface

## Scope and evidence boundary

This is an inventory of read-oriented GTM tool metadata visible in the local
runtime registry on 2026-08-10. No GTM MCP call, resource read, or remote
operation was invoked to create it. The inventory records only tool names and
input fields that were locally visible; it does not infer output shapes or
operational behavior.

| Tool | Locally verified input fields |
| --- | --- |
| `campaign_ideas` | optional: `persona`, `signal`, `vertical` |
| `config` | no defined input fields |
| `connections` | required: `entity`; optional: `depth` |
| `diagnose` | required: `symptom`; optional: `context` |
| `digest` | optional: `edition`, `topic` |
| `gtm_answer` | required: `product` (enum), `query`; optional: `limit`, `persona`, `signal`, `vertical` |
| `lookup` | required: `entity` |
| `recommend_tools` | required: `need`; optional: `category`, `limit` |
| `route` | required: `about`, `need` |
| `scrape_guide` | required: `target` |
| `search` | required: `query`; optional: `category`, `data_type`, `domain` (enum: `tools`, `campaigns`, `offers`, `personas`), `limit` |
| `source_guide` | required: `topic`; optional: `jurisdiction` |
| `trends` | required: `topic`; optional: `window_weeks` |

## Explicit limitations

- **UNVERIFIED:** concrete output schemas, pagination limits, error taxonomy,
  and authentication requirements.
- **UNVERIFIED:** a server-level side-effect guarantee. The available metadata
  supports a documentation-only read boundary, not a proof that every server
  implementation is side-effect-free.
- No Clay or full GTM provider is implemented. No Clay tool was callable in
  this runtime; installed Clay skill text is not evidence of an available MCP
  tool or its input/output schema.
- Mutation, enrichment triggering, and remote writes are prohibited by this
  phase. A later integration must obtain server-owned schemas and separately
  authorize any live operation.
