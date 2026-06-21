---
paths:
  - "scripts/**/*.py"
  - "prompts/**/*.py"
  - "monitors/**/scripts/**/*.py"
---

# Pipeline Safety Rules

## Env Loading

All scripts load env vars via `load_dotenv()` internally. Never reference `.env` in shell commands. The workspace hook blocks it.

Pattern (from `pipeline_base.py`):
```python
from dotenv import load_dotenv
load_dotenv(SCRIPT_DIR.parent / ".env")
load_dotenv(SCRIPT_DIR.parent.parent / ".env", override=False)
```

## Domain Classification

Fix domain slip-throughs by improving the classifier or seeding its cache -- never by appending to `BLOCKED_DOMAINS`. The classifier cache (`data/domain_classifications.json`) is committed to git for shared learning.

Only `real_company` verdict accepts. All other verdicts (`unknown`, API error, no key) reject.

## GPT Prompt Templates

Extraction prompts use single-brace `.replace("{items}", payload)` for variable injection. Do not convert to f-strings or `.format()` -- JSON examples inside the template contain literal braces that would break.

## Batch Indexing

`extract_companies_batch` returns 1-based local indices. Code maps back via `batch[local_idx-1]["idx"]`. Preserve this contract when modifying extraction or re-annealing prompts.

## Supabase Writes

Workspace 3 is production. Verify the target table before any write operation. Dry-run (`--dry-run`) first when testing pipeline changes that touch Supabase.

## Ground Truth

Ground truth files live in `ground-truth/[company].json` and follow `ground-truth/schema.json`. When a prompt re-anneal is needed (score drops below 0.95), add new failure cases to `test_cases.json` before re-running the anneal.
