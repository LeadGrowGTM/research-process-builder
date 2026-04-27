"""
ResearchPipeline — reusable base class for 4-stage research pipelines.

Stages:
  1. Discover: parallel SerperDev queries
  2. Filter: score, filter, dedup (process-specific — subclasses override)
  3. Enrich: scrape best source, GPT extraction, domain lookup
  4. Output: CSV + JSON + optional Supabase push

Subclasses define:
  - QUERIES: list of query dicts
  - PIPELINE_NAME: human-readable name
  - SUPABASE_TABLE: target table name
  - OUTPUT_PREFIX: file prefix (e.g. "series-a")
  - OUTPUT_FIELDNAMES: CSV column order
  - score_and_filter(): stage 2 logic
  - get_extraction_prompt(): GPT prompt for stage 3
  - post_extract_filter(): optional post-GPT filter (e.g. "NOT_SERIES_A" check)
"""

import json
import sys
import os
import re
import csv
import time
import argparse
import concurrent.futures
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional

from domain_resolver import (
    resolve_domain as _resolve_domain_waterfall,
    validate_domain,
    normalize_domain as _normalize_domain_resolver,
    detect_industry,
    fuzzy_dedup_companies,
    names_are_similar,
    match_existing_company,
)

SCRIPT_DIR_EARLY = Path(__file__).resolve().parent

from dotenv import load_dotenv
load_dotenv(SCRIPT_DIR_EARLY.parent / ".env")
load_dotenv(Path.home() / ".env", override=False)

_shared = os.environ.get("SHARED_SCRIPTS_PATH", str(SCRIPT_DIR_EARLY))
sys.path.insert(0, _shared)

import serper_search
import requests

SCRIPT_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = SCRIPT_DIR.parent / "output"
STAGE_DIR = SCRIPT_DIR.parent / "output" / "stages"

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
SPIDER_API_KEY = os.getenv("SPIDER_API_KEY")
SUPABASE_URL = os.getenv("SUPABASE_PROJECT_URL") or os.getenv("SUPABASE_URL")
if SUPABASE_URL and not SUPABASE_URL.startswith("http"):
    SUPABASE_URL = None
SUPABASE_KEY = os.getenv("SUPABASE_KEY") or os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_ANON_KEY")


