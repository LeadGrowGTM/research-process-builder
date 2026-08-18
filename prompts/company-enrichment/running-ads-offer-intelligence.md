# Running ads and offer intelligence

Return a structured per-channel advertising summary using only the supplied
Evidence. Every channel entry must cite one or more supplied `evidence_id`
values. Do not use general knowledge about the company or its industry.

The Evidence for this enrichment is machine-captured ad-library data. Each
Evidence item is a JSON excerpt from one channel:

- `google-ads-transparency`: a summary object (`running_ads`, `total_creatives`,
  `first_seen`, `last_seen`, `ad_formats`). It never contains ad copy.
- `meta-ad-library`: a `summary` object (`active_ads_count`,
  `inactive_ads_count`, `platforms`, `last_ad_date`) plus an `ads` list where
  each ad has `status`, `days_running`, `headline`, `cta_text`, `link_url`, and
  a `body` excerpt.

## Channels

Return one entry per channel that has Evidence. Each entry has:

- `channel`: `google` or `meta`, exactly as in the Evidence provider.
- `status`: `active` when the Evidence shows currently running ads
  (`running_ads: true` or `active_ads_count > 0`), `inactive` when the Evidence
  shows ads that all stopped, otherwise `unknown`.
- `angle`: the dominant message the ads lead with, in five to twelve plain
  words. Only from ad copy or headlines actually present in the Evidence.
- `offer`: the concrete thing being promoted (a product tier, a report, a
  free trial, a demo, a discount) in three to ten words. Only from Evidence.
- `call_to_action`: the most common call-to-action text among active ads,
  copied verbatim from `cta_text`.
- `landing_page`: the `link_url` of the longest-running active ad.
- `evidence_ids`: the Evidence items that support this entry.

Google Evidence contains no creative text. For a Google entry, set `angle`,
`offer`, `call_to_action`, and `landing_page` to `null`. Never borrow Meta copy
to describe Google.

## Missing support

Do not fabricate ad copy, offers, or landing pages. When a channel's Evidence
has counts but no ad list, keep `status` and set the creative fields to `null`.
When no Evidence exists for a channel, omit that channel; do not add an
`unknown` entry for a channel that was never observed. When no channel has
Evidence at all, return an empty `channels` list and list `ads` in the
top-level `unknowns` array.

## Complete good example

```json
{
  "ads": {
    "channels": [
      {
        "channel": "google",
        "status": "active",
        "angle": null,
        "offer": null,
        "call_to_action": null,
        "landing_page": null,
        "evidence_ids": ["evidence-001"]
      },
      {
        "channel": "meta",
        "status": "active",
        "angle": "stop building client reports by hand",
        "offer": "enterprise agency reporting plan",
        "call_to_action": "Contact us",
        "landing_page": "https://example.com/p/enterprise",
        "evidence_ids": ["evidence-002"]
      }
    ]
  }
}
```

## Bad examples

- Inventing copy: giving the Google entry the angle "automated reporting"
  because the company sells reporting software. Google Evidence has no copy, so
  the field must be `null`.
- Marketing paraphrase: `offer: "a powerful all-in-one platform"` when the ads
  say "Book a demo of the Enterprise plan". Use the concrete offer.
- Wrong status: `inactive` when `active_ads_count` is 90. Read the counts.

Return only the structured output required by the supplied schema.
