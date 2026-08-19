"""Search-plus-scrape collection stage shared by the news and competitor loops.

``collect_search_signals`` runs a ``SearchPlan`` for one benchmark company:

1. expand each ``QueryTemplate`` with ``{{domain}}``, ``{{company_name}}``,
   ``{{category}}`` from ``benchmarks/companies.yaml`` (category falls back
   to the base dossier's ``offers`` assertion) and run it through the search
   provider (capped by ``caps["queries"]``);
2. keep every SERP result as a small dated ``SourceRecord`` so the prompt can
   cite a dated item even when the page scrape fails, plus one SERP-page
   record per query that carries the query's paid cost;
3. dedupe result URLs by registrable domain plus path, drop the subject's own
   template, job, and login pages, and scrape the top URLs free-only (capped
   by ``caps["scrapes"]``);
4. probe the plan's first-party paths (``/blog``, ``/news``, ...) with the
   level-1 scraper only, keeping pages with real content;
5. turn every ``SourceRecord`` into a content-addressed ``EvidenceRef``
   (``ev-<16 hex>`` of the content SHA-256, the same derivation the base
   dossiers use) and merge them onto the base dossier via ``signal_dossier``.

A single provider failure never aborts the company: it is normalized and
appended to the collection log, which the entrypoints write beside the signal
dossier as ``<company>.collection.json``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Callable, Mapping, Protocol, Sequence
from urllib.parse import quote_plus, urlparse

import yaml

from .contracts import CompanyDossier, EvidenceRef, MAX_EXCERPT_CHARS, canonical_json
from .evidence import EvidenceStore, SourceRecord
from .providers import (
    KnownUrlRequest,
    ProviderFailure,
    ProviderRouter,
    ScrapeProvider,
    SearchProvider,
    SearchRequest,
    SearchResult,
    normalize_failure,
)
from .signal_evidence import signal_dossier
from .signal_loop import CollectRequest

QUERY_MODES = ("web", "news")
SERP_SOURCE_TYPE = "serp"
SERP_PAGE_SOURCE_TYPE = "serp_page"
PAGE_SOURCE_TYPE = "page"
FIRST_PARTY_SOURCE_TYPE = "first_party"
DEFAULT_SEARCH_COST_USD = "0.001"
ZERO_COST_USD = "0"
MIN_FIRST_PARTY_CHARS = 200
_OWN_PAGE_DROP = re.compile(
    r"/(templates?|careers?|jobs?|login|log-in|signin|sign-in|signup|sign-up|register|"
    r"account|auth)(/|$|[?#])", re.IGNORECASE,
)
_MONTHS = (
    "january|february|march|april|may|june|july|august|september|october|november|december|"
    "jan|feb|mar|apr|jun|jul|aug|sept|sep|oct|nov|dec"
)
DATE_PATTERN = re.compile(
    rf"\b(\d{{4}}-\d{{2}}-\d{{2}})(?!\d)"
    rf"|\b((?:{_MONTHS})\.?\s+\d{{1,2}},?\s+\d{{4}})\b"
    rf"|\b(\d{{1,2}}\s+(?:{_MONTHS})\.?,?\s+\d{{4}})\b"
    rf"|\b((?:{_MONTHS})\.?\s+\d{{4}})\b",
    re.IGNORECASE,
)


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty text")
    return value.strip()


@dataclass(frozen=True, slots=True)
class QueryTemplate:
    template: str
    mode: str = "web"
    tbs: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "template", _text(self.template, "template"))
        if self.mode not in QUERY_MODES:
            raise ValueError(f"mode must be one of {QUERY_MODES}")
        if self.tbs is not None:
            object.__setattr__(self, "tbs", _text(self.tbs, "tbs"))

    def render(self, facts: Mapping[str, str]) -> str:
        query = self.template
        for key in ("domain", "company_name", "category"):
            query = query.replace("{{" + key + "}}", facts.get(key, ""))
        return " ".join(query.split())


@dataclass(frozen=True, slots=True)
class SearchPlan:
    queries: tuple[QueryTemplate, ...]
    first_party_paths: tuple[str, ...]
    caps: Mapping[str, int]
    freshness_days: int
    top_urls_per_query: int = 3
    excerpt_chars: int = 1500

    def __post_init__(self) -> None:
        queries = tuple(self.queries)
        if not queries or not all(isinstance(item, QueryTemplate) for item in queries):
            raise ValueError("queries must contain at least one QueryTemplate")
        object.__setattr__(self, "queries", queries)
        paths = tuple(self.first_party_paths)
        if any(not isinstance(item, str) or not item.startswith("/") for item in paths):
            raise ValueError("first_party_paths must be absolute paths such as /blog")
        object.__setattr__(self, "first_party_paths", paths)
        caps = dict(self.caps)
        if set(caps) != {"queries", "scrapes"} or any(
            not isinstance(value, int) or value < 0 for value in caps.values()
        ):
            raise ValueError("caps must map queries and scrapes to non-negative integers")
        object.__setattr__(self, "caps", caps)
        if not isinstance(self.freshness_days, int) or self.freshness_days < 0:
            raise ValueError("freshness_days must be a non-negative integer")
        if not isinstance(self.top_urls_per_query, int) or self.top_urls_per_query < 0:
            raise ValueError("top_urls_per_query must be a non-negative integer")
        if not isinstance(self.excerpt_chars, int) or not 1 <= self.excerpt_chars <= 1900:
            raise ValueError("excerpt_chars must be between 1 and 1900")


class QueryModeProvider(Protocol):
    """A search provider that can derive a sibling for another mode/window."""

    def search(self, request: SearchRequest) -> SearchResult: ...

    def for_query(self, *, mode: str, tbs: str | None) -> SearchProvider: ...


class FallbackSearch:
    """Ordered search providers routed through ``ProviderRouter``.

    Providers may be instances or zero-argument factories (resolved once and
    shared across ``for_query`` siblings). A factory that raises (for example
    ``build_parallel_search`` without a key) and a provider whose ``search``
    raises ``ProviderFailure`` are both recorded as normalized failures on
    ``last_failures``; the next provider is tried. Only when every provider
    fails does the last ``ProviderFailure`` propagate.
    """

    def __init__(
        self, providers: Sequence[tuple[str, Any]], *, router: ProviderRouter | None = None,
        mode: str = "web", tbs: str | None = None, _cache: dict[str, Any] | None = None,
    ) -> None:
        if not providers:
            raise ValueError("FallbackSearch requires at least one provider")
        self._providers = tuple(providers)
        self._router = router or ProviderRouter()
        self._mode = mode
        self._tbs = tbs
        self._cache: dict[str, Any] = {} if _cache is None else _cache
        self.last_failures: list[dict[str, str]] = []
        self.provider = self._providers[0][0]
        self.cost_per_query_usd = DEFAULT_SEARCH_COST_USD

    def _order(self) -> tuple[str, ...]:
        ids = tuple(name for name, _ in self._providers)
        return self._router.route(_RouteDefinition(ids), _RouteDiscovery(ids[0], ids)).provider_ids

    def for_query(self, *, mode: str, tbs: str | None) -> "FallbackSearch":
        return FallbackSearch(
            self._providers, router=self._router, mode=mode, tbs=tbs, _cache=self._cache,
        )

    def _resolve(self, name: str, candidate: Any) -> SearchProvider:
        if name not in self._cache:
            is_factory = callable(candidate) and not hasattr(candidate, "search")
            self._cache[name] = candidate() if is_factory else candidate
        provider = self._cache[name]
        if hasattr(provider, "for_query"):
            provider = provider.for_query(mode=self._mode, tbs=self._tbs)
        return provider

    def search(self, request: SearchRequest) -> SearchResult:
        self.last_failures = []
        by_name = dict(self._providers)
        last_error: ProviderFailure | None = None
        for name in self._order():
            try:
                provider = self._resolve(name, by_name[name])
                result = provider.search(request)
            except ProviderFailure as error:
                failure = normalize_failure(error)
                self.last_failures.append({
                    "provider": name, "kind": failure.kind.value, "message": failure.message,
                })
                last_error = error
                continue
            self.provider = name
            self.cost_per_query_usd = str(
                getattr(provider, "cost_per_query_usd", DEFAULT_SEARCH_COST_USD)
            )
            return result
        raise last_error if last_error is not None else ProviderFailure("no search provider")


@dataclass(frozen=True, slots=True)
class _RouteDefinition:
    fallback_order: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _RouteDiscovery:
    selected_capability: str
    eligible_capabilities: tuple[str, ...]


@lru_cache(maxsize=8)
def _companies_index(path: str) -> Mapping[str, Mapping[str, Any]]:
    value = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    companies = value.get("companies") if isinstance(value, Mapping) else None
    if not isinstance(companies, list):
        raise ValueError(f"companies.yaml must contain a companies list: {path}")
    return {str(item["id"]): item for item in companies if isinstance(item, Mapping)}


def company_facts(repo_root: Path, company_id: str, base: CompanyDossier) -> dict[str, str]:
    """Return ``company_name``, ``domain``, ``category`` for template substitution."""
    index = _companies_index(str(Path(repo_root) / "benchmarks" / "companies.yaml"))
    fixture = index.get(company_id)
    if fixture is None:
        raise ValueError(f"company {company_id} is not in benchmarks/companies.yaml")
    category = next(
        (str(item.value) for item in base.assertions if item.field == "offers" and item.value),
        "",
    )
    if not category:
        category = str(fixture.get("business_offer") or "")
    return {
        "company_name": _text(fixture.get("company_name"), "company_name"),
        "domain": registrable_domain(_text(fixture.get("domain"), "domain")),
        "category": " ".join(category.split()),
    }


def registrable_domain(host_or_url: str) -> str:
    host = host_or_url.strip().lower()
    if "://" in host:
        host = urlparse(host).netloc
    host = host.split("@")[-1].split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    return host


def url_identity(url: str) -> str:
    """Registrable domain plus normalized path (no query, fragment, or slash)."""
    parsed = urlparse(url)
    path = parsed.path.rstrip("/") or "/"
    return f"{registrable_domain(parsed.netloc)}{path.lower()}"


def subject_tokens(facts: Mapping[str, str]) -> tuple[str, ...]:
    """Lower-case markers that identify the subject company in text or a URL."""
    name = facts["company_name"].lower()
    domain = facts["domain"].lower()
    tokens = {domain, domain.split(".")[0], re.sub(r"[^a-z0-9]+", "", name)}
    words = [word for word in re.findall(r"[a-z0-9]+", name) if len(word) >= 4]
    if words:
        tokens.add(words[0])
    return tuple(sorted(token for token in tokens if len(token) >= 4))


def mentions_subject(url: str, text: str, facts: Mapping[str, str]) -> bool:
    """True when the SERP item is on the subject's domain or names the subject."""
    if registrable_domain(urlparse(url).netloc) == facts["domain"]:
        return True
    haystack = re.sub(r"[^a-z0-9]+", "", (url + " " + text).lower())
    return any(token in haystack for token in subject_tokens(facts))


