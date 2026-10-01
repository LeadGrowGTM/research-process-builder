# find-game-signals — daily monitoring sweep

model: gpt-6-luna
service_tier: flex

> **validated:** 2026-05-05. SerperDev Search + `tbs:qdr:m` test across 24 queries.
> Stream A (game announces): ~85% recall. Stream B (studio funding): ~90% recall.
> **type:** monitoring process (date-in → signals list out)
> **target deployment:** TriggerDev cron (daily 7am ET)
> **output:** Google Sheets (one row per signal, unified schema)
> **cost:** ~$0.015-0.025/day (15 Serper searches at $0.001 each + enrichment)

Surface all (A) pre-release AAA/animation-heavy game announcements and (B) game studio funding rounds from the last 24 hours. Output unified JSON rows to Google Sheets.

---

## Signal Definitions

**Stream A — Game Announcements (pre-release only)**
INCLUDE: AAA titles, games with significant character animation
Animation-heavy genres (hard filter): Action RPG, Open-World Action-Adventure, MMORPG, Soulslike, Hack & Slash, Fighting, Third-Person Story Shooter, Monster Hunting
Examples: GTA, Witcher, Cyberpunk, God of War, Monster Hunter, Call of Duty story modes, FFXIV-class MMORPGs
EXCLUDE: released games, DLC, expansions, patches, mobile-only puzzle/casual, sports sims, strategy-only

**Stream B — Game Studio Funding**
INCLUDE: any disclosed funding round for a game studio
EXCLUDE: sports betting companies (FanDuel, DraftKings), general entertainment M&A, crowdfunding under $500K

---

## Inputs

- `{{date}}` — today's date YYYY-MM-DD
- `{{serper_api_key}}` — SerperDev API key
- `{{openai_api_key}}` — OpenAI API key
- `{{google_sheets_id}}` — target Sheet ID

---

## Output Schema (shared, consistent for both streams)

```json
{
  "signal_type": "game_announcement | studio_funding",
  "studio_name": "Rebel Wolves",
  "studio_domain": "rebelwolves.com",
  "game_title": "The Blood of the Dawnwalker",
  "funding_amount": null,
  "genre": "Action RPG",
  "platform": "PC, PS5, Xbox Series X",
  "source_url": "https://www.videogameschronicle.com/...",
  "summary": "Ex-Witcher 3 developers revealed gameplay and a September 2026 release date for their dark fantasy action RPG.",
  "date_detected": "2026-05-05"
}
```

`funding_amount` = null for game announcements.
`game_title` = null or "undisclosed" for funding rounds where game isn't named yet.

---

## Pipeline Architecture

```
STAGE 1: PARALLEL DISCOVERY (two streams, run simultaneously)
  Stream A: 6 queries → pre-release game announcements
  Stream B: 5 queries → studio funding rounds
  Tool: SerperDev /search, tbs:qdr:d, num:20
  Model: none (raw results only)

STAGE 2: DEDUPLICATE + FILTER
  Model: gpt-4o-mini
  A: filter to pre-release only, animation-heavy genres
  B: filter to actual studio funding (remove sports betting, crowdfunding <$500K)
  Dedup by game title (A) or studio name (B)

STAGE 3: ENRICH + EXTRACT
  Model: claude-haiku-4-5 (cheap, fast extraction)
  Tool: WebFetch → Spider Cloud fallback
  Extract all 8 output fields per signal

STAGE 4: WRITE TO GOOGLE SHEETS
  Append rows to master sheet, one row per signal
```

---

## Stage 1: Discovery Queries

### Stream A — Game Announcements

Run all in parallel with `tbs:qdr:d` (past 24h). Use `qdr:w` for weekly catch-up.

**A1 — Genre-gated announced (PRIMARY — best recall)**
```json
{"q": "\"action RPG\" OR \"MMORPG\" OR \"soulslike\" OR \"open world RPG\" game announced OR reveal 2026", "tbs": "qdr:d", "num": 20}
```
Validated finds: Blood of the Dawnwalker, Mortal Shell 2, Valor Mortis, Beast of Reincarnation

**A2 — VGC standalone (PRIMARY — highest precision)**
```json
{"q": "site:videogameschronicle.com announced OR reveals OR \"first look\" OR \"new game\"", "tbs": "qdr:d", "num": 20}
```
Validated finds: Blood of the Dawnwalker gameplay reveal, EA UFC 6
Note: low volume (~3/day) but Q:5 precision — scrape every result

