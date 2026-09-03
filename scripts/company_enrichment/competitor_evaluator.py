"""Deterministic scorer for the competitor-intelligence signal loop.

Ground-truth record body (``benchmarks/signals/competitor-intelligence/ground-truth/<company>.yaml``)::

    company_id: saas-01
    named:
      - {name: DashThis, aliases: [Dash This], domain: dashthis.com}
    inferred:
      - {name: Looker Studio, aliases: [Google Data Studio], domain: null}
    evidence_ids: [ev-...]        # Evidence the reviewer used to seal the set

Scoring (weights must equal ``COMPETITOR_WEIGHTS``):

- ``named_set`` 0.50: F1 of the payload's ``named`` entries against ground-truth
  ``named`` entries; two entries match when their normalized names agree
  (case/punctuation-insensitive, aliases included), when one normalized name
  is a prefix of the other and the shorter is at least five characters
  ("Informatica IDMC" against "Informatica"), or their domains agree.
- ``citation`` 0.30: fraction of payload entries (both buckets) whose cited
  excerpts contain the competitor's name or domain.
- ``labeling`` 0.20: fraction of payload entries that match any ground-truth
  entry and sit in the bucket ground truth assigns.

When ground truth lists no competitors the case is all-or-nothing: 1.0 when
both payload buckets are empty and ``competitors`` is declared unknown,
otherwise 0 with the hard failure ``invented_competitor``. Hard failures also
fire for ``hallucinated_competitor`` (name and domain absent from every cited
excerpt) and ``self_competitor`` (the subject company listed as its own
competitor; the subject is the dossier's ``identity`` assertion and the
registrable domain of its first base Evidence URL).

``ground_competitor_payload`` is the deterministic output-hygiene step the loop
runs on the model payload before scoring or shipping: each entry's citations
are narrowed to the Evidence that actually mentions the competitor (repaired
from the whole dossier when the model cited the wrong item), a domain the cited
Evidence does not state is nulled, and an entry that no Evidence mentions, or
that names the subject itself, is dropped. Grounding is code, not model
judgment.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Mapping
from urllib.parse import urlparse

from .competitor_contracts import (
    BUCKETS, COMPETITOR_FIELDS, Competitor, normalize_domain, normalize_name,
    parse_competitors_output,
)
from .contracts import CompanyDossier
from .signal_ground_truth import SignalGroundTruthRecord
from .signal_loop import CaseScore

COMPETITOR_ENRICHMENT_ID = "competitor-intelligence"
COMPETITOR_WEIGHTS = {
    "named_set": Decimal(".50"), "citation": Decimal(".30"), "labeling": Decimal(".20"),
}
_PREFIX_MATCH_MIN = 5


class GroundTruthCompetitor:
    __slots__ = ("name", "keys", "domain", "bucket")

    def __init__(self, value: Mapping[str, Any], bucket: str, index: int) -> None:
        label = f"{bucket}[{index}]"
        if not isinstance(value, Mapping) or set(value) != {"name", "aliases", "domain"}:
            raise ValueError(f"{label} must contain exactly name, aliases, domain")
        name = value["name"]
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"{label}.name must be text")
        aliases = value["aliases"]
        if not isinstance(aliases, (list, tuple)) or any(
            not isinstance(item, str) or not item.strip() for item in aliases
        ):
            raise ValueError(f"{label}.aliases must be a list of text")
        self.name = " ".join(name.split())
        self.keys = frozenset(normalize_name(item) for item in (name, *aliases))
        self.domain = normalize_domain(value["domain"])
        self.bucket = bucket

    def matches(self, competitor: Competitor) -> bool:
        if competitor.key in self.keys:
            return True
        # A product name that begins with the vendor name (or the reverse),
        # for example "Informatica IDMC" against "Informatica"; short keys are
        # excluded so "Zip" never claims "Zapier".
        if any(
            len(min(key, competitor.key, key=len)) >= _PREFIX_MATCH_MIN
            and (competitor.key.startswith(key) or key.startswith(competitor.key))
            for key in self.keys
        ):
            return True
        return bool(self.domain and competitor.domain and competitor.domain == self.domain)


def _record_competitors(record: SignalGroundTruthRecord) -> dict[str, list[GroundTruthCompetitor]]:
    body = record.body
    if set(body) != {"named", "inferred", "evidence_ids"}:
        raise ValueError("competitor ground truth must contain named, inferred, evidence_ids")
    result: dict[str, list[GroundTruthCompetitor]] = {}
    for bucket in BUCKETS:
        items = body[bucket]
        if not isinstance(items, (list, tuple)):
            raise ValueError(f"competitor ground truth {bucket} must be a list")
        result[bucket] = [GroundTruthCompetitor(item, bucket, index)
                          for index, item in enumerate(items)]
    return result


def validate_competitor_record(record: SignalGroundTruthRecord, dossier: CompanyDossier) -> None:
    """``RecordValidator`` for ``dataset_loader``: strict body shape."""
    _record_competitors(record)


def subject_identity(dossier: CompanyDossier) -> tuple[str | None, str | None]:
    """Subject company name (identity assertion) and domain (first base Evidence URL)."""
    name = next((str(item.value) for item in dossier.assertions
                 if item.field == "identity" and item.value), None)
    domain = None
    if dossier.evidence:
        host = urlparse(dossier.evidence[0].url).netloc.lower().split("@")[-1].split(":")[0]
        domain = host[4:] if host.startswith("www.") else host
    return name, domain


def entry_is_cited(competitor: Competitor, excerpts: Mapping[str, str]) -> bool:
    cited = " ".join(excerpts.get(evidence_id, "") for evidence_id in competitor.evidence_ids)
    return _mentions(competitor, cited)


def _mentions(competitor: Competitor, text: str) -> bool:
    lowered = text.lower()
    if competitor.name.lower() in lowered:
        return True
    if competitor.key and competitor.key in normalize_name(text):
        return True
    return bool(competitor.domain and competitor.domain in lowered)


_COMPARISON_MARKERS = ("alternative", "competitor", "compar", " vs ", " vs.", " versus ")


def subject_terms(subject_name: str | None, subject_domain: str | None) -> tuple[str, ...]:
    """Normalized forms that count as naming the subject: the identity
    assertion, its suffix-free key, and the registrable domain label
    ("aPriori Technologies" at apriori.com -> aprioritechnologies, apriori)."""
    terms: list[str] = []
    if subject_name:
        terms.append(normalize_name(subject_name))
        first = subject_name.split()[0]
        if len(subject_name.split()) > 1 and len(first) >= 6:
            terms.append(normalize_name(first))
    if subject_domain:
        terms.append(normalize_name(subject_domain.split(".")[0]))
    return tuple(dict.fromkeys(term for term in terms if len(term) >= 4))


# Community threads mention tools people happen to use; that is an inference,
# not an explicit comparison, even when the thread asks for alternatives.
_COMMUNITY_HOSTS = (
    "reddit.com", "quora.com", "news.ycombinator.com", "stackexchange.com",
    "stackoverflow.com", "facebook.com", "linkedin.com", "x.com", "twitter.com",
    "youtube.com", "medium.com", "indiehackers.com", "producthunt.com",
)


def _registrable_host(url: str) -> str:
    host = urlparse(url.strip().lower()).netloc.split("@")[-1].split(":")[0]
    return host[4:] if host.startswith("www.") else host


def is_community_url(url: str) -> bool:
    host = _registrable_host(url)
    return any(host == item or host.endswith("." + item) for item in _COMMUNITY_HOSTS)


def explicit_comparison(text: str, subject: tuple[str, ...], url: str = "") -> bool:
    """True when ``text`` explicitly positions companies against the subject: it
    names the subject (any of ``subject_terms``) and talks about alternatives,
    competitors, or comparison, and it is not a community thread. This is the
    ground-truth authoring rule for the ``named`` bucket."""
    if url and is_community_url(url):
        return False
    lowered = text.lower()
    names_subject = bool(subject) and any(term in normalize_name(text) for term in subject)
    return names_subject and any(marker in lowered for marker in _COMPARISON_MARKERS)


def ground_competitor_payload(
    payload: Mapping[str, Any], dossier: CompanyDossier,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Ground every competitor entry in the dossier's Evidence.

    Each entry's bucket is decided by the Evidence, not the model: ``named``
    when any cited excerpt that mentions the competitor also explicitly
    compares it with the subject (``explicit_comparison``), otherwise
    ``inferred``. Returns the grounded payload and a report (``dropped``,
    ``repaired``, ``domains_nulled``, ``relabeled``). A payload that violates
    the contract is returned unchanged with ``report["error"]`` so the
    evaluator records the failure.
    """
    retained = {item.evidence_id for item in dossier.evidence}
    try:
        output = parse_competitors_output(payload, retained)
    except ValueError as error:
        return dict(payload), {"dropped": [], "error": str(error)[:200]}
    excerpts = {item.evidence_id: item.excerpt for item in dossier.evidence}
    urls = {item.evidence_id: item.url for item in dossier.evidence}
    subject_name, subject_domain = subject_identity(dossier)
    subject_key = normalize_name(subject_name) if subject_name else None
    subject = subject_terms(subject_name, subject_domain)
    buckets: dict[str, list[dict[str, Any]]] = {bucket: [] for bucket in BUCKETS}
    dropped: list[dict[str, str]] = []
    repaired = 0
    domains_nulled = 0
    relabeled = 0
    for model_bucket in BUCKETS:
        for competitor in getattr(output, model_bucket):
            if subject_key and competitor.key == subject_key:
                dropped.append({"bucket": model_bucket, "name": competitor.name,
                                "reason": "self_competitor"})
                continue
            cited = [evidence_id for evidence_id in competitor.evidence_ids
                     if _mentions(competitor, excerpts.get(evidence_id, ""))]
            if not cited:
                cited = [evidence_id for evidence_id, text in excerpts.items()
                         if _mentions(competitor, text)]
                repaired += bool(cited)
            if not cited:
                dropped.append({"bucket": model_bucket, "name": competitor.name,
                                "reason": "hallucinated_competitor"})
                continue
            bucket = "named" if any(
                explicit_comparison(excerpts[evidence_id], subject, urls[evidence_id])
                for evidence_id in cited
            ) else "inferred"
            relabeled += bucket != model_bucket
            domain = competitor.domain
            if domain is not None:
                stated = " ".join(
                    f"{excerpts[evidence_id]} {urls[evidence_id]}" for evidence_id in cited
                ).lower()
                if domain not in stated or domain == subject_domain:
                    domain = None
                    domains_nulled += 1
            buckets[bucket].append({
                "name": competitor.name, "domain": domain,
                "relationship": competitor.relationship, "why": competitor.why,
                "evidence_ids": cited,
            })
    conflicts = list(payload["competitors"].get("conflicts") or [])
    grounded: dict[str, Any] = {"competitors": {**buckets, "conflicts": conflicts}}
    grounded["unknowns"] = [] if any(buckets.values()) else list(COMPETITOR_FIELDS)
    return grounded, {"dropped": dropped, "repaired": repaired,
                      "domains_nulled": domains_nulled, "relabeled": relabeled}


