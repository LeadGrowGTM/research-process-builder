# ADR-0001: ResearchPipeline Base Class as 4-Stage Template

## Status
Accepted

## Context

The repository started with a single script (`series_a_pipeline.py`) that handled discovery, filtering, enrichment, and output in a monolithic function. As the need arose for additional pipelines (product launches, game signals, motorica enrichment), the same 4-stage pattern was needed with different query sets, filter logic, and extraction prompts. Three options existed:

1. **Copy-paste** the monolithic script for each new pipeline — fast initially, creates maintenance debt (shared bug fixes must be applied N times).
2. **Shared utility functions** — extract helpers into a module, callers compose them manually — flexible but no enforced contract.
3. **Abstract base class** (`ResearchPipeline`) — defines the 4-stage contract, subclasses override only what differs — clear extension points, shared infrastructure guaranteed.

Domain resolution logic was also duplicated across `test_domain_resolution.py`, `backfill_domains.py`, and `pipeline_base.py`, causing the same bugs to be fixed independently in each file.

## Decision

Introduce `pipeline_base.py` containing `ResearchPipeline` as an abstract base class. The class owns:
- Stage 1: parallel SerperDev discovery (shared)
- Stage 2: score_and_filter() — abstract, subclass provides
- Stage 3: GPT extraction + domain lookup (shared, using the graduated prompt)
- Stage 4: CSV + JSON output + optional Supabase push (shared)

Separately, consolidate all domain resolution into `domain_resolver.py` as the single source of truth. `ResearchPipeline` imports from it; no other script duplicates the logic.

`series_a_pipeline.py` becomes a thin subclass that defines `QUERIES`, `PIPELINE_NAME`, `SUPABASE_TABLE`, `score_and_filter()`, and `get_extraction_prompt()`.

## Consequences

**Good:**
- New pipelines (product launches, game signals) need ~50-100 lines instead of ~500.
- Infrastructure improvements (Firecrawl fallback, confidence scoring, retry logic) are applied once and inherited by all pipelines.
- The graduated extraction prompt (`prompts/extract-companies-batch/`) is loaded by the base class; subclasses cannot accidentally diverge from it.
- `eval_pipeline.py` tests the shared modules once and covers all pipelines.

**Bad:**
- Subclasses are coupled to the 4-stage structure. A pipeline that needs a fundamentally different flow (e.g., no discovery stage, batch-from-CSV) must either inherit and override heavily or bypass the base class.
- The base class carries significant state in instance variables — harder to test individual stages in isolation without running the full class.
- `sys.path.insert()` in the base class for shared-scripts discovery is fragile on machines where the workspace layout differs from `C:\Users\mitch\Everything_CC\`.
