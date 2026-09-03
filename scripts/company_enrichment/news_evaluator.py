"""Deterministic scorer for the news-product-launches signal loop.

Ground-truth record body (``benchmarks/signals/news-product-launches/ground-truth/<company>.yaml``)::

    company_id: saas-01
    as_of: '2026-08-18'
    recent_window_days: 180
    events:
      - date: '2024-04-11'          # YYYY-MM-DD or YYYY-MM
        headline_aliases: [Expanded leadership team]
        source_domain: prnewswire.com
        kind: news                  # news | launch
        event_type: leadership
        evidence_ids: [ev-...]

Scoring (weights must equal ``NEWS_WEIGHTS``):

- ``events`` 0.60: F1 of payload events matched to ground-truth events. A
  match is a date within three days (same month when either side is
  month-only) plus either the same source registrable domain or at least one
  shared Evidence ID, so a first-party copy of a wire release still matches. Matching is kind-agnostic so
  the ``kind`` component can measure placement; ground-truth events older
  than ``recent_window_days`` before ``as_of`` are optional (they count when
  matched but never against recall).
- ``citation`` 0.25: fraction of payload events whose ``evidence_ids`` share at
  least one ID with the matched ground-truth event.
- ``kind`` 0.15: fraction of matched events placed in the correct collection.

When ground truth has zero events the case is all-or-nothing: 1.0 when the
payload has zero events and declares both collections unknown, otherwise 0
with the hard failure ``invented_event``. Any event whose date is neither written
in the excerpts it cites nor carried in a cited Evidence URL path
(``/2026/07/01/``) is the hard failure ``uncited_date``.

``ground_news_payload`` is the deterministic output-hygiene step the loop runs
on the model payload before scoring or shipping: an event whose date is not
stated by its cited Evidence is dropped (never emitted), and a collection that
empties is declared unknown. Grounding is code, not model judgment.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
import re
from typing import Any, Mapping, Sequence

from .contracts import CompanyDossier
from .news_contracts import (
    EVENT_TYPES_BY_KIND, NEWS_FIELDS, NewsEvent, NewsOutput, normalize_event_date,
    parse_news_event, parse_news_output,
)
from .signal_ground_truth import SignalGroundTruthRecord
from .signal_loop import CaseScore

NEWS_ENRICHMENT_ID = "news-product-launches"
NEWS_WEIGHTS = {"events": Decimal(".60"), "citation": Decimal(".25"), "kind": Decimal(".15")}
NEWS_EVALUATION_DEPENDENCIES = (
    normalize_event_date, parse_news_event, parse_news_output,
)
DEFAULT_RECENT_WINDOW_DAYS = 180
DATE_TOLERANCE_DAYS = 3
GT_KINDS = {"news": "news", "launch": "launches"}
_MONTH_NAMES = (
    "January", "February", "March", "April", "May", "June", "July", "August", "September",
    "October", "November", "December",
)
_MONTH_ABBREVIATIONS = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Sept", "Oct", "Nov", "Dec",
)


def _domain(value: str) -> str:
    from urllib.parse import urlparse

    host = value.strip().lower()
    if "://" in host:
        host = urlparse(host).netloc
    host = host.split("@")[-1].split(":")[0]
    return host[4:] if host.startswith("www.") else host


class GroundTruthEvent:
    __slots__ = ("date", "aliases", "source_domain", "kind", "event_type", "evidence_ids",
                 "optional")

    def __init__(self, value: Mapping[str, Any], index: int) -> None:
        label = f"events[{index}]"
        if not isinstance(value, Mapping):
            raise ValueError(f"{label} must be a mapping")
        expected = {"date", "headline_aliases", "source_domain", "kind", "event_type",
                    "evidence_ids"}
        if set(value) != expected:
            raise ValueError(f"{label} keys must exactly match {sorted(expected)}")
        self.date = normalize_event_date(value["date"])
        aliases = value["headline_aliases"]
        if not isinstance(aliases, (list, tuple)) or not aliases or any(
            not isinstance(item, str) or not item.strip() for item in aliases
        ):
            raise ValueError(f"{label}.headline_aliases must contain text")
        self.aliases = tuple(" ".join(item.split()) for item in aliases)
        domain = value["source_domain"]
        if not isinstance(domain, str) or not domain.strip() or "/" in domain:
            raise ValueError(f"{label}.source_domain must be a bare domain")
        self.source_domain = _domain(domain)
        if value["kind"] not in GT_KINDS:
            raise ValueError(f"{label}.kind must be news or launch")
        self.kind = GT_KINDS[value["kind"]]
        event_type = value["event_type"]
        if event_type not in EVENT_TYPES_BY_KIND[self.kind]:
            raise ValueError(f"{label}.event_type is not valid for {self.kind}")
        self.event_type = event_type
        self.evidence_ids = tuple(value["evidence_ids"])
        self.optional = False


def _record_events(record: SignalGroundTruthRecord) -> tuple[list[GroundTruthEvent], date, int]:
    body = record.body
    extra = set(body) - {"events", "as_of", "recent_window_days"}
    if extra:
        raise ValueError(f"news ground truth has unexpected keys: {sorted(extra)}")
    events_value = body.get("events")
    if not isinstance(events_value, (list, tuple)):
        raise ValueError("news ground truth events must be a list")
    as_of_value = body.get("as_of")
    if isinstance(as_of_value, date):
        as_of = as_of_value
    else:
        try:
            as_of = date.fromisoformat(str(as_of_value))
        except (TypeError, ValueError) as error:
            raise ValueError("news ground truth as_of must be YYYY-MM-DD") from error
    window = body.get("recent_window_days", DEFAULT_RECENT_WINDOW_DAYS)
    if not isinstance(window, int) or window <= 0:
        raise ValueError("recent_window_days must be a positive integer")
    events = [GroundTruthEvent(item, index) for index, item in enumerate(events_value)]
    cutoff = as_of - timedelta(days=window)
    for event in events:
        first_day = date.fromisoformat(event.date if len(event.date) == 10 else event.date + "-01")
        event.optional = first_day < cutoff
    return events, as_of, window


def validate_news_record(record: SignalGroundTruthRecord, dossier: CompanyDossier) -> None:
    """``RecordValidator`` for ``dataset_loader``: strict body shape."""
    _record_events(record)


def _dates_match(payload_date: str, truth_date: str) -> bool:
    if len(payload_date) == 7 or len(truth_date) == 7:
        return payload_date[:7] == truth_date[:7]
    delta = abs(date.fromisoformat(payload_date) - date.fromisoformat(truth_date))
    return delta <= timedelta(days=DATE_TOLERANCE_DAYS)


def date_variants(value: str) -> tuple[str, ...]:
    """Text forms of a payload date that count as 'stated' in an excerpt."""
    year = value[:4]
    month = int(value[5:7])
    names = (_MONTH_NAMES[month - 1], *(
        abbreviation for abbreviation in _MONTH_ABBREVIATIONS
        if _MONTH_NAMES[month - 1].startswith(abbreviation)
    ))
    if len(value) == 7:
        variants = [value] + [f"{name} {year}" for name in names]
        return tuple(dict.fromkeys(variants))
    day = int(value[8:10])
    variants = [value, f"{value[5:7]}/{value[8:10]}/{year}", f"{month}/{day}/{year}"]
    for name in names:
        variants.extend((
            f"{name} {day}, {year}", f"{name} {day} {year}", f"{name}. {day}, {year}",
            f"{day} {name} {year}", f"{day} {name}, {year}", f"{name} {day:02d}, {year}",
        ))
    return tuple(dict.fromkeys(variants))


def url_date_variants(value: str) -> tuple[str, ...]:
    """Path forms of a date as wire services and newsrooms publish them
    (``/2026/07/01/``, ``/2026-07-01-``); month-only dates have no path form
    precise enough to count."""
    if len(value) != 10:
        return ()
    year, month, day = value[:4], value[5:7], value[8:10]
    return (f"/{year}/{month}/{day}/", f"/{year}-{month}-{day}")


def date_is_cited(
    event: NewsEvent, excerpts: Mapping[str, str], urls: Mapping[str, str] | None = None,
) -> bool:
    """True when the event's date is stated by the Evidence it cites: written in
    a cited excerpt, or carried in a cited Evidence URL path."""
    cited = " ".join(excerpts.get(evidence_id, "") for evidence_id in event.evidence_ids).lower()
    if any(variant.lower() in cited for variant in date_variants(event.date)):
        return True
    if not urls:
        return False
    cited_urls = " ".join(urls.get(evidence_id, "") for evidence_id in event.evidence_ids).lower()
    return any(variant in cited_urls for variant in url_date_variants(event.date))


# Pages that describe a product or help a user rather than announce an event.
# Their crawl date is never an event date, so an event supported only by such
# pages (search-result pages do not count either way) is dropped.
_EVERGREEN_HOSTS = ("help.", "docs.", "support.", "documentation.", "kb.")
# Listing pages (author, tag, category) are deliberately not here: the collector
# stores dated post snippets under them, and those snippets announce real events.
_EVERGREEN_PATHS = (
    "/features/", "/feature/", "/solutions/", "/solution/", "/product/", "/products/",
    "/platform/", "/pricing", "/help/", "/docs/", "/documentation/", "/support/",
    "/en/articles/", "/search",
)
_SEARCH_RESULT_HOSTS = ("google.com", "bing.com", "duckduckgo.com")


def _url_parts(url: str) -> tuple[str, str]:
    from urllib.parse import urlparse

    parsed = urlparse(url.strip().lower())
    host = parsed.netloc.split("@")[-1].split(":")[0]
    path = parsed.path or "/"
    return host, path if path.endswith("/") else path + "/"


def is_evergreen_url(url: str) -> bool:
    host, path = _url_parts(url)
    return host.startswith(_EVERGREEN_HOSTS) or any(
        marker in path for marker in _EVERGREEN_PATHS
    )


def is_search_result_url(url: str) -> bool:
    host, _ = _url_parts(url)
    host = host[4:] if host.startswith("www.") else host
    return host in _SEARCH_RESULT_HOSTS


def evergreen_only(event: NewsEvent, urls: Mapping[str, str]) -> bool:
    """True when every cited non-search Evidence URL is an evergreen page."""
    cited = [urls.get(evidence_id, "") for evidence_id in event.evidence_ids]
    substantive = [url for url in cited if url and not is_search_result_url(url)]
    return bool(substantive) and all(is_evergreen_url(url) for url in substantive)


def _parse_event(item: Any, kind: str, retained: set[str]) -> NewsEvent | str:
    """A NewsEvent, or the reason the entry is malformed."""
    try:
        event = parse_news_event(item, kind)
    except ValueError as error:
        return f"invalid_event: {str(error)[:80]}"
    if event.event_type not in EVENT_TYPES_BY_KIND[kind]:
        return f"invalid_event: {kind} event_type {event.event_type}"
    if not set(event.evidence_ids) <= retained:
        return "invalid_event: unretained evidence"
    return event


_HEADLINE_STOPWORDS = frozenset({
    "a", "an", "and", "as", "for", "in", "into", "its", "new", "of", "on", "the", "to", "with",
})
DUPLICATE_HEADLINE_OVERLAP = 0.5


def _headline_tokens(text: str) -> frozenset[str]:
    return frozenset(
        token for token in re.findall(r"[a-z0-9]+", text.lower()) if token not in _HEADLINE_STOPWORDS
    )


def same_event(prior: NewsEvent, event: NewsEvent, urls: Mapping[str, str]) -> bool:
    """Two entries report one real-world event when they share a cited Evidence
    ID on the same date, or cite the same page and their headlines overlap
    (the same launch reported once from a search snippet's date and once from
    the post's own date line)."""
    shared_ids = bool(set(prior.evidence_ids) & set(event.evidence_ids))
    if shared_ids and prior.date == event.date:
        return True
    prior_urls = {urls.get(evidence_id, "") for evidence_id in prior.evidence_ids} - {""}
    event_urls = {urls.get(evidence_id, "") for evidence_id in event.evidence_ids} - {""}
    substantive = {url for url in prior_urls & event_urls if not is_search_result_url(url)}
    if not substantive:
        return False
    left, right = _headline_tokens(prior.headline), _headline_tokens(event.headline)
    if not left or not right:
        return False
    overlap = len(left & right) / len(left | right)
    return overlap >= DUPLICATE_HEADLINE_OVERLAP


def ground_news_payload(
    payload: Mapping[str, Any], dossier: CompanyDossier,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Ground the model's events in the dossier's Evidence.

    Drops a malformed entry (a year-only date, an unknown event type;
    ``invalid_event``), an event whose date is not stated by its cited Evidence
    (``uncited_date``), an event supported only by evergreen pages
    (``evergreen_page``), and a repeat of an event already kept (``same_event``:
    a shared Evidence ID on the same date, or the same cited page with an
    overlapping headline; ``duplicate_event``). A collection that empties is
    declared unknown. Returns the grounded payload and a report of what was
    dropped. A payload whose top-level shape violates the contract is returned
    unchanged with ``report["error"]`` so the evaluator records the failure.
    """
    if not isinstance(payload, Mapping) or set(payload) - {"unknowns"} != set(NEWS_FIELDS) or any(
        not isinstance(payload[kind], (list, tuple)) for kind in NEWS_FIELDS
    ):
        return dict(payload) if isinstance(payload, Mapping) else {}, {
            "dropped": [], "error": "news output must contain exactly news and launches",
        }
    retained = {item.evidence_id for item in dossier.evidence}
    excerpts = {item.evidence_id: item.excerpt for item in dossier.evidence}
    urls = {item.evidence_id: item.url for item in dossier.evidence}
    grounded: dict[str, Any] = {kind: [] for kind in NEWS_FIELDS}
    dropped: list[dict[str, str]] = []
    kept: list[NewsEvent] = []
    for kind in NEWS_FIELDS:
        for item in payload[kind]:
            parsed = _parse_event(item, kind, retained)
            if isinstance(parsed, str):
                dropped.append({"collection": kind, "date": str((item or {}).get("date", ""))
                                if isinstance(item, Mapping) else "",
                                "headline": str((item or {}).get("headline", ""))
                                if isinstance(item, Mapping) else "", "reason": parsed})
                continue
            event = parsed
            reason = None
            if not date_is_cited(event, excerpts, urls):
                reason = "uncited_date"
            elif evergreen_only(event, urls):
                reason = "evergreen_page"
            elif any(same_event(prior, event, urls) for prior in kept):
                reason = "duplicate_event"
            if reason is None:
                grounded[kind].append(dict(item))
                kept.append(event)
            else:
                dropped.append({"collection": kind, "date": event.date,
                                "headline": event.headline, "reason": reason})
    declared = payload.get("unknowns", [])
    declared = [item for item in declared if isinstance(item, str)] if isinstance(
        declared, (list, tuple)) else []
    grounded["unknowns"] = list(dict.fromkeys([
        *(item for item in declared if item in NEWS_FIELDS and not grounded[item]),
        *(kind for kind in NEWS_FIELDS if not grounded[kind]),
    ]))
    return grounded, {"dropped": dropped}


def _match_events(
    payload: Sequence[tuple[str, NewsEvent]], truth: Sequence[GroundTruthEvent],
) -> dict[int, int]:
    """Greedy one-to-one match: payload index -> ground-truth index."""
    matched: dict[int, int] = {}
    used: set[int] = set()
    for index, (_, event) in enumerate(payload):
        source_domain = _domain(event.source_url)
        cited = set(event.evidence_ids)
        for truth_index, candidate in enumerate(truth):
            if truth_index in used or not _dates_match(event.date, candidate.date):
                continue
            # Same event reported by the wire service and the company's own
            # newsroom: either the source domain or a shared Evidence ID
            # identifies it, so a first-party citation is not a miss.
            same_source = candidate.source_domain == source_domain
            shared_evidence = bool(cited & set(candidate.evidence_ids))
            if same_source or shared_evidence:
                matched[index] = truth_index
                used.add(truth_index)
                break
    return matched


def _ratio(numerator: int, denominator: int) -> Decimal:
    return Decimal(numerator) / Decimal(denominator) if denominator else Decimal("0")


def _case(company_id: str, components: Mapping[str, Decimal],
          failures: tuple[str, ...]) -> CaseScore:
    score = sum((NEWS_WEIGHTS[key] * value for key, value in components.items()), Decimal("0"))
    return CaseScore(company_id, dict(components), score, failures)


def score_news(
    payload: Mapping[str, Any], record: SignalGroundTruthRecord, dossier: CompanyDossier,
) -> CaseScore:
    zero = {key: Decimal("0") for key in NEWS_WEIGHTS}
    retained = {item.evidence_id for item in dossier.evidence}
    try:
        output = parse_news_output(payload, retained)
    except ValueError:
        return _case(record.company_id, zero, ("contract_violation",))
    truth, _, _ = _record_events(record)
    excerpts = {item.evidence_id: item.excerpt for item in dossier.evidence}
    urls = {item.evidence_id: item.url for item in dossier.evidence}
    events = output.events
    failures: list[str] = []
    for kind, event in events:
        if not date_is_cited(event, excerpts, urls):
            failures.append("uncited_date")
            break

    if not truth:
        clean = not events and set(output.unknowns) == set(NEWS_FIELDS)
        if clean and not failures:
            return _case(record.company_id, {key: Decimal("1") for key in NEWS_WEIGHTS}, ())
        if events:
            failures.append("invented_event")
        return _case(record.company_id, zero, tuple(dict.fromkeys(failures)))

    matched = _match_events(events, truth)
    required = [index for index, item in enumerate(truth) if not item.optional]
    recall_hits = sum(1 for truth_index in matched.values() if not truth[truth_index].optional)
    precision = _ratio(len(matched), len(events))
    recall = _ratio(recall_hits, len(required)) if required else Decimal("1")
    f1 = (2 * precision * recall / (precision + recall)) if precision + recall else Decimal("0")
    citation_hits = sum(
        1 for index, truth_index in matched.items()
        if set(events[index][1].evidence_ids) & set(truth[truth_index].evidence_ids)
    )
    kind_hits = sum(
        1 for index, truth_index in matched.items() if events[index][0] == truth[truth_index].kind
    )
    components = {
        "events": f1,
        "citation": _ratio(citation_hits, len(events)),
        "kind": _ratio(kind_hits, len(matched)),
    }
    return _case(record.company_id, components, tuple(dict.fromkeys(failures)))
