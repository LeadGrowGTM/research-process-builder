# Backlog

## In flight
- [ ] buying-trigger-prompt-loop - Two-idea buying-trigger prompt anneal; v2 (0.2.0, mini) and v3-luna (0.2.0, luna) executed over 10 cached dossiers, mini-vs-luna review pending; saas-01 example-parroting flagged for prompt 0.3.0 (since 2026-08-17)
- [x] repo-cleanup-full-update - Checker PASS (4.71/5); sanitized local export fallback recorded; authoritative worktree C:\Users\mitch\Everything_CC\tools\data\research-process-builder\.worktrees\repo-cleanup-full-update (since 2026-08-10)
## Queued
## Done
- [x] running-ads-free-scraper - ads-v3 1.00/1.00; approved by Mitch 2026-08-18 ("Approve after GT fixes": saas-01 call_to_action corrected, deterministic rescore to 1.00). Decision recorded in docs/reports/ads-v3-review-sheet.md; canonical prompt prompts/company-enrichment/running-ads-offer-intelligence.md (done 2026-08-18)
- [x] icp-persona-native-loop - ICP/persona enrichment approved at icp-persona-live-v26-luna: dev 1.00 / holdout 1.00, zero hard failures, gpt-5.6-luna at ~USD 0.0014/company. Twenty gpt-4.1-mini lineages stalled 0.78-0.81; luna cleared on first real run after the 2026-08-20 repricing (v1's luna attempt was cap-blocked by the stale price table). Casing normalized in code, specificity rule added from review. See docs/reports/icp-persona-anneal.md (done 2026-08-24)
- [x] news-product-launches-loop - Annealed to news-v11-luna 0.974/0.997, approved and graduated 2026-08-21; canonical prompt in prompts/company-enrichment/. See docs/reports/signal-enrichments-anneal.md (done 2026-08-21)
- [x] competitor-intelligence-loop - Annealed to comp-v11-luna 0.933/0.960, approved and graduated 2026-08-21; canonical prompt in prompts/company-enrichment/. See docs/reports/signal-enrichments-anneal.md (done 2026-08-21)
- [x] parallel-search-task-api - Parallel Search API transport + budget-guarded Task API client (base tier max, $1 ceiling); Serper-vs-Parallel A/B on news + competitor signals: Serper wins decisively (news events 0.976/1.000 vs 0.306/0.516; comp named_set 0.878/0.943 vs 0.272/0.546) at 1/5 the query price. Serper stays primary, Parallel is a live fallback. See docs/reports/serper-vs-parallel.md (done 2026-08-19)
- [x] anneal-news-competitor-signals - Anneal news and competitor signal enrichments to >=0.90 (done 2026-08-18)
- [x] research-company-enrichment-lib - Build and benchmark company enrichment library (done 2026-08-13)
  Completed corpus and benchmark library (b8bb6c2, 7578483, f15a9fc, 6df87ee). Live OpenAI matrix executed through GTM Orchestrator CLI secret injection: 72/72 cases, four exact models, synchronous and Batch tracks, zero source purchases, USD 0.143810700 conservative cap-ledger spend. Programmed scores: description 0.7743055556, ICP/persona 0.75, growth 0.71875; all correctly remain Experiment below the 0.90 Candidate gate. Clean resume rehydrated 72/72 with byte-identical outcomes and unchanged spend. Independent review PASS. See docs/reports/company-corpus-live-run.md.
  Completed and committed as 6df87ee. Full live report: docs/reports/company-corpus-live-run.md. Commit hook: 546 passed.
