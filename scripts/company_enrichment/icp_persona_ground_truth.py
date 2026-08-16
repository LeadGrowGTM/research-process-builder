"""Strict loader for the frozen, evidence-backed ICP/persona benchmark."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

import yaml

from scripts.company_enrichment.contracts import CompanyDossier
from scripts.company_enrichment.icp_persona_contracts import (
    IcpPersonaOutput,
    InferredPersona,
    ObservedPersona,
    Outcome,
    PrimaryICP,
    SecondaryICP,
    parse_icp_output,
)


_EXPECTED_IDS = frozenset(f"saas-{index:02d}" for index in range(1, 11))
_DEVELOPMENT_IDS = (
    "saas-01", "saas-02", "saas-04", "saas-05", "saas-07", "saas-09",
)
_HOLDOUT_IDS = ("saas-03", "saas-06", "saas-08", "saas-10")
_WEIGHTS = MappingProxyType({
    "buyer": Decimal(".25"),
    "need": Decimal(".20"),
    "object": Decimal(".20"),
    "citation": Decimal(".20"),
    "persona": Decimal(".10"),
    "readability": Decimal(".05"),
})
_THRESHOLD = Decimal(".90")


def _normalize(value: str) -> str:
    return " ".join(value.casefold().split())


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be a mapping")
    return value


def _exact_keys(value: Mapping[str, Any], required: set[str], field: str) -> None:
    if set(value) != required:
        raise ValueError(f"{field} keys must exactly match {sorted(required)}")


def _string_tuple(value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or not value:
        raise ValueError(f"{field} must contain non-empty aliases or Evidence IDs")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"{field} must contain non-empty aliases or Evidence IDs")
    result = tuple(item.strip() for item in value)
    if len({_normalize(item) for item in result}) != len(result):
        raise ValueError(f"{field} must not contain duplicate aliases or Evidence IDs")
    return result


@dataclass(frozen=True, slots=True)
class IcpRubric:
    weights: Mapping[str, Decimal]
    threshold: Decimal

    def __post_init__(self) -> None:
        weights = MappingProxyType(dict(self.weights))
        if weights != _WEIGHTS:
            if sum(weights.values(), Decimal("0")) != Decimal("1.0"):
                raise ValueError("rubric weights must total 1.0")
            raise ValueError("rubric weights do not match the approved rubric")
        if self.threshold != _THRESHOLD:
            raise ValueError("rubric threshold must be exactly 0.90")
        object.__setattr__(self, "weights", weights)

    @classmethod
    def default(cls) -> IcpRubric:
        return cls(_WEIGHTS, _THRESHOLD)


@dataclass(frozen=True, slots=True)
class IcpGroundTruthRecord:
    company_id: str
    expected: IcpPersonaOutput
    acceptable_aliases: Mapping[str, tuple[str, ...]]
    evidence_by_component: Mapping[str, tuple[str, ...]]

    def __post_init__(self) -> None:
        object.__setattr__(self, "acceptable_aliases", MappingProxyType(dict(self.acceptable_aliases)))
        object.__setattr__(self, "evidence_by_component", MappingProxyType(dict(self.evidence_by_component)))

    @property
    def primary_icp(self) -> PrimaryICP:
        return self.expected.primary_icp

    @property
    def secondary_icps(self) -> tuple[SecondaryICP, ...]:
        return self.expected.secondary_icps

    @property
    def outcomes(self) -> tuple[Outcome, ...]:
        return self.expected.outcomes

    @property
    def observed_personas(self) -> tuple[ObservedPersona, ...]:
        return self.expected.observed_personas

    @property
    def inferred_personas(self) -> tuple[InferredPersona, ...]:
        return self.expected.inferred_personas

    @property
    def all_evidence_ids(self) -> frozenset[str]:
        return frozenset(
            evidence_id
            for evidence_ids in self.evidence_by_component.values()
            for evidence_id in evidence_ids
        )


@dataclass(frozen=True, slots=True)
class IcpDataset:
    records: Mapping[str, IcpGroundTruthRecord]
    development_ids: tuple[str, ...]
    holdout_ids: tuple[str, ...]
    rubric: IcpRubric
    dataset_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "records", MappingProxyType(dict(self.records)))


def _load_yaml(path: Path, label: str) -> tuple[Mapping[str, Any], bytes]:
    try:
        raw = path.read_bytes()
        value = yaml.safe_load(raw)
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"{label} YAML is missing or malformed") from exc
    return _mapping(value, label), raw


def _load_rubric(path: Path) -> tuple[IcpRubric, bytes]:
    value, raw = _load_yaml(path, "rubric")
    _exact_keys(value, {"weights", "threshold"}, "rubric")
    weights_value = _mapping(value["weights"], "rubric weights")
    if set(weights_value) != set(_WEIGHTS):
        raise ValueError("rubric weights do not match the approved rubric")
    try:
        weights = {key: Decimal(str(number)) for key, number in weights_value.items()}
        threshold = Decimal(str(value["threshold"]))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("rubric weights and threshold must be decimal numbers") from exc
    return IcpRubric(weights, threshold), raw


def _load_split(path: Path) -> tuple[tuple[str, ...], tuple[str, ...], bytes]:
    value, raw = _load_yaml(path, "split")
    _exact_keys(value, {"development", "holdout"}, "split")
    development = _string_tuple(value["development"], "development split")
    holdout = _string_tuple(value["holdout"], "holdout split")
    overlap = set(development) & set(holdout)
    if overlap:
        raise ValueError(f"development and holdout split overlap: {sorted(overlap)}")
    if development != _DEVELOPMENT_IDS or holdout != _HOLDOUT_IDS:
        raise ValueError("split drift: development and holdout IDs are locked")
    if set(development) | set(holdout) != _EXPECTED_IDS:
        raise ValueError("split must cover exactly saas-01 through saas-10")
    return development, holdout, raw


def _component_keys(output: IcpPersonaOutput) -> set[str]:
    result = {"primary.buyer", "primary.need", "primary.object"}
    for index in range(len(output.secondary_icps)):
        result.update({
            f"secondary.{index}.buyer",
            f"secondary.{index}.need",
            f"secondary.{index}.object",
        })
    result.update(f"outcome.{index}" for index in range(len(output.outcomes)))
    result.update(f"observed_persona.{index}" for index in range(len(output.observed_personas)))
    result.update(f"inferred_persona.{index}" for index in range(len(output.inferred_personas)))
    return result


def _validate_component_evidence(output: IcpPersonaOutput, evidence: Mapping[str, tuple[str, ...]]) -> None:
    primary_ids = set(evidence["primary.buyer"])
    primary_ids.update(evidence["primary.need"])
    primary_ids.update(evidence["primary.object"])
    if primary_ids != set(output.primary_icp.evidence_ids):
        raise ValueError("primary component Evidence must match expected output")
    for index, secondary in enumerate(output.secondary_icps):
        ids = set(evidence[f"secondary.{index}.buyer"])
        ids.update(evidence[f"secondary.{index}.need"])
        ids.update(evidence[f"secondary.{index}.object"])
        if ids != set(secondary.evidence_ids):
            raise ValueError("secondary component Evidence must match expected output")
    for prefix, components in (
        ("outcome", output.outcomes),
        ("observed_persona", output.observed_personas),
        ("inferred_persona", output.inferred_personas),
    ):
        for index, component in enumerate(components):
            expected_ids = component.evidence_ids if hasattr(component, "evidence_ids") else component.based_on_evidence_ids
            if set(evidence[f"{prefix}.{index}"]) != set(expected_ids):
                raise ValueError(f"{prefix} component Evidence must match expected output")


def _load_record(path: Path, company_id: str, dossier: CompanyDossier) -> IcpGroundTruthRecord:
    value, _ = _load_yaml(path, f"ground truth {company_id}")
    _exact_keys(value, {"company_id", "expected", "acceptable_aliases", "evidence_by_component"}, f"ground truth {company_id}")
    if value["company_id"] != company_id or dossier.company_id != company_id:
        raise ValueError(f"ground truth company ID mismatch for {company_id}")
    retained = {item.evidence_id for item in dossier.evidence}
    expected = parse_icp_output(_mapping(value["expected"], "expected output"), retained)

    aliases_value = _mapping(value["acceptable_aliases"], "acceptable aliases")
    alias_keys = {"primary.buyer", "primary.need", "primary.object"}
    _exact_keys(aliases_value, alias_keys, "acceptable aliases")
    aliases = {key: _string_tuple(item, f"acceptable aliases for {key}") for key, item in aliases_value.items()}
    canonical_values = {
        "primary.buyer": expected.primary_icp.buyer,
        "primary.need": expected.primary_icp.need,
        "primary.object": expected.primary_icp.object,
    }
    for key, canonical in canonical_values.items():
        if _normalize(canonical) not in {_normalize(alias) for alias in aliases[key]}:
            raise ValueError(f"acceptable aliases for {key} must include canonical value")

    evidence_value = _mapping(value["evidence_by_component"], "Evidence by component")
    _exact_keys(evidence_value, _component_keys(expected), "Evidence by component")
    evidence = {key: _string_tuple(item, f"Evidence for {key}") for key, item in evidence_value.items()}
    referenced = {evidence_id for evidence_ids in evidence.values() for evidence_id in evidence_ids}
    if not referenced <= retained:
        raise ValueError("all component Evidence must exist in the matching dossier")
    _validate_component_evidence(expected, evidence)
    return IcpGroundTruthRecord(company_id, expected, aliases, evidence)


def dataset_hash(dataset_root: str | Path, records: Mapping[str, IcpGroundTruthRecord], dossiers: Mapping[str, CompanyDossier]) -> str:
    """Hash exact frozen file bytes plus referenced dossier Evidence hashes."""
    root = Path(dataset_root)
    digest = sha256()
    paths = [root / "split.yaml", root / "rubric.yaml"]
    paths.extend(root / "ground-truth" / f"{company_id}.yaml" for company_id in sorted(records))
    for path in paths:
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    for company_id in sorted(records):
        evidence = {item.evidence_id: item for item in dossiers[company_id].evidence}
        for evidence_id in sorted(records[company_id].all_evidence_ids):
            digest.update(company_id.encode("utf-8"))
            digest.update(b"\0")
            digest.update(evidence_id.encode("utf-8"))
            digest.update(b"\0")
            digest.update(evidence[evidence_id].content_hash.lower().encode("ascii"))
            digest.update(b"\0")
    return digest.hexdigest()


def load_icp_dataset(root: str | Path, dossiers: Mapping[str, CompanyDossier]) -> IcpDataset:
    """Load the exact ten-company benchmark and reject any frozen-input drift."""
    dataset_root = Path(root) / "benchmarks" / "icp-persona"
    ground_truth_root = dataset_root / "ground-truth"
    actual_files = {path.name for path in ground_truth_root.glob("*.yaml")}
    expected_files = {f"{company_id}.yaml" for company_id in _EXPECTED_IDS}
    if actual_files != expected_files:
        raise ValueError("ground truth files must be exactly saas-01 through saas-10")
    missing_dossiers = _EXPECTED_IDS - set(dossiers)
    if missing_dossiers:
        raise ValueError(f"missing matching dossiers: {sorted(missing_dossiers)}")

    development, holdout, _ = _load_split(dataset_root / "split.yaml")
    rubric, _ = _load_rubric(dataset_root / "rubric.yaml")
    records = {
        company_id: _load_record(ground_truth_root / f"{company_id}.yaml", company_id, dossiers[company_id])
        for company_id in sorted(_EXPECTED_IDS)
    }
    return IcpDataset(records, development, holdout, rubric, dataset_hash(dataset_root, records, dossiers))
