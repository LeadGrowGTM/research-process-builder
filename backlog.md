# Backlog

## In flight
- [ ] running-ads-free-scraper - Running ads enrichment via free Google Ads Transparency + Meta Ad Library scrapers; lineage ads-v3 1.00/1.00, halted for human review (since 2026-08-18)
- [ ] news-product-launches-loop - News/launches enrichment collect + prompt loop; news-v5 dev 0.709 / holdout 0.708, anneal pending (since 2026-08-18)
- [ ] competitor-intelligence-loop - Competitor enrichment collect + prompt loop; comp-v5 dev 0.678 / holdout 0.668, anneal pending (since 2026-08-18)
  All three: docs/reports/signal-enrichments-live-run.md, branch wt/signal-enrichments.
- [x] repo-cleanup-full-update - Checker PASS (4.71/5); sanitized local export fallback recorded; authoritative worktree C:\Users\mitch\Everything_CC\tools\data\research-process-builder\.worktrees\repo-cleanup-full-update (since 2026-08-10)
## Queued
## Done
- [x] parallel-search-task-api - Parallel Search API transport + budget-guarded Task API client (base tier max, $1 ceiling); Serper-vs-Parallel A/B on news + competitor signals: Serper wins decisively (news events 0.976/1.000 vs 0.306/0.516; comp named_set 0.878/0.943 vs 0.272/0.546) at 1/5 the query price. Serper stays primary, Parallel is a live fallback. See docs/reports/serper-vs-parallel.md (done 2026-08-19)
- [x] anneal-news-competitor-signals - Anneal news and competitor signal enrichments to >=0.90 (done 2026-08-18)
- [x] research-company-enrichment-lib - Build and benchmark company enrichment library (done 2026-08-13)
  Completed corpus and benchmark library (b8bb6c2, 7578483, f15a9fc, 6df87ee). Live OpenAI matrix executed through GTM Orchestrator CLI secret injection: 72/72 cases, four exact models, synchronous and Batch tracks, zero source purchases, USD 0.143810700 conservative cap-ledger spend. Programmed scores: description 0.7743055556, ICP/persona 0.75, growth 0.71875; all correctly remain Experiment below the 0.90 Candidate gate. Clean resume rehydrated 72/72 with byte-identical outcomes and unchanged spend. Independent review PASS. See docs/reports/company-corpus-live-run.md.
  Completed and committed as 6df87ee. Full live report: docs/reports/company-corpus-live-run.md. Commit hook: 546 passed.
