"""Enrichment packages: one self-describing directory per validated process.

A package is the unit the wider GTM landscape installs. It is a directory whose
name is the enrichment id::

    enrichments/<id>/
      <id>.md          prompt body plus the package manifest as YAML frontmatter
      schema.py        sidecar pydantic InputModel / OutputModel
      run.py           the package CLI (describe / render / execute)
      variants/*.yaml  narrow overlays for adjacent use cases

The prompt file is named after the id so a consumer can point the GTM
orchestrator's graduated-prompt loader straight at the package directory as its
``library_path`` with no path translation.

The manifest answers, without reading the prompt body: what goes in, what comes
out, what you get out of it in one line and in detail, which model it was proved
on, what score it earned on which dataset, and which parts of the prompt may be
edited before a run without invalidating that proof.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any, Mapping

import yaml

from .definitions import SEMVER, _secret_key

LIFECYCLE = ("proposed", "experiment", "candidate", "approved", "rejected")
KINDS = ("lookup", "monitoring")
REQUIRED_KEYS = {
    "id", "name", "title", "summary", "description", "version", "status", "kind",
    "entity", "target_model", "temperature", "max_tokens", "runner",
    "schema_module", "inputs", "outputs", "gtm", "evaluation", "adaptation",
}
ID_PATTERN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
# A variant may restate how the enrichment is described and append guidance to
# the prompt. Anything else changes what was proved, so it forces revalidation.
DESCRIPTIVE_KEYS = {"title", "summary", "description", "name"}
VARIANT_KEYS = DESCRIPTIVE_KEYS | {
    "variant", "prompt_append", "inputs", "gtm", "target_model", "notes",
}


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
    temperature: float
    max_tokens: int
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
                "required": dict(self.inputs.get("required", {})),
                "optional": dict(self.inputs.get("optional", {})),
            },
            "outputs": dict(self.outputs),
            "target_model": self.target_model,
            "gtm": dict(self.gtm),
            "evaluation": dict(self.evaluation),
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


def _validate(manifest: Mapping[str, Any], root: Path) -> None:
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
    secret = _secret_key(dict(manifest))
    if secret:
        raise PackageError(f"secret-bearing key is forbidden in a manifest: {secret}")
    for key in ("runner", "schema_module"):
        if not (root / str(manifest[key])).is_file():
            raise PackageError(f"{key} points at a missing file: {manifest[key]}")
    inputs = manifest["inputs"]
    if (
        not isinstance(inputs, dict)
        or not isinstance(inputs.get("required"), dict)
        or not inputs["required"]
    ):
        raise PackageError(
            "inputs.required must be a non-empty mapping of name to description"
        )
    if not isinstance(manifest["outputs"], dict) or not manifest["outputs"]:
        raise PackageError("outputs must be a non-empty mapping")
    adaptation = manifest["adaptation"]
    if not isinstance(adaptation, dict) or "adaptable" not in adaptation:
        raise PackageError("adaptation must declare 'adaptable'")
    if adaptation["adaptable"] and not adaptation.get("locked"):
        raise PackageError(
            "an adaptable package must list the prompt sections that stay locked"
        )
    evaluation = manifest["evaluation"]
    if not isinstance(evaluation, dict):
        raise PackageError("evaluation must be a mapping")
    if manifest["status"] == "approved":
        for key in ("dataset", "dev", "holdout", "gate", "approved_on"):
            if evaluation.get(key) in (None, ""):
                raise PackageError(f"an approved package needs evaluation.{key}")
        if not isinstance(evaluation["approved_on"], str):
            raise PackageError(
                "evaluation.approved_on must be quoted ISO text, not a YAML date"
            )
        if float(evaluation["dev"]) < float(evaluation["gate"]):
            raise PackageError("an approved package must meet its own gate on dev")


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
    _validate(manifest, root)
    package = EnrichmentPackage(
        root=root,
        body=body,
        **{
            key: (
                MappingProxyType(dict(manifest[key]))
                if isinstance(manifest[key], dict)
                else manifest[key]
            )
            for key in REQUIRED_KEYS
        },
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
    path = package.root / "variants" / f"{variant}.yaml"
    if not path.is_file():
        raise PackageError(f"unknown variant {variant!r}: {path} does not exist")
    overlay = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(overlay, dict):
        raise PackageError(f"variant {variant} must be a YAML mapping")
    unknown = sorted(set(overlay) - VARIANT_KEYS)
    if unknown:
        raise PackageError(f"variant {variant} may not override: {', '.join(unknown)}")
    if not overlay.get("prompt_append") and not set(overlay) & DESCRIPTIVE_KEYS:
        raise PackageError(f"variant {variant} changes nothing a reader would see")
    changes_proof = bool(
        set(overlay) - DESCRIPTIVE_KEYS - {"variant", "prompt_append", "notes"}
    )
    body = package.body
    if overlay.get("prompt_append"):
        appended = str(overlay["prompt_append"]).strip()
        body = f"{body.rstrip()}\n\n## Variant: {variant}\n\n{appended}\n"
    updates: dict[str, Any] = {
        key: overlay[key] for key in DESCRIPTIVE_KEYS if key in overlay
    }
    for key in ("target_model", "inputs", "gtm"):
        if key not in overlay:
            continue
        current = getattr(package, key)
        if isinstance(current, Mapping) and isinstance(overlay[key], Mapping):
            merged = dict(current)
            merged.update(overlay[key])
            updates[key] = MappingProxyType(merged)
        else:
            updates[key] = overlay[key]
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
        **updates,
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
            "input_columns", "output_columns", "linkedin_safe", "cost_per_100",
            "cost_estimate",
        )
        if key not in gtm
    ]
    if missing:
        raise PackageError(f"gtm block is missing registry keys: {', '.join(missing)}")
    if package.variant is not None:
        raise PackageError(
            "a variant does not install as its own catalog entry; install the parent"
        )
    holdout = package.evaluation.get("holdout")
    lines = [
        f'    "{gtm["slug"]}": EnrichmentSpec(',
        f'        slug="{gtm["slug"]}",',
        f'        name="{package.name}",',
        f'        description="{package.summary.strip().rstrip(".")}.",',
        f'        provider="{gtm["provider"]}",',
        f'        type="{gtm["type"]}",',
        f'        enrichment_level="{gtm["enrichment_level"]}",',
        f'        cost_estimate="{gtm["cost_estimate"]}",',
        f'        input_columns={tuple(gtm["input_columns"])!r},',
        f'        output_columns={tuple(gtm["output_columns"])!r},',
        f'        linkedin_safe={bool(gtm["linkedin_safe"])},',
        f'        cost_per_100={float(gtm["cost_per_100"])},',
    ]
    if gtm.get("requires_tools"):
        lines.append(f'        requires_tools={tuple(gtm["requires_tools"])!r},')
    lines.append(f'        runtime_prompt_name="{gtm["runtime_prompt_name"]}",')
    lines.append(f'        maturity="{MATURITY_BY_STATUS[package.status]}",')
    if holdout is not None:
        lines.append(f"        accuracy_pct={round(float(holdout) * 100, 1)},")
    lines.append("    ),")
    return "\n".join(lines)


def render(package: EnrichmentPackage, inputs: Mapping[str, Any]) -> str:
    """The prompt text as sent, with the subject stated ahead of the body."""
    missing = [
        name for name in package.required_inputs if not str(inputs.get(name, "")).strip()
    ]
    if missing:
        raise PackageError(f"missing required inputs: {', '.join(missing)}")
    stated = "\n".join(
        f"- {name}: {inputs[name]}"
        for name in (*package.required_inputs, *package.optional_inputs)
        if str(inputs.get(name, "")).strip()
    )
    return f"# Subject\n\n{stated}\n\n{package.body}"
