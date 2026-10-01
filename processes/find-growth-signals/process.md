# find growth and marketing investment signals

model: gpt-6-luna
service_tier: flex

> **validated:** 25 companies across 4 tiers (3,357 searches). covers 12 categories. per-category accuracy:
> - growth_marketing: combo_name_blog PRIMARY Q4.0
> - content_blog: combo_domain_blog PRIMARY Q4.0
> - tech_stack: combo_stackshare_wappalyzer PRIMARY Q4.0
> - newsletter_email: runner_newsletter PRIMARY Q4.0
> - events_conferences: best_meetup PRIMARY Q3.8
> - awards_recognition: combo_name_award PRIMARY Q4.0
> - social_media: best_linkedin_co PRIMARY Q3.9
> - community_platforms: runner_discord ENRICHMENT Q3.8
> - partnerships_integrations: best_partnerships PRIMARY Q4.0
> - customer_case_studies: best_case_study PRIMARY Q3.9
> - pricing_intelligence: best_pricing PRIMARY Q4.0
> - leadership_people: best_ceo_founder PRIMARY Q4.0

surface indicators of active investment and growth: content output, marketing infrastructure, social presence, community engagement, event activity, and monetization maturity. this tells you whether a company is actively investing in growth or coasting.

## inputs

- `{{company_name}}` — the company to research
- `{{domain}}` — their website domain (required for site: searches)
- `{{category}}` — what they do in 2-3 words. required if the company name is a common word or 6 characters or fewer.

## steps

### step 1: website infrastructure sweep

search: `site:{{domain}} blog OR pricing OR newsletter OR demo OR "free trial" OR "book a call"`

this single query detects multiple growth signals at once: do they have a blog, a pricing page, a newsletter, a demo flow? tested Q5/C4 for T2+ companies. 25-company tier test: pricing_intelligence (best_pricing) PRIMARY Q4.0, newsletter_email (runner_newsletter) PRIMARY Q4.0. returns nothing for T3/micro startups whose sites aren't fully indexed — if zero results, skip to step 7.

extract from results:

- blog URL and most recent post title (if visible)
- pricing page URL and model (freemium, free trial, enterprise-only, custom)
- any lead capture mechanisms (newsletter signup, demo booking, free trial, gated content)
- any webinar or event pages
- three sentence summary of their marketing infrastructure maturity

companies with blog + pricing + demo booking + newsletter = mature growth engine. companies with just a homepage and contact form = early stage or services-oriented.

**stop if:** you found a blog, pricing page, and at least one lead capture mechanism. you have a clear picture of their infrastructure. continue to step 2 for content depth.

### step 2: blog content depth

search: `site:{{domain}}/blog`

if no results, try: `site:{{domain}} blog OR news OR updates`

this goes directly to the company's own blog and returns actual post titles, dates, and topics. tested Q5 across Clay (clay.com/blog — GTM engineering, case studies, product updates) and Lovable (lovable.dev/blog — MCP, Series B, Lovable 2.0). 25-company tier test: content_blog (combo_domain_blog) PRIMARY Q4.0.

the previous pattern (`{{company_name}} {{category}} blog`) returned third-party content about the company, not the company's own posts. that's useful too (see step 5) but this step gives you the owned content signal.

extract from results:

- recent blog post titles and dates
- posting frequency signal (multiple posts in recent months = active content engine, nothing recent = dormant)
- content themes (product updates, thought leadership, case studies, SEO plays)
- three sentence summary of content output and recency

a company publishing 2+ blog posts per month is investing in organic growth. a company with a blog that hasn't been updated in 6+ months is coasting or pivoting channels.

### step 3: social media and community presence

**search A:** `{{company_name}} {{category}} site:twitter.com OR site:x.com OR site:instagram.com OR site:linkedin.com`

this is the highest-performing pattern in the process. tested Q4.75 average across all company tiers — even T3 startups have social accounts indexed. 25-company tier test: social_media (best_linkedin_co) PRIMARY Q3.9.

extract from results:

- every social account found (handle, platform, and URL)
- follower count if visible in search snippet
- most recent post topic if visible
- three sentence summary of social presence and activity level

**search B:** `{{company_name}} {{category}} discord OR slack OR community`

community platforms are a massive growth signal that most research processes miss entirely. tested Q5 across Clay (Slack community, 15K+ members, community.clay.com), Lovable (Discord, 162K+ members), and Cursor (Discord 15K+, forum.cursor.com). thin for T3 but still worth checking. 25-company tier test: community_platforms (runner_discord) ENRICHMENT Q3.8.

extract from results:

- community platform(s) found (discord, slack, forum, etc.) with URLs
- member count if visible
- activity level signals (active moderation, team participation, etc.)
- three sentence summary of community engagement

a large, active community is a stronger growth signal than social followers. it indicates product-market fit and organic advocacy.

