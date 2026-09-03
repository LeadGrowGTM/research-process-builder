"""Enrichment packages: one self-describing directory per validated process.

A package is the unit the wider GTM landscape installs. It is a directory whose
name is the enrichment id::

    enrichments/<id>/
      <id>.md          prompt body plus the package manifest as YAML frontmatter
      schema.py        sidecar pydantic InputModel / OutputModel
      run.py           the package CLI (describe / emit / body / render / execute)
      variants/*.yaml  narrow overlays for adjacent use cases

The prompt file is named after the id: one package, one prompt, found from the
directory name alone. It is not named for the consumer's lookup. The GTM
orchestrator's prompt loader resolves ``library_path/<runtime_prompt_name>.md``,
and ``gtm.runtime_prompt_name`` is a catalog-side name that need not equal the
id - ``news-product-launches`` installs as ``recent-news-summary`` - so an
install copies the prompt to ``library/<runtime_prompt_name>.md`` rather than
pointing the loader at this directory. The file is copied whole, manifest
included: the consumer's ``parse_prompt_file`` splits frontmatter from body and
loads the body alone, so the manifest reaches no model on either side.

The manifest answers, without reading the prompt body: what goes in, what comes
out, what you get out of it in one line and in detail, which model it was proved
on, what score it earned on which dataset, and which parts of the prompt may be
edited before a run without invalidating that proof.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from hashlib import sha256
import importlib.util
import json
import math
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any, Mapping

import yaml
from pydantic import BaseModel

from .definitions import SEMVER, _secret_key
from .executors import P0_ENRICHMENTS

LIFECYCLE = ("proposed", "experiment", "candidate", "approved", "rejected")
KINDS = ("lookup", "monitoring")
REQUIRED_KEYS = {
    "id", "name", "title", "summary", "description", "version", "status", "kind",
    "entity", "target_model", "proof_temperature", "proof_max_output_tokens", "runner",
    "schema_module", "inputs", "outputs", "gtm", "evaluation", "adaptation",
}
CONSUMER_RESERVED_KEYS = frozenset({"conversation", "tool_use"})
ID_PATTERN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
# A variant may restate how the enrichment is described and append guidance to
# the prompt. Anything else changes what was proved, so it forces revalidation.
DESCRIPTIVE_KEYS = {"title", "summary", "description", "name"}
VARIANT_KEYS = DESCRIPTIVE_KEYS | {
    "variant", "prompt_append", "inputs", "gtm", "target_model", "notes",
}
# Keys the emitted EnrichmentSpec reads straight through. They are optional in a
# manifest - emit_registry_entry reports the missing ones - but a key that is
# present must already hold the type the catalog expects, because emit coerces
# and coercion is silent: bool("false") is True and tuple("a b") is a tuple of
# characters.
GTM_TEXT_KEYS = {
    "slug", "provider", "type", "enrichment_level", "runtime_prompt_name",
    "cost_estimate",
}
GTM_LIST_KEYS = {"requires_tools"}
APPROVAL_GATE = 0.90
RENDER_INPUTS = frozenset({"company_name", "domain"})
ADAPTATION_LIST_KEYS = ("safe_edits", "locked", "revalidate_when")


class PackageError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class EnrichmentPackage:
    root: Path
    id: str
    name: str
    title: str
    summary: str
    description: str
    version: str
    status: str
    kind: str
    entity: str
    target_model: str
    proof_temperature: float | None
    proof_max_output_tokens: int
    runner: str
    schema_module: str
    inputs: Mapping[str, Any]
    outputs: Mapping[str, Any]
    gtm: Mapping[str, Any]
    evaluation: Mapping[str, Any]
    adaptation: Mapping[str, Any]
    body: str
    variant: str | None = None
    revalidation: str = "not_required"

    @property
    def required_inputs(self) -> tuple[str, ...]:
        return tuple(self.inputs.get("required", {}))

    @property
    def optional_inputs(self) -> tuple[str, ...]:
        return tuple(self.inputs.get("optional", {}))

    @property
    def input_columns(self) -> tuple[str, ...]:
        return tuple(
            descriptor["consumer_column"]
            for group in ("required", "optional")
            for descriptor in self.inputs.get(group, {}).values()
        )

    @property
    def output_columns(self) -> tuple[str, ...]:
        return tuple(
            descriptor["consumer_column"]
            for descriptor in self.outputs.values()
            if descriptor.get("consumer_column") is not None
        )

    def card(self) -> dict[str, Any]:
        """The machine-readable summary a consumer registry indexes."""
        return {
            "id": self.id,
            "variant": self.variant,
            "version": self.version,
            "status": self.status,
            "kind": self.kind,
            "entity": self.entity,
            "title": self.title,
            "summary": self.summary,
            "description": self.description,
            "inputs": {
                "required": _plain(self.inputs.get("required", {})),
                "optional": _plain(self.inputs.get("optional", {})),
            },
            "outputs": _plain(self.outputs),
            "target_model": self.target_model,
            "gtm": _plain(self.gtm),
            "evaluation": _plain(self.evaluation),
            "revalidation": self.revalidation,
        }


def _split_frontmatter(text: str, path: Path) -> tuple[dict[str, Any], str]:
    if not text.startswith("---"):
        raise PackageError(f"{path} must open with a frontmatter fence")
    rest = text[3:].lstrip("\r").lstrip("\n")
    end = None
    position = 0
    while position < len(rest):
        newline = rest.find("\n", position)
        line_end = len(rest) if newline == -1 else newline
        if rest[position:line_end].rstrip("\r") == "---":
            end = position
            break
        if newline == -1:
            break
        position = newline + 1
    if end is None:
        raise PackageError(f"{path} has no closing frontmatter fence")
    body = rest[end + 3:].lstrip("\r").lstrip("\n")
    try:
        manifest = yaml.safe_load(rest[:end]) or {}
    except yaml.YAMLError as error:
        raise PackageError(f"{path} has invalid frontmatter YAML: {error}") from error
    if not isinstance(manifest, dict):
        raise PackageError(f"{path} frontmatter must be a mapping")
    return manifest, body


def _plain(value: Any) -> Any:
    """A loaded field back as plain containers, so validation sees real dicts."""
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {key: _freeze(item) for key, item in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _merge_variant_inputs(
    current: Mapping[str, Any], overlay: Mapping[str, Any], variant: str,
) -> dict[str, Any]:
    merged = _plain(current)
    for key, value in overlay.items():
        if key not in ("required", "optional"):
            merged[key] = _plain(value)
    for group in ("required", "optional"):
        if group not in overlay:
            continue
        current_group = current.get(group)
        overlay_group = overlay[group]
        if not isinstance(current_group, Mapping) or not isinstance(
            overlay_group, Mapping
        ):
            merged[group] = _plain(overlay_group)
            continue
        if set(overlay_group) - set(current_group):
            raise PackageError(
                f"variant {variant} may not add, remove, or move input names; "
                "a variant needing different inputs is a different enrichment "
                "or a v2 of the parent"
            )
        merged_group = _plain(current_group)
        for name, value in overlay_group.items():
            current_descriptor = current_group[name]
            if isinstance(current_descriptor, Mapping) and isinstance(value, Mapping):
                descriptor = _plain(current_descriptor)
                descriptor.update(_plain(value))
                merged_group[name] = descriptor
            else:
                merged_group[name] = _plain(value)
        merged[group] = merged_group
    return merged


def _validate(manifest: Mapping[str, Any], root: Path) -> None:
    reserved = sorted(CONSUMER_RESERVED_KEYS & set(manifest))
    if reserved:
        raise PackageError(
            "consumer-reserved frontmatter keys are forbidden: " + ", ".join(reserved)
        )
    missing = sorted(REQUIRED_KEYS - set(manifest))
    if missing:
        raise PackageError(f"{root.name} manifest is missing keys: {', '.join(missing)}")
    if not ID_PATTERN.fullmatch(str(manifest["id"])):
        raise PackageError("id must be lower-kebab-case")
    if manifest["id"] != root.name:
        raise PackageError(
            f"id {manifest['id']!r} must equal package directory {root.name!r}"
        )
    if not isinstance(manifest["version"], str) or not SEMVER.fullmatch(manifest["version"]):
        raise PackageError("version must be a semantic version")
    if manifest["status"] not in LIFECYCLE:
        raise PackageError(f"status must be one of {LIFECYCLE}")
    if manifest["kind"] not in KINDS:
        raise PackageError(f"kind must be one of {KINDS}")
    for key in (
        "name", "title", "summary", "description", "entity", "target_model",
        "runner", "schema_module",
    ):
        if not isinstance(manifest[key], str) or not manifest[key].strip():
            raise PackageError(f"{key} must be non-empty text")
    temperature = manifest["proof_temperature"]
    if temperature is not None and (
        isinstance(temperature, bool)
        or not isinstance(temperature, (int, float))
        or not math.isfinite(temperature)
    ):
        raise PackageError("proof_temperature must be a finite number or null")
    max_output_tokens = manifest["proof_max_output_tokens"]
    if (
        isinstance(max_output_tokens, bool)
        or not isinstance(max_output_tokens, int)
        or max_output_tokens <= 0
    ):
        raise PackageError("proof_max_output_tokens must be a positive integer")
    secret = _secret_key(dict(manifest))
    if secret:
        raise PackageError(f"secret-bearing key is forbidden in a manifest: {secret}")
    for key in ("runner", "schema_module"):
        reference = Path(manifest[key])
        package_root = root.resolve()
        candidate = (root / reference).resolve()
        if reference.is_absolute() or not candidate.is_relative_to(package_root):
            raise PackageError(f"{key} must be a relative file inside the package")
        if not candidate.is_file():
            raise PackageError(f"{key} points at a missing file: {manifest[key]}")
        try:
            content = candidate.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            raise PackageError(f"{key} must be a readable Python file") from error
        if not content.strip():
            raise PackageError(f"{key} must be a non-empty Python file")
    schema_path = root / manifest["schema_module"]
    module_spec = importlib.util.spec_from_file_location(
        f"_enrichment_schema_{sha256(schema_path.read_bytes()).hexdigest()}",
        schema_path,
    )
    if module_spec is None or module_spec.loader is None:
        raise PackageError("schema_module could not be loaded")
    schema = importlib.util.module_from_spec(module_spec)
    try:
        module_spec.loader.exec_module(schema)
    except Exception as error:
        raise PackageError(f"schema_module could not be loaded: {error}") from error
    for model_name in ("InputModel", "OutputModel"):
        model = getattr(schema, model_name, None)
        if not isinstance(model, type) or not issubclass(model, BaseModel):
            raise PackageError(
                f"schema_module must export pydantic {model_name}"
            )
    inputs = manifest["inputs"]
    if not isinstance(inputs, dict):
        raise PackageError("inputs must be a mapping")
    required = inputs.get("required")
    if not isinstance(required, dict) or not required:
        raise PackageError(
            "inputs.required must be a non-empty mapping of name to descriptor"
        )
    optional = inputs.get("optional")
    if not isinstance(optional, dict):
        raise PackageError("inputs.optional must be a mapping of name to descriptor")
    overlapping_inputs = set(required) & set(optional)
    if overlapping_inputs:
        names = ", ".join(sorted((str(name) for name in overlapping_inputs)))
        raise PackageError(f"input names cannot be both required and optional: {names}")
    input_columns: list[str] = []
    for group, values in (("required", required), ("optional", optional)):
        for name, descriptor in values.items():
            if not isinstance(name, str) or not name.strip():
                raise PackageError(f"inputs.{group} names must be non-empty strings")
            if not isinstance(descriptor, dict) or set(descriptor) != {
                "consumer_column", "description",
            } or any(
                not isinstance(descriptor[key], str) or not descriptor[key].strip()
                for key in ("consumer_column", "description")
            ):
                raise PackageError(
                    f"inputs.{group}.{name} must declare non-empty description and "
                    "consumer_column"
                )
            input_columns.append(descriptor["consumer_column"])
    if len(input_columns) != len(set(input_columns)):
        raise PackageError("input consumer_column mappings must be unique")
    unsupported_inputs = sorted((set(required) | set(optional)) - RENDER_INPUTS)
    if unsupported_inputs:
        raise PackageError(
            "declared inputs cannot be rendered by this package runner: "
            f"{', '.join(unsupported_inputs)}"
        )
    outputs = manifest["outputs"]
    if not isinstance(outputs, dict) or not outputs:
        raise PackageError("outputs must be a non-empty mapping")
    output_columns: list[str] = []
    for name, descriptor in outputs.items():
        if not isinstance(name, str) or not name.strip():
            raise PackageError("output names must be non-empty strings")
        if not isinstance(descriptor, dict):
            raise PackageError(f"outputs.{name} must be a descriptor mapping")
        if set(descriptor) == {"fields"}:
            fields = descriptor["fields"]
            if not isinstance(fields, list) or not fields or any(
                not isinstance(field, str) or not field.strip() for field in fields
            ):
                raise PackageError(
                    f"outputs.{name}.fields must be a list of non-empty strings"
                )
            continue
        if set(descriptor) != {"type", "description", "consumer_column"}:
            raise PackageError(
                f"outputs.{name} must declare exactly type, description, and "
                "consumer_column"
            )
        if any(
            not isinstance(descriptor.get(key), str)
            or not descriptor[key].strip()
            for key in ("type", "description")
        ):
            raise PackageError(
                f"outputs.{name} must declare non-empty type and description"
            )
        consumer_column = descriptor["consumer_column"]
        if consumer_column is not None:
            if not isinstance(consumer_column, str) or not consumer_column.strip():
                raise PackageError(
                    f"outputs.{name}.consumer_column must be non-empty text when declared"
                )
            output_columns.append(consumer_column)
    if not output_columns:
        raise PackageError("outputs must map at least one field to a consumer_column")
    if len(output_columns) != len(set(output_columns)):
        raise PackageError("output consumer_column mappings must be unique")
    gtm = manifest["gtm"]
    if not isinstance(gtm, dict) or not gtm:
        raise PackageError("gtm must be a non-empty mapping")
    derived_columns = sorted({"input_columns", "output_columns"} & set(gtm))
    if derived_columns:
        raise PackageError(
            "gtm column lists are derived from field consumer_column mappings: "
            + ", ".join(derived_columns)
        )
    for key in GTM_TEXT_KEYS & set(gtm):
        if not isinstance(gtm[key], str) or not gtm[key].strip():
            raise PackageError(f"gtm.{key} must be non-empty text")
    for key in GTM_LIST_KEYS & set(gtm):
        if not isinstance(gtm[key], list) or not gtm[key] or any(
            not isinstance(item, str) or not item.strip() for item in gtm[key]
        ):
            raise PackageError(
                f"gtm.{key} must be a list of non-empty strings, not a bare scalar"
            )
    if "linkedin_safe" in gtm and not isinstance(gtm["linkedin_safe"], bool):
        raise PackageError(
            "gtm.linkedin_safe must be a YAML boolean; quoted text is always truthy"
        )
    cost = gtm.get("cost_per_100", 0)
    if (
        isinstance(cost, bool)
        or not isinstance(cost, (int, float))
        or not math.isfinite(cost)
    ):
        raise PackageError("gtm.cost_per_100 must be a number, not text")
    if cost < 0:
        raise PackageError("gtm.cost_per_100 must be non-negative")
    adaptation = manifest["adaptation"]
    if not isinstance(adaptation, dict):
        raise PackageError("adaptation must be a mapping")
    missing_adaptation = sorted(
        {"adaptable", *ADAPTATION_LIST_KEYS, "revalidate_with"} - set(adaptation)
    )
    if missing_adaptation:
        raise PackageError(
            "adaptation is missing fields: " + ", ".join(missing_adaptation)
        )
    if not isinstance(adaptation["adaptable"], bool):
        raise PackageError("adaptation.adaptable must be a YAML boolean")
    for key in ADAPTATION_LIST_KEYS:
        value = adaptation[key]
        if not isinstance(value, list) or any(
            not isinstance(item, str) or not item.strip() for item in value
        ):
            raise PackageError(
                f"adaptation.{key} must be a list of non-empty strings"
            )
    if adaptation["adaptable"] and not adaptation["locked"]:
        raise PackageError(
            "an adaptable package must list the prompt sections that stay locked"
        )
    if adaptation["adaptable"] and (
        not isinstance(adaptation["revalidate_with"], str)
        or not adaptation["revalidate_with"].strip()
    ):
        raise PackageError(
            "an adaptable package must provide adaptation.revalidate_with"
        )
    evaluation = manifest["evaluation"]
    if not isinstance(evaluation, dict):
        raise PackageError("evaluation must be a mapping")
    for key in ("dev", "holdout", "gate"):
        value = evaluation.get(key)
        if value is None:
            continue
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or not 0 <= value <= 1
        ):
            raise PackageError(f"evaluation.{key} must be a finite number from 0 to 1")
    if manifest["status"] == "approved":
        for key in (
            "dataset", "scorer", "candidate", "dev", "holdout", "gate",
            "approved_on", "report",
        ):
            if evaluation.get(key) in (None, ""):
                raise PackageError(f"an approved package needs evaluation.{key}")
        if not isinstance(evaluation["approved_on"], str):
            raise PackageError(
                "evaluation.approved_on must be quoted ISO text, not a YAML date"
            )
        approved_on = evaluation["approved_on"].strip()
        if (
            approved_on != evaluation["approved_on"]
            or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", approved_on)
        ):
            raise PackageError(
                "evaluation.approved_on must be a real YYYY-MM-DD calendar date"
            )
        try:
            date.fromisoformat(approved_on)
        except ValueError as error:
            raise PackageError(
                "evaluation.approved_on must be a real YYYY-MM-DD calendar date"
            ) from error
        for key in ("dataset", "scorer", "candidate", "report"):
            if not isinstance(evaluation[key], str) or not evaluation[key].strip():
                raise PackageError(f"evaluation.{key} must be non-empty text")
        if evaluation["gate"] < APPROVAL_GATE:
            raise PackageError(
                f"an approved package gate must be at least {APPROVAL_GATE:.2f}"
            )
        for split in ("dev", "holdout"):
            if evaluation[split] < evaluation["gate"]:
                raise PackageError(
                    f"an approved package must meet its own gate on {split}"
                )


def load_package(root: Path, *, variant: str | None = None) -> EnrichmentPackage:
    """Load ``enrichments/<id>/``, optionally with a variant overlay applied."""
    root = Path(root)
    prompt_path = root / f"{root.name}.md"
    if not prompt_path.is_file():
        raise PackageError(f"package {root} has no {root.name}.md prompt file")
    text = prompt_path.read_text(encoding="utf-8")
    if "${" in text:
        raise PackageError("environment interpolation is forbidden in a package")
    manifest, body = _split_frontmatter(text, prompt_path)
    if not body.strip():
        raise PackageError("prompt body must be non-empty")
    _validate(manifest, root)
    package = EnrichmentPackage(
        root=root,
        body=body,
        **{key: _freeze(manifest[key]) for key in REQUIRED_KEYS},
    )
    if variant is None:
        return package
    return apply_variant(package, variant)


def apply_variant(package: EnrichmentPackage, variant: str) -> EnrichmentPackage:
    """Overlay ``variants/<variant>.yaml`` onto a loaded package.

    A variant that only restates the description and appends prompt guidance
    inherits the parent's proof. Touching anything the score depends on - the
    model, the declared inputs, the GTM contract - keeps the package runnable
    but marks it ``revalidation: required`` and drops it out of ``approved`` so
    no consumer can spend the parent's score on it.
    """
    if not isinstance(variant, str) or not ID_PATTERN.fullmatch(variant):
        raise PackageError("variant must be a safe lower-kebab-case file stem")
    path = package.root / "variants" / f"{variant}.yaml"
    if not path.is_file():
        raise PackageError(f"unknown variant {variant!r}: {path} does not exist")
    text = path.read_text(encoding="utf-8")
    if "${" in text:
        raise PackageError("environment interpolation is forbidden in a package")
    try:
        overlay = yaml.safe_load(text) or {}
    except yaml.YAMLError as error:
        raise PackageError(f"variant {variant} has invalid YAML: {error}") from error
    if not isinstance(overlay, dict):
        raise PackageError(f"variant {variant} must be a YAML mapping")
    unknown = sorted(set(overlay) - VARIANT_KEYS)
    if unknown:
        raise PackageError(f"variant {variant} may not override: {', '.join(unknown)}")
    secret = _secret_key(overlay)
    if secret:
        raise PackageError(f"secret-bearing key is forbidden in a manifest: {secret}")
    declared = overlay.get("variant")
    if declared is not None and declared != variant:
        raise PackageError(
            f"variant {variant} declares itself {declared!r}; the declared name "
            "must equal the file name it is selected by"
        )
    if "prompt_append" in overlay and (
        not isinstance(overlay["prompt_append"], str)
        or not overlay["prompt_append"].strip()
    ):
        raise PackageError(f"variant {variant} prompt_append must be non-empty text")
    if not set(overlay) - {"variant", "notes"}:
        raise PackageError(f"variant {variant} changes nothing about the package")
    changes_proof = bool(
        set(overlay) - DESCRIPTIVE_KEYS - {"variant", "prompt_append", "notes"}
    )
    body = package.body
    if "prompt_append" in overlay:
        appended = overlay["prompt_append"].strip()
        body = f"{body.rstrip()}\n\n## Variant: {variant}\n\n{appended}\n"
    updates: dict[str, Any] = {
        key: overlay[key] for key in DESCRIPTIVE_KEYS if key in overlay
    }
    for key in ("target_model", "inputs", "gtm"):
        if key not in overlay:
            continue
        current = getattr(package, key)
        if isinstance(current, Mapping) and isinstance(overlay[key], Mapping):
            if key == "inputs":
                merged = _merge_variant_inputs(current, overlay[key], variant)
            else:
                merged = dict(current)
                merged.update(overlay[key])
            updates[key] = merged
        else:
            updates[key] = overlay[key]
    merged = {key: _plain(getattr(package, key)) for key in REQUIRED_KEYS}
    merged.update({key: _plain(value) for key, value in updates.items()})
    try:
        _validate(merged, package.root)
    except PackageError as error:
        raise PackageError(
            f"variant {variant} would produce an invalid package: {error}"
        ) from error
    frozen_updates = {key: _freeze(value) for key, value in updates.items()}
    return replace(
        package,
        body=body,
        variant=variant,
        revalidation="required" if changes_proof else "inherited",
        status=(
            "candidate"
            if changes_proof and package.status == "approved"
            else package.status
        ),
        **frozen_updates,
    )


def resolve_prompt_path(enrichment_id: str, repo_root: Path) -> Path:
    """Repo-relative prompt path: the package if it exists, else the flat file.

    Enrichments migrate into packages one at a time. Callers ask for an id and
    get whichever layout is current, so a migration is one directory move plus
    nothing else.
    """
    packaged = Path("enrichments") / enrichment_id / f"{enrichment_id}.md"
    if (Path(repo_root) / packaged).is_file():
        return packaged
    return Path("prompts/company-enrichment") / f"{enrichment_id}.md"


def prompt_text(path: Path) -> str:
    """Prompt body only. A packaged prompt carries a manifest the model never sees."""
    text = Path(path).read_text(encoding="utf-8")
    if text.startswith("---"):
        return _split_frontmatter(text, Path(path))[1].strip()
    return text.strip()


def is_package_prompt(path: Path) -> bool:
    """True for ``enrichments/<id>/<id>.md`` - the only layout that owns a manifest."""
    path = Path(path)
    return path.suffix == ".md" and path.parent.name == path.stem and (
        path.parent.parent.name == "enrichments"
    )


def candidate_prompt_text(path: Path) -> str:
    """The prompt text a loop should score for ``path``.

    A package prompt carries a manifest the model never sees, so it is stripped.
    Every other file - an operator's candidate handed to ``--prompt`` or
    ``--candidate`` - is read verbatim: a markdown thematic break opening a
    hand-written prompt is not a frontmatter fence, and treating it as one would
    silently score a truncated prompt under the candidate's id and hash.
    """
    path = Path(path)
    if is_package_prompt(path):
        return prompt_text(path)
    return path.read_text(encoding="utf-8").strip()


def load_registry(root: Path) -> dict[str, EnrichmentPackage]:
    """Every package directory under ``root``, keyed by id."""
    packages: dict[str, EnrichmentPackage] = {}
    for child in sorted(Path(root).iterdir()):
        if not child.is_dir() or not (child / f"{child.name}.md").is_file():
            continue
        package = load_package(child)
        if package.id in packages:
            raise PackageError(f"duplicate package id: {package.id}")
        packages[package.id] = package
    return packages


MATURITY_BY_STATUS = {
    "proposed": "draft",
    "experiment": "draft",
    "candidate": "validated",
    "approved": "graduated",
    "rejected": "draft",
}


def _literal(value: Any) -> str:
    """A manifest string as a Python source literal.

    Manifest text is author-controlled prose: a quote, a backslash, or a folded
    newline interpolated raw would emit source a reviewer cannot paste.
    """
    return json.dumps(str(value))


def emit_registry_entry(package: EnrichmentPackage) -> str:
    """The gtm_orchestrator ``EnrichmentSpec`` this package installs as.

    That catalog is static by ruling - "add a CATALOG entry; no plugin surface,
    no dynamic loading" - so installing a package is a reviewed diff, not a
    runtime import. This renders the diff instead of hand-copying fields, which
    is where maturity and accuracy drift from the proof that earned them.
    """
    gtm = package.gtm
    missing = [
        key for key in (
            "slug", "provider", "type", "enrichment_level", "runtime_prompt_name",
            "linkedin_safe", "cost_per_100", "cost_estimate",
        )
        if key not in gtm
    ]
    if missing:
        raise PackageError(f"gtm block is missing registry keys: {', '.join(missing)}")
    if package.variant is not None:
        raise PackageError(
            "a variant does not install as its own catalog entry; install the parent"
        )
    if package.status == "rejected":
        raise PackageError(
            "a rejected package does not install; it has no catalog entry"
        )
    holdout = package.evaluation.get("holdout")
    lines = [
        f'    {_literal(gtm["slug"])}: EnrichmentSpec(',
        f'        slug={_literal(gtm["slug"])},',
        f'        name={_literal(package.name)},',
        f'        description={_literal(package.summary.strip().rstrip(".") + ".")},',
        f'        provider={_literal(gtm["provider"])},',
        f'        type={_literal(gtm["type"])},',
        f'        enrichment_level={_literal(gtm["enrichment_level"])},',
        f'        cost_estimate={_literal(gtm["cost_estimate"])},',
        f'        input_columns={package.input_columns!r},',
        f'        output_columns={package.output_columns!r},',
        f'        linkedin_safe={bool(gtm["linkedin_safe"])},',
        f'        cost_per_100={float(gtm["cost_per_100"])},',
    ]
    if gtm.get("requires_tools"):
        lines.append(f'        requires_tools={tuple(gtm["requires_tools"])!r},')
    lines.append(f'        runtime_prompt_name={_literal(gtm["runtime_prompt_name"])},')
    lines.append(f'        maturity={_literal(MATURITY_BY_STATUS[package.status])},')
    if holdout is not None:
        lines.append(f"        accuracy_pct={round(float(holdout) * 100, 1)},")
    lines.append("    ),")
    return "\n".join(lines)


UNKNOWN_UNTIL_RUN = "<assigned by the run>"
EVIDENCE_UNTIL_RUN = "<collected at run time; not available before a run>"
FIELDS_UNTIL_RUN = "<resolved from the enrichment id at run time>"


def _subject_label(inputs: Mapping[str, Any]) -> str:
    """The live ``Subject company:`` label: the name plus the bare domain host.

    Mirrors ``OpenAIModelClient._subject_line``, which derives the host from the
    first Evidence URL and drops credentials, port, and a leading ``www.``.
    """
    name = str(inputs.get("company_name", "")).strip()
    host = str(inputs.get("domain", "")).strip().lower()
    host = host.split("//")[-1].split("/")[0].split("@")[-1].split(":")[0]
    host = host[4:] if host.startswith("www.") else host
    if not name and not host:
        return ""
    return f"{name} ({host})" if name and host else (name or host)


def render(package: EnrichmentPackage, inputs: Mapping[str, Any]) -> str:
    """The prompt as the model receives it, assembled the way the live path does.

    ``OpenAIModelClient._prompt`` sends the prompt body first, then ``Company
    ID``, ``Subject company``, ``Enrichment``, ``Requested fields``, and
    ``Evidence``. This reproduces that order and those labels, so a prompt edit
    is reviewed against the composition that is actually sent.

    Two sections cannot be filled in before a run: the run assigns the company
    id, and the Evidence is collected during the run. Both keep their header and
    carry an explicit placeholder rather than being dropped. The manifest
    validator rejects declared inputs other than ``company_name`` and ``domain``
    because the live assembly has no slot for them.
    """
    missing = [
        name for name in package.required_inputs if not str(inputs.get(name, "")).strip()
    ]
    if missing:
        raise PackageError(f"missing required inputs: {', '.join(missing)}")
    label = _subject_label(inputs)
    subject = f"Subject company: {label}\n" if label else ""
    fields = P0_ENRICHMENTS.get(package.id)
    requested = json.dumps(list(fields)) if fields else FIELDS_UNTIL_RUN
    return (
        f"{package.body.rstrip()}\n\n"
        f"Company ID: {UNKNOWN_UNTIL_RUN}\n"
        f"{subject}"
        f"Enrichment: {package.id}\n"
        f"Requested fields: {requested}\n"
        f"Evidence: {EVIDENCE_UNTIL_RUN}"
    )
