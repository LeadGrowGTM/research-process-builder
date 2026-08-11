# Enrichment Library Backlog

This is the working list of enrichments Research Process Builder should create,
benchmark, and eventually approve for reuse. The library is company-first. Direct
people/contact discovery is a separate later track, but company research may still
produce a specific target-business profile and target-persona profile.

## Decisions already made

- Split enrichments by entity scope: `company`, `target_persona`, `person`,
  `signal`, and `copy_transform`.
- Put direct people/contact discovery on the back burner. Do not mix it into the
  first company-enrichment release.
- Use Parallel for web search only. Do not depend on Parallel `web_fetch`.
- For known URLs, use GTM Orchestrator's stable `web-scraping` skill and its
  canonical `firecrawl_waterfall.py` executor. The order is plain HTTP plus
  `html2text` (free), Crawl4AI headless (free), Firecrawl standard, then
  Firecrawl JS rendering. Do not create or restore a separate scraper.
- Before designing or implementing any provider path, check both the installed
  GTM Orchestrator pipeline and Nexus for an existing skill, CLI, provider,
  historical implementation, or validated operating pattern.
- Separate deterministic/data-manipulation, LLM-only, single-search,
  search-and-scrape, and parallel-search enrichments.
- Route technology detection through the existing TechSight CLI before adding
  search or LLM work. Canonical source:
  `C:/Users/mitch/Everything_CC/tools/clis/techsight-cli`.
- Target approximately **$1 maximum paid API spend per enrichment experiment**.
  A run may stop earlier once it has enough evidence.
- Every experiment appends comparable benchmark data. Never overwrite a prior
  model/provider result.
- Accuracy is necessary but not sufficient. AI-generated outputs require a blind
  human review for readability, specificity, usefulness, casualness, and
  non-creepiness before approval.
- Cheap models are the production candidates. GPT-5.6 Luna is the premium
  comparator used to answer whether paying more materially improves an enrichment.

## Proposed YAML contract

Each enrichment should begin with machine-readable YAML front matter following
this shape:

```yaml
id: ideal-customer-profile
name: Who They Want to Work With
status: proposed # proposed | experiment | candidate | approved | rejected
entity_scope: [company, target_persona]
family: market_intelligence
priority: P0
execution:
  mode: search_and_scrape # deterministic | llm_only | web_search | search_and_scrape | parallel_search
  requires_web_search: true
  requires_scrape: true
  supports_batch: false
prerequisites:
  required: [company_name, company_domain]
  optional: [product_description, known_customers, seller_offer, time_window]
providers:
  search: [serper, parallel]
  scrape: [local_free_waterfall, firecrawl]
outputs:
  fields: [target_business, target_persona, evidence, confidence, email_lines]
benchmark:
  max_paid_usd_per_experiment: 1.00
  factual_accuracy_required: 0.90
  human_review_required: true
usage:
  message_safe: true
  filter_only: false
```

The final schema should also record version, owner, input/output schema versions,
query and retry caps, early-stop rules, freshness window, source requirements,
failure modes, and the exact benchmark dataset version.

## P0 — build and benchmark first

### 1. Plain-English company description

- **Question:** What does this company actually do, in normal language?
- **Entity:** company
- **Mode:** LLM-only when adequate source data is supplied; otherwise
  search-and-scrape.
- **Prerequisites:** company name plus a domain, description, or source text.
- **Output:** concise description, customer problem, product category, evidence,
  confidence, and a casual email-safe version.
- **Human gate:** reject jargon, padded prose, vague category language, and copy
  that sounds machine-written.

### 2. Who they want to work with

- **Question:** Which businesses and which people are their most specific ideal
  customers?
- **Entity:** company + target_persona; this is not contact discovery.
- **Mode:** search-and-scrape with LLM synthesis.
- **Prerequisites:** company domain; product/offer context when available.
- **Output:** target-company attributes, industries, size/maturity, trigger
  conditions, target departments, roles, seniority, pains, buying situations,
  exclusions, evidence, and confidence.
- **Quality bar:** specificity high enough to distinguish a real ICP from generic
  "B2B companies" or "decision makers" language.

### 3. Recent news and product launches

