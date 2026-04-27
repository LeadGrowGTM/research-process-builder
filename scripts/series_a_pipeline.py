"""
Series A Daily Discovery Pipeline

Thin subclass of ResearchPipeline — defines Series-A-specific queries,
filter logic, and extraction prompt.

Four-stage pipeline:
  1. Parallel discovery via SerperDev (10 queries, search endpoint, tbs:qdr:d)
  2. Score, filter to Series A, dedup by company
  3. Enrich: scrape best source, extract structured fields, domain lookup
  4. Output CSV + JSON

Usage:
    py scripts/series_a_pipeline.py                     # full run, daily
    py scripts/series_a_pipeline.py --tbs qdr:w         # weekly catch-up
    py scripts/series_a_pipeline.py --stage 1            # discovery only
    py scripts/series_a_pipeline.py --stage 2            # score/filter only (reads stage 1 output)
    py scripts/series_a_pipeline.py --skip-enrich        # stages 1-2 + CSV (no scraping)
    py scripts/series_a_pipeline.py --dry-run            # preview queries
    py scripts/series_a_pipeline.py --date 2026-04-20    # run for specific date
"""

import re

from pipeline_base import ResearchPipeline
from domain_resolver import fuzzy_dedup_companies, names_are_similar

# ---------------------------------------------------------------------------
# Query Definitions (validated 2026-04-20, 88% GT hit rate)
# ---------------------------------------------------------------------------

AGENT_A_QUERIES = [
    {"id": "q3", "query": "site:thesaasnews.com Series A", "num": 30, "desc": "TheSaaSNews"},
    {"id": "q4", "query": "site:finsmes.com Series A", "num": 30, "desc": "FinSMEs"},
    {"id": "q5", "query": "site:alleywatch.com funding report", "num": 10, "desc": "AlleyWatch"},
    {"id": "q9", "query": "site:vcnewsdaily.com Series A", "num": 10, "desc": "VCNewsDaily"},
    {"id": "q10", "query": "site:infotechlead.com venture capital funding", "num": 10, "desc": "InfotechLead"},
]

AGENT_B_QUERIES = [
    {"id": "q1", "query": '"Series A" raises OR raised OR funding OR round million', "num": 30, "desc": "broad sweep"},
    {"id": "q2", "query": '"Series A" announces OR secures OR closes OR completes funding', "num": 20, "desc": "announcement language"},
    {"id": "q6", "query": '"Series A" site:businesswire.com OR site:prnewswire.com OR site:einpresswire.com', "num": 10, "desc": "press wires"},
    {"id": "q7", "query": '"led the round" OR "led the Series A" OR "led a" Series A investment startup', "num": 20, "desc": "VC language"},
    {"id": "q8", "query": '"Series A" startup funding site:eu-startups.com OR site:tech.eu OR site:techround.co.uk', "num": 10, "desc": "European"},
]

# ---------------------------------------------------------------------------
# Series A filter patterns
# ---------------------------------------------------------------------------

VC_PATTERNS = re.compile(
    r'\b(Capital|Ventures|Partners|Fund|Investment|Advisors|Management|'
    r'Sequoia|Andreessen|Bessemer|Greylock|Accel|Lightspeed|GV|YC|'
    r'a16z|Khosla|NEA|Insight|Tiger Global|Coatue|General Catalyst)\b',
    re.IGNORECASE
)

NON_SERIES_A = re.compile(
    r'\b(Series\s+[B-Z]|Pre-Seed|pre-seed|Pre-IPO|IPO|Debt|Grant|'
    r'acquisition|acquires|acquired|merger|SPAC|refinanc)',
    re.IGNORECASE
)

SOFT_NON_A = re.compile(r'\b(Seed|Growth)\b', re.IGNORECASE)

SERIES_A_PATTERN = re.compile(r'\bSeries\s+A\b', re.IGNORECASE)
AMOUNT_PATTERN = re.compile(r'[\$\u20ac\u00a3\u00a5]\s*[\d,.]+\s*[MBmb](?:illion)?|\d+\s*(?:million|billion)', re.IGNORECASE)

