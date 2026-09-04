"""Deterministic semantic scorers for company-description and growth-signals.

Replaces the exact-string ``_correctness`` gate that rewarded verbatim
parroting (description) and punished a correct ``unknown`` (growth). Scoring
is alias- and keyword-based against the dated observable ground truth in
``benchmarks/description-growth/``:

- description: identity/offering/audience alias matches, per-component
  citations, readability, with verbatim parroting as a hard failure.
- growth: verdict against the ground-truth verdict, precision of claimed
  signal kinds, and citation grounding; a correct ``unknown`` on a
  ``no_signal`` company scores 1.0 instead of 0.0.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import re
from types import MappingProxyType
from typing import Any, Mapping

from scripts.company_enrichment.contracts import CompanyDossier
from scripts.company_enrichment.description_growth_ground_truth import (
    DescriptionGrowthRecord,
    FactComponent,
)


PARROT_RUN_WORDS = 25
_MIN_DESCRIPTION_WORDS = 10
_MAX_DESCRIPTION_WORDS = 150

# Kinds a model may only claim when the ground truth backs them; a claim of
# any of these without a matching ground-truth signal is a hard failure.
_HARD_FAILURE_KINDS = frozenset({
    "funding", "expansion", "customer_scale", "headcount", "hiring",
})

CLAIM_LEXICON: Mapping[str, tuple[str, ...]] = MappingProxyType({
    "hiring": (
        "hiring", "careers page", "open role", "open position", "see jobs",
        "job opening", "recruiting", "looking for top talent",
    ),
    "headcount": (
        "employees", "employee count", "headcount", "company size", "team of",
    ),
    "funding": (
        "funding", "investment", "raised", "investor", "series a", "series b",
        "series c", "series d", "series e", "seed round", "venture", "majority stake",
    ),
    "customer_scale": (
        "customers", "clients", "trusted by", "users", "serving", "sellers",
        "arr", "in revenue", "clicks and scans",
    ),
    "founded": ("founded", "since 19", "since 20"),
    "expansion": (
        "expansion", "expanding", "global presence", "fastest-growing",
        "inc. 5000", "new office", "offices in", "locations in",
    ),
    "product_momentum": (
        "launch", "roadmap", "new product", "release", "benchmarks report",
    ),
    "publishing": ("blog", "publishes", "publishing", "newsletter"),
})


@dataclass(frozen=True, slots=True)
class CaseScore:
    company_id: str
    components: Mapping[str, Decimal]
    score: Decimal
    hard_failures: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "components", MappingProxyType(dict(self.components)))
        object.__setattr__(self, "hard_failures", tuple(self.hard_failures))


def _normalize(value: str) -> str:
    return " ".join(value.casefold().split())


def _words(value: str) -> tuple[str, ...]:
    return tuple(re.findall(r"[a-z0-9]+", value.casefold()))


def _contains_alias(text: str, component: FactComponent) -> bool:
    normalized = _normalize(text)
    return any(_normalize(alias) in normalized for alias in component.aliases)


def _assertions_by_field(output: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    assertions = output.get("assertions", ())
    if not isinstance(assertions, (list, tuple)):
        raise ValueError("output assertions must be a list")
    result: dict[str, Mapping[str, Any]] = {}
    for assertion in assertions:
        entry = assertion if isinstance(assertion, Mapping) else None
        if entry is None:
            entry = {
                "field": assertion.field,
                "value": assertion.value,
                "evidence_ids": assertion.evidence_ids,
            }
        field = entry.get("field")
        if isinstance(field, str) and field not in result:
            result[field] = entry
    return result


def _assertion_text(entry: Mapping[str, Any] | None) -> str:
    if entry is None:
        return ""
    value = entry.get("value")
    return value if isinstance(value, str) else ""


def _cited_ids(entry: Mapping[str, Any] | None) -> frozenset[str]:
    if entry is None:
        return frozenset()
    ids = entry.get("evidence_ids", ())
    if not isinstance(ids, (list, tuple)):
        return frozenset()
    return frozenset(item for item in ids if isinstance(item, str))


def _weighted_score(
    components: Mapping[str, Decimal], weights: Mapping[str, Decimal],
) -> Decimal:
    if set(components) != set(weights):
        raise ValueError("score components must match rubric weights")
    return sum(
        (components[name] * weights[name] for name in weights), Decimal("0"),
    )


def _is_parroted(text: str, dossier: CompanyDossier) -> bool:
    words = _words(text)
    if len(words) < PARROT_RUN_WORDS:
        return False
    grams = {
        words[index:index + PARROT_RUN_WORDS]
        for index in range(len(words) - PARROT_RUN_WORDS + 1)
    }
    for evidence in dossier.evidence:
        excerpt_words = _words(evidence.excerpt)
        for index in range(len(excerpt_words) - PARROT_RUN_WORDS + 1):
            if excerpt_words[index:index + PARROT_RUN_WORDS] in grams:
                return True
    return False


def score_description_payload(
    output: Mapping[str, Any],
    record: DescriptionGrowthRecord,
    dossier: CompanyDossier,
    *,
    weights: Mapping[str, Decimal],
) -> CaseScore:
    """Score one company-description output against the ground truth."""
    assertions = _assertions_by_field(output)
    identity_entry = assertions.get("identity")
    description_entry = assertions.get("description")
    offers_entry = assertions.get("offers")
    prose = " ".join(
        text for text in (
            _assertion_text(description_entry), _assertion_text(offers_entry),
        ) if text
    )
    dossier_ids = frozenset(item.evidence_id for item in dossier.evidence)

    truth = record.description
    matched_entries = {
        "identity": (
            identity_entry
            if _contains_alias(_assertion_text(identity_entry), truth.identity)
            else None
        ),
    }
    for name, component in (("offering", truth.offering), ("audience", truth.audience)):
        matched = None
        for entry in (description_entry, offers_entry):
            if _contains_alias(_assertion_text(entry), component):
                matched = entry
                break
        matched_entries[name] = matched

    components: dict[str, Decimal] = {
        name: Decimal(matched_entries[name] is not None)
        for name in ("identity", "offering", "audience")
    }
    cited = Decimal("0")
    for name, component in truth.components.items():
        entry = matched_entries[name]
        if entry is not None and _cited_ids(entry) & set(component.evidence_ids):
            cited += 1
    components["citation"] = cited / Decimal("3")

    description_text = _assertion_text(description_entry)
    parroted = bool(description_text) and _is_parroted(description_text, dossier)
    word_count = len(_words(description_text))
    components["readability"] = Decimal(
        not parroted
        and _MIN_DESCRIPTION_WORDS <= word_count <= _MAX_DESCRIPTION_WORDS
    )

    failures: list[str] = []
    if description_entry is None:
        failures.append("missing_description")
    if parroted:
        failures.append("verbatim_parroting")
    for name, entry in (
        ("identity", identity_entry),
        ("description", description_entry),
        ("offers", offers_entry),
    ):
        if entry is not None:
            ids = _cited_ids(entry)
            if not ids or not ids <= dossier_ids:
                failures.append(f"uncited_{name}")
    if prose and not components["offering"] and not components["audience"]:
        failures.append("off_target_description")

    return CaseScore(
        company_id=record.company_id,
        components=components,
        score=_weighted_score(components, weights),
        hard_failures=tuple(failures),
    )


def claimed_signal_kinds(text: str) -> frozenset[str]:
    """Detect which observable signal kinds a growth statement claims."""
    normalized = _normalize(text)
    return frozenset(
        kind for kind, keywords in CLAIM_LEXICON.items()
        if any(keyword in normalized for keyword in keywords)
    )


def score_growth_payload(
    output: Mapping[str, Any],
    record: DescriptionGrowthRecord,
    dossier: CompanyDossier,
    *,
    weights: Mapping[str, Decimal],
) -> CaseScore:
    """Score one growth-signals output against the ground truth."""
    assertions = _assertions_by_field(output)
    growth_entry = assertions.get("growth")
    unknowns = output.get("unknowns", ())
    answered_unknown = growth_entry is None or (
        isinstance(unknowns, (list, tuple)) and "growth" in unknowns
    )
    truth = record.growth
    dossier_ids = frozenset(item.evidence_id for item in dossier.evidence)
    failures: list[str] = []
    components: dict[str, Decimal] = {}

    if truth.verdict == "no_signal":
        correct = answered_unknown
        components["verdict"] = Decimal(correct)
        components["signals"] = Decimal(correct)
        components["citation"] = Decimal(correct)
        if not correct:
            failures.append("unsupported_growth_claim")
        return CaseScore(
            company_id=record.company_id,
            components=components,
            score=_weighted_score(components, weights),
            hard_failures=tuple(failures),
        )

    components["verdict"] = Decimal(not answered_unknown)
    if answered_unknown:
        components["signals"] = Decimal("0")
        components["citation"] = Decimal("0")
        return CaseScore(
            company_id=record.company_id,
            components=components,
            score=_weighted_score(components, weights),
            hard_failures=tuple(failures),
        )

    claimed = claimed_signal_kinds(_assertion_text(growth_entry))
    grounded = claimed & truth.kinds
    fabricated = (claimed - truth.kinds) & _HARD_FAILURE_KINDS
    components["signals"] = (
        Decimal(len(grounded)) / Decimal(len(claimed)) if claimed else Decimal("0")
    )
    for kind in sorted(fabricated):
        failures.append(f"fabricated_{kind}")

    cited = _cited_ids(growth_entry)
    signal_ids = set(truth.dossier_evidence_ids)
    if not cited or not cited <= dossier_ids:
        components["citation"] = Decimal("0")
        failures.append("uncited_growth")
    elif signal_ids:
        components["citation"] = Decimal(bool(cited & signal_ids))
    else:
        components["citation"] = Decimal("1")

    return CaseScore(
        company_id=record.company_id,
        components=components,
        score=_weighted_score(components, weights),
        hard_failures=tuple(failures),
    )
