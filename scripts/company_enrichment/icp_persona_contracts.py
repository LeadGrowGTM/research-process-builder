"""Immutable, evidence-closed ICP and persona output contract."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-empty text")
    return " ".join(value.split())


def _ids(value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or not value or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"{field} must contain evidence IDs")
    result = tuple(dict.fromkeys(item.strip() for item in value))
    if len(result) != len(value):
        raise ValueError(f"{field} must contain unique evidence IDs")
    return result


@dataclass(frozen=True, slots=True)
class PrimaryICP:
    buyer: str
    need: str
    object: str
    evidence_ids: tuple[str, ...]

    def __post_init__(self):
        object.__setattr__(self, "buyer", _text(self.buyer, "buyer"))
        object.__setattr__(self, "need", _text(self.need, "need"))
        object.__setattr__(self, "object", _text(self.object, "object"))
        object.__setattr__(self, "evidence_ids", _ids(self.evidence_ids, "evidence_ids"))


@dataclass(frozen=True, slots=True)
class SecondaryICP(PrimaryICP):
    pass


@dataclass(frozen=True, slots=True)
class Outcome:
    text: str
    evidence_ids: tuple[str, ...]

    def __post_init__(self):
        object.__setattr__(self, "text", _text(self.text, "outcome"))
        object.__setattr__(self, "evidence_ids", _ids(self.evidence_ids, "evidence_ids"))


@dataclass(frozen=True, slots=True)
class ObservedPersona:
    role: str
    evidence_ids: tuple[str, ...]

    def __post_init__(self):
        object.__setattr__(self, "role", _text(self.role, "observed role"))
        object.__setattr__(self, "evidence_ids", _ids(self.evidence_ids, "evidence_ids"))


@dataclass(frozen=True, slots=True)
class InferredPersona:
    role: str
    based_on_evidence_ids: tuple[str, ...]

    def __post_init__(self):
        object.__setattr__(self, "role", _text(self.role, "inferred role"))
        object.__setattr__(self, "based_on_evidence_ids", _ids(self.based_on_evidence_ids, "based_on_evidence_ids"))


@dataclass(frozen=True, slots=True)
class IcpPersonaOutput:
    primary_icp: PrimaryICP
    secondary_icps: tuple[SecondaryICP, ...]
    outcomes: tuple[Outcome, ...]
    observed_personas: tuple[ObservedPersona, ...]
    inferred_personas: tuple[InferredPersona, ...]

    def __post_init__(self):
        if not isinstance(self.primary_icp, PrimaryICP) or isinstance(self.primary_icp, SecondaryICP):
            raise ValueError("primary_icp must be PrimaryICP")
        secondaries = tuple(self.secondary_icps)
        if len(secondaries) > 2:
            raise ValueError("at most two secondary ICPs are supported")
        keys = [(item.buyer.casefold(), item.need.casefold()) for item in secondaries]
        if len(set(keys)) != len(keys):
            raise ValueError("secondary buyer/use-case tuples must be unique")
        for item in secondaries:
            if not isinstance(item, SecondaryICP):
                raise ValueError("secondary_icps must contain SecondaryICP")
        object.__setattr__(self, "secondary_icps", secondaries)
        object.__setattr__(self, "outcomes", tuple(self.outcomes))
        object.__setattr__(self, "observed_personas", tuple(self.observed_personas))
        object.__setattr__(self, "inferred_personas", tuple(self.inferred_personas))


def _evidence_close(output: IcpPersonaOutput, retained: set[str]) -> None:
    components = [output.primary_icp, *output.secondary_icps, *output.outcomes, *output.observed_personas]
    components.extend(output.inferred_personas)
    for component in components:
        ids = component.evidence_ids if hasattr(component, "evidence_ids") else component.based_on_evidence_ids
        if not set(ids) <= retained:
            raise ValueError("all claims must reference retained Evidence IDs")


def parse_icp_output(value: dict[str, Any], retained_evidence_ids: set[str] | frozenset[str]) -> IcpPersonaOutput:
    if not isinstance(value, dict):
        raise ValueError("ICP output must be an object")
    primary = value.get("primary_icp") or {}
    result = IcpPersonaOutput(
        PrimaryICP(primary.get("buyer"), primary.get("need"), primary.get("object"), _ids(primary.get("evidence_ids"), "evidence_ids")),
        tuple(SecondaryICP(item.get("buyer"), item.get("need"), item.get("object"), _ids(item.get("evidence_ids"), "evidence_ids")) for item in value.get("secondary_icps", [])),
        tuple(Outcome(item.get("text"), _ids(item.get("evidence_ids"), "evidence_ids")) for item in value.get("outcomes", [])),
        tuple(ObservedPersona(item.get("role"), _ids(item.get("evidence_ids"), "evidence_ids")) for item in value.get("observed_personas", [])),
        tuple(InferredPersona(item.get("role"), _ids(item.get("based_on_evidence_ids"), "based_on_evidence_ids")) for item in value.get("inferred_personas", [])),
    )
    _evidence_close(result, set(retained_evidence_ids))
    return result


def render_segment(segment: PrimaryICP | SecondaryICP) -> str:
    if not isinstance(segment, PrimaryICP):
        raise TypeError("segment must be an ICP")
    return f"{segment.buyer} that need {segment.need} for {segment.object}"


def load_icp_contract(path: str | Path) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as handle:
        contract = yaml.safe_load(handle)
    if not isinstance(contract, dict):
        raise ValueError("contract must be a mapping")
    return contract