NOISE_PATTERNS = re.compile(
    r'(?:Series A activity|weekly recap|funding recap|venture market|job search|'
    r'quarterly.*dividend|financial results|earnings|stock|preferred stock|'
    r'broadband|announces common|\bTag\b\s*[-|]|\bTag\s*$)',
    re.IGNORECASE
)

TIER_S_DOMAINS = {
    "thesaasnews.com", "finsmes.com", "alleywatch.com", "infotechlead.com", "vcnewsdaily.com"
}
TIER_A_DOMAINS = {
    "businesswire.com", "prnewswire.com", "einpresswire.com", "ventureburn.com",
    "tech.eu", "eu-startups.com", "pulse2.com", "siliconangle.com"
}


def normalize_company_name(name: str) -> str:
    """Strip Inc/Ltd/Corp/etc and lowercase for dedup."""
    name = name.strip()
    name = re.sub(r'\s*[,.]?\s*\b(Inc|Ltd|Corp|LLC|GmbH|Co|PLC|SA|AG|BV|Pty|SAS|SRL)\b\.?\s*$', '', name, flags=re.IGNORECASE)
    name = re.sub(r'\s+Tag$', '', name, flags=re.IGNORECASE)
    name = re.sub(r'[\s,.\-:;]+$', '', name)
    return name.lower().strip()


def extract_company_name_from_title(title: str) -> str:
    """Best-effort company name extraction from article title."""
    m = re.match(r'^([A-Z][\w\s.&\'-]{1,40}?)\s+(?:raises?|secures?|closes?|announces?|gets?|lands?|nabs?|bags?|receives?|completes?)\b', title, re.IGNORECASE)
    if m:
        name = m.group(1).strip()
        if not VC_PATTERNS.search(name):
            return name

    m = re.search(r'(?:in|into|backs?|for)\s+([A-Z][\w\s.&\'-]{1,30}?)(?:\s*[,.]|\s+to\b|\s+for\b|$)', title)
    if m:
        name = m.group(1).strip()
        if not VC_PATTERNS.search(name):
            return name

    return ""


# ---------------------------------------------------------------------------
# Series A Pipeline
# ---------------------------------------------------------------------------

