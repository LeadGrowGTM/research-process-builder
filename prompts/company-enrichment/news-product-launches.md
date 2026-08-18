# News and product launches

Return dated, cited company events using only the supplied Evidence. Every
event must cite one or more supplied `evidence_id` values. Do not use general
knowledge about the company. Do not guess dates.

Evidence items are excerpts from news search results, press releases, company
blog or newsroom pages, and third-party articles. Many excerpts begin with a
detected date line. An excerpt may mention the company without describing an
event about the company; ignore those.

## Two collections

- `news`: material events about the company that are not product launches:
  funding, acquisitions, partnerships, leadership changes, expansions,
  awards with substance, and major positioning changes.
- `launches`: new products, meaningful new features, integrations, and
  releases the company itself shipped.

Each event has:

- `date`: ISO `YYYY-MM-DD` when the Evidence states a full date. Use
  `YYYY-MM` when only month and year are stated. The date must appear in the
  cited Evidence itself as an absolute date (`2026-03-09`, `March 9, 2026`,
  `9 March 2026`, or `March 2026`). Relative phrases such as "3 weeks ago",
  "recently", or "1 month ago" are not dates: an event known only from a
  relative phrase cannot be reported unless another cited Evidence item gives
  the absolute date. Never infer a date from the retrieval date. Copy the
  year exactly as written in the Evidence: `Feb 19, 2025` is `2025-02-19`,
  never `2026-02-19`. Before returning, re-read each date against its cited
  excerpt and fix any year that does not match.
- `headline`: one plain sentence of at most sixteen words stating what
  happened, in the buyer's language, not the press-release language.
- `event_type`: for `news` one of `funding`, `acquisition`, `partnership`,
  `leadership`, `expansion`, `award`, `positioning`, `other`; for `launches`
  one of `product`, `feature`, `integration`, `release`.
- `why_it_matters`: at most twenty words on what changed for the company or
  its customers. Only what the Evidence supports.
- `source_url`: the URL of the Evidence item that most directly reports it.
- `evidence_ids`: the Evidence items that support this event.

Report each real-world event once. When two Evidence items describe the same
event, cite both and keep one entry. Prefer the first-party or wire-service
source for `source_url`.

## Missing support

Return an empty collection when the Evidence supports no events of that kind,
and list that collection's name (`news` or `launches`) in the top-level
`unknowns` array. Empty collections are correct and expected for quiet
companies. Never restate a
company description, a template page, a job posting, or a competitor's article
as an event. Never manufacture a launch from a features page with no release
date.

## Complete good example

```json
{
  "news": [
    {
      "date": "2024-04-11",
      "headline": "Expanded its senior leadership team with new executive hires",
      "event_type": "leadership",
      "why_it_matters": "Signals investment in scaling the business",
      "source_url": "https://www.prnewswire.com/example-leadership",
      "evidence_ids": ["evidence-004"]
    }
  ],
  "launches": [
    {
      "date": "2024-01-30",
      "headline": "Released Smart Reports that build a client report in seconds",
      "event_type": "feature",
      "why_it_matters": "Cuts client reporting time for agencies",
      "source_url": "https://www.prnewswire.com/example-smart-reports",
      "evidence_ids": ["evidence-005", "evidence-006"]
    }
  ]
}
```

## Bad examples

- Non-event: listing "AgencyAnalytics offers 85+ integrations" as a launch.
  A capability statement is not a dated release.
- Borrowed event: listing a competitor's funding round found in an
  "alternatives" article as this company's news.
- Invented date: writing `2026-08-01` because the article was retrieved in
  August 2026. If no date is stated, the event cannot be reported.
- Converted relative date: an Evidence item says the funding round was
  announced "1 month ago"; writing `2026-07-18` is a fabricated date. Skip
  the event or find an Evidence item that states the actual date.
- Date borrowed from another item: citing a press release dated 2024-04-11
  for an event that only a different, undated Evidence item describes.
- Shifted year: the excerpt says `Dec 16, 2025` and the event is reported as
  `2026-12-16`. The year must be copied, not updated.

Return only the structured output required by the supplied schema.