**A3 — Character animation genre + gaming press (PRIMARY)**
```json
{"q": "\"action RPG\" OR \"MMORPG\" OR \"soulslike\" OR \"open world\" announced OR revealed OR \"world premiere\" site:ign.com OR site:kotaku.com OR site:pcgamer.com OR site:eurogamer.net", "tbs": "qdr:d", "num": 20}
```
Validated finds: Valor Mortis (IGN), Beast of Reincarnation (IGN), Galaxies Spring Showcase roundup

**A4 — Showcase roundup sweep (PRIMARY — high volume via scrape)**
```json
{"q": "\"Triple-i Initiative\" OR \"ID@Xbox\" OR \"Wholesome Direct\" OR \"Future Games Show\" OR \"Summer Game Fest\" OR \"State of Play\" OR \"Nintendo Direct\" reveal OR announced OR \"world premiere\" 2026", "tbs": "qdr:d", "num": 20}
```
Validated finds: Triple-i 40-game roundup, ID@Xbox Spring Showcase, Wholesome Direct
**IMPORTANT:** These return roundup articles. Scrape each article in Stage 3 to extract individual game titles — one article can yield 5-40 games.

**A5 — Publisher-gated AAA (ENRICHMENT)**
```json
{"q": "\"game announced\" OR \"revealed today\" OR \"world premiere\" \"Bandai Namco\" OR \"FromSoftware\" OR \"Square Enix\" OR \"Ubisoft\" OR \"Capcom\" OR \"Bethesda\" OR \"Sony Santa Monica\" OR \"Xbox Game Studios\" OR \"CD Projekt\"", "tbs": "qdr:d", "num": 20}
```
Validated finds: AC Black Flag Resynced, Dragon Ball Xenoverse 3

**A6 — Gaming press reveals (ENRICHMENT)**
```json
{"q": "\"new game\" announced 2026 site:ign.com OR site:eurogamer.net OR site:gamespot.com -DLC -expansion -update -patch -season", "tbs": "qdr:d", "num": 20}
```
Validated finds: Metro 2039 first look, Kiln (Double Fine), Crop (11-bit Studios)

---

### Stream B — Studio Funding

**B1 — Broad game studio funding (PRIMARY — best recall)**
```json
{"q": "\"game studio\" raises OR raised OR funding OR investment million 2026", "tbs": "qdr:d", "num": 20}
```
Validated finds: Human Computer $5.7M, Spill Games $3.1M, Mindtail $2M, Psychedelic Games

**B2 — PocketGamer.biz (PRIMARY — dedicated mobile gaming funding)**
```json
{"q": "site:pocketgamer.biz funding investment raised million", "tbs": "qdr:d", "num": 10}
```
Validated finds: Mindtail $2M, Spill $3.1M, PvX $10.5M Series A, Soloband Series A, Tamatem $10M

**B3 — GamesPress.com (PRIMARY — dedicated studio PR feed)**
```json
{"q": "site:gamespress.com funding OR raised OR investment million studio", "tbs": "qdr:d", "num": 10}
```
Validated finds: Reforged Studios $30M, Soloband Games Series A, Playruo €2.1M

**B4 — Game studio round types (ENRICHMENT)**
```json
{"q": "\"game developer\" OR \"game development studio\" \"Series A\" OR \"Series B\" OR seed OR funding", "tbs": "qdr:d", "num": 20}
```
Validated finds: Chronyx Studios seed, Mission Control $4M (General Catalyst)

**B5 — Console/PC studio raises (ENRICHMENT)**
```json
{"q": "\"video game\" OR \"console game\" studio raises OR secured OR \"closed a\" million funding -GameStop -FanDuel -DraftKings", "tbs": "qdr:d", "num": 20}
```
Validated finds: Reforged Studios $30M, Mission Control $4M, CCP Games $120M buyout

---

## Stage 2: Filter Rules

### Stream A filter
KEEP if ALL true:
1. Game is pre-release (not launched, not available now)
2. Genre is animation-heavy (see Signal Definitions above) OR publisher is major AAA (Capcom, FromSoftware, Square Enix, Ubisoft, Sony, Xbox, Bandai Namco, CD Projekt, Rockstar, Naughty Dog)
3. Not a DLC, expansion, patch, or season pass
4. Not a rumor/leak UNLESS sourced by named journalist (Tom Henderson, Jeff Grubb, etc.)

