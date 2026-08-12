from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any, Mapping

import yaml

from .contracts import SECRET_KEYS


SEMVER = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
EXPECTED_P0_IDS = {
    "analogy-value-translator",
    "company-description",
    "competitor-intelligence",
    "growth-signals",
    "icp-persona-analysis",
    "job-opportunity-mining",
    "news-product-launches",
    "running-ads-offer-intelligence",
}
ALLOWED_KEYS = {
    "id", "name", "owner", "version", "status", "family", "priority",
    "entity_scopes", "input_schema_version", "output_schema_version",
    "required_inputs", "optional_inputs", "execution_mode",
    "provider_candidates", "fallback_order", "freshness_days",
    "source_requirements", "caps", "early_stop", "failure_rule",
    "output_visibility", "benchmark_dataset_version", "automated_gate",
    "human_gate",
}


class DefinitionError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class EnrichmentDefinition:
    id: str
    name: str
    owner: str
    version: str
    status: str
    family: str
    priority: str
    entity_scopes: tuple[str, ...]
    input_schema_version: str
    output_schema_version: str
    required_inputs: tuple[str, ...]
    optional_inputs: tuple[str, ...]
    execution_mode: str
    provider_candidates: tuple[str, ...]
    fallback_order: tuple[str, ...]
    freshness_days: int
    source_requirements: tuple[str, ...]
    caps: Mapping[str, int | float]
    early_stop: str
    failure_rule: str
    output_visibility: str
    benchmark_dataset_version: str
    automated_gate: str
    human_gate: str


def _secret_key(value: Any) -> str | None:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).lower().replace("-", "_")
            if normalized in SECRET_KEYS or normalized.endswith("_token") or normalized.endswith("_secret"):
                return str(key)
            found = _secret_key(item)
            if found:
                return found
    if isinstance(value, list):
        for item in value:
            found = _secret_key(item)
            if found:
                return found
    return None


def _tuple_of_text(data: dict[str, Any], key: str) -> tuple[str, ...]:
    value = data[key]
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise DefinitionError(f"{key} must be a list of non-empty strings")
    return tuple(value)


def load_definition(path: Path) -> EnrichmentDefinition:
    text = path.read_text(encoding="utf-8")
    if "${" in text:
        raise DefinitionError("environment interpolation is forbidden")
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise DefinitionError(f"invalid YAML in {path}: {error}") from error
    if not isinstance(data, dict):
        raise DefinitionError("definition must be a YAML mapping")
    secret = _secret_key(data)
    if secret:
        raise DefinitionError(f"secret-bearing key is forbidden: {secret}")
    unknown = sorted(set(data) - ALLOWED_KEYS)
    missing = sorted(ALLOWED_KEYS - set(data))
    if unknown:
        raise DefinitionError(f"unknown keys: {', '.join(unknown)}")
    if missing:
        raise DefinitionError(f"missing keys: {', '.join(missing)}")
    if not isinstance(data["version"], str) or not SEMVER.fullmatch(data["version"]):
        raise DefinitionError("version must be a semantic version")
    if data["priority"] != "P0" or data["status"] != "proposed":
        raise DefinitionError("P0 definitions must start in proposed status")
    if data["input_schema_version"] != "1.0" or data["output_schema_version"] != "1.0":
        raise DefinitionError("only schema version 1.0 is supported")
    if data["output_visibility"] not in {"message_safe", "filter_only"}:
        raise DefinitionError("invalid output_visibility")
    if data["automated_gate"] != "candidate_only" or data["human_gate"] != "blind_verdict_required":
        raise DefinitionError("definitions must enforce candidate-only automation and blind human review")
    caps = data["caps"]
    expected_caps = {"queries", "scrapes", "retries", "paid_cost_usd"}
    if not isinstance(caps, dict) or set(caps) != expected_caps:
        raise DefinitionError("caps must define queries, scrapes, retries, and paid_cost_usd")
    if any(not isinstance(value, (int, float)) or value < 0 for value in caps.values()):
        raise DefinitionError("caps must be non-negative numbers")
    if caps["paid_cost_usd"] > 1:
        raise DefinitionError("paid_cost_usd cannot exceed the aggregate experiment cap")
    values = {
        key: _tuple_of_text(data, key)
        for key in ("entity_scopes", "required_inputs", "optional_inputs", "provider_candidates", "fallback_order", "source_requirements")
    }
    return EnrichmentDefinition(
        **{key: data[key] for key in ALLOWED_KEYS - set(values) - {"caps"}},
        **values,
        caps=MappingProxyType(dict(caps)),
    )


def load_registry(root: Path) -> dict[str, EnrichmentDefinition]:
    definitions: dict[str, EnrichmentDefinition] = {}
    for path in sorted(root.glob("*.yaml")):
        item = load_definition(path)
        if item.id in definitions:
            raise DefinitionError(f"duplicate enrichment id: {item.id}")
        definitions[item.id] = item
    actual = set(definitions)
    if actual != EXPECTED_P0_IDS:
        raise DefinitionError(f"P0 registry mismatch; missing={sorted(EXPECTED_P0_IDS - actual)} extra={sorted(actual - EXPECTED_P0_IDS)}")
    return dict(sorted(definitions.items()))
