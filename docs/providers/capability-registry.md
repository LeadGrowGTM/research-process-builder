# Company enrichment capability registry

**Recorded:** 2026-08-12  
**Boundary:** Local capability metadata only; this record does not prove a live provider call.

Every enrichment run performs GTM discovery, records a Nexus result or explicit
authentication failure, and only then selects a registered capability. AI-Ark is
seed provenance and is intentionally absent from runtime routing.

| Capability | Role | Provenance | Cost / validation state |
| --- | --- | --- | --- |
| `homepage-scrape` | Known-URL primary | GTM Orchestrator `web-scraping` 2.1.0 / `firecrawl_waterfall.py` | Levels 1–2 free; levels 3–4 require a prior reservation; Firecrawl approved |
| `lg-free` | Structured gap filler | Local `lg_free` bridge | Free; observed |
| `harvest-jobs` | Jobs primary | Harvest | Preferred; live availability still requires preflight |
| `free-job-enrichment` | Jobs fallback | Local registry | Unverified until preflight |
| `company-careers-scrape` | First-party jobs fallback | GTM known-URL scraper | Uses the same waterfall policy |
| `parallel-search` | Late search fallback/comparator | Parallel Search MCP | Search only; never fetches known URLs |
| `linkedin-ads` | B2B ads | Local LinkedIn bridge | Observed |
| `meta-ads` | Applicable ads | Historical actor `ZQyDz7154hrOfrDMK` | Requires a 1–3 URL schema/cost validation before batch use |
| `tiktok-ads` | Commerce ads | Runtime discovery | Ineligible until a current capability is proven |
| `techsight` | Technology detection | Local launcher | Authentication required unless preflight proves availability |
| `model-router` | Model comparison | Injected model clients | Exact requested and resolved IDs are recorded |

Nexus authentication failure is nonfatal but visible. It cannot be converted to
success or silently skipped. Parallel remains configured with only `web_search`.