def is_own_noise_page(url: str, domain: str) -> bool:
    parsed = urlparse(url)
    if registrable_domain(parsed.netloc) != domain:
        return False
    return bool(_OWN_PAGE_DROP.search(parsed.path or "/"))


def detect_date_line(text: str) -> str | None:
    match = DATE_PATTERN.search(text)
    if match is None:
        return None
    return " ".join(match.group(0).split())


def evidence_ref(source: SourceRecord) -> EvidenceRef:
    """Content-addressed reference identical to ``EvidenceStore.put`` output."""
    content_hash = hashlib.sha256(source.content.encode("utf-8")).hexdigest()
    return EvidenceRef(
        f"ev-{content_hash[:16]}", source.url, source.retrieved_at, content_hash, source.excerpt,
    )


def serp_url(query: str, mode: str, tbs: str | None) -> str:
    url = f"https://www.google.com/search?q={quote_plus(query)}"
    if mode == "news":
        url += "&tbm=nws"
    if tbs:
        url += f"&tbs={quote_plus(tbs)}"
    return url


def page_excerpt(text: str, limit: int) -> str:
    """Detected date line (when present) plus the first ``limit`` characters."""
    body = text.strip()
    date_line = detect_date_line(body)
    head = body[:limit]
    excerpt = f"Detected date: {date_line}\n{head}" if date_line else head
    return excerpt[:MAX_EXCERPT_CHARS]


