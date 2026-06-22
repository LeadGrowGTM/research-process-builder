# Domain Context

## Context Model: Single-Context with Monitor Worktrees

This repository operates as a **single-context** repo on `master`. However it has an isolated multi-context layer via **git worktree monitors**:

- `monitors/series-a-daily/` — mounted as an orphan branch (`monitor/series-a-daily`), independent history, independent versioning.
- Additional monitors follow the same pattern (see `MONITORS.md`).

Each monitor has its own `config/monitor.json`, `runs/run-log.json`, and `output/YYYY-MM-DD/` directories. Monitors are gitignored on master.

## Domain Doc Layout

```
research-process-builder/
  CLAUDE.md                          — operational playbook (install, run commands, key conventions)
  SKILL.md                           — methodology for building new research processes (6-phase loop)
  README.md                          — product overview (what exists, key discoveries)
  MONITORS.md                        — how to create and mount monitors
  processes/                         — validated process files (find-*/process.md + STATUS.md)
  scripts/                           — all Python pipeline scripts
    pipeline_base.py                 — ResearchPipeline base class (4-stage architecture)
    series_a_pipeline.py             — Series A pipeline (subclass of ResearchPipeline)
    domain_resolver.py               — unified domain resolution module
    domain_classifier.py             — LLM-backed domain category classifier with cache
    confidence_scorer.py             — 3-signal composite confidence gate
    eval_pipeline.py                 — regression harness (exit 0 = pass at >= 90% accuracy)
    gt_validation.py                 — continuous ground-truth promotion from Supabase
    autoresearch_agent.py            — OpenAI tool-calling loop for pattern optimization
    anneal.py / eval_iter.py         — prompt annealing infrastructure
  prompts/                           — graduated prompts (extract-companies-batch, etc.)
  ground-truth/                      — per-company JSON ground truth files (schema.json)
  baselines/                         — historical iteration snapshots (iter1-iter10)
  data/                              — committed classifier cache, gt_validation_state
  output/                            — pipeline run outputs (gitignored day-to-day)
  monitors/                          — worktree mount point (gitignored on master)
  .claude/rules/pipeline-safety.md  — operational safety rules for all agents
```

## Who Reads What

| Consumer | First Files to Load |
|----------|-------------------|
| Agent building a new process | SKILL.md, then processes/[closest-existing]/process.md |
| Agent running the pipeline | CLAUDE.md, then scripts/series_a_pipeline.py |
| Agent fixing a domain bug | scripts/domain_resolver.py, scripts/domain_classifier.py, data/domain_classifications.json |
| Agent improving confidence scoring | scripts/confidence_scorer.py, scripts/test_confidence_scorer.py |
| Agent promoting GT | scripts/gt_validation.py, scripts/eval_pipeline.py |
