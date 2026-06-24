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
# Workspace root .env (C:/Users/mitch/Everything_CC/.env) — primary key store
load_dotenv(SCRIPT_DIR_EARLY.parent.parent / ".env", override=False)
load_dotenv(Path.home() / ".env", override=False)

_shared = os.environ.get("SHARED_SCRIPTS_PATH")
if not _shared:
    # Auto-discover: workspace-root/leadgrow-hq/tools/shared-scripts is canonical
    _candidate = SCRIPT_DIR_EARLY.parent.parent / "leadgrow-hq" / "tools" / "shared-scripts"
    _shared = str(_candidate) if _candidate.exists() else str(SCRIPT_DIR_EARLY)
sys.path.insert(0, _shared)

import serper_search
import requests

SCRIPT_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = SCRIPT_DIR.parent / "output"
STAGE_DIR = SCRIPT_DIR.parent / "output" / "stages"

_FC_DIR = SCRIPT_DIR_EARLY.parent.parent / "leadgrow-hq" / "tools" / "firecrawl"
if str(_FC_DIR) not in sys.path:
    sys.path.insert(0, str(_FC_DIR))
try:
    from fc_client import scrape as _fc_scrape
    _FC_AVAILABLE = True
except ImportError:
    _FC_AVAILABLE = False

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
FIRECRAWL_API_KEY = os.getenv("FIRECRAWL_API_KEY")
SUPABASE_URL = os.getenv("SUPABASE_PROJECT_URL") or os.getenv("SUPABASE_URL")
if SUPABASE_URL and not SUPABASE_URL.startswith("http"):
    SUPABASE_URL = None
SUPABASE_KEY = os.getenv("SUPABASE_KEY") or os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_ANON_KEY")


# ---------------------------------------------------------------------------
# Graduated extraction prompt — see prompts/extract-companies-batch/
# v004, score 1.0000 / 32 GT cases (mean 0.9939 across 4 reruns), $0.0012/batch.
# Edit by re-annealing, not in-place: changes here drift from the test suite.
# ---------------------------------------------------------------------------

_EXTRACT_COMPANIES_BATCH_SYSTEM = (
    "You extract structured data from news search results. Output strict JSON only."
)

