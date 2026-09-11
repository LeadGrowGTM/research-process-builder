---
name: research-agent
description: Standardized web research agent using FireCrawl (deep scrape) + SerperDev (search) + gpt-4o-mini (extraction). Validated against ground truth with annealable scoring loop. Trigger: any research task that needs web-sourced structured data extraction.
---

# Research Agent — FireCrawl + SerperDev + 4o-mini

Standardized, low-cost web research agent. Combines three tools into one deterministic pipeline:

| Tool | Role | Cost |
|------|------|------|
| **SerperDev** | Google search — find relevant URLs + snippets | ~$0.001/search |
| **FireCrawl** | Deep page scrape — extract clean markdown from top URLs | ~$0.001/scrape |
| **gpt-4o-mini** | Structured extraction — classify, extract fields, validate | ~$0.00015/1K tokens |

## When to Use

Any task that needs web-sourced structured data:
- Company profile extraction (description, size, HQ, funding)
- People discovery (founders, executives, decision makers)
- Competitive intelligence (competitors, market position)
- Signal detection (hiring, funding, product launches)
- Any research process in `processes/find-*/`

## Architecture

```
Input (domain/company)
  │
  ├─► SerperDev search → URLs + snippets
  │
  ├─► FireCrawl scrape → clean markdown (top 3 URLs)
  │
  ├─► gpt-4o-mini extraction → structured JSON
  │
  └─► GT validator → accuracy score vs ground-truth/*.json
```

## Usage

```bash
# Research a single company
py scripts/agent_research.py --domain stripe.com --category company_profile

# Research with ground truth validation
py scripts/agent_research.py --domain stripe.com --category company_profile --gt

# Batch run against all GT companies
py scripts/agent_research.py --all-gt --category founders_ceo

# Anneal: score against GT and identify failure patterns
py scripts/agent_research.py --all-gt --anneal --rounds 5
```

## Annealing Loop

1. Run against all GT companies → get scores per category
2. Identify categories/companies scoring below 0.90
3. Examine failure patterns (missing fields, wrong extraction, bad URLs)
4. Adjust Serper queries or extraction prompt
5. Re-run → compare scores → iterate until 90%+ across all GT

Cost per annealing round: ~$0.03 (10 companies × 3 searches + 3 scrapes + 1 extraction each)

## Output Format

Results written to `output/agent-research/{company}/{category}.json`:

```json
{
  "company": "Stripe",
  "domain": "stripe.com",
  "category": "company_profile",
  "model": "gpt-4o-mini",
  "timestamp": "2026-05-13T19:00:00Z",
  "sources": ["https://stripe.com/about", "https://en.wikipedia.org/wiki/Stripe"],
  "extracted": {
    "description": "Financial infrastructure platform...",
    "employee_count": "8,500-10,000",
    "headquarters": "South San Francisco, CA",
    "founded_year": "2010"
  },
  "gt_score": 0.95
}
```

## Env Vars Required

All read from `C:\Users\mitch\Everything_CC\.env`:
- `FIRECRAWL_API_KEY`
- `SERPER_API_KEY`
- `OPENAI_API_KEY`