@dataclass
class CollectionOutcome:
    dossier: CompanyDossier
    log: list[dict[str, Any]] = field(default_factory=list)
    sources: tuple[SourceRecord, ...] = ()

    @property
    def paid_cost_usd(self) -> str:
        return str(sum((Decimal(item.paid_cost_usd) for item in self.sources), Decimal("0")))


def _log_failure(log: list[dict[str, Any]], step: str, target: str, error: BaseException) -> None:
    failure = normalize_failure(error)
    log.append({
        "step": step, "target": target, "status": "failed",
        "kind": failure.kind.value, "message": failure.message,
    })


def _search_provider_for(search: Any, query: QueryTemplate) -> SearchProvider:
    if hasattr(search, "for_query"):
        return search.for_query(mode=query.mode, tbs=query.tbs)
    return search


def _provider_name(provider: Any, default: str) -> str:
    name = getattr(provider, "provider", None)
    return name if isinstance(name, str) and name.strip() else default


def _scrape_records(
    scrape: ScrapeProvider, url: str, *, source_type: str, plan: SearchPlan, now: datetime,
    log: list[dict[str, Any]], step: str, min_chars: int = 1,
) -> list[SourceRecord]:
    try:
        result = scrape.scrape(KnownUrlRequest(url))
    except (ProviderFailure, ValueError) as error:
        _log_failure(log, step, url, error)
        return []
    records: list[SourceRecord] = []
    thin = 0
    for observation in result.observations:
        text = observation.excerpt.strip()
        if len(text) < min_chars:
            thin += 1
            continue
        records.append(SourceRecord(
            url=observation.url, retrieved_at=now, source_type=source_type,
            provider=_provider_name(scrape, "known-url-scrape"),
            content=f"URL: {observation.url}\n{text}",
            excerpt=page_excerpt(text, plan.excerpt_chars),
            freshness_days=plan.freshness_days, paid_cost_usd=ZERO_COST_USD,
        ))
    status = "ok" if records else ("thin_content" if thin else "no_content")
    log.append({"step": step, "target": url, "status": status,
                "waterfall_level": result.waterfall_level, "records": len(records)})
    return records


