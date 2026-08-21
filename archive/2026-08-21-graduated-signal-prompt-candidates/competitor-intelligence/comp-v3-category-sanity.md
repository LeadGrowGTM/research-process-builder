# Competitor intelligence

Return a defensible, complete competitor set for the subject company using only
the supplied Evidence. Every competitor must cite one or more supplied
`evidence_id` values whose excerpt contains the competitor's name. Do not add
competitors from general knowledge, even well-known ones.

The prompt names the subject company and its website host. Never list the
subject itself, under any name or domain.

## Procedure

1. Find every Evidence item that is explicitly about alternatives to, or
   competitors of, the subject: a title or excerpt such as "<subject>
   alternatives", "<subject> competitors", "<subject> vs <other>", "compare
   <subject>", a review-site alternatives page (G2, Capterra, Gartner Peer
   Insights, TrustRadius, GetApp, Slashdot, SourceForge), an analyst or
   directory page listing the subject's competitors, or the subject's own
   competitor or comparison page.
2. From each of those items, list every company or product named as an
   alternative or competitor. Each one is `named`. Enumerate the whole list;
   ten to twenty-five `named` entries is normal for an established product.
   Do not stop after a few, and do not skip smaller vendors.
3. Then, from Evidence that is not about the subject (a general "best tools"
   roundup, a community thread, a category page), add companies solving the
   same buyer problem for the same buyer as `inferred`.
4. Re-check: no company appears twice or in both buckets; the subject is
   absent; every entry's cited excerpts contain its name.

## Two collections

- `named`: the Evidence explicitly positions the company against the subject
  (steps 1 and 2). If any excerpt says the company is an alternative to, a
  competitor of, or compared with the subject, it is `named`, whatever the
  size of the vendor.
- `inferred`: Evidence shows the company solving the same buyer problem, but no
  Evidence item compares it with the subject. Never demote an explicitly
  compared company to `inferred`; never promote an inferred one to `named`.

Each competitor has:

- `name`: the company or product name exactly as written in the cited
  Evidence. Do not rename, expand, or abbreviate it (write "ServiceNow ITOM"
  if that is what the excerpt says, not "ServiceNow IT Operations
  Management").
- `domain`: the competitor's website domain only when the cited excerpt or the
  cited Evidence URL literally shows it; otherwise `null`. Never guess a
  domain, and never reuse the subject's domain.
- `relationship`: `direct` when it sells the same category to the same buyer;
  `adjacent` when it overlaps on part of the job; `alternative` when buyers
  substitute it although the category differs (spreadsheets, a services firm).
- `why`: at most twenty words stating what the Evidence says about how it
  competes.
- `evidence_ids`: only Evidence items whose excerpt contains the name. If a
  company appears in several items, cite all of them on one entry.

## Category sanity

Aggregator "alternatives" pages (software directories, market-share and
tech-stack trackers, app catalogs) pad their lists with tools from other
categories. Keep a listed tool only when it plausibly sells the subject's
category to the subject's buyer. Skip generic productivity, diagramming,
project-management, database, spreadsheet, integration-glue, or infrastructure
tools that merely share a page with the subject (Trello, Visio, Confluence,
Zapier, Asana, Airflow, Baserow for a process-automation platform), unless an
Evidence item compares them with the subject on the subject's own job. When a
list names both a vendor and one of its products (IBM and IBM Cloud Pak for
Business Automation, Oracle and Oracle BPM), return one entry, using the name
as written where it is compared with the subject.

## Exclusions

- The subject company itself.
- Partners, integrations, data sources, and platforms the subject builds on,
  unless an Evidence item explicitly calls them an alternative or competitor.
- Namesakes: a product with the same name in a different category (an HR tool
  called "Built" when the subject is a construction-finance platform).
- Companies that appear only in a competitor's own article as that
  competitor's peers, not the subject's.

## Conflicts

When Evidence items disagree, for example one source calls a company a partner
and another calls it a competitor, keep the competitor entry and add a
`conflicts` note with the disagreement and both `evidence_ids`. Do not resolve
the conflict silently.

## Missing support

Return empty collections when the Evidence supports no competitors, and list
`competitors` in the top-level `unknowns` array only when both collections are
empty. Whenever at least one competitor is returned, `unknowns` must be `[]`.

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
        "domain": null,
        "relationship": "direct",
        "why": "Named in a G2 alternatives-to-subject list",
        "evidence_ids": ["evidence-003"]
      },
      {
        "name": "Swydo",
        "domain": null,
        "relationship": "direct",
        "why": "Named in the same G2 alternatives-to-subject list",
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
  },
  "unknowns": []
}
```

## Bad examples

- Truncated list: an "Archive360 alternatives" page names eight tools and only
  the first is returned. Every in-category tool on the list is `named`.
- Padded list: Trello, Visio, and Zapier returned as `named` competitors of a
  BPM platform because a software directory listed them under its
  alternatives. They do not sell the subject's category.
- Demoted comparison: placing BL.INK in `inferred` although the cited excerpt
  is a "Bitly alternatives" list. Explicit comparison makes it `named`.
- Duplicate entry: the same company in both `named` and `inferred`.
- Guessed domain: writing `betterworks.com` for a competitor because the
  subject's page was cited. The domain must be shown in the cited Evidence.
- Wrong citation: citing an excerpt that never mentions the competitor.
- Renamed entry: expanding "Splunk ITSI" to "Splunk IT Service Intelligence"
  when the excerpt does not say so.
- Keyword neighbor: naming Google Analytics as a competitor because it appears
  near the word "analytics". It is a data source unless Evidence positions it
  against the subject.
- Redundant unknown: returning competitors and also `"unknowns":
  ["competitors"]`.

Return only the structured output required by the supplied schema.
