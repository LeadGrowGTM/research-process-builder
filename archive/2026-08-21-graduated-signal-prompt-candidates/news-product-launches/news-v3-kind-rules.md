# News and product launches

Return dated, cited events about the subject company using only the supplied
Evidence. Every event must cite one or more supplied `evidence_id` values. Do
not use general knowledge about the company. Do not guess dates.

## The subject

The prompt names the subject company and its website host. Report only events
about that company. A company with a similar name on a different domain (for
example "Apriori Bio" when the subject is aPriori at apriori.com) is a
different entity: never report its events, even when the Evidence about it is
plentiful.

## What counts as an event

An event is something that happened on a stated date and was announced or
reported: a press release, a newsroom or blog announcement, a wire-service
item, a news article, an award listing with a date, a dated release note that
describes a specific shipped change.

Many excerpts start with `Detected date:` or `Date:`. That is page metadata
(the crawl, publish, or last-modified date of the page), not proof that an
event happened on that day. Use it as the event date only when the page itself
is an announcement of that event. It is never the event date for evergreen
pages:

- product feature pages (`/features/`, `/solutions/`, `/product/`, `/platform/`)
- help-center or documentation articles and release-note index pages
- author, category, tag, or search-result listing pages
- homepages, pricing pages, comparison pages, job posts
- video or social-post listings whose date is not written in the excerpt

An evergreen page with a detected date is not a launch. Skip it. A capability
statement ("offers 85+ integrations", "you can drag and drop widgets") is not a
dated release.

## Two collections

- `news`: material events that are not product launches: funding,
  acquisitions, partnerships, leadership changes, expansions, awards with
  substance, rebrands, and major positioning changes. An acquisition, a
  partnership, a funding round, an executive hire, an award, or a positioning
  or milestone announcement is always `news`, even when the press release also
  describes product capabilities the deal will bring.
- `launches`: new products, meaningful new features, integrations, and
  releases the company itself shipped and announced. A press release whose
  subject is "launches", "introduces", "unveils", or "releases" a product or
  feature is a launch. Report the launch itself; do not also report the same
  release as `news`.

Each event has:

- `date`: ISO `YYYY-MM-DD` when the Evidence states a full date, `YYYY-MM` when
  only month and year are stated. The date must be stated by the cited Evidence
  itself: written in the excerpt as an absolute date (`2026-03-09`,
  `March 9, 2026`, `9 March 2026`, `March 2026`) or carried in the cited
  Evidence URL path (`/news-release/2026/07/01/...`). Relative phrases such as
  "3 weeks ago", "recently", or "1 month ago" are not dates. Never infer a date
  from `retrieved_at`. Copy the year exactly as written: `Feb 19, 2025` is
  `2025-02-19`, never `2026-02-19`. A date later than the Evidence
  `retrieved_at` is impossible; re-read the excerpt and fix it or drop the
  event. Before returning, re-read every date against its cited excerpt or URL.
- `headline`: one plain sentence of at most sixteen words stating what
  happened, in the buyer's language, not the press-release language.
- `event_type`: for `news` one of `funding`, `acquisition`, `partnership`,
  `leadership`, `expansion`, `award`, `positioning`, `other`; for `launches`
  one of `product`, `feature`, `integration`, `release`.
- `why_it_matters`: at most twenty words on what changed for the company or
  its customers. Only what the Evidence supports.
- `source_url`: the URL of the Evidence item that most directly reports it.
- `evidence_ids`: the Evidence items that support this event. Cite the item
  that states the date.

Report each real-world event once. When two Evidence items describe the same
event (a wire release and the company's own newsroom copy), cite both and keep
one entry. Prefer the first-party or wire-service source for `source_url`.
Report every dated announcement about the subject that the Evidence supports,
old or new; do not stop at the first few.

## Missing support

Return an empty collection when the Evidence supports no events of that kind,
and list that collection's name (`news` or `launches`) in the top-level
`unknowns` array. Empty collections are correct and expected for quiet
companies. Never restate a company description, a template page, a job
posting, or a competitor's article as an event. Never manufacture a launch from
a features page with no release announcement.

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
  ],
  "unknowns": []
}
```

## Bad examples

- Evergreen page as launch: `/features/embeddable-content` with
  `Detected date: Dec 14, 2025` reported as a December 2025 feature launch. The
  page describes a capability; nothing says it shipped that day.
- Docs index as launch: a `2024 Release Notes` page with
  `Detected date: Mar 24, 2026` reported as a March 2026 release.
- Namesake company: a partnership announced by "Apriori Bio" reported as news
  for aPriori (apriori.com).
- Borrowed event: a competitor's funding round found in an "alternatives"
  article reported as this company's news.
- Invented date: `2026-08-01` because the article was retrieved in August
  2026. If no date is stated, the event cannot be reported.
- Converted relative date: an item says the round was announced "1 month ago";
  writing `2026-07-18` is fabricated. Use the URL path date if the cited URL
  carries one, otherwise skip the event.
- Shifted year: the excerpt says `Dec 16, 2025` and the event is reported as
  `2026-12-16`. Copy the year.
- Future date: an event dated after `retrieved_at`.
- Misplaced acquisition: "Acquired Rypple to advance manager effectiveness"
  reported under `launches`. Acquisitions are `news`.
- Double report: the same partnership press release returned once as `news`
  and again as a `launches` entry.

Return only the structured output required by the supplied schema.
