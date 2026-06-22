# Interface Depth Analysis

Analysis of public surface area vs. implementation depth for major modules.

A high depth ratio (implementation / interface) indicates good encapsulation: callers see a clean surface while complexity is hidden internally.

## Summary Table

| Module | Public Interface | Internal Impl | Depth Ratio | Verdict |
|--------|-----------------|--------------|-------------|---------|
| `scripts/domain_resolver.py` | 8 exported | 14 internal | 1.75 | SHALLOW |
| `scripts/domain_classifier.py` | 3 exported | 5 internal | 1.67 | SHALLOW |
| `scripts/confidence_scorer.py` | 2 exported (+ 2 types) | 5 internal | 2.5 | ADEQUATE |
| `scripts/pipeline_base.py` (ResearchPipeline) | 8 abstract/public | 25 implementation methods | 3.1 | ADEQUATE |
| `scripts/autoresearch_agent.py` | 1 exported (run_agent) | 5 internal | 5.0 | DEEP |
| `scripts/eval_pipeline.py` | 1 exported (main) | 3 internal eval functions | 3.0 | ADEQUATE |

---

## Module-by-Module Analysis

### `scripts/domain_resolver.py`
**Path:** `C:\Users\mitch\Everything_CC\research-process-builder\scripts\domain_resolver.py`

**Exported interface (8):**
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

**Depth ratio:** 14/8 = **1.75** — SHALLOW

**Verdict:** The exported surface is too large relative to implementation depth. Many helpers that are purely internal details (`domain_matches_company`, `is_blocked`, `normalize_domain`) are exported by necessity because `series_a_pipeline.py` and `eval_pipeline.py` import them directly. This leaks resolver internals into callers.

---

### `scripts/domain_classifier.py`
**Path:** `C:\Users\mitch\Everything_CC\research-process-builder\scripts\domain_classifier.py`

**Exported interface (3):**
- `classify_domain()`
- `is_blocked_smart()`
- `seed_cache()`

**Internal (5):**
`_load_cache`, `_save_cache`, `_normalize`, `_gpt_classify`, `_cli`

**Depth ratio:** 5/3 = **1.67** — SHALLOW

**Verdict:** The module is actually well-structured — callers only need `classify_domain()` and `is_blocked_smart()`. `seed_cache()` is quasi-operational. The ratio appears shallow because the implementation is genuinely thin (correct for this module's scope). The main concern is that `classify_domain()` returns a raw dict instead of a typed result — callers must know the `"category"` key name.

---

### `scripts/confidence_scorer.py`
**Path:** `C:\Users\mitch\Everything_CC\research-process-builder\scripts\confidence_scorer.py`

**Exported interface (2 functions + 2 types):**
- `score_confidence()` — primary entry point
- `ConfidenceLevel` enum, `SignalScores` NamedTuple (exported types)
- `score_name_quality()`, `score_funding_explicit()`, `score_source_tier()` — exported but callers should use `score_confidence()`

**Internal (5):**
`HEADLINE_FRAMING` regex, `_normalize_domain`, `_composite`, signal-level source tier lists

**Depth ratio:** ~2.5 — ADEQUATE

**Verdict:** Well-structured. `score_confidence()` is the clean single entry point. The three sub-scorers are exported (useful for debugging and testing) but the primary caller interface is clean. Typed return via `SignalScores` NamedTuple is a good pattern.

---

### `scripts/pipeline_base.py` (ResearchPipeline)
**Path:** `C:\Users\mitch\Everything_CC\research-process-builder\scripts\pipeline_base.py`

**Public / abstract methods (8):**
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

**Depth ratio:** 25/8 = **3.1** — ADEQUATE

**Verdict:** The base class has grown significantly — 1,000+ lines. The Supabase CRUD methods (`check_supabase_table`, `create_supabase_table`, `get_supabase_row`) could be extracted into a `SupabaseClient` helper, which would deepen the base class's domain focus.

---

### `scripts/autoresearch_agent.py`
**Path:** `C:\Users\mitch\Everything_CC\research-process-builder\scripts\autoresearch_agent.py`

**Exported interface (1):**
- `run_agent()` — the agent loop

**Internal (5):**
`ToolTracker` class, `run_shell()`, `handle_tool_call()`, `assemble_system_prompt()`, `main()`

**Depth ratio:** 5/1 = **5.0** — DEEP

**Verdict:** Well encapsulated. The agent loop is the only public concept; all the scaffolding (tool tracking, shell execution, prompt assembly) is internal.

---

### `scripts/eval_pipeline.py`
**Path:** `C:\Users\mitch\Everything_CC\research-process-builder\scripts\eval_pipeline.py`

**Exported interface (1):**
- `main()` — runs eval, exits 0/1

**Internal (3):**
`eval_validation_gate()`, `eval_dedup()`, `eval_domain_resolution()`

**Depth ratio:** 3/1 = **3.0** — ADEQUATE

**Verdict:** Clean structure for an eval harness. Exit code convention (0=pass at >=90%, 1=fail) is a good scriptable contract. The three eval functions are internal implementation details correctly kept private.
