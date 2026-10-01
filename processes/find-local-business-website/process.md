# Find Local Business Website

model: gpt-6-luna
service_tier: flex

**Accuracy:** 93% validated across 15 companies (3 tiers)
**Built:** 2026-04-26
**Methodology:** research-process-builder, 3 iteration runs, 45 total tests
**Model:** gpt-4o-mini (OpenAI) with web_search (Serper) + scrape_url (Spider)
**Cost:** ~$0.0014/lead average

## Inputs

- `{{company_name}}` — business name from UCC filing or lead list (may include LLC/Inc/Corp)
- `{{city}}` — city
- `{{state}}` — state abbreviation
- `{{zip}}` — ZIP code (optional, used for disambiguation)

## Preprocessing

Strip legal suffixes (LLC, Inc, Corp, Ltd, LTD) from `{{company_name}}` for search queries.
Keep the original name for exact-match searches.

## Steps

### Step 1: BBB Search (PRIMARY — Q5/C4)

**Search:** `"{{company_name}}" {{city}} site:bbb.org`

**If no result**, try ONE name variant — strip suffixes AND try common trade name substitutions:
- "Contractors" → "Builders"
- "Group" → company name keywords only
- Core name words + `{{city}}` site:bbb.org

**If BBB page found:**
- Scrape it with scrape_url
- Extract: website URL, owner/principal name, business address, phone
- Verify BBB address matches `{{city}}, {{state}}`
- **Stop if:** website URL extracted and address matches → return immediately

**Why first:** BBB gives website + owner + address in one scrape. Address match = built-in identity verification. 60%+ hit rate on local SMBs with BBB presence.

### Step 2: Direct Search (PRIMARY — Q4/C4)

**When:** BBB returned no website

**Search:** `"{{company_name}}" {{city}} {{state}}`

**Extract:** Business's own domain from results. Scoring:
- Domain contains company name words → +3
- Result mentions `{{city}}` or `{{state}}` → +2
- Multiple results point to same domain → +2
- Domain is a directory site (Yelp, YP, Manta, Facebook) → -10 (not the target)

**Stop if:** Found a domain with score 5+ → return immediately

### Step 3: Give Up (FALLBACK)

**When:** 2 searches returned nothing useful

**Return:** `website: null, confidence: low, source: none`

"No website found" is a valid signal — many local businesses have no web presence. Do not waste searches on increasingly desperate queries.

## Kill List

- `{{company_name}} website` alone — too generic, high false positive
- `site:crunchbase.com` — returns Crunchbase page, not company site
- `{{company_name}} .com` — the dot confuses search engines
- `site:reddit.com` — zero results universally
- Extended directory cascade (Yelp scrape → YP scrape → Manta scrape) — burns turns without improving accuracy
- More than 3 fallback searches — diminishing returns, increases cost without improving accuracy

## Confidence Scoring

| Level | Criteria | Action |
|---|---|---|
| **high** | BBB address match + website extracted, OR domain contains company name + city/state confirmed | Keep — ~95% correct |
| **medium** | Website found via search, location partially confirmed | Keep but flag for review — ~80% correct |
| **low** | No website found, OR candidate from different city/state, OR ambiguous name with multiple entities | Discard or manual review — ~40% correct |

**Production filter:** Keep high + medium confidence. Discard low. Expected yield: ~85% of leads get a result, ~93% of results are correct.

## Output Template

```json
{
  "website": "https://example.com or null",
  "bbb_url": "https://bbb.org/... or null",
  "owner_name": "name or null",
  "owner_title": "title or null",
  "phone": "phone or null",
  "address_match": "exact|partial|none",
  "confidence": "high|medium|low",
  "source": "bbb|directory|search|none"
}
```

## Known Failure Modes

| Mode | Example | Mitigation |
|---|---|---|
| Ambiguous common name | "Opal Group" (multiple entities nationwide) | Address-based disambiguation — if candidate website is in wrong city, return null with low confidence |
| Generic SEO domain | `roofingcontractormanitouspringsco.com` for Highpoint Builders | Name-variant BBB search finds the real domain |
| Rebrand / parent brand | Just Mystic → justbrandapparel.com (parent) vs justmystic.com (primary) | Both valid — any domain confirmed at the address works |
| No web presence | Small LLCs, trusts, holding companies | Return null with low confidence — valid signal, not a failure |
| Spider timeout | BBB/Yelp pages occasionally timeout (>120s) | Retry logic in production pipeline |
| UCC typos | "CAPMBELL" instead of "CAMPBELL" | Agent handles gracefully — searches both variants |

## Performance Characteristics

| Metric | Value |
|---|---|
| Avg turns per lead | 4.3 |
| Avg cost per lead | $0.0014 |
| Avg time per lead | 45s (dominated by Spider scrape latency) |
| BBB hit rate | ~60% (when BBB listing exists) |
| Projected cost at 75K leads | ~$105 |
| Projected time at 75K leads | ~9 hours (at concurrency 1) |

## Batch Pipeline Integration

For production use against the UCC lead list:

```python
from test_baseline import build_prompt, run_agent_with_retry

# Per-lead call
result = run_agent_with_retry(prompt=build_prompt(lead), verbose=False)

# Filter by confidence
if result.get("parsed", {}).get("confidence") in ("high", "medium"):
    # Keep result
else:
    # Discard or queue for manual review
```

Scale with asyncio concurrency (5-10 parallel) to reduce wall time from 9h to ~1-2h.
Budget: ~$105 for 75K leads at $0.0014/lead.