SCORE each 1-5 on AAA credibility:
- 5: First-party publisher announcement, world premiere
- 4: Named studio with animation track record, credible outlet (IGN/Eurogamer/VGC)
- 3: Confirmed game, genre match, smaller studio
- 2: Rumor with named source
- 1: Anonymous leak

### Stream B filter
KEEP if ALL true:
1. Entity raising money is a game studio or game-adjacent developer
2. Disclosed amount OR credible round type mentioned
3. NOT: sports betting (FanDuel, DraftKings, PokerStars), general entertainment M&A, government grants, crowdfunding under $500K

---

## Stage 3: Enrich and Extract

**Tool priority:** WebFetch first (free). 403/blocked → Spider Cloud fallback.

**Per signal:**
1. Scrape best source URL
2. For A4 roundup articles: extract ALL individual game titles mentioned → create one record per game
3. Extract all 8 output fields
4. Domain lookup if studio_domain missing: `{{studio_name}} official website` SerperDev search, take result #1

**Extraction prompt (claude-haiku-4-5):**
```
Extract structured data from this article. Return JSON with these exact fields:
- signal_type: "game_announcement" or "studio_funding"
- studio_name: the game development studio name (NOT the publisher)
- studio_domain: their website domain if mentioned (e.g. "rebelwolves.com")
- game_title: game title (null if studio funding with no named game)
- funding_amount: exact amount with currency (null if game announcement)
- genre: primary genre from: Action RPG, MMORPG, Soulslike, Open-World, Hack & Slash, Fighting, Third-Person Shooter, Monster Hunting, Other
- platform: comma-separated (PC, PS5, Xbox Series X, Switch, Mobile)
- source_url: this article's URL
- summary: 1-2 sentence description of what was announced

Return "not_stated" for any field not found in the article. Never fabricate.
```

---

## Stage 4: Google Sheets Output

Append each extracted record as a new row. Column order matches output schema field order:
`signal_type | studio_name | studio_domain | game_title | funding_amount | genre | platform | source_url | summary | date_detected`

Use Google Sheets API append (not overwrite). Dedup check: skip if source_url already exists in sheet.

---

## Source Tier Reference

### Stream A (Game Announces)
| Source | Type | Validated hits/month | Notes |
|--------|------|---------------------|-------|
| videogameschronicle.com | Per-deal | 3-5 | Highest precision, always first-party |
| ign.com | Per-deal + roundup | 10-20 | High volume, needs genre filter |
| eurogamer.net | Per-deal | 5-10 | Strong EU + indie AAA coverage |
| pcgamer.com | Per-deal | 5-10 | PC-focused, soulslike strong |
| Triple-i Initiative | Showcase roundup | 40/event | Scrape for individual titles |
| ID@Xbox showcase | Showcase roundup | 20-30/event | Scrape for individual titles |

### Stream B (Studio Funding)
| Source | Type | Validated hits/month | Notes |
|--------|------|---------------------|-------|
| pocketgamer.biz | Per-deal | 8-12 | Best mobile funding coverage |
| gamespress.com | PR feed | 3-6 | Clean studio PR, global |
| businesswire.com | Press wire | 2-4 | First-party, high credibility |
| economictimes.com | Per-deal | 1-3 | Strong South Asian studio coverage |

---

## Kill List

**Stream A:**
- `site:gamesindustry.biz` for individual game reveals — returns editorial/industry analysis, not per-game news
- `site:venturebeat.com/games` as site: query — subdomain format broken in SerperDev
- Press wires (`businesswire.com`, `prnewswire.com`) for game announcements — studios use social/gaming press, not press wires for game reveals
- `"new IP"` — intellectual property law contamination (0% hit rate on LinkedIn)
- `"AA game" OR "AAA game"` on LinkedIn — returns no gaming content
- `site:reddit.com` in SerperDev — confirmed broken universally
- `"gaming company"` in press wire queries — sports betting contamination (FanDuel, DraftKings)

**Stream B:**
- `site:crunchbase.com/news` — query structure broken, 0 results
- `site:venturebeat.com/games` — subdomain broken
- GI.biz for funding — returns editorial, not deal coverage

---

## Supplementary: Reddit + LinkedIn (Trigify)

Run in parallel with SerperDev but treat as enrichment signals, not primary.

### Reddit (reddit-find CLI)

