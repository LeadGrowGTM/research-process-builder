"""Signal dossiers: a cached base dossier plus enrichment-specific Evidence.

Base dossiers under ``benchmarks/dossiers`` are never modified. A signal
enrichment (ads, news, competitors) collects extra Evidence and writes a
self-contained signal dossier to ``benchmarks/signals/<enrichment_id>/<company>.yaml``
so a prompt can cite either the base or the signal Evidence.
"""

from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from .contracts import CompanyDossier, EvidenceRef, FieldAssertion, Visibility


ALL_IDS = tuple(f"saas-{index:02d}" for index in range(1, 11))


def signal_dossier_path(repo_root: Path, enrichment_id: str, company_id: str) -> Path:
    return Path(repo_root) / "benchmarks" / "signals" / enrichment_id / f"{company_id}.yaml"


def _dossier_from_value(value: Any, path: Path) -> CompanyDossier:
    if not isinstance(value, Mapping):
        raise ValueError(f"dossier YAML must be a mapping: {path}")
    if value.get("corrections"):
        raise ValueError(f"cached dossiers must not carry corrections: {path}")
    evidence = tuple(EvidenceRef(
        item["evidence_id"], item["url"], datetime.fromisoformat(item["retrieved_at"]),
        item["content_hash"], item["excerpt"],
    ) for item in value["evidence"])
    assertions = tuple(FieldAssertion(
        item["field"], item["value"], tuple(item["evidence_ids"]),
        float(item["confidence"]), Visibility(item["visibility"]),
    ) for item in value["assertions"])
    return CompanyDossier(
        value["company_id"], value["schema_version"], assertions, evidence,
        tuple(value.get("unknowns", ())),
    )


def load_dossier(path: Path) -> CompanyDossier:
    """Load one dossier YAML (base or signal) into a validated CompanyDossier."""
    path = Path(path)
    return _dossier_from_value(yaml.safe_load(path.read_text(encoding="utf-8")), path)


def load_cached_dossiers(
    repo_root: Path, ids: Sequence[str] = ALL_IDS,
) -> dict[str, CompanyDossier]:
    """Load the frozen base dossiers for the SaaS benchmark corpus."""
    return {
        company_id: load_dossier(Path(repo_root) / "benchmarks" / "dossiers" / f"{company_id}.yaml")
        for company_id in ids
    }


load_signal_dossier = load_dossier


def _dossier_value(dossier: CompanyDossier) -> dict[str, Any]:
    if dossier.corrections:
        raise ValueError("signal dossiers must not carry corrections")
    return {
        "assertions": [{
            "confidence": item.confidence,
            "evidence_ids": list(item.evidence_ids),
            "field": item.field,
            "value": item.value,
            "visibility": item.visibility.value,
        } for item in dossier.assertions],
        "company_id": dossier.company_id,
        "corrections": [],
        "evidence": [{
            "content_hash": item.content_hash,
            "evidence_id": item.evidence_id,
            "excerpt": item.excerpt,
            "retrieved_at": item.retrieved_at.isoformat(),
            "url": item.url,
        } for item in dossier.evidence],
        "schema_version": dossier.schema_version,
        "unknowns": list(dossier.unknowns),
    }


def save_signal_dossier(path: Path, dossier: CompanyDossier) -> None:
    """Atomically write a signal dossier as YAML with the base-dossier layout."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(
        _dossier_value(dossier), allow_unicode=True, sort_keys=True, default_flow_style=False,
    )
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def signal_dossier(
    company_id: str,
    base: CompanyDossier,
    refs: Sequence[EvidenceRef],
    assertions: Sequence[FieldAssertion] = (),
    unknowns: Sequence[str] = (),
) -> CompanyDossier:
    """Merge base Evidence with new signal Evidence without mutating the base.

    Evidence is deduplicated by ``evidence_id``: an identical duplicate is
    kept once, and a differing record behind the same ID is a collision error.
    """
    if company_id != base.company_id:
        raise ValueError("signal dossier company ID must match the base dossier")
    if not all(isinstance(item, EvidenceRef) for item in refs):
        raise ValueError("refs must contain EvidenceRef values")
    if not all(isinstance(item, FieldAssertion) for item in assertions):
        raise ValueError("assertions must contain FieldAssertion values")
    by_id: dict[str, EvidenceRef] = {item.evidence_id: item for item in base.evidence}
    merged = list(base.evidence)
    for ref in refs:
        existing = by_id.get(ref.evidence_id)
        if existing is None:
            by_id[ref.evidence_id] = ref
            merged.append(ref)
        elif existing != ref:
            raise ValueError(f"evidence ID collision: {ref.evidence_id}")
    merged_unknowns = tuple(dict.fromkeys((*base.unknowns, *unknowns)))
    if any(not isinstance(item, str) or not item for item in merged_unknowns):
        raise ValueError("unknowns must contain non-empty field names")
    return CompanyDossier(
        company_id, base.schema_version, (*base.assertions, *assertions),
        tuple(merged), merged_unknowns, base.corrections,
    )
