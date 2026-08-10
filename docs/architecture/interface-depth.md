# Interface Depth Analysis

Analysis of module interfaces and implementation depth for major modules. Canonical resumable-autoresearch language is defined in [CONTEXT.md](../../CONTEXT.md).

A deep module gives callers leverage by hiding substantial behavior behind a small interface; a shallow module asks callers to learn nearly as much as its implementation.

## Summary Table

| Module | Interface | Internal implementation | Depth | Verdict |
|--------|-----------|-------------------------|-------|---------|
| `scripts/domain_resolver.py` | 8 exports | 14 internal functions | Shallow | SHALLOW |
| `scripts/domain_classifier.py` | 3 exports | 5 internal functions | Narrow but thin | ADEQUATE |
| `scripts/confidence_scorer.py` | 2 entry points + 2 types | 5 internal details | Adequate | ADEQUATE |
| `scripts/pipeline_base.py` (ResearchPipeline) | 8 abstract/public methods | 25 implementation methods | Broad | ADEQUATE |
| `scripts/autoresearch_agent.py` | 1 export (`run_agent`) | 5 internal details | Deep | DEEP |
| `scripts/eval_pipeline.py` | 1 export (`main`) | 3 internal evaluators | Adequate | ADEQUATE |
---

## Module-by-Module Analysis

### `scripts/domain_resolver.py`
**Path:** `C:\Users\mitch\Everything_CC\research-process-builder\scripts\domain_resolver.py`

**Interface (8):**
- `normalize_domain()`
- `is_blocked()`
- `domain_matches_company()`
- `validate_domain()`
- `detect_industry()`
- `resolve_domain_agent()`
- `resolve_domain()`
- `fuzzy_dedup_companies()`, `names_are_similar()`, `match_existing_company()`

**Internal (non-exported) functions (14):**
`_url_hostname`, `_extract_domain_from_article`, `_extract_domain_gpt`, `_serper_search`, `_extract_domains_from_text`, `_find_domain_serper`, `_format_serper_results`, `_tokenize_name`, `_levenshtein`, plus supporting private wrappers.

**Verdict:** The exported surface is too large relative to implementation depth. Many helpers that are purely internal details (`domain_matches_company`, `is_blocked`, `normalize_domain`) are exported by necessity because `series_a_pipeline.py` and `eval_pipeline.py` import them directly. This leaks resolver internals into callers.

---

### `scripts/domain_classifier.py`
**Path:** `C:\Users\mitch\Everything_CC\research-process-builder\scripts\domain_classifier.py`

**Interface (3):**
- `classify_domain()`
- `is_blocked_smart()`
- `seed_cache()`

**Internal (5):**
`_load_cache`, `_save_cache`, `_normalize`, `_gpt_classify`, `_cli`

**Verdict:** The module is actually well-structured — callers only need `classify_domain()` and `is_blocked_smart()`. `seed_cache()` is quasi-operational. Its implementation is genuinely thin, which is appropriate for this module's scope. The main concern is that `classify_domain()` returns a raw dict instead of a typed result — callers must know the `"category"` key name.

---

### `scripts/confidence_scorer.py`
**Path:** `C:\Users\mitch\Everything_CC\research-process-builder\scripts\confidence_scorer.py`

**Interface (2 functions + 2 types):**
- `score_confidence()` — primary entry point
- `ConfidenceLevel` enum, `SignalScores` NamedTuple (exported types)
- `score_name_quality()`, `score_funding_explicit()`, `score_source_tier()` — exported but callers should use `score_confidence()`

**Internal (5):**
`HEADLINE_FRAMING` regex, `_normalize_domain`, `_composite`, signal-level source tier lists

**Verdict:** Well-structured. `score_confidence()` is the clean single entry point. The three sub-scorers are exported (useful for debugging and testing) but the primary caller interface is clean. Typed return via `SignalScores` NamedTuple is a good pattern.

---

### `scripts/pipeline_base.py` (ResearchPipeline)
**Path:** `C:\Users\mitch\Everything_CC\research-process-builder\scripts\pipeline_base.py`

**Interface methods (8):**
- `run()` — primary entry point
- `run_discovery()`
- `score_and_filter()` — abstract (subclass overrides)
- `get_extraction_prompt()` — abstract
- `enrich_companies()`
- `write_output()`
- `push_to_supabase()`
- `add_arguments()` / `build_parser()`

**Internal implementation methods (25+):**
`run_single_query`, `extract_companies_batch`, `fetch_url`, `extract_with_openai`, `lookup_domain`, `_log_domain_resolution`, `post_extract_filter`, `build_enriched_record`, `build_skip_enrich_record`, `clean_article_content`, `validate_domain_semantic`, `supabase_headers`, `check_supabase_table`, `get_supabase_schema_sql`, `create_supabase_table`, `get_supabase_row`, `fetch_recent_companies`, `push_to_webhook`, `get_webhook_row`, `get_pipeline_version`, and helpers.

**Verdict:** The base class has grown significantly — 1,000+ lines. The Supabase CRUD methods (`check_supabase_table`, `create_supabase_table`, `get_supabase_row`) could be extracted into a `SupabaseClient` helper, which would deepen the base class's domain focus.

---

### `scripts/autoresearch_agent.py`
**Path:** `C:\Users\mitch\Everything_CC\research-process-builder\scripts\autoresearch_agent.py`

**Interface (1):**
- `run_agent()` — the agent loop

**Internal (5):**
`ToolTracker` class, `run_shell()`, `handle_tool_call()`, `assemble_system_prompt()`, `main()`

**Verdict:** Well encapsulated. The agent loop is the only public concept; all the scaffolding (tool tracking, shell execution, prompt assembly) is internal.

---

### `scripts/eval_pipeline.py`
**Path:** `C:\Users\mitch\Everything_CC\research-process-builder\scripts\eval_pipeline.py`

**Interface (1):**
- `main()` — runs eval, exits 0/1

**Internal (3):**
`eval_validation_gate()`, `eval_dedup()`, `eval_domain_resolution()`

**Verdict:** Clean structure for an eval harness. Exit code convention (0=pass at >=90%, 1=fail) is a good scriptable contract. The three eval functions are internal implementation details correctly kept private.

---

## Planned Autoresearch Orchestration

The planned `AutoresearchOrchestrator` is a deep Module: callers cross one Interface, `run(request: RunRequest) -> RunSummary`, while role order, resume, budgets, idempotency, and transition handling remain local to its implementation. CLI entry points are composition roots, not coordinators. Provider seams are read-only Source Adapters with local test adapters, so provider details do not leak through the orchestration Interface. The rationale and tradeoffs are recorded in [ADR 0003](../domain/adr/0003-resumable-autoresearch-orchestration.md).
