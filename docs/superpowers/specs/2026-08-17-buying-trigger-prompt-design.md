# Buying Trigger Prompt Design

## Goal

For each company, generate exactly two distinct campaign ideas describing an
observable situation that could make its buyer need the company's outcome now.

## Output

The prompt produces two fields: `campaign_idea_1` and `campaign_idea_2`. Each
supported idea is one plain sentence of at most 14 words and cites retained
Evidence. If Evidence supports fewer than two ideas, the unsupported field is
returned as unknown instead of being invented.

## Constraints

- One company per API call.
- Use cached dossier Evidence only; no web research or source purchases.
- The two ideas must describe different situations, not paraphrases.
- Prefer observable changes such as growing workload, added complexity, new
  requirements, or rising risk.
- Do not claim that the situation is happening unless Evidence says it is.
- Avoid jargon, products, features, commas, and combined ideas.
- Report requested and resolved model IDs with the outputs.
- Keep the prompt provisional; no automatic Approval.

## Execution

A thin CLI loads the ten SaaS dossiers, sends one synchronous request per
company through the existing OpenAI adapter, writes an append-safe lineage
artifact under `runs/company-enrichment/buying-trigger/`, and prints all outputs.