_EXTRACT_COMPANIES_BATCH_USER_TEMPLATE = """Identify the COMPANY THAT RAISED FUNDING in each numbered news item.

CRITICAL: company and is_funding are INDEPENDENT.
- is_funding=true if the item is ANY way about a funding round (announced, closed, eyed, secured, raising, even multi-company roundups, even aggregator listings of past rounds). Funding verbs: raises/raised/secures/closes/lands/snags/announces/eyes (eyeing future round still counts).
- is_funding=false ONLY for: profile pages with no funding mention, generic explainers about VC mechanics, 404/empty pages, or news fully unrelated to funding.
- company = the SINGLE startup that got the money. Returning null does NOT make is_funding false. A roundup of 5 funded startups is is_funding=true with company=null.

NEVER return as company:
- An investor / VC firm / fund
- A publication name (TechCrunch, AI Market Watch, FemWealth, InforCapital, etc.)
- A person's name (founder, journalist)

RETURN company=null WHEN:
- Roundup / weekly digest / multi-company list (2+ funded companies named)
- Snippet truncates the company name with "..." before it can be read
- Title and snippet describe DIFFERENT funding deals (conflict where neither is clearly "the" subject)
- Aggregator listing (Tracxn / Crunchbase feed where title is unrelated to snippet contents)
- Publisher feed (multiple unrelated funded companies in the snippet)

TITLE vs SNIPPET PRIORITY:
- Title clearly names a funded company with a funding verb -> trust title even if snippet is unrelated boilerplate.
- Title generic ("AI Startup Secures..."), publisher column ("TechCrunch Mobility:..."), social junk ("...- Facebook/LinkedIn/Instagram"), or possessive ("Jane's Era Raises..." -> "Era") -> use snippet to find company.
- Investor-led syntax "X led a Series A in Y" -> Y is the funded company.
- Bullet snippets where one entity "led the round" = investor; the other named entity is the funded co.

EXAMPLES:
- TITLE: "Lumio eyes Series A round after $4 million seed funding" SNIPPET: "Lumio eyes Series A round after $4 million seed funding..." -> {"company":"Lumio","is_funding":true}  // "eyes" still counts; company named in title
- TITLE: "TechCrunch Mobility: Elon's admission" SNIPPET: "A&K Robotics, a Vancouver maker of AVs, raised $8M Series A led by BDC..." -> {"company":"A&K Robotics","is_funding":true}
- TITLE: "AI Startup Secures $150M..." SNIPPET: "...Amperos Health raised Series A to enhance AI denial mgmt..." -> {"company":"Amperos Health","is_funding":true}
- TITLE: "Itaú Ventures led a Series A in Minter, a startup..." -> {"company":"Minter","is_funding":true}
- TITLE: "Elizabeth Dorman & Megan Gole's Era Raises $11M" SNIPPET: "<unrelated German startup>" -> {"company":"Era","is_funding":true}
- TITLE: "[Korean Startup Weekly News #115] Point2..." SNIPPET: "Dnotitia Raises $63.4M..." -> {"company":null,"is_funding":true}  // weekly roundup, still about funding
- TITLE: "Startups are raising big bucks!..." SNIPPET: "Mindbridge AI raises 8.4M... Whimstay Raises $10M..." -> {"company":null,"is_funding":true}  // roundup, still funding
- TITLE: "Fintech VC Funding Remains Steady..." SNIPPET: "...inKind's $450M, Vestwell's $385M Series E, Fundamental's $225M Series A" -> {"company":null,"is_funding":true}  // aggregator list, still funding
- TITLE: "Latest tech trends - InfotechLead" SNIPPET: "Verda secures $117M... Venture Capital Funding: Realm, Capsule Security, Prefix..." -> {"company":null,"is_funding":true}  // publisher feed of multiple deals, still funding
- TITLE: "OpenAI - 2026 Funding Rounds - Tracxn" SNIPPET: "BigBuy - raised $4.68M Series A..." -> {"company":null,"is_funding":true}  // Tracxn aggregator
- TITLE: "India-based Nava has raised US$22..." SNIPPET: "Foundry Group, key investor in Graphen, led a $23.5M round..." -> {"company":"Nava","is_funding":true}
- TITLE: "WhoaZone Equine - Facebook" SNIPPET: "...Series A investment into Etalon. Series A funding is..." -> {"company":"Etalon","is_funding":true}
- TITLE: "Alphabet may put up to $40B..." SNIPPET: "...• Thrive Capital led the round, with Microsoft, Nvidia... • OpenAI..." -> {"company":"OpenAI","is_funding":true}
- TITLE: "AI Market Watch's Post - LinkedIn" SNIPPET: "... raised 1.7B JPY Series A, led by Angel..." -> {"company":null,"is_funding":true}  // truncated name
- TITLE: "Warehoused Deal Closing for New Fund Managers" SNIPPET: "The company raises Series A at $20M... LPs inherit a 4x markup..." -> {"company":null,"is_funding":false}  // generic LP mechanics explainer
- TITLE: "India Post to open payments bank..." SNIPPET: "Verda secures $117M..." -> {"company":null,"is_funding":false}  // title is non-funding, snippet is unrelated feed

Return STRICT JSON: {"results":[{"idx":1,"company":"Auth0","is_funding":true},{"idx":2,"company":null,"is_funding":false}]}

Items:
{items}"""


# ---------------------------------------------------------------------------
# SupabaseClient — storage layer, extracted from ResearchPipeline
# ---------------------------------------------------------------------------

