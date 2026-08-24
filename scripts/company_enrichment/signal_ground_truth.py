"""Strict loader for frozen, evidence-backed signal-enrichment benchmarks.

Layout under ``benchmarks/signals/<enrichment_id>/``:

- ``split.yaml``: the locked development/holdout split shared with the ICP loop
- ``rubric.yaml``: ``weights`` (must equal the spec weights and total 1.0) and
  ``threshold`` (exactly 0.90)
- ``<company>.yaml``: the signal dossier (base Evidence plus signal Evidence)
- ``ground-truth/<company>.yaml``: ``company_id`` plus a spec-specific body

The record body is kept as an opaque, frozen mapping. The only generic checks
are that every ``evidence_ids`` list inside it is non-empty and cites Evidence
present in the matching signal dossier. The dataset hash covers the split,
rubric, ground-truth files, and signal-dossier files byte-for-byte so the
holdout stays sealed against silent edits.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from functools import partial
from hashlib import sha256
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Mapping, Protocol

import yaml

from .contracts import CompanyDossier
from .executors import P0_ENRICHMENTS


ALL_IDS = tuple(f"saas-{index:02d}" for index in range(1, 11))
_EXPECTED_IDS = frozenset(ALL_IDS)
DEVELOPMENT_IDS = ("saas-01", "saas-02", "saas-04", "saas-05", "saas-07", "saas-09")
HOLDOUT_IDS = ("saas-03", "saas-06", "saas-08", "saas-10")
THRESHOLD = Decimal(".90")


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be a mapping")
    return value


def _exact_keys(value: Mapping[str, Any], required: set[str], field: str) -> None:
    if set(value) != required:
        raise ValueError(f"{field} keys must exactly match {sorted(required)}")


def _string_tuple(value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or not value:
        raise ValueError(f"{field} must contain non-empty Evidence IDs")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"{field} must contain non-empty Evidence IDs")
    result = tuple(item.strip() for item in value)
    if len(set(result)) != len(result):
        raise ValueError(f"{field} must not contain duplicate Evidence IDs")
    return result


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def validate_weights(weights: Mapping[str, Any]) -> Mapping[str, Decimal]:
    """Return frozen Decimal weights that are named, non-negative, and total 1.0."""
    if not isinstance(weights, Mapping) or not weights:
        raise ValueError("weights must be a non-empty mapping")
    result: dict[str, Decimal] = {}
    for key, number in weights.items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError("weight names must be non-empty text")
        try:
            value = Decimal(str(number))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValueError("weights must be decimal numbers") from exc
        if not value.is_finite() or value < 0:
            raise ValueError("weights must be finite and non-negative")
        result[key] = value
    if sum(result.values(), Decimal("0")) != Decimal("1.0"):
        raise ValueError("rubric weights must total 1.0")
    return MappingProxyType(result)


def collect_evidence_ids(value: Any, field: str = "record") -> tuple[str, ...]:
    """Collect every ``evidence_ids`` list nested anywhere in ``value``."""
    found: list[str] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key == "evidence_ids":
                found.extend(_string_tuple(item, f"{field}.evidence_ids"))
            else:
                found.extend(collect_evidence_ids(item, f"{field}.{key}"))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            found.extend(collect_evidence_ids(item, f"{field}[{index}]"))
    return tuple(dict.fromkeys(found))


@dataclass(frozen=True, slots=True)
class SignalRubric:
    weights: Mapping[str, Decimal]
    threshold: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(self, "weights", validate_weights(self.weights))
        if self.threshold != THRESHOLD:
            raise ValueError("rubric threshold must be exactly 0.90")


@dataclass(frozen=True, slots=True)
class SignalGroundTruthRecord:
    company_id: str
    body: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "body", _freeze(_mapping(self.body, "record body")))

    @property
    def all_evidence_ids(self) -> frozenset[str]:
        return frozenset(collect_evidence_ids(self.body))


@dataclass(frozen=True, slots=True)
class SignalDataset:
    enrichment_id: str
    records: Mapping[str, SignalGroundTruthRecord]
    all_ids: tuple[str, ...]
    development_ids: tuple[str, ...]
    holdout_ids: tuple[str, ...]
    rubric: SignalRubric
    dataset_hash: str
    holdout_manifest: Mapping[str, str]
    holdout_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "records", MappingProxyType(dict(self.records)))
        object.__setattr__(self, "holdout_manifest", MappingProxyType(dict(self.holdout_manifest)))


_EVALUATOR_TOKEN = object()


@dataclass(frozen=True, slots=True, repr=False)
class EvaluatorDatasetCapability:
    _token: object


@dataclass(frozen=True, slots=True)
class EvaluatorSignalDataset:
    public: SignalDataset
    records: Mapping[str, SignalGroundTruthRecord]

    def __post_init__(self) -> None:
        object.__setattr__(self, "records", MappingProxyType(dict(self.records)))


def issue_evaluator_dataset_capability() -> EvaluatorDatasetCapability:
    """Issue the capability that composition must withhold from non-evaluators."""
    return EvaluatorDatasetCapability(_EVALUATOR_TOKEN)


RecordValidator = Callable[[SignalGroundTruthRecord, CompanyDossier], None]


class GroundTruthLoader(Protocol):
    def __call__(
        self, root: str | Path, dossiers: Mapping[str, CompanyDossier], *,
        capability: object | None = None,
    ) -> SignalDataset | EvaluatorSignalDataset: ...


def _load_yaml(path: Path, label: str) -> tuple[Mapping[str, Any], bytes]:
    try:
        raw = path.read_bytes()
        value = yaml.safe_load(raw)
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"{label} YAML is missing or malformed") from exc
    return _mapping(value, label), raw


def _load_rubric(path: Path, weights: Mapping[str, Decimal]) -> SignalRubric:
    value, _ = _load_yaml(path, "rubric")
    _exact_keys(value, {"weights", "threshold"}, "rubric")
    file_weights = validate_weights(_mapping(value["weights"], "rubric weights"))
    if dict(file_weights) != dict(weights):
        raise ValueError("rubric weights do not match the spec weights")
    try:
        threshold = Decimal(str(value["threshold"]))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("rubric threshold must be a decimal number") from exc
    return SignalRubric(file_weights, threshold)


def _load_split(path: Path) -> tuple[tuple[str, ...], tuple[str, ...]]:
    value, _ = _load_yaml(path, "split")
    _exact_keys(value, {"development", "holdout"}, "split")
    development = _string_tuple(value["development"], "development split")
    holdout = _string_tuple(value["holdout"], "holdout split")
    if development != DEVELOPMENT_IDS or holdout != HOLDOUT_IDS:
        raise ValueError("split drift: development and holdout IDs are locked")
    return development, holdout


def _load_record(
    path: Path, company_id: str, dossier: CompanyDossier, validate_record: RecordValidator | None,
    *, check_citations: bool = True,
) -> SignalGroundTruthRecord:
    value, _ = _load_yaml(path, f"ground truth {company_id}")
    if value.get("company_id") != company_id or dossier.company_id != company_id:
        raise ValueError(f"ground truth company ID mismatch for {company_id}")
    body = {key: item for key, item in value.items() if key != "company_id"}
    if not body:
        raise ValueError(f"ground truth {company_id} must contain a record body")
    record = SignalGroundTruthRecord(company_id, body)
    retained = {item.evidence_id for item in dossier.evidence}
    if check_citations and not record.all_evidence_ids <= retained:
        raise ValueError(f"ground truth {company_id} cites Evidence absent from its signal dossier")
    if validate_record is not None:
        validate_record(record, dossier)
    return record


def _validate_files(dataset_root: Path) -> None:
    ground_truth = {path.name for path in (dataset_root / "ground-truth").glob("*.yaml")}
    expected = {f"{company_id}.yaml" for company_id in _EXPECTED_IDS}
    if ground_truth != expected:
        raise ValueError("ground truth files must be exactly saas-01 through saas-10")
    signals = {path.name for path in dataset_root.glob("*.yaml")} - {"split.yaml", "rubric.yaml"}
    if signals != expected:
        raise ValueError("signal dossier files must be exactly saas-01 through saas-10")


def _hash_paths(digest: Any, dataset_root: Path, paths: list[Path]) -> None:
    for path in paths:
        digest.update(path.relative_to(dataset_root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")


def _record_hash(dataset_root: Path, company_id: str) -> str:
    digest = sha256()
    _hash_paths(digest, dataset_root, [
        dataset_root / f"{company_id}.yaml",
        dataset_root / "ground-truth" / f"{company_id}.yaml",
    ])
    return digest.hexdigest()


def dataset_hash(dataset_root: str | Path) -> str:
    """Hash split, rubric, every ground-truth file, and every signal dossier file."""
    root = Path(dataset_root)
    _validate_files(root)
    digest = sha256()
    paths = [root / "split.yaml", root / "rubric.yaml"]
    paths.extend(root / f"{company_id}.yaml" for company_id in ALL_IDS)
    paths.extend(root / "ground-truth" / f"{company_id}.yaml" for company_id in ALL_IDS)
    _hash_paths(digest, root, paths)
    return digest.hexdigest()


def load_signal_dataset(
    root: str | Path,
    dossiers: Mapping[str, CompanyDossier],
    *,
    enrichment_id: str,
    weights: Mapping[str, Decimal],
    validate_record: RecordValidator | None = None,
    capability: object | None = None,
    check_citations: bool = True,
) -> SignalDataset | EvaluatorSignalDataset:
    """Validate all ten records; return development answers only unless the caller
    holds the evaluator capability.

    ``check_citations=False`` skips the citation-vs-dossier subset check for
    variant corpora collected by another search provider: ground-truth Evidence
    IDs are content-addressed to the sealed collection, so a new collection can
    never contain them and the citation component is excluded cross-collector.
    """
    if enrichment_id not in P0_ENRICHMENTS:
        raise ValueError(f"unknown P0 enrichment: {enrichment_id}")
    if capability is not None and (
        not isinstance(capability, EvaluatorDatasetCapability)
        or capability._token is not _EVALUATOR_TOKEN
    ):
        raise PermissionError("valid evaluator capability is required")
    dataset_root = Path(root) / "benchmarks" / "signals" / enrichment_id
    _validate_files(dataset_root)
    missing = _EXPECTED_IDS - set(dossiers)
    if missing:
        raise ValueError(f"missing matching signal dossiers: {sorted(missing)}")
    development, holdout = _load_split(dataset_root / "split.yaml")
    rubric = _load_rubric(dataset_root / "rubric.yaml", validate_weights(weights))
    records = {
        company_id: _load_record(
            dataset_root / "ground-truth" / f"{company_id}.yaml", company_id,
            dossiers[company_id], validate_record, check_citations=check_citations,
        )
        for company_id in ALL_IDS
    }
    manifest = {company_id: _record_hash(dataset_root, company_id) for company_id in holdout}
    holdout_digest = sha256()
    for company_id in holdout:
        holdout_digest.update(company_id.encode("utf-8"))
        holdout_digest.update(b"\0")
        holdout_digest.update(manifest[company_id].encode("ascii"))
        holdout_digest.update(b"\0")
    public = SignalDataset(
        enrichment_id, {company_id: records[company_id] for company_id in development},
        ALL_IDS, development, holdout, rubric, dataset_hash(dataset_root),
        manifest, holdout_digest.hexdigest(),
    )
    if capability is None:
        return public
    return EvaluatorSignalDataset(public, records)


def dataset_loader(
    enrichment_id: str, weights: Mapping[str, Decimal],
    validate_record: RecordValidator | None = None,
) -> GroundTruthLoader:
    """Bind a spec's enrichment ID and weights into a ``GroundTruthLoader``."""
    return partial(
        load_signal_dataset, enrichment_id=enrichment_id,
        weights=validate_weights(weights), validate_record=validate_record,
    )
