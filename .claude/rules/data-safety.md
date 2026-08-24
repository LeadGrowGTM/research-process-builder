---
description: Supabase workspace 3 = production. Verify before write. Ground-truth files immutable.
globs: ["scripts/**"]
---

# Data Safety

- Supabase workspace 3 = production. Always verify target table before any write.
- Ground truth files (`ground-truth/*.json`) are immutable reference data.
- `benchmarks/**/ground-truth/*.yaml` is sealed reference data, especially the
  Serper corpus in `benchmarks/signals/`; never overwrite it. New provider
  collections (e.g. Parallel) go in their own dir
  (`benchmarks/signals-parallel/`, `benchmarks/signals-parallel-v2/`) — see
  `docs/reports/serper-vs-parallel.md`.
- Baselines in `baselines/` — compare against, don't modify.
- Domain classifier: `real_company` accepts, anything else rejects. Fix the classifier, don't append to `BLOCKED_DOMAINS`.
