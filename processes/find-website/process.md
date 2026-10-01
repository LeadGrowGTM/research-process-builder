# find company website

model: gpt-6-luna
service_tier: flex

> **status:** draft, needs validation against ground truth
> **type:** lookup process (company-in → domain out). used as sub-step in monitoring pipelines (Series A/B/C discovery) and enrichment workflows.

find the actual official website domain for a company. this is harder than it sounds — common names, rebrands, acqui-hires, and domain squatters make naive "company name + website" searches unreliable.

## inputs

- `{{company_name}}` — the company to find
- `{{article_text}}` — (optional) scraped article about the company. often contains the domain in About section, boilerplate, or contact info.
- `{{context_clues}}` — any known context: industry, product type, location, investors, founder names. extracted from the source article or pipeline metadata.

## strategy

two layers. layer 1 is free (already have the data). layer 2 costs searches.

---

## layer 1: extract from source article (no search cost)

if `{{article_text}}` is available, scan for the domain. PR articles (prnewswire, businesswire, einpresswire) almost always include the company website in:

- **"About [Company]" section** — usually last 2-3 paragraphs. pattern: `"About {{company_name}}"` followed by a URL or `"visit [domain]"` or `"learn more at [domain]"`
- **"Learn more" / "For more information"** — often a direct URL: `visit mosaic.pe` or `www.example.com`
- **Contact info block** — email domains match company domain (e.g. `press@mosaic.pe` → `mosaic.pe`)
- **Inline mentions** — `"{{company_name}} (www.example.com)"` or `"{{company_name}} (example.com)"`
- **Linked company name** — in HTML source, the company name links to their site

extraction rules:
- ignore PR wire domains (prnewswire.com, businesswire.com, einpresswire.com, globenewswire.com)
- ignore social domains (linkedin.com, twitter.com, facebook.com, crunchbase.com, github.com)
- ignore investor/VC domains
- prefer `.com`, `.io`, `.ai`, `.co` TLDs over country TLDs unless company is clearly regional
- if email address found (press@company.com), extract domain from it

**stop if:** domain found with high confidence (appears in About section or contact info). skip to output.

---

## layer 2: multi-signal search (fallback)

run when layer 1 fails or article_text is unavailable. 4 searches, score candidates, pick the best.

### step 1: gather context from source

before searching, extract from `{{article_text}}` or `{{context_clues}}`:
- **industry/category** — what does this company do? (e.g. "AI deal-making platform", "healthcare SaaS")
- **company type** — B2B SaaS, marketplace, biotech, fintech, etc.
- **location** — HQ city/country if mentioned
- **founder/CEO name** — sometimes more unique than company name
- **product name** — if different from company name

### step 2: construct disambiguation-aware searches

search 1: `{{company_name}} {{industry}} official website`
- most direct. works for unique names.

search 2: `{{company_name}} {{company_type}} site`
- different angle. if company does AI, include "AI". if AI agents, "AI agents".

search 3: `{{company_name}} {{product_or_service}} homepage`
- targets product pages that often rank higher than corporate sites for newer companies.

search 4 (conditional — only if location known): `{{company_name}} {{location}} website`
- disambiguates local businesses and companies with common names.

search 5 (conditional — only if founder known): `{{founder_name}} {{company_name}}`
- founder LinkedIn/bio pages often link to company site. useful for very new startups.

### step 3: score candidates

for each unique domain found across all searches, score:

| signal | points |
|--------|--------|
| domain contains company name (or close variant) | +3 |
| appears in multiple search results | +2 per additional appearance |
| result title mentions the company by name | +2 |
| result snippet describes what the company does (matches context) | +2 |
| TLD is .com, .io, .ai, .co | +1 |
| domain is a known social/directory site | -10 (disqualify) |
| domain is a news site reporting on the company | -5 (not the company itself) |
| domain content (if visited) has company name in title tag | +3 |

### step 4: validate top candidate

before returning the highest-scored domain:
- does the domain contain or closely match the company name? (mosaic.pe for Mosaic — yes)
- if the domain does NOT contain the company name, flag as low confidence
- if two candidates score within 2 points, flag as ambiguous — return both with confidence scores

---

## output

```
{{company_domain}}: [domain]
{{confidence}}: [high|medium|low]
{{source}}: [article_extract|search_validated|search_only]
{{evidence}}: [where the domain was found — e.g. "About section of PR article" or "3/4 searches returned this domain"]
```

## known failure modes

- **common company names** — "Mosaic" could be mosaic.pe (deal-making AI), mosaicml.com (ML infra, acquired by Databricks), or mosaic.co (flooring). the industry context from the article is critical for disambiguation.
- **rebranded companies** — old domain still ranks higher than new one. check if the top result redirects.
- **acquired companies** — domain may redirect to acquirer. check for redirect.
- **pre-launch startups** — may not have a public website yet. "not_found" is valid.

## do not search

- `{{company_name}} website` alone — too generic, high false positive rate
- `site:crunchbase.com {{company_name}}` — gives crunchbase page, not company site
- `{{company_name}} .com` — the dot confuses search engines
