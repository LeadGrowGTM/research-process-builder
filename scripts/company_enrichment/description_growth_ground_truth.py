"""Strict loader for the dated, observable description/growth benchmark.

The dataset replaces the invalid exact-string ``_correctness`` gate for the
``company-description`` and ``growth-signals`` enrichments (see
docs/benchmarks/model-outcomes.md). Every ground-truth fact is observable and
dated: description components cite dossier Evidence, and growth signals carry
either a dossier Evidence quote or a page-signals observation URL with its
check date.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

import yaml

from scripts.company_enrichment.contracts import CompanyDossier


_ALL_IDS = tuple(f"saas-{index:02d}" for index in range(1, 11))
_EXPECTED_IDS = frozenset(_ALL_IDS)
_DEVELOPMENT_IDS = (
    "saas-01", "saas-02", "saas-04", "saas-05", "saas-07", "saas-09",
)
_HOLDOUT_IDS = ("saas-03", "saas-06", "saas-08", "saas-10")
_DESCRIPTION_WEIGHTS = MappingProxyType({
    "identity": Decimal(".25"),
    "offering": Decimal(".25"),
    "audience": Decimal(".20"),
    "citation": Decimal(".20"),
    "readability": Decimal(".10"),
})
_GROWTH_WEIGHTS = MappingProxyType({
    "verdict": Decimal(".45"),
    "signals": Decimal(".35"),
    "citation": Decimal(".20"),
})
_THRESHOLD = Decimal(".90")

SIGNAL_KINDS = frozenset({
    "hiring", "publishing", "headcount", "funding", "customer_scale",
    "founded", "expansion", "product_momentum",
})
GROWTH_VERDICTS = frozenset({"growth_signals", "no_signal"})
_PAGE_SIGNAL_SOURCES = frozenset({"page-signals-v1"})


def _normalize(value: str) -> str:
    return " ".join(value.casefold().split())


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be a mapping")
    return value


def _exact_keys(
    value: Mapping[str, Any], required: set[str], field: str,
    optional: set[str] = frozenset(),
) -> None:
    keys = set(value)
    if not required <= keys or keys - required - optional:
        raise ValueError(
            f"{field} keys must be {sorted(required)}"
            + (f" plus optional {sorted(optional)}" if optional else "")
        )


def _string_tuple(value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or not value:
        raise ValueError(f"{field} must be a non-empty list of text values")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"{field} must contain non-empty text values")
    result = tuple(item.strip() for item in value)
    if len({_normalize(item) for item in result}) != len(result):
        raise ValueError(f"{field} must not contain duplicates")
    return result


@dataclass(frozen=True, slots=True)
class FactComponent:
    """One description fact: a canonical value, accepted aliases, Evidence."""

    canonical: str
    aliases: tuple[str, ...]
    evidence_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.canonical, str) or not self.canonical.strip():
            raise ValueError("component canonical value must be non-empty text")
        object.__setattr__(self, "aliases", _string_tuple(self.aliases, "aliases"))
        object.__setattr__(
            self, "evidence_ids", _string_tuple(self.evidence_ids, "evidence_ids"),
        )
        if _normalize(self.canonical) not in {
            _normalize(alias) for alias in self.aliases
        }:
            raise ValueError("aliases must include the canonical value")


@dataclass(frozen=True, slots=True)
class DescriptionGroundTruth:
    identity: FactComponent
    offering: FactComponent
    audience: FactComponent

    @property
    def components(self) -> Mapping[str, FactComponent]:
        return MappingProxyType({
            "identity": self.identity,
            "offering": self.offering,
            "audience": self.audience,
        })


@dataclass(frozen=True, slots=True)
class GrowthSignal:
    """One dated, observable growth signal.

    Dossier-sourced signals quote the cited Evidence verbatim; page-signals
    observations carry the checked URL and check date instead.
    """

    kind: str
    source: str
    observed_at: date
    evidence_ids: tuple[str, ...] = ()
    quote: str | None = None
    url: str | None = None
    note: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in SIGNAL_KINDS:
            raise ValueError(f"unknown growth signal kind: {self.kind}")
        if not isinstance(self.observed_at, date) or isinstance(self.observed_at, bool):
            raise ValueError("observed_at must be a date")
        object.__setattr__(self, "evidence_ids", tuple(self.evidence_ids))
        if self.source == "dossier":
            if not self.evidence_ids or not self.quote or not self.quote.strip():
                raise ValueError(
                    "dossier growth signals require evidence_ids and a quote"
                )
            _string_tuple(self.evidence_ids, "signal evidence_ids")
        elif self.source in _PAGE_SIGNAL_SOURCES:
            if not self.url or not self.url.strip():
                raise ValueError("page-signal observations require a url")
            if self.evidence_ids or self.quote:
                raise ValueError(
                    "page-signal observations cannot cite dossier Evidence"
                )
        else:
            raise ValueError(f"unknown growth signal source: {self.source}")


@dataclass(frozen=True, slots=True)
class GrowthGroundTruth:
    verdict: str
    signals: tuple[GrowthSignal, ...]

    def __post_init__(self) -> None:
        if self.verdict not in GROWTH_VERDICTS:
            raise ValueError(f"unknown growth verdict: {self.verdict}")
        object.__setattr__(self, "signals", tuple(self.signals))
        if not all(isinstance(item, GrowthSignal) for item in self.signals):
            raise ValueError("signals must contain GrowthSignal values")
        if self.verdict == "no_signal" and self.signals:
            raise ValueError("a no_signal verdict cannot list growth signals")
        if self.verdict == "growth_signals" and not self.signals:
            raise ValueError("a growth_signals verdict requires signals")

    @property
    def kinds(self) -> frozenset[str]:
        return frozenset(signal.kind for signal in self.signals)

    @property
    def dossier_evidence_ids(self) -> frozenset[str]:
        return frozenset(
            evidence_id
            for signal in self.signals
            for evidence_id in signal.evidence_ids
        )


@dataclass(frozen=True, slots=True)
class DescriptionGrowthRecord:
    company_id: str
    as_of: date
    description: DescriptionGroundTruth
    growth: GrowthGroundTruth


@dataclass(frozen=True, slots=True)
class TrackRubric:
    weights: Mapping[str, Decimal]
    threshold: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(self, "weights", MappingProxyType(dict(self.weights)))
        if sum(self.weights.values(), Decimal("0")) != Decimal("1.0"):
            raise ValueError("rubric weights must total 1.0")
        if self.threshold != _THRESHOLD:
            raise ValueError("rubric threshold must be exactly 0.90")


@dataclass(frozen=True, slots=True)
class DescriptionGrowthDataset:
    records: Mapping[str, DescriptionGrowthRecord]
    all_ids: tuple[str, ...]
    development_ids: tuple[str, ...]
    holdout_ids: tuple[str, ...]
    description_rubric: TrackRubric
    growth_rubric: TrackRubric
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


def _load_rubrics(path: Path) -> tuple[TrackRubric, TrackRubric]:
    value, _ = _load_yaml(path, "rubric")
    _exact_keys(value, {"description", "growth"}, "rubric")
    rubrics = []
    for track, expected in (
        ("description", _DESCRIPTION_WEIGHTS),
        ("growth", _GROWTH_WEIGHTS),
    ):
        entry = _mapping(value[track], f"{track} rubric")
        _exact_keys(entry, {"weights", "threshold"}, f"{track} rubric")
        weights_value = _mapping(entry["weights"], f"{track} rubric weights")
        if set(weights_value) != set(expected):
            raise ValueError(f"{track} rubric weights do not match the approved rubric")
        try:
            weights = {
                key: Decimal(str(number)) for key, number in weights_value.items()
            }
            threshold = Decimal(str(entry["threshold"]))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValueError(
                f"{track} rubric weights and threshold must be decimal numbers"
            ) from exc
        if weights != dict(expected):
            raise ValueError(f"{track} rubric weights do not match the approved rubric")
        rubrics.append(TrackRubric(weights, threshold))
    return rubrics[0], rubrics[1]


def _load_split(path: Path) -> tuple[tuple[str, ...], tuple[str, ...]]:
    value, _ = _load_yaml(path, "split")
    _exact_keys(value, {"development", "holdout"}, "split")
    development = _string_tuple(value["development"], "development split")
    holdout = _string_tuple(value["holdout"], "holdout split")
    if development != _DEVELOPMENT_IDS or holdout != _HOLDOUT_IDS:
        raise ValueError("split drift: development and holdout IDs are locked")
    return development, holdout


def _parse_date(value: Any, field: str) -> date:
    if isinstance(value, date) and not isinstance(value, bool):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"{field} must be an ISO date") from exc
    raise ValueError(f"{field} must be an ISO date")


def _parse_component(value: Any, field: str) -> FactComponent:
    entry = _mapping(value, field)
    _exact_keys(entry, {"canonical", "aliases", "evidence_ids"}, field)
    return FactComponent(
        canonical=entry["canonical"],
        aliases=tuple(entry["aliases"]) if isinstance(entry["aliases"], (list, tuple)) else entry["aliases"],
        evidence_ids=tuple(entry["evidence_ids"]) if isinstance(entry["evidence_ids"], (list, tuple)) else entry["evidence_ids"],
    )


def _parse_signal(value: Any, field: str) -> GrowthSignal:
    entry = _mapping(value, field)
    _exact_keys(
        entry, {"kind", "source", "observed_at"}, field,
        optional={"evidence_ids", "quote", "url", "note"},
    )
    evidence_ids = entry.get("evidence_ids", ())
    if not isinstance(evidence_ids, (list, tuple)):
        raise ValueError(f"{field} evidence_ids must be a list")
    return GrowthSignal(
        kind=entry["kind"],
        source=entry["source"],
        observed_at=_parse_date(entry["observed_at"], f"{field} observed_at"),
        evidence_ids=tuple(evidence_ids),
        quote=entry.get("quote"),
        url=entry.get("url"),
        note=entry.get("note"),
    )


def _validate_dossier_links(
    record: DescriptionGrowthRecord, dossier: CompanyDossier,
) -> None:
    evidence = {item.evidence_id: item for item in dossier.evidence}
    for name, component in record.description.components.items():
        missing = set(component.evidence_ids) - set(evidence)
        if missing:
            raise ValueError(
                f"{record.company_id} {name} cites Evidence absent from the dossier"
            )
    for signal in record.growth.signals:
        missing = set(signal.evidence_ids) - set(evidence)
        if missing:
            raise ValueError(
                f"{record.company_id} {signal.kind} signal cites Evidence absent"
                " from the dossier"
            )
        if signal.source == "dossier":
            assert signal.quote is not None
            if not any(
                signal.quote in evidence[evidence_id].excerpt
                for evidence_id in signal.evidence_ids
            ):
                raise ValueError(
                    f"{record.company_id} {signal.kind} signal quote is not a"
                    " verbatim excerpt of its cited Evidence"
                )


def _load_record(
    path: Path, company_id: str, dossier: CompanyDossier,
) -> DescriptionGrowthRecord:
    value, _ = _load_yaml(path, f"ground truth {company_id}")
    _exact_keys(
        value, {"company_id", "as_of", "description", "growth"},
        f"ground truth {company_id}",
    )
    if value["company_id"] != company_id or dossier.company_id != company_id:
        raise ValueError(f"ground truth company ID mismatch for {company_id}")
    description_value = _mapping(value["description"], "description ground truth")
    _exact_keys(
        description_value, {"identity", "offering", "audience"},
        "description ground truth",
    )
    description = DescriptionGroundTruth(
        identity=_parse_component(description_value["identity"], "identity"),
        offering=_parse_component(description_value["offering"], "offering"),
        audience=_parse_component(description_value["audience"], "audience"),
    )
    growth_value = _mapping(value["growth"], "growth ground truth")
    _exact_keys(growth_value, {"verdict", "signals"}, "growth ground truth")
    signals_value = growth_value["signals"]
    if not isinstance(signals_value, (list, tuple)):
        raise ValueError("growth signals must be a list")
    growth = GrowthGroundTruth(
        verdict=growth_value["verdict"],
        signals=tuple(
            _parse_signal(item, f"growth signal {index}")
            for index, item in enumerate(signals_value)
        ),
    )
    record = DescriptionGrowthRecord(
        company_id=company_id,
        as_of=_parse_date(value["as_of"], "as_of"),
        description=description,
        growth=growth,
    )
    _validate_dossier_links(record, dossier)
    return record


def _validate_exact_files(dataset_root: Path) -> None:
    actual_files = {
        path.name for path in (dataset_root / "ground-truth").glob("*.yaml")
    }
    expected_files = {f"{company_id}.yaml" for company_id in _EXPECTED_IDS}
    if actual_files != expected_files:
        raise ValueError("ground truth files must be exactly saas-01 through saas-10")


def _dataset_hash(
    dataset_root: Path,
    records: Mapping[str, DescriptionGrowthRecord],
    dossiers: Mapping[str, CompanyDossier],
) -> str:
    digest = sha256()
    paths = [dataset_root / "split.yaml", dataset_root / "rubric.yaml"]
    paths.extend(
        dataset_root / "ground-truth" / f"{company_id}.yaml"
        for company_id in _ALL_IDS
    )
    for path in paths:
        digest.update(path.relative_to(dataset_root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    for company_id in _ALL_IDS:
        record = records[company_id]
        evidence = {
            item.evidence_id: item for item in dossiers[company_id].evidence
        }
        referenced = set(record.growth.dossier_evidence_ids)
        for component in record.description.components.values():
            referenced.update(component.evidence_ids)
        for evidence_id in sorted(referenced):
            digest.update(company_id.encode("utf-8"))
            digest.update(b"\0")
            digest.update(evidence_id.encode("utf-8"))
            digest.update(b"\0")
            digest.update(evidence[evidence_id].content_hash.lower().encode("ascii"))
            digest.update(b"\0")
    return digest.hexdigest()


def load_description_growth_dataset(
    root: str | Path,
    dossiers: Mapping[str, CompanyDossier],
) -> DescriptionGrowthDataset:
    """Validate and return the frozen ten-company description/growth dataset."""
    dataset_root = Path(root) / "benchmarks" / "description-growth"
    _validate_exact_files(dataset_root)
    missing_dossiers = _EXPECTED_IDS - set(dossiers)
    if missing_dossiers:
        raise ValueError(f"missing matching dossiers: {sorted(missing_dossiers)}")
    development, holdout = _load_split(dataset_root / "split.yaml")
    description_rubric, growth_rubric = _load_rubrics(dataset_root / "rubric.yaml")
    ground_truth_root = dataset_root / "ground-truth"
    records = {
        company_id: _load_record(
            ground_truth_root / f"{company_id}.yaml",
            company_id,
            dossiers[company_id],
        )
        for company_id in _ALL_IDS
    }
    return DescriptionGrowthDataset(
        records=records,
        all_ids=_ALL_IDS,
        development_ids=development,
        holdout_ids=holdout,
        description_rubric=description_rubric,
        growth_rubric=growth_rubric,
        dataset_hash=_dataset_hash(dataset_root, records, dossiers),
    )
