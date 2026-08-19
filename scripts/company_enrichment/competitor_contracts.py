"""Immutable, evidence-closed output contract for competitor intelligence.

Mirrors ``prompts/company-enrichment/competitor-intelligence.md``: a single
``competitors`` object with ``named``, ``inferred``, and ``conflicts``
collections plus an optional top-level ``unknowns`` list.
``competitors_output_contract`` renders the strict JSON schema the model
client sends; ``parse_competitors_output`` validates a returned payload
strictly (relationship enum, non-empty retained Evidence IDs, no duplicate
companies) so evaluators score typed values.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import re
from typing import Any, Mapping

from .contracts import CompanyDossier

COMPETITOR_FIELDS = ("competitors",)
RELATIONSHIPS = ("direct", "adjacent", "alternative")
BUCKETS = ("named", "inferred")
WHY_MAX_WORDS = 20
_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_CORPORATE_SUFFIXES = ("inc", "llc", "ltd", "limited", "corp", "corporation", "co", "gmbh",
                       "plc", "sa", "ag", "srl", "bv", "pty")


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-empty text")
    return " ".join(value.split())


def _ids(value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or not value or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise ValueError(f"{field} must contain evidence IDs")
    # A repeated ID is redundant citation, not a contract failure: keep first
    # occurrence order and drop the repeats.
    result = tuple(dict.fromkeys(item.strip() for item in value))
    return result


def normalize_domain(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError("domain must be text or null")
    host = value.strip().lower()
    if "://" in host:
        from urllib.parse import urlparse

        host = urlparse(host).netloc
    host = host.split("/")[0].split("@")[-1].split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    if "." not in host or " " in host:
        # domain is optional; a malformed guess such as "service now.com" is
        # dropped to null instead of failing the whole payload.
        return None
    return host


def normalize_name(value: str) -> str:
    """Case- and punctuation-insensitive company key with corporate suffixes dropped."""
    tokens = [token for token in _NON_ALNUM.split(value.lower()) if token]
    while len(tokens) > 1 and tokens[-1] in _CORPORATE_SUFFIXES:
        tokens.pop()
    return "".join(tokens)


@dataclass(frozen=True, slots=True)
class Competitor:
    name: str
    domain: str | None
    relationship: str
    why: str
    evidence_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _text(self.name, "name"))
        object.__setattr__(self, "domain", normalize_domain(self.domain))
        relationship = _text(self.relationship, "relationship").lower()
        if relationship not in RELATIONSHIPS:
            raise ValueError(f"relationship must be one of {RELATIONSHIPS}")
        object.__setattr__(self, "relationship", relationship)
        # WHY_MAX_WORDS is the prompt's target length; prose that runs long is a
        # style flaw for human review, not a structural contract failure.
        object.__setattr__(self, "why", _text(self.why, "why"))
        object.__setattr__(self, "evidence_ids", _ids(self.evidence_ids, "evidence_ids"))

    @property
    def key(self) -> str:
        return normalize_name(self.name)


@dataclass(frozen=True, slots=True)
class Conflict:
    note: str
    evidence_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "note", _text(self.note, "conflict note"))
        object.__setattr__(self, "evidence_ids", _ids(self.evidence_ids, "evidence_ids"))


@dataclass(frozen=True, slots=True)
class CompetitorsOutput:
    named: tuple[Competitor, ...]
    inferred: tuple[Competitor, ...]
    conflicts: tuple[Conflict, ...] = ()
    unknowns: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for bucket in BUCKETS:
            entries = tuple(getattr(self, bucket))
            if not all(isinstance(item, Competitor) for item in entries):
                raise ValueError(f"{bucket} must contain Competitor values")
            object.__setattr__(self, bucket, entries)
        conflicts = tuple(self.conflicts)
        if not all(isinstance(item, Conflict) for item in conflicts):
            raise ValueError("conflicts must contain Conflict values")
        object.__setattr__(self, "conflicts", conflicts)
        keys = [item.key for item in (*self.named, *self.inferred)]
        if len(set(keys)) != len(keys):
            raise ValueError("competitors must be deduplicated by company")
        unknowns = tuple(dict.fromkeys(self.unknowns))
        if any(item not in COMPETITOR_FIELDS for item in unknowns):
            raise ValueError("unknowns may only name competitors")
        if unknowns and (self.named or self.inferred):
            raise ValueError("competitors is declared unknown but contains entries")
        object.__setattr__(self, "unknowns", unknowns)

    @property
    def entries(self) -> tuple[tuple[str, Competitor], ...]:
        return tuple((bucket, item) for bucket in BUCKETS for item in getattr(self, bucket))


def _competitor(value: Any, bucket: str) -> Competitor:
    if not isinstance(value, Mapping):
        raise ValueError(f"{bucket} entries must be objects")
    extra = set(value) - {"name", "domain", "relationship", "why", "evidence_ids"}
    if extra:
        raise ValueError(f"{bucket} entry has unexpected keys: {sorted(extra)}")
    return Competitor(
        value.get("name"), value.get("domain"), value.get("relationship"), value.get("why"),
        _ids(value.get("evidence_ids"), "evidence_ids"),
    )


def _conflict(value: Any) -> Conflict:
    if not isinstance(value, Mapping) or set(value) != {"note", "evidence_ids"}:
        raise ValueError("conflicts entries must contain note and evidence_ids")
    return Conflict(value.get("note"), _ids(value.get("evidence_ids"), "evidence_ids"))


def parse_competitors_output(
    value: Mapping[str, Any], retained_evidence_ids: set[str] | frozenset[str],
) -> CompetitorsOutput:
    if not isinstance(value, Mapping):
        raise ValueError("competitors output must be an object")
    if set(value) - {"unknowns"} != set(COMPETITOR_FIELDS):
        raise ValueError("competitors output must contain exactly competitors")
    body = value.get("competitors")
    if not isinstance(body, Mapping) or set(body) != {"named", "inferred", "conflicts"}:
        raise ValueError("competitors must contain named, inferred, and conflicts")
    buckets: dict[str, tuple[Competitor, ...]] = {}
    seen: dict[str, Competitor] = {}
    for bucket in BUCKETS:
        items = body.get(bucket)
        if not isinstance(items, (list, tuple)):
            raise ValueError(f"{bucket} must be an array")
        merged: list[Competitor] = []
        for item in items:
            competitor = _competitor(item, bucket)
            prior = seen.get(competitor.key)
            if prior is None:
                seen[competitor.key] = competitor
                merged.append(competitor)
                continue
            # The same company listed twice (or in both buckets): keep the first
            # entry, fold the later citations into it. The bucket of the first
            # occurrence wins so an explicit "named" placement is not demoted.
            folded = replace(prior, evidence_ids=tuple(dict.fromkeys(
                (*prior.evidence_ids, *competitor.evidence_ids)
            )))
            seen[competitor.key] = folded
            merged = [folded if entry.key == folded.key else entry for entry in merged]
            for other_bucket, entries in buckets.items():
                buckets[other_bucket] = tuple(
                    folded if entry.key == folded.key else entry for entry in entries
                )
        buckets[bucket] = tuple(merged)
    conflicts_value = body.get("conflicts")
    if not isinstance(conflicts_value, (list, tuple)):
        raise ValueError("conflicts must be an array")
    unknowns = value.get("unknowns", [])
    if not isinstance(unknowns, (list, tuple)) or any(
        not isinstance(item, str) for item in unknowns
    ):
        raise ValueError("unknowns must be an array of field names")
    output = CompetitorsOutput(
        buckets["named"], buckets["inferred"],
        tuple(_conflict(item) for item in conflicts_value), tuple(unknowns),
    )
    retained = set(retained_evidence_ids)
    cited = [item.evidence_ids for _, item in output.entries]
    cited.extend(item.evidence_ids for item in output.conflicts)
    for ids in cited:
        if not set(ids) <= retained:
            raise ValueError("all competitors must reference retained Evidence IDs")
    return output


def _competitor_schema(evidence_ids: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "domain": {"type": ["string", "null"]},
            "relationship": {"type": "string", "enum": list(RELATIONSHIPS)},
            "why": {"type": "string"},
            "evidence_ids": {
                "type": "array", "items": {"type": "string", "enum": evidence_ids},
                "minItems": 1,
            },
        },
        "required": ["name", "domain", "relationship", "why", "evidence_ids"],
        "additionalProperties": False,
    }


def competitors_output_contract(dossier: CompanyDossier) -> dict[str, Any]:
    """Strict JSON schema: ``competitors`` object plus ``unknowns``.

    ``unknowns`` is required by the strict schema (empty when competitors are
    supported) and optional for ``parse_competitors_output``.
    """
    ids = [item.evidence_id for item in dossier.evidence]
    competitor = _competitor_schema(ids)
    return {
        "type": "object",
        "properties": {
            "competitors": {
                "type": "object",
                "properties": {
                    "named": {"type": "array", "items": competitor},
                    "inferred": {"type": "array", "items": competitor},
                    "conflicts": {"type": "array", "items": {
                        "type": "object",
                        "properties": {
                            "note": {"type": "string"},
                            "evidence_ids": {
                                "type": "array", "items": {"type": "string", "enum": ids},
                                "minItems": 1,
                            },
                        },
                        "required": ["note", "evidence_ids"],
                        "additionalProperties": False,
                    }},
                },
                "required": ["named", "inferred", "conflicts"],
                "additionalProperties": False,
            },
            "unknowns": {
                "type": "array", "items": {"type": "string", "enum": list(COMPETITOR_FIELDS)},
            },
        },
        "required": ["competitors", "unknowns"],
        "additionalProperties": False,
    }