def collect_search_signals(
    request: CollectRequest, *, plan: SearchPlan, search: SearchProvider,
    scrape: ScrapeProvider, now: datetime, first_party_scrape: ScrapeProvider | None = None,
    evidence_store: EvidenceStore | None = None,
    search_cost_usd: str = DEFAULT_SEARCH_COST_USD,
) -> CollectionOutcome:
    """Run ``plan`` for one company and return the merged signal dossier plus log."""
    if now.tzinfo is None:
        raise ValueError("now must include a timezone")
    facts = company_facts(request.repo_root, request.company_id, request.base)
    domain = facts["domain"]
    log: list[dict[str, Any]] = [{"step": "facts", "status": "ok", **facts}]
    sources: list[SourceRecord] = []
    candidates: list[str] = []
    seen_urls: set[str] = set()

    for query in plan.queries[: plan.caps["queries"]]:
        rendered = query.render(facts)
        provider = _search_provider_for(search, query)
        try:
            result = provider.search(SearchRequest(rendered))
        except (ProviderFailure, ValueError) as error:
            _log_failure(log, "search", rendered, error)
            log[-1]["fallbacks"] = list(getattr(provider, "last_failures", ()))
            continue
        cost = getattr(provider, "cost_per_query_usd", search_cost_usd)
        provider_name = _provider_name(provider, "search")
        listing_lines = []
        kept = 0
        dropped_off_subject = 0
        for rank, observation in enumerate(result.observations, 1):
            identity = url_identity(observation.url)
            listing_lines.append(f"{rank}. {observation.url}\n{observation.excerpt}")
            if not mentions_subject(observation.url, observation.excerpt, facts):
                dropped_off_subject += 1
                continue
            sources.append(SourceRecord(
                url=observation.url, retrieved_at=now, source_type=SERP_SOURCE_TYPE,
                provider=provider_name,
                content=f"URL: {observation.url}\n{observation.excerpt}",
                excerpt=page_excerpt(observation.excerpt, plan.excerpt_chars),
                freshness_days=plan.freshness_days, paid_cost_usd=ZERO_COST_USD,
            ))
            if identity in seen_urls or is_own_noise_page(observation.url, domain):
                continue
            if kept < plan.top_urls_per_query:
                seen_urls.add(identity)
                candidates.append(observation.url)
                kept += 1
        listing = "\n".join(listing_lines) or "No results returned."
        page_url = serp_url(rendered, query.mode, query.tbs)
        sources.append(SourceRecord(
            url=page_url, retrieved_at=now, source_type=SERP_PAGE_SOURCE_TYPE,
            provider=provider_name,
            content=f"URL: {page_url}\nQuery: {rendered}\nMode: {query.mode}\n{listing}",
            excerpt=f"Query: {rendered}\n{listing}"[: plan.excerpt_chars],
            freshness_days=plan.freshness_days, paid_cost_usd=str(cost),
        ))
        log.append({
            "step": "search", "target": rendered, "status": "ok", "mode": query.mode,
            "tbs": query.tbs, "provider": provider_name, "results": len(result.observations),
            "dropped_off_subject": dropped_off_subject, "paid_cost_usd": str(cost),
            "fallbacks": list(getattr(provider, "last_failures", ())),
        })

    for url in candidates[: plan.caps["scrapes"]]:
        sources.extend(_scrape_records(
            scrape, url, source_type=PAGE_SOURCE_TYPE, plan=plan, now=now, log=log,
            step="scrape",
        ))

    probe = first_party_scrape or scrape
    for path in plan.first_party_paths:
        url = f"https://{domain}{path}"
        if url_identity(url) in seen_urls:
            log.append({"step": "first_party", "target": url, "status": "already_scraped"})
            continue
        sources.extend(_scrape_records(
            probe, url, source_type=FIRST_PARTY_SOURCE_TYPE, plan=plan, now=now, log=log,
            step="first_party", min_chars=MIN_FIRST_PARTY_CHARS,
        ))

    refs: list[EvidenceRef] = []
    seen_ids: set[str] = set()
    for source in sources:
        ref = evidence_store.put(source) if evidence_store is not None else evidence_ref(source)
        if ref.evidence_id in seen_ids:
            continue
        seen_ids.add(ref.evidence_id)
        refs.append(ref)
    dossier = signal_dossier(request.company_id, request.base, refs, assertions=(), unknowns=())
    log.append({"step": "merge", "status": "ok", "signal_evidence": len(refs),
                "base_evidence": len(request.base.evidence)})
    return CollectionOutcome(dossier, log, tuple(sources))


