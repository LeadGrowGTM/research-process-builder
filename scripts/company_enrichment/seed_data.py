from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


EMPTY_VALUES = {"", "null", "null+", "none", "n/a", "na", "not_found", "not_enriched"}


@dataclass(frozen=True, slots=True)
class CompanySeed:
    company_name: str
    domain: str | None
    linkedin_company_url: str | None
    headcount: int | None
    employee_size: str | None
    industry: str | None
    industry_tags: tuple[str, ...]
    products_services: str | None
    description: str | None
    seo_description: str | None
    company_type: str | None
    country: str | None
    state: str | None
    city: str | None
    founding_year: int | None
    annual_revenue_band: str | None
    total_funding: int | None
    last_funding_type: str | None
    last_funding_amount: int | None
    last_funding_date: str | None
    technologies: tuple[str, ...]
    target_customer: str | None = None
    provenance: str = "ai_ark_seed"


def _text(value: str | None) -> str | None:
    normalized = (value or "").strip()
    return None if normalized.lower() in EMPTY_VALUES else normalized


def _integer(value: str | None) -> int | None:
    normalized = _text(value)
    if normalized is None:
        return None
    try:
        return int(normalized.replace(",", "").replace("$", ""))
    except ValueError:
        return None


def _domain(value: str | None) -> str | None:
    normalized = _text(value)
    if normalized is None:
        return None
    candidate = normalized if "://" in normalized else f"https://{normalized}"
    parsed = urlparse(candidate)
    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host or None


def _tuple(value: str | None) -> tuple[str, ...]:
    normalized = _text(value)
    if normalized is None:
        return ()
    return tuple(item.strip() for item in normalized.split(",") if item.strip())


def load_ai_ark_seed(path: Path, *, reject_duplicate_domains: bool = True) -> tuple[CompanySeed, ...]:
    records: list[CompanySeed] = []
    seen_domains: set[str] = set()
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row_number, row in enumerate(csv.DictReader(handle), start=2):
            company_name = _text(row.get("Company Name"))
            if company_name is None:
                raise ValueError(f"row {row_number} is missing Company Name")
            domain = _domain(row.get("Website"))
            if domain and domain in seen_domains and reject_duplicate_domains:
                raise ValueError(f"duplicate domain at row {row_number}: {domain}")
            if domain:
                seen_domains.add(domain)
            records.append(
                CompanySeed(
                    company_name=company_name,
                    domain=domain,
                    linkedin_company_url=_text(row.get("LinkedIn")),
                    headcount=_integer(row.get("Headcount")),
                    employee_size=_text(row.get("Employee Size")),
                    industry=_text(row.get("Industry")),
                    industry_tags=_tuple(row.get("Industry Tags")),
                    products_services=_text(row.get("Product and Services")),
                    description=_text(row.get("Description")),
                    seo_description=_text(row.get("SEO Description")),
                    company_type=_text(row.get("Company Type")),
                    country=_text(row.get("Company Country")),
                    state=_text(row.get("Company State")),
                    city=_text(row.get("Company City")),
                    founding_year=_integer(row.get("Founding Year")),
                    annual_revenue_band=_text(row.get("Annual Revenue")),
                    total_funding=_integer(row.get("Total Funding")),
                    last_funding_type=_text(row.get("Last Funding Type")),
                    last_funding_amount=_integer(row.get("Last Funding Amount")),
                    last_funding_date=_text(row.get("Last Funding Date")),
                    technologies=_tuple(row.get("Technologies")),
                )
            )
    return tuple(records)


def missing_seed_fields(seed: CompanySeed) -> tuple[str, ...]:
    required = ("domain", "description", "industry", "products_services", "target_customer")
    return tuple(field for field in required if getattr(seed, field) in (None, "", ()))


def _completeness(seed: CompanySeed) -> int:
    return sum(
        value not in (None, "", ())
        for field_name in seed.__dataclass_fields__
        if field_name not in {"provenance", "target_customer"}
        for value in (getattr(seed, field_name),)
    )


def deduplicate_ai_ark_seed(records: tuple[CompanySeed, ...]) -> tuple[CompanySeed, ...]:
    by_identity: dict[str, CompanySeed] = {}
    for record in records:
        identity = record.domain or record.linkedin_company_url or record.company_name.casefold()
        current = by_identity.get(identity)
        if current is None or _completeness(record) > _completeness(current):
            by_identity[identity] = record
    return tuple(sorted(by_identity.values(), key=lambda item: (item.company_name.casefold(), item.domain or "")))
