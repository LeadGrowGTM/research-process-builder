# find funding announcements

model: gpt-6-luna
service_tier: flex

> **validated:** 16 companies across 3 tiers (45 searches, 3 batches). PRIMARY stack (steps 1–3): 14/16 = 87.5%. Full stack with step 4 company blog: 16/16 = 100%. Basecamp correctly outputs "no recent funding" — valid result, not a miss.

surface the most recent funding round for a company. extract round type, amount, date, lead investors, and stated use of funds.

## inputs

- `{{company_name}}` — the company to research
- `{{domain}}` — their website domain
- `{{category}}` — what they do in 2-3 words. required if name is ambiguous (≤6 chars, common word, or shares name with something famous)
- `{{current_year}}` — the current year (e.g. 2026). in Clay: `YEAR({Created At})`.

## preprocessing

### name disambiguation

check if `{{company_name}}` is ambiguous:
- 6 characters or fewer (Clay, Keep, Glean, Cursor)
- common english word
- shares name with something famous

if ambiguous: append `{{category}}` to all search queries (e.g. `Clay GTM software`, `Cursor AI code editor`). if not: use name as-is.

### company size detection

search: `{{company_name}} company overview`

count third-party profiles in results:
- 5+ profiles → Tier 1 (known) → full pattern stack
- 2–4 profiles → Tier 2 (mid) → core stack + enrichment
- 0–1 profiles → Tier 3 (obscure) → core + fallbacks, thin results are the signal

---

## steps

### step 1: round-type sweep (ALL companies)

**search:** `{{disambiguated_name}} Series A OR Series B OR Seed OR "funding round"`

extract from results:
- round type (Seed, Series A/B/C/D, Bridge, Growth, Pre-IPO, Debt)
- amount raised ($Xm / $Xbn / undisclosed)
- exact date of announcement (e.g. "Jun 12, 2024") — never fabricate; use "date unknown" if not in snippet
- lead investor name(s)
- source URL

**stop if:** you found the most recent round with amount + date confirmed. skip to step 5 for use-of-funds.

### step 2: year-anchored sweep (ALL companies)

**search:** `{{disambiguated_name}} funding {{current_year}}`

extract from results:
- any rounds not found in step 1
- prioritize results from the current year — discard results older than 2 years unless nothing else exists
- for each: round type, amount, date, lead investor, source URL

**stop if:** combined with step 1 you have round + amount + date. proceed to step 5.

### step 3: crunchbase structured data (ALL companies)

**search:** `site:crunchbase.com {{disambiguated_name}} funding`

extract from results:
- total raised (all time)
- full round history (all rounds with dates and amounts if visible in snippet)
- most recent round type + amount + date
- investor names

note: crunchbase snippets often surface 2–3 rounds worth of data without clicking through. use what's visible.

**stop if:** you have a complete picture of most recent round + total raised. proceed to output.

### step 4: company self-announcement (ALL companies, when steps 1–3 return thin results)

**when:** steps 1–3 returned fewer than 2 results, OR round amount/date still unconfirmed.

**search:** `site:{{domain}} funding OR raised OR investors OR "we raised"`

extract from results:
- the company's own blog post or newsroom announcement about a raise
- round type, amount, exact date, investor names as stated by the company itself
- use-of-funds language — company blog posts almost always state what the money is for

**if domain anchor returns nothing:**

**search:** `{{disambiguated_name}} "we raised" OR "excited to announce" OR "proud to announce" funding`

this catches companies that announced on Medium, Substack, or a third-party platform instead of their own domain.

note: for T3 companies with minimal press coverage, this is often the only primary source. a $2.5M seed may not get TechCrunch coverage but the founders almost always post about it somewhere.

**stop if:** you found the announcement with amount + date confirmed.

### step 5: press release (T1–T2, or when use-of-funds is needed)

**when:** Tier 1 or Tier 2, AND use-of-funds field is still empty after steps 1–4.

**search:** `site:prnewswire.com OR site:businesswire.com {{disambiguated_name}} funding raises`

extract from results:
- official announcement language about what they plan to do with the capital
- exact quote about use of funds if available (e.g. "will use the funding to expand internationally and double its R&D team")
- confirm or correct round amount + date from official source

note: press releases reliably state use-of-funds. this is the single best source for that field.

### step 6: techcrunch coverage (T1–T2 only)

**when:** Tier 1 or Tier 2 only.