class SupabaseClient:
    """
    Encapsulates all Supabase HTTP I/O.

    ResearchPipeline delegates storage to this class via self._supabase.
    Injectable for testing: pass url=None/key=None for a no-op client.

    Methods:
        headers(prefer=None) -> dict
        table_exists(table) -> bool
        fetch_recent(table, days) -> list[dict]
        push_rows(table, rows, date_str, recent) -> int
        create_table(table, schema_sql) -> bool
    """

    def __init__(self, url: str | None = None, key: str | None = None):
        self._url = url
        self._key = key

    def _ready(self) -> bool:
        return bool(self._url and self._key)

    def headers(self, prefer: str | None = None) -> dict:
        h = {
            "apikey": self._key or "",
            "Authorization": f"Bearer {self._key or ''}",
            "Content-Type": "application/json",
        }
        if prefer:
            h["Prefer"] = prefer
        return h

    def table_exists(self, table: str) -> bool:
        if not self._ready():
            return False
        try:
            resp = requests.get(
                f"{self._url}/rest/v1/{table}?limit=1",
                headers=self.headers(),
                timeout=10,
            )
            return resp.status_code == 200
        except Exception:
            return False

    def fetch_recent(self, table: str, days: int = 30) -> list[dict]:
        if not self._ready():
            return []
        cutoff = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
        try:
            resp = requests.get(
                f"{self._url}/rest/v1/{table}"
                f"?select=id,company_name,company_domain,source_count,score,source_url,discovered_date"
                f"&discovered_date=gte.{cutoff}",
                headers=self.headers(),
                timeout=30,
            )
            if resp.status_code == 200:
                return resp.json()
            print(f"    Cross-day dedup fetch failed {resp.status_code}: {resp.text[:200]}")
            return []
        except Exception as e:
            print(f"    Cross-day dedup fetch error: {e}")
            return []

    def push_rows(
        self,
        table: str,
        rows: list[dict],
        date_str: str,
        recent: list[dict] | None = None,
    ) -> int:
        """Upsert rows to Supabase with cross-day dedup. Returns count written."""
        if not self._ready():
            return 0
        if recent is None:
            recent = []

        # Within-batch dedup by source_url — keep first occurrence
        seen: set[str] = set()
        deduped = []
        for row in rows:
            url = row.get("source_url", "")
            if url and url in seen:
                continue
            if url:
                seen.add(url)
            deduped.append(row)
        rows = deduped

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
                        f"{self._url}/rest/v1/{table}?id=eq.{existing['id']}",
                        headers=self.headers(prefer="return=minimal"),
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
                    f"{self._url}/rest/v1/{table}?on_conflict=source_url",
                    headers=self.headers(prefer="resolution=merge-duplicates"),
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

    def create_table(self, table: str, schema_sql: str) -> bool:
        """Create table via Management API. Returns True on success."""
        if not schema_sql:
            print(f"    No schema SQL defined for {table}")
            return False
        mcp_token = os.getenv("SUPABASE_MCP_TOKEN")
        if mcp_token and self._url:
            ref = self._url.replace("https://", "").split(".")[0]
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
                if resp.status_code in (200, 201):
                    print("    Table created via Management API")
                    return True
                else:
                    print(f"    Management API: {resp.status_code} {resp.text[:150]}")
            except Exception as e:
                print(f"    Management API error: {e}")
        print(f"    Run the schema SQL in Supabase SQL Editor to create the table.")
        return False


