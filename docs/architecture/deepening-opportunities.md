# Deepening Opportunities

Concrete improvements to reduce leaked abstractions, improve encapsulation, and simplify caller code.

---

## 1. domain_resolver.py — Too Many Exports Leak Internals

**Problem:** `domain_resolver.py` exports `normalize_domain`, `is_blocked`, `domain_matches_company`, `validate_domain`, `detect_industry`, and `names_are_similar` — all used directly by callers (`series_a_pipeline.py`, `eval_pipeline.py`). These are resolver implementation details, not caller concerns. Every caller must know the internal domain model.

**Proposed Solution:** Introduce a `DomainResolver` class that wraps these as private methods. The public API shrinks to:
- `resolve(company_name, source_url) -> str | None`
- `dedup(companies) -> list[dict]`
- `validate(domain) -> bool`

Callers stop importing 8 functions and import 1 class.

**Impact:** HIGH — affects all pipeline scripts, removes coupling to resolver internals, makes the dedup + domain-validation contract clearer.

---

## 2. pipeline_base.py — Supabase CRUD Should Be a Separate Client

**Problem:** `ResearchPipeline` contains ~200 lines of Supabase operations: `check_supabase_table`, `create_supabase_table`, `get_supabase_schema_sql`, `get_supabase_row`, `fetch_recent_companies`, `push_to_supabase`, `supabase_headers`. These are storage implementation details mixed into the pipeline orchestration layer.

**Proposed Solution:** Extract a `SupabaseClient` class (or module) with the storage operations. `ResearchPipeline` holds a `SupabaseClient` instance and delegates. Pipeline logic stays at the pipeline level; storage logic stays at the storage level.

**Impact:** HIGH — makes the base class ~200 lines shorter, makes `SupabaseClient` independently testable without running a pipeline, reduces the cost of swapping Supabase for another store.

---

## 3. domain_classifier.py — Returns Raw Dict Instead of Typed Result

**Problem:** `classify_domain()` returns `{"category": str, "confidence": str, "source": str}`. Callers must know the key name `"category"` and the valid category strings. This is an untyped contract that breaks silently on typos.

**Proposed Solution:** Return a `DomainClassification` NamedTuple (or dataclass) with typed fields `category: str`, `confidence: str`, `source: str`. Match the pattern `confidence_scorer.py` already uses with `SignalScores`. Add a `ClassificationCategory` enum matching the 10 valid values.

**Impact:** MEDIUM — improves call-site safety, enables IDE completion, makes the contract explicit. Low migration effort (only `is_blocked_smart()` and callers in `domain_resolver.py`).

---

## 4. series_a_pipeline.py — Pass-Through Filter Methods Add No Value

**Problem:** `series_a_pipeline.py` defines `score_and_filter()` which calls `_is_valid_funding_article()`, `_extract_amount()`, and `_deduplicate()` in a chain. These three helpers are private to this file and used exactly once each. The method chain exists primarily to satisfy the base class abstract method, but adds no reusable value.

**Proposed Solution:** Collapse `score_and_filter()` into a single method. Remove the private helpers that exist only to be called once. Reduces 3 private methods to 0 while keeping the same logic.

**Impact:** LOW — reduces line count and one level of indirection with no architectural benefit. Only touches one file.

---

## 5. autoresearch_agent.py — ToolTracker Has No Test Coverage

**Problem:** `ToolTracker` manages budget caps and query/scrape counts. It's the agent's guard against runaway API spend. But it has zero test coverage — a regression in its accounting logic would only be caught when the agent burns over-budget.

**Proposed Solution:** Add to `__tests__/` or `scripts/test_confidence_scorer.py`-style: unit tests for `ToolTracker.can_query()`, `can_scrape()`, and budget enforcement. 3-4 test cases covering: at-limit, over-limit, and zero-budget initialization.

**Impact:** MEDIUM — the financial risk of an under-counting bug is real ($0.50 default budget cap, but agents can be given higher limits). Low implementation cost.

---

## 6. eval_pipeline.py — Accuracy Threshold Is Magic Number

**Problem:** The 90% accuracy threshold is hardcoded as a literal in `main()`. When the threshold needs to change (e.g., per-pipeline thresholds, tighter gates for production promotion), it requires editing the eval harness.

**Proposed Solution:** Read the threshold from a config file (e.g., `data/eval_config.json`) or an environment variable (`EVAL_ACCURACY_THRESHOLD`). Default to 90% if not set. Allows different thresholds for CI vs. manual runs.

**Impact:** LOW — pure config externalization, no logic change. Useful primarily when adding new pipeline types with different maturity levels.
