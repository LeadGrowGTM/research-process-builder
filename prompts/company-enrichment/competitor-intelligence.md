# Competitor intelligence

Return a defensible competitor set for the subject company using only the
supplied Evidence. Every competitor must cite one or more supplied
`evidence_id` values. Do not add competitors from general knowledge, even
well-known ones.

Evidence items are excerpts from the company's own comparison or competitor
pages, third-party "alternatives" and "vs" articles, review-site comparisons,
and community threads. Some excerpts list many tools; not every tool listed is
a competitor of the subject.

## Two collections

- `named`: companies that an Evidence item explicitly positions against the
  subject: a "vs" page, an "alternatives to <subject>" or "<subject>
  competitors" list (review sites, analyst pages, and blog roundups about the
  subject all count), a "compared with" table, or the subject's own competitor
  page. If the excerpt says the company is an alternative to, a competitor
  of, or compared with the subject, it is `named`.
- `inferred`: companies that Evidence shows solving the same buyer problem for
  the same buyer, but which no Evidence item explicitly compares with the
  subject: for example a tool mentioned in a community thread or a general
  "best tools" article that never names the subject. Keep them here; never
  promote an inferred competitor to `named`, and never demote an explicitly
  compared company to `inferred`.

Each competitor has:

- `name`: the company or product name as written in the Evidence.
- `domain`: the competitor's website domain when the Evidence states or links
  it, otherwise `null`.
- `relationship`: `direct` when it sells the same category to the same buyer;
  `adjacent` when it overlaps on part of the job; `alternative` when buyers
  substitute it although the category differs (for example spreadsheets or a
  services firm).
- `why`: at most twenty words stating what the Evidence says about how it
  competes.
- `evidence_ids`: the Evidence items that support this entry.

Deduplicate by company. If a company appears in several Evidence items, cite
all of them on one entry. Do not list the subject company itself.

## Conflicts

When Evidence items disagree, for example one source calls a company a partner
and another calls it a competitor, keep the competitor entry and add a
`conflicts` note with the disagreement and both `evidence_ids`. Do not resolve
the conflict silently.

## Missing support

Return empty collections when the Evidence supports no competitors, and list
`competitors` in the top-level `unknowns` array only when both collections
are empty. Whenever at least one competitor is returned, `unknowns` must be
`[]`. Do not fill `named` with keyword-sharing vendors from a generic
listicle that does not compare them with the subject. Do not fill `inferred`
with every tool that appears in a "best tools" roundup.

## Complete good example

```json
{
  "competitors": {
    "named": [
      {
        "name": "DashThis",
        "domain": "dashthis.com",
        "relationship": "direct",
        "why": "Listed on the subject's own competitor comparison page",
        "evidence_ids": ["evidence-002"]
      },
      {
        "name": "TapClicks",
        "domain": "tapclicks.com",
        "relationship": "direct",
        "why": "Publishes an alternative-to-subject comparison article",
        "evidence_ids": ["evidence-003"]
      }
    ],
    "inferred": [
      {
        "name": "Looker Studio",
        "domain": null,
        "relationship": "alternative",
        "why": "Agency thread mentions building client reports in it instead",
        "evidence_ids": ["evidence-005"]
      }
    ],
    "conflicts": []
  }
}
```

## Bad examples

- Keyword neighbor: naming Google Analytics as a direct competitor because it
  appears in the same sentence as "analytics". It is a data source, not a
  competitor, unless Evidence positions it against the subject.
- Unlabeled inference: putting Looker Studio in `named` when no Evidence item
  compares it with the subject.
- Roundup padding: copying all eleven tools from an "11 alternatives" list
  without checking that the article is about alternatives to the subject.
- Demoted comparison: placing DashThis in `inferred` even though the cited
  excerpt is an "AgencyAnalytics alternatives" list. Explicit comparison
  makes it `named`.
- Redundant unknown: returning competitors and also `"unknowns":
  ["competitors"]`. When any competitor exists, `unknowns` is `[]`.

Return only the structured output required by the supplied schema.