def _ratio(numerator: int, denominator: int) -> Decimal:
    return Decimal(numerator) / Decimal(denominator) if denominator else Decimal("0")


def _case(company_id: str, components: Mapping[str, Decimal],
          failures: tuple[str, ...]) -> CaseScore:
    score = sum((COMPETITOR_WEIGHTS[key] * value for key, value in components.items()),
                Decimal("0"))
    return CaseScore(company_id, dict(components), score, failures)


def score_competitors(
    payload: Mapping[str, Any], record: SignalGroundTruthRecord, dossier: CompanyDossier,
) -> CaseScore:
    zero = {key: Decimal("0") for key in COMPETITOR_WEIGHTS}
    retained = {item.evidence_id for item in dossier.evidence}
    try:
        output = parse_competitors_output(payload, retained)
    except ValueError:
        return _case(record.company_id, zero, ("contract_violation",))
    truth = _record_competitors(record)
    excerpts = {item.evidence_id: item.excerpt for item in dossier.evidence}
    subject_name, subject_domain = subject_identity(dossier)
    subject_key = normalize_name(subject_name) if subject_name else None
    entries = output.entries
    failures: list[str] = []
    for _, competitor in entries:
        if not entry_is_cited(competitor, excerpts):
            failures.append("hallucinated_competitor")
        if (subject_key and competitor.key == subject_key) or (
            subject_domain and competitor.domain == subject_domain
        ):
            failures.append("self_competitor")

    all_truth = [*truth["named"], *truth["inferred"]]
    if not all_truth:
        clean = not entries and set(output.unknowns) == set(COMPETITOR_FIELDS)
        if clean and not failures:
            return _case(record.company_id, {key: Decimal("1") for key in COMPETITOR_WEIGHTS}, ())
        if entries:
            failures.append("invented_competitor")
        return _case(record.company_id, zero, tuple(dict.fromkeys(failures)))

    used: set[int] = set()
    matched: dict[int, GroundTruthCompetitor] = {}
    for index, (_, competitor) in enumerate(entries):
        for truth_index, candidate in enumerate(all_truth):
            if truth_index in used or not candidate.matches(competitor):
                continue
            used.add(truth_index)
            matched[index] = candidate
            break

    named_payload = [index for index, (bucket, _) in enumerate(entries) if bucket == "named"]
    named_hits = sum(1 for index in named_payload
                     if index in matched and matched[index].bucket == "named")
    precision = _ratio(named_hits, len(named_payload))
    recall = _ratio(named_hits, len(truth["named"])) if truth["named"] else (
        Decimal("1") if not named_payload else Decimal("0")
    )
    if not truth["named"] and not named_payload:
        precision = Decimal("1")
    f1 = (2 * precision * recall / (precision + recall)) if precision + recall else Decimal("0")

    cited = sum(1 for _, competitor in entries if entry_is_cited(competitor, excerpts))
    citation = _ratio(cited, len(entries))
    labeling_hits = sum(1 for index, candidate in matched.items()
                        if entries[index][0] == candidate.bucket)
    labeling = _ratio(labeling_hits, len(matched)) if matched else (
        Decimal("1") if not entries else Decimal("0")
    )
    components = {"named_set": f1, "citation": citation, "labeling": labeling}
    return _case(record.company_id, components, tuple(dict.fromkeys(failures)))
