# find people via media appearances, platforms, and content

model: gpt-6-luna
service_tier: flex

find company people through non-traditional channels: podcast/media appearances, data platforms (ZoomInfo, RocketReach, WellFound, TheOrg), and authored content (blogs, Medium, Substack).

## inputs

- `{{company_name}}` — the company to research
- `{{domain}}` — their website domain (e.g. clay.com)
- `{{category}}` — what they do in 2-3 words. required if the company name is a common word or 6 characters or fewer.

## steps

### step 1: multi-platform people sweep

search: `{{company_name}} site:zoominfo.com OR site:rocketreach.co OR site:wellfound.com OR site:theorg.com`

broadest people search across all major data platforms in one query. tested at Q3.9 across 25 companies (PRIMARY). each platform contributes different data: ZoomInfo has titles and department data, RocketReach has org charts, WellFound has startup team profiles, TheOrg has visual org charts.

extract from results:

- every person named across all platforms, with their title
- which platform surfaced them — include the URL for each
- department or function if visible
- seniority level (C-suite, VP, Director, Manager, IC)
- any org chart or reporting structure data

**stop if:** you found 10+ named people across platforms. skip to step 3 for media mentions.

### step 2: single-platform fallback (use only if step 1 returned thin results)

search: `{{company_name}} site:zoominfo.com -jobs`

narrow to ZoomInfo alone if the multi-platform query returned noise. ZoomInfo has the broadest coverage across company sizes.

extract from results:

- any people not found in step 1
- more detailed title and department data from ZoomInfo specifically
- source URL

### step 3: media and podcast appearances

search: `{{company_name}} podcast guest OR interview OR episode`

finds people who have appeared on podcasts or in interviews. tested at Q3.7 across 25 companies (ENRICHMENT). T4 drops to Q3.2 because micro companies rarely get podcast invites.

extract from results:

- full name and title of every person who appeared in media
- the podcast/publication/event name — include the URL
- what they discussed (topic, product launch, industry trend)
- date of appearance if visible

### step 4: broader media search (runner)

search: `{{company_name}} podcast OR interview OR talk OR keynote OR conference speaker`

broader media query that catches conference talks and keynotes in addition to podcasts. useful when step 3 returned limited results.

extract from results:

- any media appearances not found in step 3
- conference or event names
- source URLs

### step 5: authored content and blog posts

search: `site:{{domain}} author OR "by" OR team OR blog`

finds people who have authored content on the company's own domain. tested at Q3.5 across 25 companies (ENRICHMENT). T3-T4 weak (Q3.2-Q3.3) because micro companies rarely have attributed blog content.

extract from results:

- author names and their titles (if listed on byline or author bio)
- the type of content they authored (blog post, case study, whitepaper, technical doc)
- any author bio information
- source URLs

### step 6: external content platforms (runner)

search: `{{company_name}} site:medium.com OR site:substack.com OR site:linkedin.com/pulse`

finds company people who publish on external content platforms. useful for thought leaders who write outside the company blog.

extract from results:

- author names and their titles
- the platform where they publish
- topics they write about
- source URLs

## do not search

- `{{company_name}} site:youtube.com OR site:podcasts.apple.com` — scored KILL (Q2.1). these platforms have poor search result snippets for extracting people data.
- `{{company_name}} team page` — returns the page but not individual names in search snippets
- `{{company_name}} employees list` — returns job board aggregators, not actual people

## output

```
## people found via creative channels: {{company_name}}

**from data platforms (PRIMARY Q3.9):**
- [name] — [title] — found on [platform]
- [name] — [title] — found on [platform]

**from media / podcasts (ENRICHMENT Q3.7):**
- [name] — [title] — appeared on [podcast/publication] discussing [topic]
- or: "no media appearances found — common for T4 micro companies"

**from authored content (ENRICHMENT Q3.5):**
- [name] — [title] — authored [content type] on [platform/domain]
- or: "no attributed content found — T3-T4 companies rarely have public bylines"

**coverage notes:** [note tier-specific expectations. platform data is strong across all tiers. media and content drop off significantly for T3-T4 companies.]

**sources:**
- [source name](url) — what was found there
- [source name](url) — what was found there
```
