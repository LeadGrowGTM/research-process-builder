# Company corpus live run

Recorded as of 2026-08-12 for rollout stage saas_shared_core.

## Execution

- Selected IDs in order: saas-01, saas-04, saas-07.
- Duplicate IDs: 0.
- Free public HTTP sources first; no paid provider was enabled.
- Immutable paid ceiling when explicitly enabled: USD 2.00.
- Actual paid calls and cost: 0 calls, USD 0.
- Authentication and source gaps in the authoritative run: none.
- Authoritative artifacts: runs/company-corpus-live-v5, containing nine
  content-addressed Evidence objects, three validated dossier YAML files, and
  an append-only machine stage report.
- Resume: three dossiers resumed, zero sources persisted or repurchased, and
  USD 0 incremental cost.

Each dossier passed the strict research-complete validator with typed,
citation-backed identity and description assertions. Every other required
category is an explicit unknown rather than a fabricated inference. The
machine summary records why each remained unknown after the bounded source and
dry-angle process.

## Source process

Each company had one official source, two independent public sources, and two
recorded dry angles.

| Company | Dry angles |
| --- | --- |
| AgencyAnalytics | ad transparency; funding or investor transaction |
| aPriori | ad transparency; public dollar pricing |
| Betterworks | ad transparency; audited financials |

## Validation and limitations

The first run proved transport, persistence, validation, and zero-repurchase
resume, but self-review found an AgencyAnalytics page misclassified as
independent and a Betterworks BuiltWith response containing only a loading
shell. Those artifacts and the subsequent blocked v2-v4 attempts remain in
ignored run directories as audit history; they are not the authoritative
proof.

The v5 composition rejects readable bodies shorter than 200 characters,
normalizes UTF-8 before fallback decoding, and records a source gap instead of
claiming saturation when the bar is missed. It passed the source bar for all
three companies.

The AgencyAnalytics SourceForge profile is independent and substantial, but it
is lower-confidence and potentially stale compared with official and LinkedIn
records. It supplies source diversity, not corroboration of every claim.
Unsupported categories therefore remain explicit unknowns.

This is corpus-construction evidence, not an Approval. Promotion still
requires programmed ground-truth validation at at least 90 percent and
explicit human review.
