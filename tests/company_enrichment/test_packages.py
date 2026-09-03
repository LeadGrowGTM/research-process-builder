"""Enrichment package manifest, variant, and installation-diff behaviour."""

from __future__ import annotations

import ast
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

import pytest
from pydantic import ValidationError

from scripts.company_enrichment.news_contracts import (
    LAUNCH_EVENT_TYPES,
    NEWS_EVENT_TYPES,
)
from scripts.company_enrichment.contracts import (
    CompanyDossier,
    EvidenceRef,
    FieldAssertion,
    Visibility,
)
from scripts.company_enrichment.experiment_runner import ExperimentInput
from scripts.company_enrichment.openai_model_client import OpenAIModelClient
from scripts.company_enrichment.packages import (
    EVIDENCE_UNTIL_RUN,
    UNKNOWN_UNTIL_RUN,
    PackageError,
    apply_variant,
    candidate_prompt_text,
    emit_registry_entry,
    load_package,
    load_registry,
    prompt_text,
    render,
    resolve_prompt_path,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
NEWS = REPO_ROOT / "enrichments" / "news-product-launches"
# The blob the news prompt body was scored on, pinned by object id: the path it
# lived at moves in this change, and a branch name stops resolving after a merge.
SCORED_PROMPT_BLOB = "1a8115f2b32e49afb5542583cdad7049a308be40"


MANIFEST = """---
id: {id}
name: Demo enrichment
title: What you get out of it
summary: One line.
description: More detail.
version: 1.0.0
status: {status}
kind: lookup
entity: company
target_model: gpt-5.6-luna
proof_temperature: 0.2
proof_max_output_tokens: 500
runner: run.py
schema_module: schema.py
inputs:
  required:
    domain:
      description: the host
      consumer_column: company_domain
  optional: {{}}
outputs:
  events:
    type: array
    description: extracted events
    consumer_column: events_json
gtm:
  slug: demo_slug
  provider: runtime
  type: enrichment
  enrichment_level: company
  runtime_prompt_name: demo-prompt
  linkedin_safe: false
  cost_per_100: 0.25
  cost_estimate: "$0.25 per 100 rows"
evaluation:
  dataset: benchmarks/demo
  scorer: tests.company_enrichment.demo:score
  candidate: demo-v1
  dev: 0.95
  holdout: 0.93
  gate: 0.9
  approved_on: "2026-08-21"
  report: docs/reports/demo.md
adaptation:
  adaptable: true
  safe_edits:
    - wording
  locked:
    - the citation rule
  revalidate_when:
    - a locked section changes
  revalidate_with: py demo_loop.py --evaluate --lineage <name> --model gpt-5.6-luna --allow-paid
---

Body text.
"""


def _package(tmp_path: Path, *, name: str = "demo", **fields: str) -> Path:
    root = tmp_path / name
    (root / "variants").mkdir(parents=True)
    (root / "run.py").write_text(
        "def main():\n    return 0\n", encoding="utf-8",
    )
    (root / "schema.py").write_text(
        "from pydantic import BaseModel\n\n"
        "class InputModel(BaseModel):\n    domain: str\n\n"
        "class OutputModel(BaseModel):\n    events: list[dict[str, object]]\n",
        encoding="utf-8",
    )
    manifest = MANIFEST.format(id=fields.get("id", name), status=fields.get("status", "approved"))
    for old, new in fields.get("edits", ()):
        manifest = manifest.replace(old, new)
    (root / f"{name}.md").write_text(manifest, encoding="utf-8")
    return root


def test_loads_manifest_and_body(tmp_path: Path) -> None:
    package = load_package(_package(tmp_path))
    assert package.id == "demo"
    assert package.required_inputs == ("domain",)
    assert package.optional_inputs == ()
    assert package.body.strip() == "Body text."
    assert package.revalidation == "not_required"


def test_id_must_match_directory(tmp_path: Path) -> None:
    root = _package(tmp_path, id="other")
    with pytest.raises(PackageError, match="must equal package directory"):
        load_package(root)


def test_approved_package_must_clear_its_own_gate(tmp_path: Path) -> None:
    root = _package(tmp_path, edits=(("  dev: 0.95", "  dev: 0.80"),))
    with pytest.raises(PackageError, match="must meet its own gate"):
        load_package(root)


def test_approved_package_must_clear_the_gate_on_holdout(tmp_path: Path) -> None:
    root = _package(tmp_path, edits=(("  holdout: 0.93", "  holdout: 0.80"),))
    with pytest.raises(PackageError, match="must meet its own gate on holdout"):
        load_package(root)


def test_approved_package_cannot_lower_the_repository_gate(tmp_path: Path) -> None:
    root = _package(tmp_path, edits=(("  gate: 0.9", "  gate: 0.5"),))
    with pytest.raises(PackageError, match="gate must be at least 0.90"):
        load_package(root)


def test_approval_scores_must_be_finite_numbers(tmp_path: Path) -> None:
    root = _package(tmp_path, edits=(("  holdout: 0.93", "  holdout: .nan"),))
    with pytest.raises(PackageError, match="evaluation.holdout must be a finite number"):
        load_package(root)


def test_unquoted_approval_date_is_rejected(tmp_path: Path) -> None:
    root = _package(tmp_path, edits=(('approved_on: "2026-08-21"', "approved_on: 2026-08-21"),))
    with pytest.raises(PackageError, match="quoted ISO text"):
        load_package(root)


def test_adaptable_package_must_declare_locked_sections(tmp_path: Path) -> None:
    root = _package(tmp_path, edits=(("  locked:\n    - the citation rule\n", "  locked: []\n"),))
    with pytest.raises(PackageError, match="stay locked"):
        load_package(root)


def test_missing_runner_file_is_rejected(tmp_path: Path) -> None:
    root = _package(tmp_path)
    (root / "run.py").unlink()
    with pytest.raises(PackageError, match="runner points at a missing file"):
        load_package(root)


def test_blank_prompt_body_is_rejected(tmp_path: Path) -> None:
    root = _package(tmp_path, edits=(("Body text.", "   "),))
    with pytest.raises(PackageError, match="prompt body must be non-empty"):
        load_package(root)


@pytest.mark.parametrize("artifact", ("runner", "schema_module"))
def test_runtime_artifacts_must_be_non_empty(
    tmp_path: Path, artifact: str,
) -> None:
    root = _package(tmp_path)
    path = root / ("run.py" if artifact == "runner" else "schema.py")
    path.write_text("", encoding="utf-8")
    with pytest.raises(PackageError, match=rf"{artifact} must be a non-empty Python file"):
        load_package(root)


def test_schema_module_must_export_both_pydantic_models(tmp_path: Path) -> None:
    root = _package(tmp_path)
    (root / "schema.py").write_text(
        "from pydantic import BaseModel\n\n"
        "class InputModel(BaseModel):\n    pass\n",
        encoding="utf-8",
    )
    with pytest.raises(PackageError, match="pydantic OutputModel"):
        load_package(root)


@pytest.mark.parametrize(
    ("input_fields", "message"),
    (
        ("    pass", "missing domain"),
        ("    domain: str\n    company_name: str", "unexpected company_name"),
    ),
)
def test_schema_input_fields_must_match_manifest(
    tmp_path: Path, input_fields: str, message: str,
) -> None:
    root = _package(tmp_path)
    (root / "schema.py").write_text(
        "from pydantic import BaseModel\n\n"
        f"class InputModel(BaseModel):\n{input_fields}\n\n"
        "class OutputModel(BaseModel):\n    events: list[dict[str, object]]\n",
        encoding="utf-8",
    )

    with pytest.raises(PackageError, match=message):
        load_package(root)


def test_schema_input_requiredness_must_match_manifest(tmp_path: Path) -> None:
    root = _package(tmp_path)
    (root / "schema.py").write_text(
        "from pydantic import BaseModel\n\n"
        "class InputModel(BaseModel):\n    domain: str | None = None\n\n"
        "class OutputModel(BaseModel):\n    events: list[dict[str, object]]\n",
        encoding="utf-8",
    )

    with pytest.raises(PackageError, match="schema marks optional: domain"):
        load_package(root)


@pytest.mark.parametrize(
    ("output_fields", "message"),
    (
        ("    pass", "missing events"),
        (
            "    events: list[dict[str, object]]\n    summary: str",
            "unexpected summary",
        ),
    ),
)
def test_schema_output_fields_must_match_manifest(
    tmp_path: Path, output_fields: str, message: str,
) -> None:
    root = _package(tmp_path)
    (root / "schema.py").write_text(
        "from pydantic import BaseModel\n\n"
        "class InputModel(BaseModel):\n    domain: str\n\n"
        f"class OutputModel(BaseModel):\n{output_fields}\n",
        encoding="utf-8",
    )

    with pytest.raises(PackageError, match=message):
        load_package(root)


def test_adaptable_must_be_a_yaml_boolean(tmp_path: Path) -> None:
    root = _package(tmp_path, edits=(("  adaptable: true", '  adaptable: "false"'),))
    with pytest.raises(PackageError, match="adaptation.adaptable must be a YAML boolean"):
        load_package(root)


@pytest.mark.parametrize(
    ("field", "block"),
    (
        ("safe_edits", "  safe_edits:\n    - wording"),
        ("locked", "  locked:\n    - the citation rule"),
        ("revalidate_when", "  revalidate_when:\n    - a locked section changes"),
    ),
)
def test_adaptation_guidance_must_be_a_list(
    tmp_path: Path, field: str, block: str,
) -> None:
    root = _package(tmp_path, edits=((block, f"  {field}: guidance"),))
    with pytest.raises(PackageError, match=rf"adaptation\.{field} must be a list"):
        load_package(root)


@pytest.mark.parametrize(
    "field", ("safe_edits", "locked", "revalidate_when", "revalidate_with"),
)
def test_adaptation_contract_requires_every_field(tmp_path: Path, field: str) -> None:
    root = _package(tmp_path, edits=((f"  {field}:", f"  omitted_{field}:"),))
    with pytest.raises(PackageError, match=rf"adaptation is missing fields:.*{field}"):
        load_package(root)


def test_adaptable_package_requires_a_revalidation_command(tmp_path: Path) -> None:
    root = _package(
        tmp_path,
        edits=((
            "  revalidate_with: py demo_loop.py --evaluate --lineage <name> "
            "--model gpt-5.6-luna --allow-paid",
            '  revalidate_with: ""',
        ),),
    )
    with pytest.raises(PackageError, match="must provide adaptation.revalidate_with"):
        load_package(root)


@pytest.mark.parametrize("approved_on", ("yesterday", " 2026-08-21 ", "2026-02-30"))
def test_approval_date_must_be_a_canonical_calendar_date(
    tmp_path: Path, approved_on: str,
) -> None:
    root = _package(
        tmp_path,
        edits=((
            'approved_on: "2026-08-21"',
            f'approved_on: "{approved_on}"',
        ),),
    )
    with pytest.raises(PackageError, match="real YYYY-MM-DD calendar date"):
        load_package(root)


@pytest.mark.parametrize("field", ("scorer", "candidate", "report"))
def test_approved_package_requires_complete_proof_provenance(
    tmp_path: Path, field: str,
) -> None:
    root = _package(tmp_path, edits=((f"  {field}: ", f"  omitted_{field}: "),))
    with pytest.raises(PackageError, match=rf"needs evaluation\.{field}"):
        load_package(root)


def test_approved_package_provenance_must_be_text(tmp_path: Path) -> None:
    root = _package(
        tmp_path,
        edits=(("  scorer: tests.company_enrichment.demo:score", "  scorer: [demo]"),),
    )
    with pytest.raises(PackageError, match="evaluation.scorer must be non-empty text"):
        load_package(root)


def test_package_file_references_cannot_escape_the_package(tmp_path: Path) -> None:
    (tmp_path / "outside.py").write_text("", encoding="utf-8")
    root = _package(tmp_path, edits=(("runner: run.py", "runner: ../outside.py"),))
    with pytest.raises(PackageError, match="relative file inside the package"):
        load_package(root)


def test_optional_inputs_must_be_a_mapping(tmp_path: Path) -> None:
    root = _package(
        tmp_path,
        edits=(("  optional: {}", "  optional: as_of"),),
    )
    with pytest.raises(PackageError, match="inputs.optional must be a mapping"):
        load_package(root)


def test_input_name_cannot_be_both_required_and_optional(tmp_path: Path) -> None:
    root = _package(
        tmp_path,
        edits=((
            "  optional: {}",
            "  optional:\n"
            "    domain:\n"
            "      description: an optional host\n"
            "      consumer_column: optional_company_domain",
        ),),
    )
    with pytest.raises(PackageError, match="both required and optional: domain"):
        load_package(root)


def test_declared_inputs_must_be_supported_by_render(tmp_path: Path) -> None:
    root = _package(
        tmp_path,
        edits=((
            "  optional: {}",
            "  optional:\n    as_of:\n      description: anchor date\n"
            "      consumer_column: as_of",
        ),),
    )
    with pytest.raises(PackageError, match="declared inputs cannot be rendered.*as_of"):
        load_package(root)


def test_input_descriptions_must_be_non_empty_text(tmp_path: Path) -> None:
    root = _package(tmp_path, edits=(("description: the host", 'description: ""'),))
    with pytest.raises(PackageError, match="description and consumer_column"):
        load_package(root)


@pytest.mark.parametrize(
    "outputs",
    (
        "outputs:\n  events: nope",
        "outputs:\n  events:\n    type: array",
    ),
)
def test_public_output_descriptors_require_type_and_description(
    tmp_path: Path, outputs: str,
) -> None:
    root = _package(
        tmp_path,
        edits=((
            "outputs:\n  events:\n    type: array\n    description: extracted events\n"
            "    consumer_column: events_json",
            outputs,
        ),),
    )
    with pytest.raises(PackageError, match=r"outputs\.events"):
        load_package(root)


def test_descriptive_variant_inherits_the_parent_proof(tmp_path: Path) -> None:
    root = _package(tmp_path)
    (root / "variants" / "narrow.yaml").write_text(
        "title: Narrower thing\nprompt_append: Only report funding.\n", encoding="utf-8"
    )
    package = apply_variant(load_package(root), "narrow")
    assert package.revalidation == "inherited"
    assert package.status == "approved"
    assert package.title == "Narrower thing"
    assert "## Variant: narrow" in package.body


def test_variant_that_changes_the_model_forces_revalidation(tmp_path: Path) -> None:
    root = _package(tmp_path)
    (root / "variants" / "cheap.yaml").write_text(
        "title: Cheaper\ntarget_model: gpt-5-nano\n", encoding="utf-8"
    )
    package = apply_variant(load_package(root), "cheap")
    assert package.revalidation == "required"
    assert package.status == "candidate"
    assert package.target_model == "gpt-5-nano"


def test_a_model_only_variant_is_accepted_and_forces_revalidation(tmp_path: Path) -> None:
    """Changing what the enrichment runs on is a real change, not an empty overlay."""
    root = _package(tmp_path)
    (root / "variants" / "cheap.yaml").write_text(
        "target_model: gpt-5-nano\n", encoding="utf-8"
    )
    package = apply_variant(load_package(root), "cheap")
    assert package.revalidation == "required"
    assert package.status == "candidate"
    assert package.target_model == "gpt-5-nano"


def test_an_overlay_that_changes_nothing_is_refused(tmp_path: Path) -> None:
    root = _package(tmp_path)
    (root / "variants" / "empty.yaml").write_text(
        "variant: empty\nnotes: just a note\n", encoding="utf-8"
    )
    with pytest.raises(PackageError, match="changes nothing about the package"):
        apply_variant(load_package(root), "empty")


@pytest.mark.parametrize("value", ("false", "[one, two]", "'   '"))
def test_variant_prompt_append_must_be_non_empty_text(
    tmp_path: Path, value: str,
) -> None:
    root = _package(tmp_path)
    (root / "variants" / "broken.yaml").write_text(
        f"prompt_append: {value}\n", encoding="utf-8"
    )
    with pytest.raises(PackageError, match="prompt_append must be non-empty text"):
        apply_variant(load_package(root), "broken")


def test_a_variant_may_not_declare_a_name_other_than_its_own(tmp_path: Path) -> None:
    root = _package(tmp_path)
    (root / "variants" / "cheap.yaml").write_text(
        "variant: expensive\ntitle: Cheap\n", encoding="utf-8"
    )
    with pytest.raises(PackageError, match="declares itself 'expensive'"):
        apply_variant(load_package(root), "cheap")


def test_a_variant_name_cannot_escape_the_variants_directory(tmp_path: Path) -> None:
    with pytest.raises(PackageError, match="safe lower-kebab-case file stem"):
        apply_variant(load_package(_package(tmp_path)), "../../other")


def test_a_variant_target_model_must_remain_text(tmp_path: Path) -> None:
    root = _package(tmp_path)
    (root / "variants" / "broken.yaml").write_text(
        "target_model:\n  name: gpt-5-nano\n", encoding="utf-8"
    )
    with pytest.raises(PackageError, match="target_model must be non-empty text"):
        apply_variant(load_package(root), "broken")


def test_variant_cannot_override_the_evaluation(tmp_path: Path) -> None:
    root = _package(tmp_path)
    (root / "variants" / "cheat.yaml").write_text(
        "title: Cheat\nevaluation:\n  dev: 1.0\n", encoding="utf-8"
    )
    with pytest.raises(PackageError, match="may not override: evaluation"):
        apply_variant(load_package(root), "cheat")


def test_variant_cannot_drop_an_input_name(tmp_path: Path) -> None:
    root = _package(tmp_path)
    (root / "variants" / "loose.yaml").write_text(
        "inputs:\n"
        "  required:\n"
        "    company_name:\n"
        "      description: the company\n"
        "      consumer_column: company_name\n",
        encoding="utf-8",
    )
    with pytest.raises(PackageError, match="different enrichment or a v2"):
        apply_variant(load_package(root), "loose")


def test_variant_cannot_add_an_input_name(tmp_path: Path) -> None:
    root = _package(tmp_path)
    (root / "variants" / "contract-change.yaml").write_text(
        "inputs:\n"
        "  required:\n"
        "    domain:\n"
        "      description: the host\n"
        "      consumer_column: company_domain\n"
        "    company_name:\n"
        "      description: the company\n"
        "      consumer_column: company_name\n",
        encoding="utf-8",
    )
    with pytest.raises(PackageError, match="different enrichment or a v2"):
        apply_variant(load_package(root), "contract-change")


def test_variant_cannot_move_an_input_between_groups(tmp_path: Path) -> None:
    root = _package(
        tmp_path,
        edits=((
            "      consumer_column: company_domain\n  optional: {}",
            "      consumer_column: company_domain\n"
            "    company_name:\n"
            "      description: the company\n"
            "      consumer_column: company_name\n"
            "  optional: {}",
            ),),
        )
    (root / "schema.py").write_text(
        "from pydantic import BaseModel\n\n"
        "class InputModel(BaseModel):\n"
        "    domain: str\n"
        "    company_name: str\n\n"
        "class OutputModel(BaseModel):\n"
        "    events: list[dict[str, object]]\n",
        encoding="utf-8",
    )
    (root / "variants" / "contract-change.yaml").write_text(
        "inputs:\n"
        "  required:\n"
        "    company_name:\n"
        "      description: the company\n"
        "      consumer_column: company_name\n"
        "  optional:\n"
        "    domain:\n"
        "      description: the host\n"
        "      consumer_column: company_domain\n",
        encoding="utf-8",
    )
    with pytest.raises(PackageError, match="different enrichment or a v2"):
        apply_variant(load_package(root), "contract-change")


@pytest.mark.parametrize(
    "description, consumer_column",
    (
        ("a normalized host", "company_domain"),
        ("the host", "normalized_company_domain"),
    ),
)
def test_variant_may_refine_an_input_descriptor(
    tmp_path: Path, description: str, consumer_column: str,
) -> None:
    root = _package(tmp_path)
    (root / "variants" / "refined.yaml").write_text(
        "inputs:\n"
        "  required:\n"
        "    domain:\n"
        f"      description: {description}\n"
        f"      consumer_column: {consumer_column}\n",
        encoding="utf-8",
    )
    package = apply_variant(load_package(root), "refined")
    assert package.inputs["required"]["domain"] == {
        "description": description,
        "consumer_column": consumer_column,
    }
    assert package.input_columns == (consumer_column,)
    assert package.revalidation == "required"
    assert package.status == "candidate"


def test_variant_may_partially_refine_one_shipped_input(tmp_path: Path) -> None:
    root = tmp_path / NEWS.name
    shutil.copytree(NEWS, root)
    (root / "variants" / "domain-wording.yaml").write_text(
        "inputs:\n"
        "  required:\n"
        "    domain:\n"
        "      description: the normalized company host\n",
        encoding="utf-8",
    )

    package = apply_variant(load_package(root), "domain-wording")

    assert package.required_inputs == ("company_name", "domain")
    assert package.inputs["required"]["domain"] == {
        "description": "the normalized company host",
        "consumer_column": "company_domain",
    }


def test_variant_may_not_replace_the_gtm_block_with_a_non_mapping(tmp_path: Path) -> None:
    root = _package(tmp_path)
    (root / "variants" / "broken.yaml").write_text(
        "title: Broken\ngtm: not-a-mapping\n", encoding="utf-8"
    )
    with pytest.raises(PackageError, match="gtm must be a non-empty mapping"):
        apply_variant(load_package(root), "broken")


def test_a_quoted_linkedin_safe_cannot_flip_the_safety_flag(tmp_path: Path) -> None:
    """bool("false") is True; the emitted entry must never learn that the hard way."""
    root = _package(tmp_path, edits=(("linkedin_safe: false", 'linkedin_safe: "false"'),))
    with pytest.raises(PackageError, match="gtm.linkedin_safe must be a YAML boolean"):
        load_package(root)


@pytest.mark.parametrize("field", ("input_columns", "output_columns"))
def test_gtm_column_lists_cannot_duplicate_field_mappings(
    tmp_path: Path, field: str,
) -> None:
    root = _package(tmp_path, edits=((
        "  runtime_prompt_name: demo-prompt",
        f"  runtime_prompt_name: demo-prompt\n  {field}: [duplicate]",
    ),))
    with pytest.raises(PackageError, match="derived from field consumer_column mappings"):
        load_package(root)


@pytest.mark.parametrize("field", ("tool_use", "conversation"))
def test_consumer_reserved_frontmatter_is_rejected(tmp_path: Path, field: str) -> None:
    root = _package(tmp_path, edits=(("inputs:\n", f"{field}: true\ninputs:\n"),))
    with pytest.raises(PackageError, match="consumer-reserved frontmatter"):
        load_package(root)


def test_a_quoted_cost_per_100_is_refused(tmp_path: Path) -> None:
    root = _package(tmp_path, edits=(("cost_per_100: 0.25", 'cost_per_100: "0.25"'),))
    with pytest.raises(PackageError, match="gtm.cost_per_100 must be a number"):
        load_package(root)


def test_a_negative_cost_per_100_is_refused(tmp_path: Path) -> None:
    root = _package(tmp_path, edits=(("cost_per_100: 0.25", "cost_per_100: -0.25"),))
    with pytest.raises(PackageError, match="gtm.cost_per_100 must be non-negative"):
        load_package(root)


def test_an_empty_gtm_block_fails_to_load_rather_than_crashing_later(tmp_path: Path) -> None:
    """A malformed block is a PackageError, not a TypeError out of card()."""
    root = _package(tmp_path, edits=(("gtm:\n  slug: demo_slug", "gtm:\nx_unused:\n  slug: x"),))
    with pytest.raises(PackageError, match="gtm must be a non-empty mapping"):
        load_package(root)


def test_loaded_manifest_and_card_are_recursively_isolated(tmp_path: Path) -> None:
    package = load_package(_package(tmp_path))
    emitted = emit_registry_entry(package)

    with pytest.raises(TypeError):
        package.inputs["required"]["domain"]["consumer_column"] = "changed"
    with pytest.raises(AttributeError):
        package.adaptation["safe_edits"].append("changed")

    card = package.card()
    card["inputs"]["required"]["domain"]["consumer_column"] = "changed"
    card["outputs"]["events"]["consumer_column"] = "changed"
    card["evaluation"]["holdout"] = 0.0

    assert emit_registry_entry(package) == emitted
    assert package.input_columns == ("company_domain",)
    assert package.output_columns == ("events_json",)
    assert package.card()["evaluation"]["holdout"] == 0.93


def test_variant_may_not_smuggle_in_a_secret_key(tmp_path: Path) -> None:
    root = _package(tmp_path)
    (root / "variants" / "keyed.yaml").write_text(
        "title: Keyed\ngtm:\n  auth:\n    api_key: sk-live-123\n", encoding="utf-8"
    )
    with pytest.raises(PackageError, match="secret-bearing key"):
        apply_variant(load_package(root), "keyed")


def test_variant_may_not_interpolate_the_environment(tmp_path: Path) -> None:
    root = _package(tmp_path)
    (root / "variants" / "env.yaml").write_text(
        "title: Env\nprompt_append: Use ${OPENAI_API_KEY}.\n", encoding="utf-8"
    )
    with pytest.raises(PackageError, match="environment interpolation is forbidden"):
        apply_variant(load_package(root), "env")


def test_render_requires_declared_inputs(tmp_path: Path) -> None:
    package = load_package(_package(tmp_path))
    with pytest.raises(PackageError, match="missing required inputs: domain"):
        render(package, {})
    text = render(package, {"domain": "attio.com"})
    assert text.startswith("Body text.\n\n")
    assert "Subject company: attio.com" in text


def test_render_places_the_unknowns_behind_their_own_headers(tmp_path: Path) -> None:
    """A preview drops nothing silently: what a run supplies keeps its header."""
    text = render(load_package(_package(tmp_path)), {"domain": "www.attio.com:443"})
    assert f"Company ID: {UNKNOWN_UNTIL_RUN}" in text
    assert f"Evidence: {EVIDENCE_UNTIL_RUN}" in text
    assert "Subject company: attio.com" in text


def test_render_reproduces_the_prompt_the_live_client_sends() -> None:
    """The preview is the live assembly, not a second composition of its own."""
    package = load_package(NEWS)
    evidence = EvidenceRef(
        "ev-1", "https://www.attio.com/blog/launch",
        datetime(2026, 8, 1, tzinfo=timezone.utc), "c" * 64, "Attio ships X.",
    )
    identity = FieldAssertion("identity", "Attio", ("ev-1",), 1.0, Visibility.MESSAGE_SAFE)
    dossier = CompanyDossier("saas-01", "1.0", (identity,), (evidence,))
    request = ExperimentInput(
        "news-product-launches", "saas-01", package.target_model, dossier, "baseline",
        prompt_text(NEWS / "news-product-launches.md"),
        {"type": "object", "properties": {"news": {"type": "array"}}},
    )
    live = OpenAIModelClient._prompt(request)
    expected = live.replace(
        f"Company ID: {request.company_id}", f"Company ID: {UNKNOWN_UNTIL_RUN}"
    )
    expected = expected[:expected.index("Evidence: ")] + f"Evidence: {EVIDENCE_UNTIL_RUN}"
    assert render(package, {"company_name": "Attio", "domain": "attio.com"}) == expected


def test_emit_renders_a_catalog_entry_from_the_proof(tmp_path: Path) -> None:
    entry = emit_registry_entry(load_package(_package(tmp_path)))
    assert '"demo_slug": EnrichmentSpec(' in entry
    assert 'runtime_prompt_name="demo-prompt",' in entry
    assert "input_columns=('company_domain',)," in entry
    assert "output_columns=('events_json',)," in entry
    assert 'maturity="graduated",' in entry
    assert "accuracy_pct=93.0," in entry


def test_experiment_status_emits_draft_maturity(tmp_path: Path) -> None:
    root = _package(tmp_path, status="experiment")
    assert 'maturity="draft",' in emit_registry_entry(load_package(root))


def test_a_rejected_package_does_not_install(tmp_path: Path) -> None:
    root = _package(tmp_path, status="rejected")
    with pytest.raises(PackageError, match="rejected package does not install"):
        emit_registry_entry(load_package(root))


def test_emitted_entry_stays_valid_python_for_awkward_manifest_text(tmp_path: Path) -> None:
    """Manifest text is prose; a quote or backslash must not break the paste."""
    root = _package(tmp_path, edits=(
        ("summary: One line.", 'summary: \'A "quoted" cost of 50\\100 rows\''),
        ('cost_estimate: "$0.25 per 100 rows"', 'cost_estimate: "$0.25 \\"per\\" row"'),
    ))
    entry = emit_registry_entry(load_package(root))
    call = ast.parse(f"CATALOG = {{\n{entry}\n}}").body[0].value.values[0]
    fields = {keyword.arg: keyword.value for keyword in call.keywords}
    assert ast.literal_eval(fields["description"]) == 'A "quoted" cost of 50\\100 rows.'
    assert ast.literal_eval(fields["cost_estimate"]) == '$0.25 "per" row'


def test_a_variant_does_not_install_as_its_own_entry(tmp_path: Path) -> None:
    root = _package(tmp_path)
    (root / "variants" / "narrow.yaml").write_text(
        "title: Narrower\nprompt_append: Only funding.\n", encoding="utf-8"
    )
    with pytest.raises(PackageError, match="install the parent"):
        emit_registry_entry(apply_variant(load_package(root), "narrow"))


def test_registry_skips_directories_without_a_prompt(tmp_path: Path) -> None:
    _package(tmp_path)
    (tmp_path / "p0").mkdir()
    (tmp_path / "p0" / "some-definition.yaml").write_text("id: x\n", encoding="utf-8")
    assert sorted(load_registry(tmp_path)) == ["demo"]


def test_an_operator_candidate_opening_with_a_rule_is_read_verbatim(tmp_path: Path) -> None:
    """A thematic break in a hand-written prompt is not a manifest fence."""
    candidate = tmp_path / "news-v12.md"
    candidate.write_text(
        "---\n\n# News v12\n\nReturn dated events.\n\n---\n\nCite every claim.\n",
        encoding="utf-8",
    )
    assert candidate_prompt_text(candidate) == candidate.read_text(encoding="utf-8").strip()


def test_a_package_prompt_still_has_its_manifest_stripped() -> None:
    text = candidate_prompt_text(NEWS / "news-product-launches.md")
    assert text == prompt_text(NEWS / "news-product-launches.md")
    assert not text.startswith("---")


def test_package_schema_imports_where_this_repository_does_not_exist(tmp_path: Path) -> None:
    """The sidecar is copied into a consumer that cannot import scripts.*."""
    shutil.copy(NEWS / "schema.py", tmp_path / "schema.py")
    event = {
        "date": "2026-01-02", "headline": "Attio raises a round",
        "why_it_matters": "budget", "source_url": "https://attio.test/a",
        "evidence_ids": ["ev-1"], "event_type": "funding",
    }
    probe = (
        "import json, sys, schema;"
        "sys.stdout.write(schema.OutputModel(news=[json.loads(sys.argv[1])],"
        "launches=[],unknowns=[])"
        ".model_dump_json())"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe, json.dumps(event)],
        capture_output=True, text=True, cwd=tmp_path, env={**os.environ, "PYTHONPATH": ""},
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["news"][0]["event_type"] == "funding"


def test_package_schema_requires_every_top_level_output() -> None:
    spec = importlib.util.spec_from_file_location("_required_pkg_schema", NEWS / "schema.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    with pytest.raises(ValidationError):
        module.OutputModel(news=[], launches=[])


def test_package_schema_rejects_unknown_top_level_output() -> None:
    spec = importlib.util.spec_from_file_location("_strict_pkg_schema", NEWS / "schema.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    with pytest.raises(ValidationError):
        module.OutputModel(news=[], launches=[], unknowns=[], extra=[])


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("company_name", ""),
        ("domain", " "),
    ),
)
def test_package_schema_rejects_invalid_subject_inputs(
    field: str, value: str,
) -> None:
    spec = importlib.util.spec_from_file_location("_input_pkg_schema", NEWS / "schema.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    inputs = {"company_name": "Attio", "domain": "attio.com"}
    inputs[field] = value
    with pytest.raises(ValidationError):
        module.InputModel(**inputs)


def test_package_schema_rejects_removed_as_of_input() -> None:
    spec = importlib.util.spec_from_file_location("_input_extra_pkg_schema", NEWS / "schema.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    with pytest.raises(ValidationError):
        module.InputModel(
            company_name="Attio", domain="attio.com", as_of="2026-08-21",
        )


def test_package_schema_rejects_events_in_an_unknown_collection() -> None:
    spec = importlib.util.spec_from_file_location("_unknown_pkg_schema", NEWS / "schema.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    event = {
        "date": "2026-01-02", "headline": "Attio raises a round",
        "why_it_matters": "budget", "source_url": "https://attio.test/a",
        "evidence_ids": ["ev-1"], "event_type": "funding",
    }
    with pytest.raises(ValidationError):
        module.OutputModel(news=[event], launches=[], unknowns=["news"])


def test_package_schema_enforces_evidence_closure_when_context_is_supplied() -> None:
    spec = importlib.util.spec_from_file_location("_evidence_pkg_schema", NEWS / "schema.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    event = {
        "date": "2026-01-02", "headline": "Attio raises a round",
        "why_it_matters": "budget", "source_url": "https://attio.test/a",
        "evidence_ids": ["ev-1"], "event_type": "funding",
    }
    payload = {"news": [event], "launches": [], "unknowns": []}
    validated = module.OutputModel.model_validate(
        payload, context={"retained_evidence_ids": {"ev-1"}},
    )
    assert validated.news[0].evidence_ids == ["ev-1"]

    event["evidence_ids"] = ["fabricated-id"]
    module.OutputModel.model_validate(payload)
    with pytest.raises(ValidationError, match="retained Evidence IDs"):
        module.OutputModel.model_validate(
            payload, context={"retained_evidence_ids": {"ev-1"}},
        )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("date", "yesterday"),
        ("date", "2026-02-30"),
        ("headline", ""),
        ("source_url", "not-a-url"),
        ("evidence_ids", [""]),
    ),
)
def test_package_schema_rejects_invalid_event_values(field: str, value: object) -> None:
    spec = importlib.util.spec_from_file_location("_validated_pkg_schema", NEWS / "schema.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    event = {
        "date": "2026-01-02", "headline": "Attio raises a round",
        "why_it_matters": "budget", "source_url": "https://attio.test/a",
        "evidence_ids": ["ev-1"], "event_type": "funding",
    }
    event[field] = value
    with pytest.raises(ValidationError):
        module.OutputModel(news=[event], launches=[], unknowns=[])


def test_package_schema_rejects_unknown_event_fields() -> None:
    spec = importlib.util.spec_from_file_location("_nested_strict_pkg_schema", NEWS / "schema.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    event = {
        "date": "2026-01-02", "headline": "Attio raises a round",
        "why_it_matters": "budget", "source_url": "https://attio.test/a",
        "evidence_ids": ["ev-1"], "event_type": "funding", "extra": True,
    }
    with pytest.raises(ValidationError):
        module.OutputModel(news=[event], launches=[], unknowns=[])


def test_package_schema_event_types_match_the_repo_contract() -> None:
    """The inlined copy is the transport declaration; news_contracts is authority."""
    spec = importlib.util.spec_from_file_location("_pkg_schema", NEWS / "schema.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.NEWS_EVENT_TYPES == NEWS_EVENT_TYPES
    assert module.LAUNCH_EVENT_TYPES == LAUNCH_EVENT_TYPES


def test_prompt_path_resolves_to_the_package_when_it_exists() -> None:
    assert resolve_prompt_path("news-product-launches", REPO_ROOT) == Path(
        "enrichments/news-product-launches/news-product-launches.md"
    )
    assert resolve_prompt_path("competitor-intelligence", REPO_ROOT) == Path(
        "prompts/company-enrichment/competitor-intelligence.md"
    )


def test_packaging_did_not_change_the_scored_prompt_text() -> None:
    """The manifest is metadata: the model must see the same words it was scored on."""
    original = subprocess.run(
        ["git", "show", SCORED_PROMPT_BLOB],
        capture_output=True, text=True, cwd=REPO_ROOT, check=True,
    ).stdout.strip()
    assert prompt_text(NEWS / "news-product-launches.md") == original


def test_shipped_news_package_describes_and_emits() -> None:
    package = load_package(NEWS)
    assert package.status == "approved"
    assert package.target_model == "gpt-5.6-luna"
    assert package.proof_temperature is None
    assert package.proof_max_output_tokens == 4096
    card = package.card()
    assert card["evaluation"]["holdout"] == 0.997
    assert card["inputs"]["required"].keys() == {"company_name", "domain"}
    assert package.input_columns == ("company_name", "company_domain")
    assert package.output_columns == ("news_events_json", "launch_events_json")
    assert '"recent_news": EnrichmentSpec(' in emit_registry_entry(package)


def _news_run_module():
    """The shipped package CLI, imported as the module a consumer would run."""
    spec = importlib.util.spec_from_file_location("_news_run", NEWS / "run.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_cli(*argv: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(NEWS / "run.py"), *argv],
        capture_output=True, text=True, cwd=REPO_ROOT,
    )


def test_body_mode_emits_a_prompt_a_run_can_be_handed() -> None:
    """`body` is what --prompt wants: no live-assembly sections to double up."""
    result = _run_cli("body", "--variant", "funding-only")
    assert result.returncode == 0
    body = result.stdout.strip()
    for label in (
        "Company ID:", "Subject company:", "Enrichment:", "Requested fields:", "Evidence:",
    ):
        assert label not in body
    assert "## Variant: funding-only" in body
    assert body == apply_variant(load_package(NEWS), "funding-only").body.strip()


def test_body_and_render_share_the_same_prompt_text() -> None:
    body = _run_cli("body").stdout.strip()
    rendered = _run_cli(
        "render", "--company-name", "Attio", "--domain", "attio.com"
    ).stdout.strip()
    assert rendered.startswith(body)
    assert rendered != body


def test_shipped_run_cli_refuses_to_spend_without_the_flag() -> None:
    result = _run_cli("execute", "--lineage", "smoke")
    assert result.returncode == 3
    assert "refusing to spend without --allow-paid" in result.stderr


def test_execute_refuses_subject_flags_it_cannot_honour() -> None:
    """The delegated loop scores the sealed corpus; a per-company ask is not it."""
    result = _run_cli(
        "execute", "--lineage", "smoke", "--company-name", "Attio",
        "--domain", "attio.com", "--allow-paid",
    )
    assert result.returncode == 2
    assert "--company-name, --domain" in result.stderr
    assert "sealed benchmark corpus" in result.stderr


def test_execute_needs_a_lineage_to_label_its_artifacts() -> None:
    result = _run_cli("execute")
    assert result.returncode == 2
    assert "--lineage" in result.stderr


def test_execute_rejects_an_unsafe_lineage_before_printing() -> None:
    result = _run_cli("execute", "--lineage", "smoke&calc")
    assert result.returncode == 2
    assert result.stdout == ""
    assert "safe task-scoped name" in result.stderr


def test_execute_rejects_an_unsafe_lineage_before_delegating(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    module = _news_run_module()
    monkeypatch.setattr(
        module.subprocess,
        "call",
        lambda *_args, **_kwargs: pytest.fail("unsafe lineage was delegated"),
    )
    assert module.main([
        "execute", "--lineage", "smoke&calc", "--allow-paid",
    ]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "safe task-scoped name" in captured.err


def test_execute_delegates_to_an_entry_point_that_runs() -> None:
    """The printed command is a real CLI, not a module without a __main__ guard."""
    module = _news_run_module()
    command = module.live_command("smoke", "gpt-5.6-luna")
    package_command = shlex.split(load_package(NEWS).adaptation["revalidate_with"])
    package_command[package_command.index("<name>")] = "smoke"
    assert package_command[1:] == list(command[1:])
    assert _run_cli("execute", "--lineage", "smoke").stdout.strip() == (
        module.quote_command(command)
    )
    parsed = subprocess.run(
        [*command[:-1], "--dry-run"], capture_output=True, text=True, cwd=REPO_ROOT,
    )
    assert parsed.returncode == 0
    assert '"enrichment_id":"news-product-launches"' in parsed.stdout
    assert '"model":"gpt-5.6-luna"' in parsed.stdout


@pytest.mark.skipif(os.name != "nt", reason="PowerShell handoff is Windows-specific")
def test_printed_command_survives_a_spaced_path(tmp_path: Path) -> None:
    """An operator pastes the printed line back into a shell; it has to run there."""
    spaced = tmp_path / "a b"
    spaced.mkdir()
    script = spaced / "probe.py"
    script.write_text("print('delegated-ok')\n", encoding="utf-8")
    launcher = spaced / "python launcher.cmd"
    launcher.write_text(f'@echo off\n"{sys.executable}" %*\n', encoding="utf-8")
    module = _news_run_module()
    command = (str(launcher), str(script))
    line = module.quote_command(command)
    assert " ".join(command) != line
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", line],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "delegated-ok" in result.stdout