# Build module-level default client from env vars (mirrors old global SUPABASE_URL / SUPABASE_KEY)
_default_supabase = SupabaseClient(url=SUPABASE_URL, key=SUPABASE_KEY)


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

    def __init__(self, supabase: SupabaseClient | None = None):
        # Allow injection of a mock/custom SupabaseClient for tests.
        # Defaults to the module-level client built from env vars.
        self._supabase = supabase if supabase is not None else _default_supabase

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
    # Batch GPT name extraction (replaces regex band-aid pile)
    # -----------------------------------------------------------------------

    def extract_companies_batch(
        self, items: list[dict], batch_size: int = 25
    ) -> dict[int, dict]:
        """
        Identify the funded company in each item using a single GPT-4o-mini call
        per batch. Items must carry a unique 'idx' plus 'title' and 'snippet'.

        Returns: {idx: {"company": str|None, "is_funding": bool}}.

        Uses the graduated v004 prompt from prompts/extract-companies-batch/
        (annealed 2026-04-27, score 1.0000 on 32-case GT, mean 0.9939 across
        4 reruns). Items are formatted with 1-based LOCAL idx within each
        batch; the model returns 1-based local idx and we map back to the
        item's global idx for the output dict.
        """
        out: dict[int, dict] = {}
        if not items:
            return out
        if not OPENAI_API_KEY:
            print("    [WARN] OPENAI_API_KEY missing — Stage 2 GPT extract no-op'd")
            return out

        for start in range(0, len(items), batch_size):
            batch = items[start : start + batch_size]
            payload_lines = []
            for local_idx, it in enumerate(batch, 1):
                snippet = (it.get("snippet") or "")[:280].replace("\n", " ").strip()
                title = (it.get("title") or "").replace("\n", " ").strip()
                payload_lines.append(
                    f"[{local_idx}] TITLE: {title} | SNIPPET: {snippet}"
                )

            user_msg = _EXTRACT_COMPANIES_BATCH_USER_TEMPLATE.replace(
                "{items}", "\n".join(payload_lines)
            )

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
                        "response_format": {"type": "json_object"},
                        "max_tokens": 2000,
                        "messages": [
                            {
                                "role": "system",
                                "content": _EXTRACT_COMPANIES_BATCH_SYSTEM,
                            },
                            {"role": "user", "content": user_msg},
                        ],
                    },
                    timeout=60,
                )
                resp.raise_for_status()
                body = json.loads(resp.json()["choices"][0]["message"]["content"])
                for r in body.get("results", []):
                    local_idx = r.get("idx")
                    if local_idx is None or not (1 <= local_idx <= len(batch)):
                        continue
                    global_idx = batch[local_idx - 1]["idx"]
                    company = (r.get("company") or "").strip() or None
                    out[global_idx] = {
                        "company": company,
                        "is_funding": bool(r.get("is_funding")),
                    }
            except Exception as e:
                print(f"    [WARN] GPT batch extract failed: {e}")

        return out

    # -----------------------------------------------------------------------
    # STAGE 3: Enrich & Extract (generic with hooks)
    # -----------------------------------------------------------------------

    def fetch_url(self, url: str) -> Optional[str]:
        """Fetch URL content. Firecrawl primary, requests fallback."""
        if _FC_AVAILABLE:
            try:
                result = _fc_scrape(url, main_only=True)
                if isinstance(result, dict):
                    content = result.get("markdown") or result.get("content") or ""
                else:
                    content = getattr(result, "markdown", "") or ""
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
        self._log_domain_resolution(company_name, source_url, result)
        return result["domain"]

    def _log_domain_resolution(self, company_name: str, source_url: str, result: dict) -> None:
        """Append one resolution record to data/domain_resolution_log.jsonl."""
        try:
            log_path = Path(__file__).resolve().parent.parent / "data" / "domain_resolution_log.jsonl"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            entry = {
                "ts": datetime.utcnow().isoformat() + "Z",
                "run_date": getattr(self, '_run_date', datetime.utcnow().strftime("%Y-%m-%d")),
                "company_name": company_name,
                "source_url": source_url,
                "domain": result.get("domain", ""),
                "tier": result.get("tier"),
                "tier_name": result.get("tier_name", ""),
                "confidence": result.get("confidence", ""),
                "evidence": result.get("evidence", ""),
                "failed": result.get("domain", "") in ("", "not_found"),
            }
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            pass  # never let logging break the pipeline

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

    def validate_domain_semantic(
        self,
        source_url: str,
        company_name: str,
        domain: str,
        raw_article_text: str,
    ) -> dict:
        """
        Post-resolution semantic validation via LLM.

        Uses the raw (uncleaned) article text so hyperlinks are preserved —
        a hyperlinked company name in the article is the strongest possible
        signal for the correct domain.

        Returns: {"correctCompanyName": str, "correctDomain": str,
                  "status": "Correct"|"Wrong"|"Unclear", "reason": str}
        """
        if not OPENAI_API_KEY or not raw_article_text:
            return {"status": "Unclear", "reason": "no API key or article text", "correctCompanyName": company_name, "correctDomain": domain}

        system_prompt = (
            "You are a company domain verification agent. You are given a CANDIDATE domain to verify — "
            "it may be correct or wrong. Your job: find the TRUE domain, then compare.\n\n"
            "Step 1 — Find the true domain from the article:\n"
            "  a) Is the company name a markdown hyperlink like [Company](https://example.com)?\n"
            "     YES → that hyperlinked URL is the true domain. Stop here.\n"
            "  b) Is there a URL in the article that belongs to the company itself (not a news site, not social media)?\n"
            "     YES → that is the true domain.\n"
            "  c) Neither → the article does not contain a verifiable domain.\n\n"
            "Step 2 — Validate the CANDIDATE domain from the article context:\n"
            "  A candidate is VALID only if ALL of these hold:\n"
            "  - Belongs to the company that raised funding (not a news site, CDN, investor, or social platform)\n"
            "  - Product/service described on that site matches the article\n"
            "  - Industry matches\n"
            "  - Geography matches (if stated)\n"
            "  NEVER accept a news/media domain as the company's domain.\n\n"
            "Step 3 — Set status:\n"
            "  - If true domain found AND it EXACTLY matches the candidate → status = 'Correct'\n"
            "  - If true domain found AND it DIFFERS from the candidate → status = 'Wrong', set correctDomain\n"
            "  - If no true domain found in article AND candidate passes Step 2 validation → status = 'Correct'\n"
            "  - If no true domain found AND candidate fails validation → status = 'Unclear'\n"
            "  - If you cannot confidently determine anything → status = 'Unclear', DO NOT GUESS\n\n"
            "Rules:\n"
            "  - NEVER guess a domain not explicitly in the article\n"
            "  - NEVER default to company-name.com as a guess\n"
            "  - News/media sites (techcrunch.com, finsmes.com, etc.) are NEVER the company domain\n\n"
            'Output ONLY valid JSON:\n'
            '{"correctCompanyName": "", "correctDomain": "", "status": "Correct / Wrong / Unclear", "reason": ""}\n\n'
            "reason: max 2 short sentences explaining your decision."
        )

        user_msg = (
            f"source_url: {source_url}\n"
            f"company_name: {company_name}\n"
            f"domain: {domain}\n\n"
            f"Article text:\n{raw_article_text[:8000]}"
        )

        try:
            resp = requests.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
                json={
                    "model": "gpt-4o-mini",
                    "temperature": 0,
                    "max_tokens": 200,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_msg},
                    ],
                },
                timeout=25,
            )
            if resp.status_code == 200:
                content = resp.json()["choices"][0]["message"]["content"].strip()
                m = re.search(r"\{[\s\S]*\}", content)
                if m:
                    result = json.loads(m.group(0))
                    # Normalize correctDomain to bare domain (GPT sometimes returns full URLs)
                    if result.get("correctDomain"):
                        result["correctDomain"] = _normalize_domain_resolver(result["correctDomain"])
                    return result
        except Exception as e:
            print(f"    [WARN] Semantic validation error: {e}")

        return {"status": "Unclear", "reason": "validation call failed", "correctCompanyName": company_name, "correctDomain": domain}

    def enrich_companies(self, scored: dict) -> list[dict]:
        """Stage 3: Scrape, extract, and enrich each company."""
        companies = scored["companies"]
        enriched = []

        for i, company in enumerate(companies):
            name = company["company_name"]
            print(f"\n  [{i+1}/{len(companies)}] Enriching: {name}")

            # Scrape best source — keep raw text for semantic validation (hyperlinks intact)
            raw_article_text = None
            article_text = None
            source_url = company["best_source_url"]
            if source_url:
                print(f"    Scraping {source_url[:80]}...")
                raw_article_text = self.fetch_url(source_url)
                if raw_article_text:
                    raw_len = len(raw_article_text)
                    article_text = self.clean_article_content(raw_article_text)
                    print(f"    Got {raw_len} chars, cleaned to {len(article_text)}")
                else:
                    print(f"    Scrape failed, trying next source...")
                    for src in company["sources"]:
                        if src["url"] != source_url:
                            raw_article_text = self.fetch_url(src["url"])
                            if raw_article_text:
                                article_text = self.clean_article_content(raw_article_text)
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

            # Semantic validation — uses raw text so hyperlinks are preserved
            if domain != "not_found" and raw_article_text:
                vresult = self.validate_domain_semantic(source_url, name, domain, raw_article_text)
                vstatus = vresult.get("status", "Unclear")
                if vstatus == "Wrong":
                    corrected = (vresult.get("correctDomain") or "").strip()
                    if corrected and corrected not in ("", "not_found", "not_stated"):
                        cv = validate_domain(corrected, name, source_domain)
                        if cv["valid"]:
                            print(f"    Semantic: corrected {domain} -> {corrected} ({vresult.get('reason', '')})")
                            domain = corrected
                            corrected_name = (vresult.get("correctCompanyName") or "").strip()
                            if corrected_name and corrected_name != name:
                                company = dict(company)
                                company["company_name"] = corrected_name
                                name = corrected_name
                        else:
                            print(f"    Semantic: corrected domain {corrected} failed structural validation — marking not_found")
                            domain = "not_found"
                    else:
                        print(f"    Semantic: domain rejected, no correction available ({vresult.get('reason', '')})")
                        domain = "not_found"
                elif vstatus == "Unclear":
                    print(f"    Semantic: unclear ({vresult.get('reason', '')}) — demoting to low confidence")
                    company = dict(company)
                    company["confidence"] = "low"
                else:
                    print(f"    Semantic: correct ✓")

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

        # Confidence gate: split before Supabase write
        high_medium = [r for r in enriched if r.get("confidence", "medium") in ("high", "medium")]
        low_conf = [r for r in enriched if r.get("confidence", "medium") == "low"]
        if low_conf:
            print(f"\n  Confidence gate: dropped {len(low_conf)} LOW-confidence records")
            for r in low_conf:
                print(f"    DROP: {r.get('company_name', '?')} ({r.get('confidence', '?')})")
        review_records = [r for r in enriched if r.get("confidence", "medium") == "medium"]
        if review_records:
            review_path = OUTPUT_DIR / f"review-{date_str}.json"
            review_path.write_text(json.dumps(review_records, indent=2, ensure_ascii=False), encoding="utf-8")
            print(f"  Review queue: {len(review_records)} MEDIUM-confidence records -> {review_path}")

        # Supabase upsert (high + medium only) — delegated to SupabaseClient
        if self._supabase._ready():
            if self._supabase.table_exists(self.SUPABASE_TABLE):
                print(f"\n  Pushing to Supabase...")
                rows = [self.get_supabase_row(r, date_str) for r in high_medium]
                recent = self._supabase.fetch_recent(self.SUPABASE_TABLE, days=30)
                if recent:
                    print(f"    Cross-day dedup: scanning {len(recent)} rows from last 30 days")
                upserted = self._supabase.push_rows(self.SUPABASE_TABLE, rows, date_str, recent)
                print(f"  Supabase: {upserted}/{len(high_medium)} rows upserted")
            else:
                print(f"\n  Supabase: table '{self.SUPABASE_TABLE}' not found")
                self._supabase.create_table(self.SUPABASE_TABLE, self.get_supabase_schema_sql())
        else:
            print(f"\n  Supabase: SKIPPED (no SUPABASE_URL/SUPABASE_KEY)")

        # Webhook push (Clay, Zapier, etc.) — high + medium only
        if self.WEBHOOK_URL:
            print(f"\n  Pushing to webhook...")
            sent = self.push_to_webhook(high_medium, date_str)
            print(f"  Webhook: {sent}/{len(high_medium)} rows sent")

        return csv_path, json_path

    def get_supabase_schema_sql(self) -> str:
        """Return SQL to create the target table. Override in subclass."""
        return ""

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
        self._run_date = date_str

        STAGE_DIR.mkdir(parents=True, exist_ok=True)

        print(f"\n{'='*60}")
        print(f"  {self.PIPELINE_NAME.upper()} -- {date_str}")
        print(f"  TBS: {args.tbs} | Skip enrich: {args.skip_enrich}")
        print(f"  API keys: OpenAI={'YES' if OPENAI_API_KEY else 'NO'} | Firecrawl={'YES' if FIRECRAWL_API_KEY else 'NO'} | Supabase={'YES' if (SUPABASE_URL and SUPABASE_KEY) else 'NO'}")
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
