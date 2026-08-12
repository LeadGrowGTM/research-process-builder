# Company Enrichment Input/Output Decision Trees

**Status:** Living design record; update as each enrichment and provider is reviewed.
**Last updated:** 2026-08-12

## Purpose

Define each enrichment end-to-end without exposing provider payloads through the public interface:

`goal → caller-required inputs → system-required inputs → optional inputs → evidence routes → stopping rules → exact outputs → unknown/failure states`

Provider responses are evidence behind adapters. AI-Ark, DiscoLike, lg_free, Harvest, Parallel, or a scraper may fill fields, but none defines the public schema by itself.

## Input classifications

- **Caller-required:** the caller must supply it.
- **System-required:** the runner supplies it deterministically.
- **Conditionally required:** required only when an enrichment goal depends on it.
- **Optional:** improves routing or specificity but cannot be assumed.
- **Derived:** produced from cited evidence, never accepted as unverified truth.
- **Provider-only:** retained for evidence, cost, and diagnosis; never required from the caller.

## Shared company target — provisional

| Field | Classification | Current decision |
|---|---|---|
| company_name | Caller-required | Identity matching and wrong-company rejection |
| domain | Caller-required for v1 | Homepage-first evidence; reconsider LinkedIn substitution after samples |
| linkedin_company_url | Optional | May improve provider identity resolution |
| as_of | System-required | Fixed per run and benchmark |
| language | Optional | Defaults to English |
| geography | Optional | Needed only when scope changes the answer |
| seller_context | Conditionally required | Opportunity mining and analogy/value translation |
| existing_dossier | Optional generally | Likely required for analogy/value; decide in Gate 2B |
| budget_scope | System-required | Corpus or experiment ledger |
| dataset_version | System-required | Fixed benchmark identity |

## Shared result envelope

Every enrichment returns:

- complete, partial, or failed status and normalized failure;
- field-level values with evidence, confidence, freshness, and visibility;
- explicit unknowns and conflicts;
- provider route and provider-only metadata;
- queries, unique URLs, pages, provider calls, retries, latency, and cost;
- prompt/model identity when an LLM is used.

## 1. Company description

```text
Goal: explain what the B2B company does in plain English
├── Required: company_name, domain
├── Optional: linkedin_company_url, language
├── Evidence: homepage scrape → lg_free → targeted search fallback
├── Stop: identity + offer + customer + category cited, or saturation/cap
├── Outputs: description, category, products/services, customer type, unknowns
└── Failures: wrong identity, inaccessible site, insufficient evidence, auth, budget
```

## 2. ICP and target personas

```text
Goal: identify who the company sells to and why
├── Required: company_name, domain
├── Optional: market category, geography, existing dossier
├── Evidence: homepage/customer/case studies → lg_free facts → search
├── Stop: segment + persona + use case + buying evidence are cited
├── Outputs: company segments, personas, pains, use cases, triggers, exclusions
└── Unknown: never infer personas from generic category alone
```

## 3. News and product launches

```text
Goal: identify material recent company events
├── Caller-required: company_name, domain
├── System-required: as_of
├── Optional: lookback window
├── Evidence: newsroom/blog/product pages → targeted search
├── Stop: first-party + independent sources + two dry search angles
├── Outputs: event type, headline, date, summary, materiality, citations
└── Unknown: no evidence is not a negative event
```

## 4. Growth signals

```text
Goal: consolidate evidence of company growth or contraction
├── Caller-required: company_name, domain
├── System-required: as_of
├── Optional: geography, lookback window
├── Evidence: company site → lg_free → targeted search
├── Outputs: hiring, funding, customer, product, geography, usage signals
├── Every signal: observation date, evidence, confidence, fact vs implication
└── Safety: social activity remains filter-only
```

## 5. Ads and offer intelligence

```text
Goal: determine channel activity and the promoted offer
├── Caller-required: company_name, domain
├── System-required: as_of
├── Optional: geography, expected channels, B2B segment
├── Evidence: lg_free Google → LinkedIn Ads → Meta → landing pages
├── Outputs: channel status, dates, geography, angle, offer, CTA, landing page
└── Unknown: stale library presence cannot become “active”
```

## 6. Job opportunity mining

```text
Goal: turn current hiring evidence into seller-relevant opportunities
├── Caller-required: company_name, domain
├── Conditionally required: seller_context
├── Optional: geography, role families
├── Evidence: Harvest → free jobs → careers pages → Parallel fallback
├── Outputs: job facts, labeled inferred need, seller linkage, evidence/status
└── Safety: no invented offer, proof, promise, timeline, or guarantee
```

## 7. Competitor intelligence

```text
Goal: identify credible competitors and material competitor changes
├── Caller-required: company_name, domain
├── Optional: market category, geography, existing dossier
├── Evidence: company/alternative pages → targeted search
├── Outputs: competitor, basis for competition, observed change/date, evidence
└── Unknown: similarity alone does not establish competition
```

## 8. Analogy/value translator

```text
Goal: translate cited prospect evidence into a seller-relevant value frame
├── Required: company_name, domain, seller_context
├── Pending: make cited company_dossier required
├── Evidence: cited dossier only; no fresh web call by translator
├── Outputs: analogy, evidence used, seller linkage, caveats, safe ingredients
└── Safety: no invented proof, outcome, guarantee, or surveillance-like fact
```

## Provider evidence register

### DiscoLike — observed local evidence

- Input: domain.
- Normalized fields: name, description, industry, location, LinkedIn URL, employees, footprint score.
- Raw responses may also contain revenue range and business model; verify before promotion.
- Miss: HTTP 200 with an empty/non-JSON body.
- Cost: approximately $0.18/lookup; monthly `/usage` is authoritative.
- Comparative sample from 2026-06-10:
  - funded companies: 7/10 hit rate, about 95% field coverage when found;
  - new Product Hunt launches: 0/10;
  - union with free Blitz: 8/10 funded-company coverage.
- Decision: paid delayed fallback/comparator for suitable established or funded companies, not a universal first-line provider.
- Gaps: weak new-launch coverage; employee/follower gaps; identity requires validation.

### AI-Ark — sample required

No current AI-Ark implementation or response sample was found in this workspace. Do not infer its schema.

Requested company-level samples:

1. Typical established B2B company success.
2. Recently funded/new B2B company.
3. Sparse or ambiguous company.
4. Miss/not-found response.
5. Error or partial response if available.

For each include the exact input, raw company response, result status, retrieval date, latency, and cost/credit metadata. Remove credentials and person/contact records.

## Decision protocol

1. Record raw fields and nullability.
2. Measure field presence across samples.
3. Classify fields as identity, fact, derived signal, metadata, or unsafe.
4. Map useful facts into provider-neutral assertions.
5. Classify provider as production, fallback, comparator-only, or rejected.
6. Change required inputs only when the enrichment goal—not provider convenience—requires them.
7. Save evidence and decisions here before changing manifests or prompts.