**search:** `site:techcrunch.com {{disambiguated_name}} raises OR funding`

extract from results:
- CEO/founder quotes about what the money is for
- any round details not yet confirmed
- context on growth trajectory or reason for raising

skip if the company is small, bootstrapped, or Tier 3.

### step 7: tracxn structured history (enrichment, any tier)

**when:** you want a clean round history (all rounds, dates, amounts) or step 3 returned thin crunchbase data.

**search:** `site:tracxn.com {{disambiguated_name}} funding`

extract from results:
- full funding history with round types, dates, amounts
- valuation if disclosed
- investor list

### step 8: wellfound fallback (T3 only)

**when:** Tier 3 only AND steps 1–3 returned fewer than 2 results.

**search:** `site:wellfound.com {{disambiguated_name}} funding`

extract from results:
- funding stage (pre-seed / seed / series a)
- amount if disclosed
- investor names

if even this returns nothing: output "no funding data found" — that IS the finding for very early or bootstrapped companies. it is not a failure.

### step 9: domain anchor fallback (T3 only, ambiguous name)

**when:** Tier 3 AND name is ambiguous AND steps 1–3 returned polluted results about the wrong company.

**search:** `{{domain}} funding OR investment OR investors`

extract from results:
- any funding mentions tied directly to the domain
- use domain to anchor results to the correct company

---

## stale funding handling

if the most recent round found is **2+ years old**, output it with the recency flag set to `2+ years` and add a note:

> "Last publicly disclosed round was [round] in [year]. Company may be bootstrapped, profitable, or preparing for an exit. No recent funding activity found."

this is a valid, complete output — not a failure to retry.

---

## do not search

- `site:reddit.com {{name}} funding` — zero results universally
- `{{name}} venture capital OR investors OR VC backed` — too generic; surfaces VC industry content, not company-specific data
- `{{name}} "use of proceeds" OR "will use" OR "plans to use" funding` — SEC/formal language; startup announcements don't use these exact phrases
- `site:zoominfo.com OR site:crunchbase.com {{name}} funding investors` — ZoomInfo doesn't surface funding snippets; use crunchbase alone (step 3)
- `{{name}} investment round announcement` — generic; fully covered by steps 1 and 2
- `{{name}} "million" OR "billion" raised funding` — pollutes with historical rounds and VC-category content for small companies

---

## output

```
## funding — {{company_name}}

**most recent round:** [Series X / Seed / Bridge / Growth / Debt / undisclosed]
**amount:** [$Xm / $Xbn / undisclosed]
**date:** [exact date e.g. "Jun 12, 2024", or "date unknown"]
**lead investor(s):** [name(s), or "undisclosed"]
**participating investors:** [comma-separated, or "not disclosed"]
**use of funds:** [what they said they'd do with the capital, 1-2 sentences, or "not stated"]
**recency:** [< 3 months / 3–12 months / 1–2 years / 2+ years / no recent funding found]
**source:** [url of primary announcement]

**total raised (all time):** [$Xm / $Xbn, if available from step 3 or 7]
**round history:** [Seed $Xm (YYYY) → Series A $Xm (YYYY) → ... or "unavailable"]
```

---

## ground truth (validated 2026-04-06)

| company | tier | most recent round | amount | date |
|---------|------|-------------------|--------|------|
| OpenAI | T1 | funding round | $122B | Mar 2026 |
| Stripe | T1 | Series I | $694M | Apr 2024 |
| Cohere | T1 | Series D | $100M | Sep 2025 |
| ElevenLabs | T2 | funding round | $500M | Feb 2026 |
| Harvey AI | T2 | growth round | $200M | Mar 2026 |
| Cursor | T2 | Series D | $2.3B | Nov 2025 |
| Lovable | T2 | Series B | $330M | Dec 2025 |
| Clay | T2 (ambiguous) | Series C | $100M | Aug 2025 |
| Glean | T2 | Series F | $150M | Jun 2025 |
| Mistral AI | T2 | Series C | $2B | Sep 2025 |
| Infisical | T3 | Series A | $16M | Jun 2025 |
| Mintlify | T3 | Series A | $18.5M | Sep 2024 |
| Doola | T3 | strategic round | $8M | Jan 2024 |
| Nango | T3 | Seed | $2.5M | Apr 2023 |
| Basecamp | T3 | Series B | undisclosed | 2015 — stale/self-funded |