def collection_log_path(signal_path: Path) -> Path:
    return Path(signal_path).with_suffix(".collection.json")


def write_collection_log(path: Path, outcome: CollectionOutcome, *, extra: Mapping[str, Any] | None = None) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    value = {
        "company_id": outcome.dossier.company_id, "entries": outcome.log,
        "paid_cost_usd": outcome.paid_cost_usd,
        "signal_evidence": len(outcome.dossier.evidence), **(extra or {}),
    }
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(json.loads(canonical_json(value)), indent=2, sort_keys=True)
                         + "\n", encoding="utf-8")
    os.replace(temporary, path)


def bind_collect(
    *, enrichment_id: str, plan: SearchPlan, search_factory: Callable[[], SearchProvider],
    scrape_factory: Callable[[], ScrapeProvider],
    first_party_scrape_factory: Callable[[], ScrapeProvider] | None = None,
    clock: Callable[[], datetime] | None = None,
) -> Callable[[CollectRequest], CompanyDossier]:
    """Build a ``SignalSpec.collect`` callable that logs beside the signal file.

    Factories are invoked lazily on the first company so importing an
    entrypoint (or ``--dry-run``) never reads a secret or opens a socket.
    """
    from .signal_evidence import signal_dossier_path

    state: dict[str, Any] = {}

    def collect(request: CollectRequest) -> CompanyDossier:
        if "search" not in state:
            state["search"] = search_factory()
            state["scrape"] = scrape_factory()
            state["first_party"] = (
                first_party_scrape_factory() if first_party_scrape_factory else None
            )
        now = clock() if clock else datetime.now().astimezone()
        store = EvidenceStore(
            Path(request.repo_root) / "runs" / "company-enrichment" / enrichment_id / "evidence"
        )
        outcome = collect_search_signals(
            request, plan=plan, search=state["search"], scrape=state["scrape"], now=now,
            first_party_scrape=state["first_party"], evidence_store=store,
        )
        if request.benchmark_dir is not None:
            target = Path(request.repo_root) / request.benchmark_dir / f"{request.company_id}.yaml"
        else:
            target = signal_dossier_path(request.repo_root, enrichment_id, request.company_id)
        write_collection_log(collection_log_path(target), outcome, extra={
            "enrichment_id": enrichment_id, "collected_at": now.isoformat(),
        })
        return outcome.dossier

    return collect
