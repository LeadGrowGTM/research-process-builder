# find portfolio company count for PE / investment firms

model: gpt-6-luna
service_tier: flex

determine how many portfolio companies a PE firm, roll-up, holding company, or investment entity currently owns. optimized for acquisition-heavy firms. correctly filters out non-investment entities.

## inputs

- `{{company_name}}` — the firm to research
- `{{website_url}}` — their website URL (if available)
- `{{current_year}}` — current year for recency queries

## preprocessing

### name disambiguation

if the firm name is 6 characters or fewer, a common word, or shares a name with something famous, add a category qualifier:

- `{{company_name}} private equity` or `{{company_name}} investment firm`
- or use the domain as anchor: `{{website_url}}`

disambiguation is the #1 failure mode. short or common names return wrong entities without anchoring.

## steps

### step 1: visit the firm's homepage

**action:** fetch `{{website_url}}` directly (do NOT rely on Google `site:` search — 30% of T3 firms aren't indexed)

**extract:**

- what the firm does (one sentence)
- firm type: PE, roll-up/consolidator, holding company, VC, startup studio, venture debt, accelerator, search fund, asset manager, or NOT an investment firm
- nav links to portfolio/companies page (look for: "Portfolio", "Companies", "Our Companies", "Our Brands", "Investments", "Acquisitions")
- any company names mentioned on the homepage (testimonials, logos, case studies)
- acquisition language: "we acquire", "sell your business", "partner with owners"

**stop if:** the entity is clearly NOT an investment/acquisition firm (healthcare provider, SaaS company, aircraft leasing, etc.). report `is_acquirer: false` and move on.

### step 2: fetch the portfolio/companies page

**when:** step 1 found a nav link to a portfolio or companies page

**action:** fetch that page directly and count individual companies listed by name

**extract:**

- count of companies listed
- each company name
- whether listings show "Current" vs "Realized/Exited" (only count current)
- "Platform" vs "Add-on/Bolt-on" distinctions if shown
- whether the list appears complete or curated ("select investments", "representative portfolio")

**stop if:** you have a list of individual companies counted by name. this is HIGH confidence. skip to output.

**handle:** some firms deliberately don't disclose portfolio companies as a matter of policy. this is a valid, useful data point. report it with confidence "low" and note the non-disclosure policy.

### step 3: google search for portfolio count

**search:** `{{company_name}} portfolio companies`

this is the single highest-value search.

**extract:**

- portfolio count if stated ("backed 80+ companies", "invested in 48 companies")
- distinguish "since inception" from "currently active"
- URLs for Crunchbase, Tracxn, PitchBook profiles (use in step 4 if needed)
- portfolio page URL if not found in step 1

**stop if:** you have a confirmed count from a credible source (firm's website, SEC filing, Crunchbase) that aligns with what you saw in steps 1-2.

### step 4: structured platform lookup

**when:** steps 1-3 returned thin results, OR you need to cross-reference

**search:** `site:zoominfo.com OR site:crunchbase.com OR site:pitchbook.com {{company_name}}`

**extract:**

- number of investments/portfolio companies from platform profiles
- firm classification (PE, VC, etc.)
- any deal count or exit count

**caution:** if the firm name is ambiguous, this will return multiple entities. use domain or category qualifier to disambiguate.

### step 5: acquisition-specific search (roll-ups only)

**when:** step 1 identified the firm as a roll-up/consolidator AND steps 2-4 didn't yield a count

**search:** `{{company_name}} acquisitions`

**extract:**

- individual deal announcements (each = one acquisition)
- total deal count if mentioned in press
- whether the firm is actively acquiring (recent deals in {{current_year}})

**also check:** testimonials on the homepage often contain acquired company names that appear in no other search.

## kill list

do NOT waste searches on these patterns:

| pattern                                                                 | why it fails                                                                 |
| ----------------------------------------------------------------------- | ---------------------------------------------------------------------------- |
| `"{{company_name}}" "our companies" OR "our brands" OR "our portfolio"` | exact match quotes + OR causes massive disambiguation failures.              |
| `site:linkedin.com/company "{{company_name}}"`                          | descriptions rarely shown in search snippets.                                |
| `{{company_name}} "number of investments" OR "number of portfolio"`     | too specific. nobody phrases it this way. returns nothing.                   |
| `{{company_name}} holdings subsidiaries`                                | returns legal filings and unrelated holding companies. noise.                |
| `{{company_name}} bolt-on acquisitions OR add-on acquisitions`          | too niche. only works for PE firms using this exact jargon. wastes a search. |

## output template

```json
{
  "firm_name": "official name from website",
  "website_url": "{{website_url}}",
  "website_accessible": true,
  "is_acquirer": true,
  "firm_type": "PE | Roll-up | Holding Company | VC | Startup Studio | Venture Debt | Accelerator | Search Fund | Asset Manager | Not an Investment Firm",
  // SEE DISAMBIGUATION BELOW
  "portfolio_count": 32,
  "portfolio_count_type": "active_current | total_since_inception | estimated | unknown",
  "platform_companies": null,
  "bolt_on_acquisitions": null,
  "actively_acquiring": true,
  "confidence": "high | medium | low | none",
  "confidence_reasoning": "counted 32 companies on portfolio page, confirmed by SEC filing",
  "notes": "venture debt lender — portfolio count represents borrowers, not equity holdings"
}
```

### firm type disambiguation

the #1 classification error is Roll-up vs Holding Company. use these signals:

**Roll-up / Consolidator:**

- acquires companies in ONE industry or vertical
- uses language: "consolidation platform", "sell your business to us", "partner with owners", "seamless exit"
- has acquisition criteria (revenue floor, geography, industry focus)
- has a Corp Dev / Business Development role on the team
- often PE-backed (a PE firm's portfolio company executing a roll-up strategy)
- acquired companies may keep their brand ("your name stays on the door")

**Holding Company:**

- owns companies across MULTIPLE unrelated industries
- long-term conglomerate hold, less operational integration
- diversified portfolio of businesses

**PE (fund structure):**

- raises capital from LPs, deploys across fund vintages
- has GP/LP structure, fund pages, investor relations
- if a PE firm is executing a roll-up through a portfolio company, classify that portfolio company as Roll-up

**rule of thumb:** if they acquire in ONE vertical = Roll-up. if they own across MANY verticals = Holding Company. if they have a fund structure with LPs = PE.

### confidence calibration

- **high:** counted individual companies by name on their website, or firm explicitly states the number and it aligns with visible listings.
- **medium:** number from Crunchbase/PitchBook/news article within last 12 months, or firm says "50+" but only lists some.
- **low:** inferred from partial data (testimonials, press releases), or firm deliberately doesn't disclose. also use for counts from articles older than 12 months.
- **none:** site broken, no data found anywhere, or entity couldn't be verified.

### special cases in output

- **non-investment firm:** `is_acquirer: false`, `portfolio_count: null`, `confidence: "high"`, notes explain what they actually are.
- **deliberate non-disclosure:** `portfolio_count: null`, `confidence: "low"`, notes explain the non-disclosure policy and link any platform profiles found.
- **venture debt / lending:** flag in notes that portfolio count represents borrowers, not equity-owned companies.
- **startup studio:** flag that count represents "companies launched/built", not acquired.
- **roll-up brands vs locations:** always count distinct brands/companies, NOT locations or franchise units.