- **Question:** What materially changed recently?
- **Entity:** signal
- **Mode:** web search, then local scrape with Firecrawl fallback.
- **Prerequisites:** company identity and a freshness window.
- **Output:** dated event, event type, why it matters, source, confidence, and
  conversational email lines.
- **Includes:** launches, meaningful features, partnerships, expansions, and
  major positioning changes. Avoid low-value press-release restatements.

### 4. Consolidated growth signals

- **Question:** What evidence suggests this company is growing or investing?
- **Entity:** company + signal
- **Mode:** multi-source enrichment that composes other approved enrichments.
- **Prerequisites:** company identity, freshness window, and selected signal
  families.
- **Output:** individual signals plus a transparent evidence summary; never hide
  unknown dimensions inside a composite zero.
- **Includes:** funding/traction, hiring, ad activity, product launches,
  technology changes, expansion, and other evidence-backed momentum.

### 5. Running ads and offer intelligence

- **Question:** Are they actively advertising, where, and what are they selling?
- **Entity:** company + signal
- **Mode:** provider/API or search-and-scrape, depending on channel.
- **Prerequisites:** company name/domain, requested ad channels, geography, and
  freshness window.
- **Output:** active/inactive/unknown by channel, observed dates, creative angle,
  offer, landing page, CTA, evidence, and confidence.
- **Quality bar:** distinguish live ads from stale library entries, organic posts,
  agency portfolios, and mentions of the company by others.
- **Priority note:** this is a flagship enrichment; invest in coverage and
  source-specific validation rather than treating it as a single generic search.

### 6. Job-post opportunity mining

- **Question:** What does an open role reveal about a problem our offer can help
  solve?
- **Entity:** company + signal + target_persona
- **Mode:** search-and-scrape followed by LLM extraction and offer alignment.
- **Prerequisites:** company identity, seller offer/capabilities, relevant role
  families, and freshness window.
- **Output:** exact role and URL, requirements, initiatives, tools, pains,
  implied priorities, specific offer alignment, confidence, and email-safe lines.
- **Quality bar:** do not stop at "they are hiring SDRs." Extract evidence from
  the job description and explain the specific operational opportunity.
- **Durability note:** lists exhaust, but new postings make this enrichment
  renewable each quarter.

### 7. Competitor and competitor-change intelligence

- **Question:** Who competes with them, and what has changed across those
  competitors?
- **Entity:** company + signal
- **Mode:** question-driven branching; single or parallel search depending on the
  requested intelligence.
- **Prerequisites:** subject company, market/category, known competitors when
  available, time window, and the user's intelligence objective.
- **Required intake:** ask which branch matters before expensive work: competitor
  discovery, ads, new offers, product/features, positioning, customer pain, or
  pricing changes.
- **Output:** defensible competitor set, relationship/type, branch-specific
  changes, dates, evidence, confidence, and usable implications.
- **Quality bar:** avoid generic "alternatives" lists and vendors that merely
  share keywords.

### 8. Analogy/value translator

- **Question:** How can we explain our value through a sharp, memorable analogy?
- **Entity:** copy_transform
- **Mode:** LLM-only and batch-compatible.
- **Prerequisites:** seller value proposition plus the prospect's product,
  customer, and way of creating value.
- **Output:** several variants of "we help X the way you help Y," with the factual
  premise and risk flags.
- **Human gate:** witty but immediately understandable; never forced, flattering,
  or factually invented.

## P1 — next company enrichments

### 9. Technology installs and technology changes

- Use TechSight as the deterministic/free primary detector.
- Compare current evidence with historical captures, including Wayback where
  appropriate, to identify recent installs or removals.
- Search and LLM work are fallbacks or interpretation layers, not the primary
  detector.
- **Prerequisite:** repair the local `techsight` installation; the current source
  exists, but the installed launcher raises `ModuleNotFoundError: techsight`.

### 10. Pricing and packaging changes

- Detect new tiers, changed prices, new limits, free-trial changes, packaging,
  and enterprise-plan changes.
- Prefer current-versus-historical page comparison over unsupported inference.
- Produce both the factual change and the commercial implication.

### 11. Third-party review, pain, and complaint mining

