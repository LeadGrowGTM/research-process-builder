# Issue Tracker

## GitHub Remotes

This repository has two GitHub remotes:

| Remote | URL | Issues Tab |
|--------|-----|-----------|
| origin (org) | https://github.com/LeadGrowGTM/research-process-builder | https://github.com/LeadGrowGTM/research-process-builder/issues |
| public (personal) | https://github.com/MitchellkellerLG/research-process-builder | https://github.com/MitchellkellerLG/research-process-builder/issues |

Primary issue tracking: **https://github.com/LeadGrowGTM/research-process-builder/issues**

## Issue Types for This Repo

Given the pipeline-heavy nature of this repo, issues typically fall into:

| Category | Examples |
|----------|---------|
| **pipeline-accuracy** | GT hit rate drops, domain resolution regression, confidence scorer false positives |
| **process-file** | New process needed, existing process stale, kill list needs update |
| **data-quality** | Domain slip-throughs, bad company names extracted, duplicate records |
| **infra** | Serper quota, Supabase write errors, Firecrawl timeouts |
| **prompt-anneal** | Extraction prompt score < 0.95, new failure cases found |

## Creating Issues

When a pipeline run fails or a metric degrades:
1. Run `py scripts/eval_pipeline.py` and capture exit code + output
2. Open an issue on the org remote with the eval output pasted in
3. Tag with relevant category above
4. Link to the relevant HANDOFF-*.md if this is a known in-progress item