class ResearchPipeline:
    """
    Base class for 4-stage research pipelines.

    Subclasses MUST define:
      - QUERIES: list[dict] with keys {id, query, num, desc}
      - PIPELINE_NAME: str
      - SUPABASE_TABLE: str
      - OUTPUT_PREFIX: str (used in filenames like "{prefix}-{date}.csv")
      - OUTPUT_FIELDNAMES: list[str]
      - score_and_filter(raw_results) -> dict with "companies" and "filtered_out"
      - get_extraction_prompt(article_text, company_hint, amount_hint) -> list[dict] (OpenAI messages)

    Subclasses MAY override:
      - post_extract_filter(extracted) -> bool  (return True to KEEP the record)
      - build_enriched_record(company, extracted, domain, source_url) -> dict
      - build_skip_enrich_record(company) -> dict
      - get_supabase_row(record, date_str) -> dict
      - get_supabase_schema_sql() -> str
      - add_arguments(parser) -> None  (add subclass-specific CLI args)
      - get_pipeline_version() -> str
    """

    # --- Subclass config (override these) ---
    QUERIES: list[dict] = []
    PIPELINE_NAME: str = "Research Pipeline"
    SUPABASE_TABLE: str = "funding_discoveries"
    OUTPUT_PREFIX: str = "research"
    OUTPUT_FIELDNAMES: list[str] = []
    WEBHOOK_URL: str = ""
    WEBHOOK_AUTH_TOKEN: str = ""

    # -----------------------------------------------------------------------
    # STAGE 1: Discovery (generic)
    # -----------------------------------------------------------------------

    def run_single_query(self, qdef: dict, tbs: str) -> dict:
        """Run one SerperDev query and return structured results."""
        original_num = serper_search.DEFAULT_NUM_RESULTS
        serper_search.DEFAULT_NUM_RESULTS = qdef["num"]

        try:
            raw = serper_search.search(query=qdef["query"], news=False, tbs=tbs)
        except Exception as e:
            serper_search.DEFAULT_NUM_RESULTS = original_num
            return {"query_id": qdef["id"], "desc": qdef["desc"], "error": str(e), "results": []}
        finally:
            serper_search.DEFAULT_NUM_RESULTS = original_num

        items = raw.get("organic", [])
        results = []
        for item in items:
            results.append({
                "company_name_raw": "",
                "amount_raw": "",
                "round_type_raw": "",
                "source_url": item.get("link", ""),
                "source_domain": item.get("link", "").split("/")[2] if "://" in item.get("link", "") else "",
                "snippet": item.get("snippet", "")[:300],
                "title": item.get("title", ""),
                "query_source": qdef["id"],
            })

        return {
            "query_id": qdef["id"],
            "desc": qdef["desc"],
            "result_count": len(results),
            "results": results,
        }

    def run_discovery(self, tbs: str, dry_run: bool = False) -> list[dict]:
        """Stage 1: Run all queries in parallel."""
        all_queries = self.QUERIES

        if dry_run:
            for q in all_queries:
                print(f"  [DRY] {q['id']}: {q['desc']} (num={q['num']})")
            return []

        all_results = []

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            futures = {executor.submit(self.run_single_query, q, tbs): q for q in all_queries}
            for future in concurrent.futures.as_completed(futures):
                qdef = futures[future]
                result = future.result()
                count = result.get("result_count", 0)
                err = result.get("error", "")
                status = f"ERROR: {err}" if err else f"{count} results"
                print(f"  [{result['query_id']}] {result['desc']}: {status}")
                all_results.extend(result.get("results", []))

        print(f"\n  Stage 1 total: {len(all_results)} raw results from {len(all_queries)} queries")
        return all_results

    # -----------------------------------------------------------------------
    # STAGE 2: Score & Filter (subclass MUST override)
    # -----------------------------------------------------------------------

    def score_and_filter(self, raw_results: list[dict]) -> dict:
        """
        Stage 2: Filter raw results to relevant items, dedup, score.

        Must return dict with:
          - "companies": list[dict] — each with at minimum:
              company_name, best_score, best_source_url, sources, amount (optional)
          - "filtered_out": list[dict]
          - "stats": dict with raw_count, company_count, filtered_count
        """
        raise NotImplementedError("Subclasses must implement score_and_filter()")

    # -----------------------------------------------------------------------
    # STAGE 3: Enrich & Extract (generic with hooks)
    # -----------------------------------------------------------------------

    def fetch_url(self, url: str) -> Optional[str]:
        """Fetch URL content. Spider Cloud primary, requests fallback."""
        if SPIDER_API_KEY:
            try:
                resp = requests.post(
                    "https://api.spider.cloud/crawl",
                    headers={
                        "Authorization": f"Bearer {SPIDER_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json={"url": url, "limit": 1, "return_format": "markdown"},
                    timeout=20,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    content = ""
                    if isinstance(data, list) and data:
                        content = data[0].get("content", "")
                    elif isinstance(data, dict):
                        content = data.get("content", "")
                    if content and len(content) > 200:
                        return content[:15000]
            except Exception:
                pass

        try:
            resp = requests.get(url, timeout=10, headers={
                "User-Agent": "Mozilla/5.0 (compatible; LeadGrow/1.0)"
            })
            if resp.status_code == 200 and len(resp.text) > 200:
                return resp.text[:15000]
        except Exception:
            pass

        return None

    def get_extraction_prompt(self, article_text: str, company_hint: str, amount_hint: str) -> list[dict]:
        """
        Return OpenAI messages list for GPT extraction.
        Subclasses override this to customize the extraction prompt.
        """
        raise NotImplementedError("Subclasses must implement get_extraction_prompt()")

    def extract_with_openai(self, article_text: str, company_hint: str, amount_hint: str) -> Optional[dict]:
        """Use GPT-4o-mini to extract structured data from article text."""
        if not OPENAI_API_KEY:
            return None

        messages = self.get_extraction_prompt(article_text, company_hint, amount_hint)

        try:
            resp = requests.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENAI_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "gpt-4o-mini",
                    "temperature": 0,
                    "max_tokens": 500,
                    "messages": messages,
                },
                timeout=30,
            )
            if resp.status_code == 200:
                content = resp.json()["choices"][0]["message"]["content"]
                content = content.strip()
                if content.startswith("```"):
                    content = re.sub(r'^```(?:json)?\s*', '', content)
                    content = re.sub(r'\s*```$', '', content)
                return json.loads(content)
        except Exception as e:
            print(f"    OpenAI extraction error: {e}")

        return None

    def lookup_domain(self, company_name: str, source_url: str = "", article_text: str = None, industry: str = "") -> str:
        """Resolve company domain via unified 3-tier waterfall + optional agent fallback."""
        result = _resolve_domain_waterfall(
            company_name=company_name,
            source_url=source_url,
            article_text=article_text,
            industry=industry,
            use_agent_fallback=getattr(self, '_use_domain_agent', False),
        )
        return result["domain"]

    def post_extract_filter(self, extracted: dict) -> bool:
        """Return True to KEEP the record, False to filter it out. Override in subclass."""
        return True

    def build_enriched_record(self, company: dict, extracted: Optional[dict], domain: str, source_url: str) -> dict:
        """
        Build the final enriched record dict. Override in subclass for custom fields.
        Default implementation covers common funding pipeline fields.
        """
        return {
            "company_name": extracted.get("company_name", company["company_name"]) if extracted else company["company_name"],
            "company_domain": domain,
            "amount_raised": extracted.get("amount_raised", company.get("amount", "")) if extracted else company.get("amount", ""),
            "round_type": company.get("round_type", "Unknown"),
            "source_url": source_url,
            "lead_investors": extracted.get("lead_investors", "not_stated") if extracted else "not_stated",
            "round_reasoning": extracted.get("round_reasoning", "not_stated") if extracted else "not_stated",
            "source_count": len(company["sources"]),
            "score": company["best_score"],
            "discovered_by": ",".join(set(s["query_source"] for s in company["sources"])),
        }

    def build_skip_enrich_record(self, company: dict) -> dict:
        """Build a record when --skip-enrich is used. Override for custom fields."""
        return {
            "company_name": company["company_name"],
            "company_domain": "not_enriched",
            "amount_raised": company.get("amount", ""),
            "round_type": company.get("round_type", "Unknown"),
            "source_url": company["best_source_url"],
            "lead_investors": "not_enriched",
            "round_reasoning": "not_enriched",
            "source_count": len(company["sources"]),
            "score": company["best_score"],
            "discovered_by": ",".join(set(s["query_source"] for s in company["sources"])),
        }

    @staticmethod
    def clean_article_content(text: str) -> str:
        """Strip nav, ads, footer, and link garbage from scraped markdown before GPT extraction."""
        lines = text.split("\n")
        cleaned = []
        skip_section = False

        nav_link_re = re.compile(r'^\s*[\*\-]\s*\[.+?\]\(.+?\)\s*$')
        bare_url_re = re.compile(r'https?://\S+')
        social_re = re.compile(r'\b(share|tweet|facebook|twitter|whatsapp|linkedin|pinterest|reddit)\b', re.IGNORECASE)
        signup_re = re.compile(r'\b(sign\s*in|log\s*in|your\s+username|your\s+password|subscribe|newsletter|create\s+account|forgot\s+password)\b', re.IGNORECASE)
        related_re = re.compile(r'\b(related\s+articles?|previous\s+article|next\s+article|you\s+may\s+also|more\s+from|read\s+more|also\s+read)\b', re.IGNORECASE)
        footer_re = re.compile(r'\b(privacy\s+policy|terms\s+(&|and)\s+conditions?|cookie\s+policy|advertise\s+with|contact\s+us|about\s+us|all\s+rights\s+reserved|©)\b', re.IGNORECASE)

        for line in lines:
            stripped = line.strip()

            if not stripped:
                cleaned.append("")
                continue

            if related_re.search(stripped) or footer_re.search(stripped):
                skip_section = True
                continue

            if skip_section:
                if stripped.startswith("#"):
                    skip_section = False
                else:
                    continue

            if nav_link_re.match(stripped):
                continue
            if signup_re.search(stripped) and len(stripped) < 120:
                continue
            if social_re.search(stripped) and len(stripped) < 100:
                continue

            line = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', line)
            line = bare_url_re.sub('', line).rstrip()

            if line.strip():
                cleaned.append(line)
            else:
                cleaned.append("")

        result = "\n".join(cleaned)
        result = re.sub(r'\n{3,}', '\n\n', result).strip()
        return result

    def enrich_companies(self, scored: dict) -> list[dict]:
        """Stage 3: Scrape, extract, and enrich each company."""
        companies = scored["companies"]
        enriched = []

        for i, company in enumerate(companies):
            name = company["company_name"]
            print(f"\n  [{i+1}/{len(companies)}] Enriching: {name}")

            # Scrape best source
            article_text = None
            source_url = company["best_source_url"]
            if source_url:
                print(f"    Scraping {source_url[:80]}...")
                article_text = self.fetch_url(source_url)
                if article_text:
                    raw_len = len(article_text)
                    article_text = self.clean_article_content(article_text)
                    print(f"    Got {raw_len} chars, cleaned to {len(article_text)}")
                else:
                    print(f"    Scrape failed, trying next source...")
                    for src in company["sources"]:
                        if src["url"] != source_url:
                            article_text = self.fetch_url(src["url"])
                            if article_text:
                                article_text = self.clean_article_content(article_text)
                                source_url = src["url"]
                                print(f"    Fallback worked: {src['url'][:80]}")
                                break

            # Extract with GPT
            extracted = None
            if article_text and OPENAI_API_KEY:
                print(f"    Extracting with GPT-4o-mini...")
                extracted = self.extract_with_openai(article_text, name, company.get("amount", ""))
                if extracted and not self.post_extract_filter(extracted):
                    print(f"    FILTERED: post-extraction filter")
                    continue

            # Domain resolution with validation gate
            domain = "not_found"
            source_domain = source_url.split("/")[2] if "://" in source_url else ""
            industry = detect_industry(article_text or "")

            if extracted and extracted.get("company_domain") and extracted["company_domain"] not in ("not_stated", "not_found", ""):
                candidate = extracted["company_domain"]
                v = validate_domain(candidate, name, source_domain)
                if v["valid"]:
                    domain = candidate
                    print(f"    GPT domain accepted: {candidate} ({v['confidence']} confidence)")
                else:
                    print(f"    GPT domain REJECTED: {candidate} ({v['reason']})")

            if domain == "not_found":
                print(f"    Running domain resolver...")
                domain = self.lookup_domain(name, source_url, article_text, industry)

            record = self.build_enriched_record(company, extracted, domain, source_url)
            enriched.append(record)
            display_name = record['company_name'].encode('ascii', 'replace').decode()
            display_amt = record.get('amount_raised', '').encode('ascii', 'replace').decode()
            print(f"    => {display_name} | {record.get('company_domain', '')} | {display_amt}")

            time.sleep(0.5)

        print(f"\n  Stage 3: {len(enriched)} companies enriched")
        return enriched

    # -----------------------------------------------------------------------
    # STAGE 4: Output (generic)
    # -----------------------------------------------------------------------

    def get_pipeline_version(self) -> str:
        return "1.0"

    def write_output(self, enriched: list[dict], date_str: str):
        """Stage 4: Write CSV and JSON output, push to Supabase."""
        pre_dedup = len(enriched)
        enriched = fuzzy_dedup_companies(
            enriched,
            name_key="company_name",
            domain_key="company_domain",
            score_key="score",
        )
        if len(enriched) < pre_dedup:
            print(f"\n  Post-enrichment dedup: {pre_dedup} -> {len(enriched)} companies")

        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        fieldnames = self.OUTPUT_FIELDNAMES

        # Daily CSV
        csv_path = OUTPUT_DIR / f"{self.OUTPUT_PREFIX}-{date_str}.csv"
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for r in enriched:
                row = {"date": date_str, **{k: r.get(k, "") for k in fieldnames if k != "date"}}
                writer.writerow(row)

        print(f"\n  CSV: {csv_path} ({len(enriched)} rows)")

        # Daily JSON
        json_path = OUTPUT_DIR / f"{self.OUTPUT_PREFIX}-{date_str}.json"
        output_json = {
            "date": date_str,
            f"{self.OUTPUT_PREFIX}_count": len(enriched),
            "companies": enriched,
            "metadata": {
                "pipeline_version": self.get_pipeline_version(),
                "tbs": "qdr:d",
                "generated_at": datetime.now().isoformat(),
            }
        }
        json_path.write_text(json.dumps(output_json, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"  JSON: {json_path}")

        # Append to master CSV
        master_path = OUTPUT_DIR / f"{self.OUTPUT_PREFIX}-master.csv"
        write_header = not master_path.exists()
        with open(master_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            if write_header:
                writer.writeheader()
            for r in enriched:
                row = {"date": date_str, **{k: r.get(k, "") for k in fieldnames if k != "date"}}
                writer.writerow(row)

        print(f"  Master CSV: {master_path} (appended {len(enriched)} rows)")

        # Supabase upsert
        if SUPABASE_URL and SUPABASE_KEY:
            if self.check_supabase_table():
                print(f"\n  Pushing to Supabase...")
                upserted = self.push_to_supabase(enriched, date_str)
                print(f"  Supabase: {upserted}/{len(enriched)} rows upserted")
            else:
                print(f"\n  Supabase: table '{self.SUPABASE_TABLE}' not found")
                self.create_supabase_table()
        else:
            print(f"\n  Supabase: SKIPPED (no SUPABASE_URL/SUPABASE_KEY)")

        # Webhook push (Clay, Zapier, etc.)
        if self.WEBHOOK_URL:
            print(f"\n  Pushing to webhook...")
            sent = self.push_to_webhook(enriched, date_str)
            print(f"  Webhook: {sent}/{len(enriched)} rows sent")

        return csv_path, json_path

    # -----------------------------------------------------------------------
    # Supabase helpers
    # -----------------------------------------------------------------------

    def supabase_headers(self, prefer: str = None) -> dict:
        h = {
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "application/json",
        }
        if prefer:
            h["Prefer"] = prefer
        return h

    def check_supabase_table(self) -> bool:
        """Check if the target Supabase table exists."""
        try:
            resp = requests.get(
                f"{SUPABASE_URL}/rest/v1/{self.SUPABASE_TABLE}?limit=1",
                headers=self.supabase_headers(),
                timeout=10,
            )
            return resp.status_code == 200
        except Exception:
            return False

    def get_supabase_schema_sql(self) -> str:
        """Return SQL to create the target table. Override in subclass."""
        return ""

    def create_supabase_table(self) -> bool:
        """Create the Supabase table via Management API."""
        schema_sql = self.get_supabase_schema_sql()
        if not schema_sql:
            print(f"    No schema SQL defined for {self.SUPABASE_TABLE}")
            return False

        mcp_token = os.getenv("SUPABASE_MCP_TOKEN")
        if mcp_token and SUPABASE_URL:
            ref = SUPABASE_URL.replace("https://", "").split(".")[0]
            try:
                resp = requests.post(
                    f"https://api.supabase.com/v1/projects/{ref}/database/query",
                    headers={
                        "Authorization": f"Bearer {mcp_token}",
                        "Content-Type": "application/json",
                    },
                    json={"query": schema_sql},
                    timeout=15,
                )
                if resp.status_code == 200 or resp.status_code == 201:
                    print("    Table created via Management API")
                    return True
                else:
                    print(f"    Management API: {resp.status_code} {resp.text[:150]}")
            except Exception as e:
                print(f"    Management API error: {e}")

        print(f"    Run the schema SQL in Supabase SQL Editor to create the table.")
        return False

    def get_supabase_row(self, record: dict, date_str: str) -> dict:
        """Transform an enriched record into a Supabase row. Override for custom mapping."""
        return {
            "discovered_date": date_str,
            "company_name": record.get("company_name", ""),
            "company_domain": record.get("company_domain", ""),
            "amount_raised": record.get("amount_raised", ""),
            "round_type": record.get("round_type", ""),
            "source_url": record.get("source_url", ""),
            "lead_investors": record.get("lead_investors", "not_stated"),
            "round_reasoning": record.get("round_reasoning", "not_stated"),
            "article_text": record.get("article_text"),
            "discovered_by_pipeline": record.get("discovered_by_pipeline", self.PIPELINE_NAME),
            "source_count": record.get("source_count", 1),
            "score": record.get("score", 0),
            "pipeline_version": self.get_pipeline_version(),
        }

    def fetch_recent_companies(self, days: int = 30) -> list[dict]:
        """Fetch existing companies from last N days for cross-day dedup."""
        if not (SUPABASE_URL and SUPABASE_KEY):
            return []
        cutoff = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
        try:
            resp = requests.get(
                f"{SUPABASE_URL}/rest/v1/{self.SUPABASE_TABLE}"
                f"?select=id,company_name,company_domain,source_count,score,source_url,discovered_date"
                f"&discovered_date=gte.{cutoff}",
                headers=self.supabase_headers(),
                timeout=30,
            )
            if resp.status_code == 200:
                return resp.json()
            print(f"    Cross-day dedup fetch failed {resp.status_code}: {resp.text[:200]}")
            return []
        except Exception as e:
            print(f"    Cross-day dedup fetch error: {e}")
            return []

    def push_to_supabase(self, enriched: list[dict], date_str: str) -> int:
        """Upsert enriched companies to Supabase. Returns count of successful writes (insert + merge).

        Cross-day dedup: before inserting, scans last 30 days for matching company by domain
        or fuzzy name. If matched, PATCHes existing row (bumps source_count, max score) instead
        of inserting a duplicate.
        """
        rows = [self.get_supabase_row(r, date_str) for r in enriched]

        # Within-batch dedup by source_url — keep first occurrence
        seen = set()
        deduped = []
        for row in rows:
            url = row.get("source_url", "")
            if url and url in seen:
                continue
            if url:
                seen.add(url)
            deduped.append(row)
        rows = deduped

        # Cross-day dedup: load recent rows once
        recent = self.fetch_recent_companies(days=30)
        if recent:
            print(f"    Cross-day dedup: scanning {len(recent)} rows from last 30 days")

        inserted = 0
        merged = 0
        for row in rows:
            existing = match_existing_company(row, recent)
            if existing and existing.get("id"):
                ex_count = existing.get("source_count") or 1
                row_count = row.get("source_count") or 1
                ex_score = existing.get("score") or 0
                row_score = row.get("score") or 0
                patch = {
                    "source_count": max(ex_count, ex_count + row_count - 1),
                    "score": max(ex_score, row_score),
                }
                if row.get("company_domain") and not existing.get("company_domain"):
                    patch["company_domain"] = row["company_domain"]
                try:
                    resp = requests.patch(
                        f"{SUPABASE_URL}/rest/v1/{self.SUPABASE_TABLE}?id=eq.{existing['id']}",
                        headers=self.supabase_headers(prefer="return=minimal"),
                        json=patch,
                        timeout=15,
                    )
                    if resp.status_code in (200, 204):
                        merged += 1
                        existing.update(patch)
                    else:
                        print(f"    Supabase merge error {resp.status_code}: {resp.text[:200]}")
                except Exception as e:
                    print(f"    Supabase merge error: {e}")
                continue

            try:
                resp = requests.post(
                    f"{SUPABASE_URL}/rest/v1/{self.SUPABASE_TABLE}?on_conflict=source_url",
                    headers=self.supabase_headers(prefer="resolution=merge-duplicates"),
                    json=[row],
                    timeout=15,
                )
                if resp.status_code in (200, 201):
                    inserted += 1
                    recent.append({
                        "id": None,
                        "company_name": row.get("company_name"),
                        "company_domain": row.get("company_domain"),
                        "source_count": row.get("source_count", 1),
                        "score": row.get("score", 0),
                        "source_url": row.get("source_url"),
                        "discovered_date": date_str,
                    })
                else:
                    print(f"    Supabase error {resp.status_code}: {resp.text[:200]}")
            except Exception as e:
                print(f"    Supabase error: {e}")
        if merged:
            print(f"    Cross-day merged: {merged} (bumped source_count instead of duplicate insert)")
        return inserted + merged

    # -----------------------------------------------------------------------
    # Webhook push (Clay, Zapier, etc.)
    # -----------------------------------------------------------------------

    def get_webhook_row(self, record: dict, date_str: str) -> dict:
        """Transform an enriched record into a webhook payload row. Override for custom mapping."""
        return {"date": date_str, **record}

    def push_to_webhook(self, enriched: list[dict], date_str: str) -> int:
        """POST enriched records to webhook URL. Sends one request per row (Clay expects this)."""
        if not self.WEBHOOK_URL:
            return 0

        headers = {"Content-Type": "application/json"}
        if self.WEBHOOK_AUTH_TOKEN:
            headers["x-clay-webhook-auth"] = self.WEBHOOK_AUTH_TOKEN

        sent = 0
        for record in enriched:
            row = self.get_webhook_row(record, date_str)
            try:
                resp = requests.post(self.WEBHOOK_URL, headers=headers, json=row, timeout=10)
                if resp.status_code in (200, 201, 202):
                    sent += 1
                else:
                    print(f"    Webhook error {resp.status_code}: {resp.text[:150]}")
            except Exception as e:
                print(f"    Webhook error: {e}")
        return sent

    # -----------------------------------------------------------------------
    # CLI & Main
    # -----------------------------------------------------------------------

    def add_arguments(self, parser: argparse.ArgumentParser):
        """Hook for subclasses to add extra CLI args."""
        pass

    def build_parser(self) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(description=self.PIPELINE_NAME)
        parser.add_argument("--tbs", default="qdr:d", help="Time filter (qdr:d=day, qdr:w=week)")
        parser.add_argument("--stage", type=int, help="Run only this stage (1-4)")
        parser.add_argument("--skip-enrich", action="store_true", help="Skip stage 3 (scraping/extraction)")
        parser.add_argument("--dry-run", action="store_true", help="Preview queries without running")
        parser.add_argument("--max-enrich", type=int, default=20, help="Max companies to enrich in stage 3")
        parser.add_argument("--date", type=str, default=None, help="Run date as YYYY-MM-DD (default: today)")
        parser.add_argument("--domain-agent", action="store_true", help="Use GPT agent fallback for not_found domains (~$0.02/company)")
        self.add_arguments(parser)
        return parser

    def run(self, date_str: str = None, args=None):
        """
        Execute the full pipeline.

        Args:
            date_str: Date string YYYY-MM-DD. Defaults to today if not provided.
            args: Parsed argparse Namespace. If None, parses sys.argv.
        """
        if args is None:
            parser = self.build_parser()
            args = parser.parse_args()

        if date_str is None:
            date_str = args.date if hasattr(args, 'date') and args.date else datetime.now().strftime("%Y-%m-%d")

        self._use_domain_agent = getattr(args, 'domain_agent', False)

        STAGE_DIR.mkdir(parents=True, exist_ok=True)

        print(f"\n{'='*60}")
        print(f"  {self.PIPELINE_NAME.upper()} -- {date_str}")
        print(f"  TBS: {args.tbs} | Skip enrich: {args.skip_enrich}")
        print(f"  API keys: OpenAI={'YES' if OPENAI_API_KEY else 'NO'} | Spider={'YES' if SPIDER_API_KEY else 'NO'} | Supabase={'YES' if (SUPABASE_URL and SUPABASE_KEY) else 'NO'}")
        print(f"{'='*60}")

        # --- STAGE 1: Discovery ---
        stage1_file = STAGE_DIR / f"stage1-{date_str}.json"

        if args.stage and args.stage > 1 and stage1_file.exists():
            print(f"\n  Loading stage 1 from {stage1_file}")
            raw_results = json.loads(stage1_file.read_text(encoding="utf-8"))
        else:
            print(f"\n  STAGE 1: DISCOVERY")
            raw_results = self.run_discovery(args.tbs, args.dry_run)
            if args.dry_run:
                return
            stage1_file.write_text(json.dumps(raw_results, indent=2, ensure_ascii=False), encoding="utf-8")
            print(f"  Saved: {stage1_file}")

        if args.stage == 1:
            print("\n  Done (stage 1 only)")
            return

        # --- STAGE 2: Score & Filter ---
        print(f"\n  STAGE 2: SCORE & FILTER")
        scored = self.score_and_filter(raw_results)

        stage2_file = STAGE_DIR / f"stage2-{date_str}.json"
        stage2_file.write_text(json.dumps(scored, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"  Saved: {stage2_file}")

        if args.stage == 2:
            print("\n  Done (stage 2 only)")
            return

        # --- STAGE 3: Enrich ---
        if args.skip_enrich:
            print(f"\n  STAGE 3: SKIPPED (--skip-enrich)")
            enriched = [self.build_skip_enrich_record(c) for c in scored["companies"]]
        else:
            print(f"\n  STAGE 3: ENRICH & EXTRACT (max {args.max_enrich})")
            scored_limited = dict(scored)
            scored_limited["companies"] = scored["companies"][:args.max_enrich]
            enriched = self.enrich_companies(scored_limited)

        if args.stage == 3:
            stage3_file = STAGE_DIR / f"stage3-{date_str}.json"
            stage3_file.write_text(json.dumps(enriched, indent=2, ensure_ascii=False), encoding="utf-8")
            print(f"  Saved: {stage3_file}")
            print("\n  Done (stage 3 only)")
            return

        # --- STAGE 4: Output ---
        print(f"\n  STAGE 4: OUTPUT")
        csv_path, json_path = self.write_output(enriched, date_str)

        print(f"\n{'='*60}")
        print(f"  PIPELINE COMPLETE")
        print(f"  Companies found: {len(enriched)}")
        print(f"  CSV: {csv_path}")
        print(f"  JSON: {json_path}")
        print(f"{'='*60}\n")
