# Company benchmark selection policy

The company corpus contains exactly ten fixtures in each of six primary
cohorts. A fixture is publishable only after live research establishes its
canonical identity and domain, an explicit B2B buyer, a business-facing offer,
and cohort fit from retained Evidence. Seed industry and product text are
discovery hints only and cannot qualify a fixture.

## Cohort requirements

- B2B SaaS: the retained first-party source must establish a software offer for
  business buyers.
- Recently funded B2B: a retained primary or authoritative funding source must
  include an event date from 2025-08-12 through 2026-08-12. Amount and event
  claims are not copied from seed data.
- B2B agencies: retained evidence must show a service-led business model for
  business clients. A software-only vendor does not qualify.
- Well-known B2B and commerce suppliers: retained evidence must show a
  business-facing offer and the stated cohort relationship.
- Local B2B services: identity, canonical domain, and NAP must match a durable
  local listing such as Maps CID, BBB, Yelp, a regulator, or a comparable
  directory. A same-name business in another location is not sufficient.

Every company uses at least one first-party source and at least two entity-relevant
independent sources on distinct normalized domains where available. Source
bodies shorter than the quality threshold, redirects to unrelated entities,
and search-result shells do not count.

## Saturation and unknowns

Each of the eight P0 enrichments runs through the typed EnrichmentRunner.
Assertions require retained Evidence. A category may be marked unknown only
after its actual search angles run and two distinct angles are closed. A
no-material-fact result closes directly. A material result closes only after
its exact typed field assertion cites newly retained relevant Evidence; it is
never relabeled as an empty search. Unknowns are category-specific and are not
permission to infer from seed data.

## Replacement and approval

A fixture that cannot meet its cohort contract fails the stage. Replacement
requires documented evidence that the original is ineligible, a candidate
that preserves cohort and difficulty balance, programmed validation, and
explicit human review. No automated run silently substitutes a company.

For the 2026-08-12 corpus, `funded-09` was explicitly replaced: Virtual
Peaker's primary funding event was 2025-08-06, six days before the eligibility
window, and no primary source supported its seeded 2026 debt claim. DualEntry
preserves the ambiguous recently-funded B2B slot and has an authoritative
2025-10-02 Series A announcement within the window. The stable fixture ID is
retained so the audited rollout ordering does not change; verified identity,
domain, and funding fields replace the stale seed only after live validation.

Completing research does not create an Approval. Promotion still requires at
least 90 percent programmed ground-truth validation followed by explicit human
review of attribution, scope, and safety.

`agency-04` was explicitly replaced. Audiense presents a software product
suite and did not satisfy the service-led agency contract. Walker Sands keeps
the same stable cohort slot and difficulty intent while its retained official
and independent profiles establish a B2B agency offer. `agency-02` is named
AbelsonTaylor to bind the fixture to the flagship `abelsontaylor.com` domain;
the group relationship remains independently cited.

`agency-08` is normalized from Capacity Interactive to its current `Capacity`
brand. The stable fixture ID, domain, and LinkedIn identity are retained; the
2026 rebrand and service-led client work are checked against retained sources.

`agency-10` is normalized from the domain-style label Chartis.io to the current
company name Chartis Interactive. Its stable ID, `chartis.io` domain, and
LinkedIn identity remain unchanged and are validated against retained sources.

Local qualification is data-driven: each fixture names the exact retained
listing and location source plus its reviewed match basis. Normalized company
name and either street/postal components or phone must agree between those two
sources. Direction, street-type, and suite abbreviations are normalized, while
word order and harmless extra components do not invalidate a match.

`local-07` is the one documented `name_locality` exception. Apollo Mission
Critical Engineering's official service page does not publish a street
address, so its exact company name and Atlanta locality are cross-checked
between its retained LinkedIn profile and the Cobb County business listing.
The county classification says engineering services are not a certified
engineer; the corpus does not infer or claim a professional-engineer license.
`local-08` uses a lower-authority B2BHint registry mirror because the stronger
Georgia registry endpoint was inaccessible; the exact address is also present
on the retained first-party page. `local-10` is explicitly bound to the
hyphenated `atlas-mechanical.com` Woodland, Washington company and its OSHA
listing, excluding the unrelated San Diego business at the unhyphenated
domain.
