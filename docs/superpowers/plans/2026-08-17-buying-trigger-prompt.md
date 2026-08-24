# Buying Trigger Prompt Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Run a provisional two-idea buying-trigger prompt over the ten cached SaaS dossiers.

**Architecture:** Add one YAML prompt and one thin CLI around the existing OpenAI model client. Extend the enrichment field map with two output slots so the existing citation and unknown validation remains authoritative.

**Tech Stack:** Python 3.12, PyYAML, existing company-enrichment OpenAI adapter.

## Global Constraints

- Exactly two output slots per company.
- One company per API call.
- Cached Evidence only and zero source purchases.
- Unsupported output becomes unknown.
- No automatic Approval.

### Task 1: Prompt and execution

**Files:**
- Create: `prompts/company-enrichment/buying-trigger-analysis.md`
- Create: `scripts/company_enrichment_buying_trigger_loop.py`
- Modify: `scripts/company_enrichment/executors.py`

**Interfaces:**
- Consumes: ten published SaaS dossier YAML files and `OpenAIModelClient`.
- Produces: `campaign_idea_1`, `campaign_idea_2`, and a lineage `results.json`.

- [ ] Add the YAML Goal Output Contract and single-turn prompt.
- [ ] Register the two output fields in `P0_ENRICHMENTS`.
- [ ] Add the thin one-company-per-call CLI.
- [ ] Compile the changed Python files.
- [ ] Run one paid round through `lg run --env prod` and present every output.
