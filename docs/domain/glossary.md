# Ubiquitous Language Glossary

Terms extracted from the codebase, process files, and SKILL.md. Use these exact terms when discussing this system.

| Term | Definition | Key Files |
|------|-----------|-----------|
| **Research Process** | A validated, portable `.md` file with step-by-step search instructions, extract specs, stop-if conditions, and a kill list. Output of the 6-phase build methodology. | `processes/find-*/process.md`, `SKILL.md` |
| **Search Pattern** | A parameterized Google query template (e.g., `[name] alternatives OR competitors OR "vs"`). Patterns are the atomic unit tested in the anneal loop. | `SKILL.md Phase 2`, `scripts/patterns_config.json` |
| **Anneal Loop** | The iterative test-score-fix-retest cycle that drives accuracy from a starting point to the 90%+ target. Inspired by simulated annealing — systematic elimination of weak patterns. | `scripts/anneal.py`, `scripts/eval_iter.py` |
| **Ground Truth (GT)** | Known-correct company/domain pairs used to measure accuracy objectively. GT misses drive targeted fix patterns in Phase 5. | `ground-truth/[company].json`, `ground-truth/schema.json`, `scripts/eval_pipeline.py` |
| **Tier** | Company size category: T1 (Known — Fortune 500/unicorns), T2 (Mid — growth-stage with some press), T3 (Obscure — micro/bootstrapped/early-stage). Processes must work across all three tiers. | `SKILL.md`, `scripts/tier_analysis.py` |
| **Kill List** | Patterns that visually appear relevant but consistently return zero-quality results. Documented so future agents don't retry them. Saves 30-40% of search budget. | Every `processes/find-*/process.md` |
| **Pattern Classification** | Score-based label: PRIMARY (Q>=4, C>=4), ENRICHMENT (Q>=4, C>=3), SITUATIONAL (Q>=4, C<=2), FALLBACK (Q>=3), KILL (Q<=2). | `SKILL.md Phase 4` |
| **Quality Score (Q)** | 1-5 rating: how useful/specific are search results for the stated goal? | `SKILL.md Phase 3` |
| **Consistency Score (C)** | 1-5 rating: does the pattern work across company size tiers? 5 = works for T1-T3. | `SKILL.md Phase 3` |
| **Domain Resolver** | 3-tier waterfall: article regex extraction → GPT extraction → Serper search. Resolves a company name to its official domain. | `scripts/domain_resolver.py` |
| **Domain Classifier** | LLM-backed categorizer (gpt-4o-mini) with a committed JSON cache. Classifies domains as `real_company`, `news`, `social`, `data_platform`, `legal`, `cdn`, `tracker`, `short_url`, `edu`, `unknown`. Only `real_company` accepts; all others reject. | `scripts/domain_classifier.py`, `data/domain_classifications.json` |
| **ResearchPipeline** | The abstract base class for 4-stage research pipelines: (1) Discover via SerperDev, (2) Score+Filter, (3) Enrich via GPT+scrape, (4) Output CSV+JSON+Supabase. | `scripts/pipeline_base.py` |
| **Confidence Scorer** | 3-signal composite gate: name_quality + funding_explicit + source_tier. Any LOW signal → composite LOW (drop). All HIGH → write to Supabase. | `scripts/confidence_scorer.py` |
| **Source Tier** | Classification of a news/data source's reliability. Tier-S = highest (finsmes.com, prnewswire.com). Used by the confidence scorer. | `scripts/confidence_scorer.py` |
| **Monitor** | A validated research process promoted to a scheduled daily pipeline run. Lives on an orphan git branch, mounted as a worktree under `monitors/`. | `monitors/series-a-daily/`, `MONITORS.md` |
| **Graduated Prompt** | A production-ready LLM prompt that has been annealed to a target score on a test set. Lives in `prompts/[name]/` with `prompt.md` + `metadata.json`. Edit by re-annealing, not in-place. | `prompts/extract-companies-batch/` |
| **Fuzzy Dedup** | Token-overlap + domain-based deduplication to catch the same company reported under slightly different names across multiple sources. | `scripts/domain_resolver.py:fuzzy_dedup_companies()` |
| **Disambiguation** | Adding a category qualifier or domain anchor to resolve ambiguous company names (e.g., "Clay" → "Clay GTM" or `clay.com`). Required for names <= 6 chars or common English words. | `SKILL.md Preprocessing`, `processes/find-*/process.md` |
| **Stop-If Condition** | A per-step exit rule that halts the process when sufficient data has been found, preventing wasted search budget. | Every `processes/find-*/process.md` |
| **Backfill** | Retroactively resolving domains for historical pipeline records that were stored without a valid domain. | `scripts/backfill_domains.py`, `output/backfill-*.json` |
| **GT Validation State** | Persistent JSON file tracking which (company, domain) pairs have already been agent-verified, so reruns don't re-spend agent budget on known-good entries. | `data/gt_validation_state.json`, `scripts/gt_validation.py` |