class SeriesAPipeline(ResearchPipeline):
    PIPELINE_NAME = "Series A Daily Pipeline"
    SUPABASE_TABLE = "funding_discoveries"
    OUTPUT_PREFIX = "series-a"
    QUERIES = AGENT_A_QUERIES + AGENT_B_QUERIES
    WEBHOOK_URL = "https://api.clay.com/v3/sources/webhook/pull-in-data-from-a-webhook-d1b53ce2-fe64-40e4-a86c-faef265c5a63"
    WEBHOOK_AUTH_TOKEN = "0be318b702699f40b68f"
    OUTPUT_FIELDNAMES = [
        "date", "company_name", "company_domain", "amount_raised", "round_type",
        "source_url", "lead_investors", "round_reasoning", "discovered_by", "source_count", "score"
    ]

    # --- Stage 2: Series-A-specific filter ---

    def score_and_filter(self, raw_results: list[dict]) -> dict:
        """Stage 2: Filter to Series A, dedup, score."""
        candidates = {}
        filtered_out = []

        for r in raw_results:
            title = r.get("title", "")
            snippet = r.get("snippet", "")
            combined = f"{title} {snippet}"
            url = r.get("source_url", "")
            domain = r.get("source_domain", "")

            # Noise filter
            if NOISE_PATTERNS.search(title):
                filtered_out.append({"title": title[:80], "reason": "noise (report/listicle/filing)", "url": url})
                continue

            title_has_series_a = bool(SERIES_A_PATTERN.search(title))
            title_has_hard_non_a = bool(NON_SERIES_A.search(title))
            title_has_soft_non_a = bool(SOFT_NON_A.search(title))

            has_series_a = bool(SERIES_A_PATTERN.search(combined))
            has_hard_non_a = bool(NON_SERIES_A.search(combined))

            if title_has_hard_non_a:
                filtered_out.append({"title": title[:80], "reason": "non-Series A in title", "url": url})
                continue

            if title_has_soft_non_a and not title_has_series_a:
                filtered_out.append({"title": title[:80], "reason": "Seed/Growth in title, no Series A", "url": url})
                continue

            if has_hard_non_a and not has_series_a:
                filtered_out.append({"title": title[:80], "reason": "non-Series A round detected", "url": url})
                continue

            if not has_series_a:
                if not re.search(r'(?:raises?|raised|secures?|closes?)\s+[\$\u20ac\u00a3]', combined, re.IGNORECASE):
                    filtered_out.append({"title": title[:80], "reason": "no Series A and no funding amount", "url": url})
                    continue

            # Extract company name
            company = extract_company_name_from_title(title)
            if not company:
                fallback = title.split(" - ")[0].split(" | ")[0]
                fallback = re.split(r'\s+(?:Raises?|Secures?|Closes?|Announces?)\b', fallback)[0]
                company = fallback.strip()[:50]

            company = re.sub(r'\s+Tag$', '', company, flags=re.IGNORECASE).strip()
            company = re.sub(r'^\[PDF\]\s*', '', company).strip()

            if not company or len(company) < 3:
                continue

            if company.lower() in {"u.s", "u.s.", "us", "series a", "series a funding", "funding", "startup", "the"}:
                continue

            if len(company) > 45:
                filtered_out.append({"title": title[:80], "reason": "company name too long (likely bad parse)", "url": url})
                continue

            needs_disambiguation = bool(VC_PATTERNS.search(company))

            amount_match = AMOUNT_PATTERN.search(combined)
            amount = amount_match.group(0) if amount_match else ""

            # Score source quality
            if domain in TIER_S_DOMAINS:
                source_quality = 4
            elif domain in TIER_A_DOMAINS:
                source_quality = 5
            elif "crunchbase" in domain or "techcrunch" in domain:
                source_quality = 3
            else:
                source_quality = 2

            # Score data completeness
            data_completeness = 1
            if company:
                data_completeness += 1
            if amount:
                data_completeness += 1
            if has_series_a:
                data_completeness += 1
            if re.search(r'(?:led by|investors?|participated)', combined, re.IGNORECASE):
                data_completeness += 1

            score = source_quality * data_completeness

            # Dedup by normalized company name
            norm = normalize_company_name(company)

            if norm not in candidates:
                candidates[norm] = {
                    "company_name": company.strip(),
                    "company_name_normalized": norm,
                    "amount": amount,
                    "round_type": "Series A" if has_series_a else "Unknown",
                    "needs_disambiguation": needs_disambiguation,
                    "sources": [],
                    "best_score": 0,
                    "best_source_url": "",
                }

            candidates[norm]["sources"].append({
                "url": url,
                "domain": domain,
                "score": score,
                "query_source": r.get("query_source", ""),
                "title": title[:100],
            })

            if score > candidates[norm]["best_score"]:
                candidates[norm]["best_score"] = score
                candidates[norm]["best_source_url"] = url
                if amount and not candidates[norm]["amount"]:
                    candidates[norm]["amount"] = amount

        # Sort by score descending
        companies = sorted(candidates.values(), key=lambda x: x["best_score"], reverse=True)

        # Fuzzy dedup: merge "Strider" + "Strider Technologies", etc.
        pre_fuzzy = len(companies)
        companies = fuzzy_dedup_companies(companies, name_key="company_name", score_key="best_score")
        if len(companies) < pre_fuzzy:
            print(f"\n  Fuzzy dedup: {pre_fuzzy} -> {len(companies)} companies")

        print(f"\n  Stage 2: {len(raw_results)} raw -> {len(companies)} companies (filtered {len(filtered_out)})")
        for c in companies:
            flag = " [VC?]" if c["needs_disambiguation"] else ""
            amt = (c['amount'] or '?').encode('ascii', 'replace').decode()
            name = c['company_name'].encode('ascii', 'replace').decode()
            print(f"    {name}{flag} - {amt} - score {c['best_score']} - {len(c['sources'])} sources")

        return {
            "companies": companies,
            "filtered_out": filtered_out,
            "stats": {
                "raw_count": len(raw_results),
                "company_count": len(companies),
                "filtered_count": len(filtered_out),
            }
        }

    # --- Stage 3: Series-A-specific extraction prompt ---

    def get_extraction_prompt(self, article_text: str, company_hint: str, amount_hint: str) -> list[dict]:
        return [
            {"role": "system", "content": "You extract structured funding data from articles. Return valid JSON only, no markdown fences, no explanation."},
            {"role": "user", "content": f"""Extract Series A funding data from this article.

Company hint: {company_hint}
Amount hint: {amount_hint}

Article:
{article_text[:8000]}

Return exactly this JSON:
{{"company_name": "...", "company_domain": "...", "amount_raised": "...", "lead_investors": "...", "round_reasoning": "..."}}

Rules:
- company_name = the company that RAISED money (NOT the investor/VC)
- company_domain = their ACTUAL official website domain (e.g. zenskar.com). "not_stated" if not clearly in article
- amount_raised = exact amount with currency symbol (e.g. "$15M", "EUR10M", "KRW 90B")
- lead_investors = who led the round, comma-separated. "not_stated" if unknown
- round_reasoning = why they raised / what funds are for, 1-2 sentences. "not_stated" if unknown
- If this is NOT actually a Series A funding announcement, set company_name to "NOT_SERIES_A"

CRITICAL — company_domain must be the company's OWN website. NEVER return:
- The article source domain (e.g. infomoney.com, thesaasnews.com, finsmes.com)
- CDN domains (cdninstagram.com, amazonaws.com, cloudfront.net, filerobot.com)
- Data platforms (dealroom.co, crunchbase.com, pitchbook.com, tracxn.com)
- News/media sites (economictimes.com, statnews.com, technews180.com, investing.com)
- Social media (linkedin.com, twitter.com, instagram.com)
- Short URLs (t.co, bit.ly)
If you cannot find the company's actual website in the article, return "not_stated" — do NOT guess"""},
        ]

    def post_extract_filter(self, extracted: dict) -> bool:
        """Filter out results GPT identifies as not Series A."""
        if extracted.get("company_name") == "NOT_SERIES_A":
            return False
        return True

    # --- Stage 3: Series-A-specific enriched record ---

    def build_enriched_record(self, company: dict, extracted, domain: str, source_url: str) -> dict:
        return {
            "company_name": extracted.get("company_name", company["company_name"]) if extracted else company["company_name"],
            "company_domain": domain,
            "amount_raised": extracted.get("amount_raised", company.get("amount", "")) if extracted else company.get("amount", ""),
            "round_type": company.get("round_type", "Series A"),
            "source_url": source_url,
            "lead_investors": extracted.get("lead_investors", "not_stated") if extracted else "not_stated",
            "round_reasoning": extracted.get("round_reasoning", "not_stated") if extracted else "not_stated",
            "source_count": len(company["sources"]),
            "score": company["best_score"],
            "discovered_by": ",".join(set(s["query_source"] for s in company["sources"])),
        }

    def build_skip_enrich_record(self, company: dict) -> dict:
        return {
            "company_name": company["company_name"],
            "company_domain": "not_enriched",
            "amount_raised": company.get("amount", ""),
            "round_type": company.get("round_type", "Series A"),
            "source_url": company["best_source_url"],
            "lead_investors": "not_enriched",
            "round_reasoning": "not_enriched",
            "source_count": len(company["sources"]),
            "score": company["best_score"],
            "discovered_by": ",".join(set(s["query_source"] for s in company["sources"])),
        }

    # --- Supabase schema ---

    def get_supabase_schema_sql(self) -> str:
        return """
    -- See trigger/supabase/migrations/001_unified_funding_discoveries.sql
    -- All rounds now use the unified funding_discoveries table
    """

    def get_supabase_row(self, record: dict, date_str: str) -> dict:
        return {
            "discovered_date": date_str,
            "company_name": record.get("company_name", ""),
            "company_domain": record.get("company_domain", ""),
            "amount_raised": record.get("amount_raised", ""),
            "round_type": record.get("round_type", "Series A"),
            "source_url": record.get("source_url", ""),
            "lead_investors": record.get("lead_investors", "not_stated"),
            "round_reasoning": record.get("round_reasoning", "not_stated"),
            "discovered_by": record.get("discovered_by", ""),
            "discovered_by_pipeline": self.PIPELINE_NAME,
            "source_count": record.get("source_count", 1),
            "score": record.get("score", 0),
            "pipeline_version": "1.0",
        }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    pipeline = SeriesAPipeline()
    pipeline.run()