- Prioritize authentic third-party evidence such as Product Hunt and relevant
  review/community platforms over polished first-party case studies.
- Extract recurring pains, complaints, unmet needs, praised outcomes, and the
  affected user/customer segment.
- Support both subject-company reviews and competitor-review mining.
- Preserve citations and separate isolated anecdotes from repeated patterns.

### 12. Funding and traction

- Treat funding, revenue/ARR claims, customer growth, usage, geographic expansion,
  and other traction evidence as components of the consolidated growth signal.
- Keep source/date/confidence per fact and do not turn absence of evidence into a
  negative signal.

## P2 — valuable but lower priority or conditional

### 13. M&A, PE ownership, cost cutting, and layoffs

- M&A and PE activity is a lower-priority signal, but can indicate upcoming
  efficiency mandates.
- Detect active layoffs or severe cost cutting as a suppression/ignore signal by
  default, not as messaging material.
- Do not assume every acquisition means immediate cost cutting; record the event
  and evidence separately from the inferred implication.

### 14. Social activity as a filter-only signal

- Use the Harvest API path when in scope.
- Identify people engaging with relevant topics, posting about them, or following
  competitors/industry educators.
- Use only for list filtering and prioritization. Never mention surveillance-like
  social behavior in outbound copy.

### 15. Creative LinkedIn profile sourcing

- Keep this in the separate people-enrichment library.
- Explore higher-quality and more creative ways to source the right profiles,
  with strict identity matching and source evidence.
- Do not let this block the company-enrichment library.

## Benchmark and approval contract

Every run should append one record containing at least:

- enrichment id/version and benchmark dataset version;
- input fixture id and prerequisites supplied;
- execution mode and routing path;
- search, scrape, and model providers with exact requested/resolved model ids;
- query, scrape, retry, token, latency, and paid-cost totals;
- structured output, citations, confidence, and failure reason;
- factual precision/recall or field-level correctness where ground truth exists;
- citation validity, completeness, and freshness;
- generated email lines and whether each is message-safe or filter-only;
- blind human scores for readability, specificity, usefulness, casualness, and
  non-creepiness;
- human verdict: `approve`, `revise`, or `reject`.

An automated run may move an enrichment from `experiment` to `candidate` after
passing its mechanical and accuracy gates. Only explicit human review may move it
to `approved`.

## Model benchmark ladder

Use representative fixed fixtures and identical prompts/settings when comparing
models. Record synchronous and Batch API results separately.

- **Low-cost production candidates:** GPT-5 nano and GPT-4o mini.
- **Instruction-following comparison:** GPT-4.1 mini where its stronger instruction
  following may justify the additional cost.
- **Premium comparator:** GPT-5.6 Luna. It answers whether higher model spend
  materially improves accuracy or human happiness; it is not the cheap default.
- Do not assume the newest model wins. Promote per enrichment based on measured
  cost, accuracy, and human approval.

## Resolved implementation decisions

- P0 ordering is fixed as listed above; technology changes, pricing changes,
  third-party review mining, and standalone funding/traction remain P1.
- Benchmark corpus: 60 unique companies split into six 10-company primary
  cohorts (Google Maps/local, SaaS, recently funded, well-known, agencies, and
  ecommerce/CPG). Select 15 of the 60 as the shared cross-category core.
- Dossier construction has a $2 total paid-API cap. Each enrichment experiment
  has its own $1 total paid-API cap; neither budget is per company.
- Canonical scraper: GTM Orchestrator `web-scraping` skill v2.1.0, invoked via
  `.claude/skills/web-scraping/scripts/firecrawl_waterfall.py`; use
  `--no-firecrawl` or `--max-level 2` for explicitly free-only runs.
- Ad coverage: Google and Meta for applicable companies, LinkedIn for B2B, and
  TikTok for ecommerce/CPG. Reuse GTM Orchestrator capabilities first,
  including the historical `apify-meta-ads` implementation where appropriate,
  after its actor schema and cost are revalidated.
- Seller-offer inputs: target market/persona, capabilities, named offer,
  timeline, promised outcome, proof, guarantee/de-risking, exclusions, and
  prospect current-investment/worldview fields.
