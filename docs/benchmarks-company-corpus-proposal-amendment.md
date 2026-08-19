# Corpus Proposal Amendment — B2B Only

**Date:** 2026-08-12
**Status:** Approved B2B selection foundation; live qualification evidence and dossiers remain pending.

The prior proposal is invalid for the test because it included consumer-facing companies and B2C-oriented businesses. The benchmark must contain **B2B companies only**.

## Revised cohort rules

- **Local:** B2B local service providers, commercial contractors, industrial suppliers, and business services with verifiable local listings.
- **SaaS:** Business software companies.
- **Recently funded:** B2B companies with a qualifying funding event in the 12 months before the recorded `as_of` date.
- **Well-known:** Recognizable B2B companies or companies whose tested offer is explicitly business-facing.
- **Agencies:** B2B marketing, creative, consulting, recruiting, or professional-services agencies.
- **Ecommerce/CPG:** B2B commerce, wholesale, commercial-supply, or business-oriented CPG companies; exclude consumer-only brands.

The revised corpus still requires exactly 60 unique companies, 10 per primary cohort, and 15 shared-core members. Every fixture must document its B2B buyer, business-facing offer, canonical domain, cohort evidence, difficulty, and expected ad channels.

## Required changes before rebuilding the list

- [ ] Replace all consumer-only and primarily B2C fixtures.
- [ ] Rebuild the 60-company proposal using B2B qualification as a hard gate.
- [ ] Verify recent-funding dates from primary sources.
- [ ] Verify local listings for the local cohort.
- [ ] Ensure every cohort contains easy, ambiguous, and hard cases.
- [ ] Present the revised list for human approval before creating `benchmarks/companies.yaml` or spending on dossiers.

**Decision:** The original corpus proposal is rejected and must not be used for testing.
The B2B-only 60-company selection was approved on 2026-08-12. Approval closes
the corpus-composition and corrected-foundation gates; it does not waive the
funding-source, local-listing, source-saturation, budget, or human-review gates.