**stop if:** combined with steps 1-2, you have a clear picture of their marketing investment across content, infrastructure, social, and community. skip to output if you only need a high-level growth signal.

### step 4: lead capture and newsletter

search: `site:{{domain}} "subscribe" OR "newsletter" OR "sign up" OR "book a demo"`

this searches the company's own site for signup and newsletter mechanisms. tested Q4 on Clay — found newsletter subscription page, community newsletter, LinkedIn newsletter, affiliate signup. 25-company tier test: newsletter_email (runner_newsletter) PRIMARY Q4.0.

extract from results:

- newsletter name and platform (substack, beehiiv, mailchimp, custom) if visible
- any email capture forms or gated content
- demo booking or consultation pages
- three sentence summary of lead capture maturity

companies that run newsletters are investing in owned audience. this is a stronger growth signal than social media because it requires consistent effort and indicates long-term thinking.

### step 5: third-party coverage and buzz

search: `{{company_name}} {{category}} blog`

25-company tier test: growth_marketing (combo_name_blog) PRIMARY Q4.0.

this step explicitly looks for what OTHERS write about the company — reviews, mentions, comparisons, guides. a company getting third-party coverage without paying for it = organic buzz.

extract from results:

- third-party blog posts mentioning the company (reviewer name, publication, topic)
- review roundups or comparison articles that include the company
- any coverage from industry publications
- three sentence summary of third-party buzz level

**important distinction:** step 2 finds the company's own blog. step 5 finds what others say about them. a company with both = mature content presence. a company with only third-party coverage = getting attention but not investing in owned content.

### step 6: podcast, webinar, and event activity (skip for very small companies)

search: `{{company_name}} podcast OR webinar OR event OR conference`

tested Q4 for T2+ companies. 25-company tier test: events_conferences (best_meetup) PRIMARY Q3.8. returns unrelated conferences for T3/micro startups — skip if the company has fewer than ~20 employees.

extract from results:

- any podcast appearances by founders/execs (name of podcast, topic)
- any webinars hosted or co-hosted
- any conference appearances or sponsorships
- community events (meetups, hackathons)
- three sentence summary of event-based growth activity

companies appearing on podcasts and hosting webinars = active demand gen. this is especially strong signal for B2B companies.

**stop if:** you have enough data across content, social, community, newsletter, and events to assess their overall growth investment. skip to output.

### step 7: fallback for small/obscure companies (only if steps 1-2 returned almost nothing)

search: `{{company_name}} {{category}} site:producthunt.com OR site:wellfound.com`

producthunt and wellfound index startups that may not have fully-indexed websites. tested Q4 — wellfound returns company profiles with careers, funding, and industry tags even for smaller companies.

extract from results:

- product hunt launch info (upvotes, launch date, tagline)
- wellfound profile data (employee count, funding stage, industry)
- three sentence summary

if even this returns nothing, try: `{{company_name}} {{category}}` as a blunt last resort. if a company has ZERO mentions anywhere, that's itself a signal of inactivity or extreme early stage.

## do not search

- `{{company_name}} {{category}} newsletter` — returns product feature content about newsletters or generic "how to build newsletters" guides. tested Q2.25 average across all tiers. use `site:{{domain}} "subscribe" OR "newsletter"` instead.
- `{{company_name}} social media twitter youtube instagram` — returns product feature content, not the company's own accounts. tested Q1/C0 across all tiers.
- `site:youtube.com {{company_name}}` — returns zero results universally. YouTube is not indexed by web search this way.
- `{{company_name}} youtube channel` — returns unrelated channels for ambiguous names (clay art channels, scam warnings, etc.)
- `{{company_name}} marketing strategy` — returns generic marketing advice articles, not company-specific data
- `{{company_name}} google ads` — returns the company's ad-related product features, not whether they run ads
- `site:facebook.com/ads/library {{company_name}}` — facebook ad library is not indexed by search engines

## output

```
## growth and marketing signals for {{company_name}}

**overall growth investment:** [heavy / moderate / light / minimal]

**content signals:**
- blog: [active (X posts/month) / sporadic / dormant / not found] — [url]
- newsletter: [name, platform, frequency] or "not found"
- podcast/events: [appearances or hosted events] or "none found"

**marketing infrastructure:**
- lead magnets: [list what was found: ebooks, webinars, guides, etc.] or "none found"
- conversion flow: [free trial / freemium / demo booking / contact form only / unclear]
- pricing page: [public pricing / enterprise-only / custom / not found]

**social and community:**
- [platform]: [handle] — [follower count if visible]
- [platform]: [handle]
- community: [platform, member count, URL] or "none found"
(list all found)

**what this tells us:**
[three sentences. what growth stage are they in based on these signals? are they actively investing in demand gen, or is growth happening through other channels (product-led, partnerships, word of mouth)? what's the gap between their product maturity and their marketing maturity?]

**sources:**
- [source name](url) — what was found there
- [source name](url) — what was found there
```