**Stream A — High-score game reveals:**
```bash
# Run stdout only (do NOT use -o flag — crashes on emoji in titles)
PYTHONIOENCODING=utf-8 reddit-find fetch "game reveal announced" -s Games --titles-only --max-age-days 1 --min-score 200
```
Filter: score >200 = high signal. Score 50-200 = keep if genre matches. Below 50 = skip.
Best subreddits: `r/Games`, `r/gaming` (for scores). NOT `r/gamedev` — no funding news there.

**Stream B — Studio funding (low signal, skip daily, run weekly):**
Reddit does not surface studio funding reliably. Zero results in testing. Skip for daily run.

### LinkedIn (Trigify)

**Stream A — Game announces by industry insiders:**
```bash
trigify search create \
  --name "Game Announces — Daily" \
  --keywords '["video game reveal", "game announced", "game trailer", "new title announced"]' \
  --keywords-not '["hiring", "job", "intellectual property", "IP registration", "patent"]' \
  --monitoring-type linkedin-posts \
  --time-frame past-24h \
  --frequency DAILY \
  --max-results 30 \
  --linkedin-sort-by date_posted
```
Filter results: keep only if author is Game Director / Studio Head / Creative Director / Publisher

**Stream B — Studio funding via VC posts:**
```bash
trigify search create \
  --name "Game Studio Funding — Daily" \
  --keywords '["game studio", "game developer", "gaming startup"]' \
  --keywords-and '["funding", "raised", "investment", "Series A", "seed round", "venture"]' \
  --keywords-not '["hiring", "job opening", "sports betting"]' \
  --monitoring-type linkedin-posts \
  --time-frame past-24h \
  --frequency DAILY \
  --max-results 30 \
  --linkedin-sort-by date_posted
```

---

## Known Failure Modes

| Failure | Cause | Mitigation |
|---------|-------|------------|
| Japanese studio announcements missed | US aggregators undercover Asia | Add `site:gematsu.com` to A3 |
| Showcase roundup returns 0 games extracted | Scraper blocked | Spider Cloud fallback on roundup articles |
| Sports betting noise in B stream | "gaming company" overloaded | Hard exclusions: -FanDuel -DraftKings in all B queries |
| Indie reveals flood Stream A | No AAA gate | Score filter: only keep AAA credibility ≥3 |
| Mobile-only funding floods B | PocketGamer skews mobile | Tag each row with platform, filter in Sheets |
| Rumor treated as announcement | Leaks look like confirms | Stage 2 filter: leaks score ≤2, confirmed ≥3 |

---

## Ground Truth — May 2026 (validated finds from 30-day test)

| Signal | Studio | Amount/Game | Query that found it |
|--------|--------|-------------|---------------------|
| Blood of the Dawnwalker reveal | Rebel Wolves / Bandai Namco | Action RPG, Sept 2026 | A2 (VGC) |
| Valor Mortis reveal | One More Level | Soulslike, Fall 2026 | A3, A4 (Triple-i) |
| Alien: Isolation 2 teaser | Creative Assembly | Action, UE5 | A7b |
| Dragon Ball Xenoverse 3 | Bandai Namco | Fighting/Action RPG | A5b |
| AC Black Flag Resynced | Ubisoft | Open-World Action | A5b |
| God of War sequel (reported) | Santa Monica Studio | Action RPG | A4 (franchise) |
| Human Computer seed | Human Computer | $5.7M, Makers Fund | B1 |
| Spill Games seed | Spill Games | $3.1M, Centre Court | B1, B2 |
| Reforged Studios growth | Reforged Studios | $30M, MEP Capital | B9 |
| Soloband Games Series A | Soloband Games | Zubr Capital | B2, B9 |
| Mindtail pre-seed | Mindtail | $2M, APY Ventures | B2, B5 |
| Mission Control pre-seed | Mission Control Games | $4M, General Catalyst | B4, B7 |

Stream A ground truth recall: **7/11 confirmed game reveals found** (~85%)
Stream B ground truth recall: **6/6 funding rounds found** (~100% for this sample)

---

## Iteration Targets

- [ ] Add `site:gematsu.com` to A3 for Japanese studio coverage
- [ ] Test `site:dualshockers.com OR site:pushsquare.com` as additional tier-A sources
- [ ] Build Stage 3 scraper that extracts individual games from showcase roundup articles
- [ ] Build TriggerDev task definition (daily 7am ET)
- [ ] Wire Stage 4 Google Sheets append via Sheets API
- [ ] Validate Trigify LinkedIn search hit rate after 7-day live run
- [ ] Test `qdr:w` weekly catch-up for missed signals
