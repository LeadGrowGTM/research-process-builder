"""Enrichment package manifest, variant, and installation-diff behaviour."""

from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys

import pytest

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
temperature: 0.2
max_tokens: 500
runner: run.py
schema_module: schema.py
inputs:
  required:
    domain: the host
  optional:
    as_of: anchor date
outputs:
  events:
    type: array
gtm:
  slug: demo_slug
  provider: runtime
  type: enrichment
  enrichment_level: company
  runtime_prompt_name: demo-prompt
  input_columns: [company_domain]
  output_columns: [events_json]
  linkedin_safe: false
  cost_per_100: 0.25
  cost_estimate: "$0.25 per 100 rows"
evaluation:
  dataset: benchmarks/demo
  dev: 0.95
  holdout: 0.93
  gate: 0.9
  approved_on: "2026-08-21"
adaptation:
  adaptable: true
  locked:
    - the citation rule
---

Body text.
"""


def _package(tmp_path: Path, *, name: str = "demo", **fields: str) -> Path:
    root = tmp_path / name
    (root / "variants").mkdir(parents=True)
    (root / "run.py").write_text("", encoding="utf-8")
    (root / "schema.py").write_text("", encoding="utf-8")
    manifest = MANIFEST.format(id=fields.get("id", name), status=fields.get("status", "approved"))
    for old, new in fields.get("edits", ()):
        manifest = manifest.replace(old, new)
    (root / f"{name}.md").write_text(manifest, encoding="utf-8")
    return root


def test_loads_manifest_and_body(tmp_path: Path) -> None:
    package = load_package(_package(tmp_path))
    assert package.id == "demo"
    assert package.required_inputs == ("domain",)
    assert package.optional_inputs == ("as_of",)
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


def test_variant_cannot_override_the_evaluation(tmp_path: Path) -> None:
    root = _package(tmp_path)
    (root / "variants" / "cheat.yaml").write_text(
        "title: Cheat\nevaluation:\n  dev: 1.0\n", encoding="utf-8"
    )
    with pytest.raises(PackageError, match="may not override: evaluation"):
        apply_variant(load_package(root), "cheat")


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
    text = render(package, {"domain": "attio.com", "as_of": "2026-09-01"})
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
    card = package.card()
    assert card["evaluation"]["holdout"] == 0.997
    assert card["inputs"]["required"].keys() == {"company_name", "domain"}
    assert '"recent_news": EnrichmentSpec(' in emit_registry_entry(package)


def _run_cli(*argv: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(NEWS / "run.py"), *argv],
        capture_output=True, text=True, cwd=REPO_ROOT,
    )


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


def test_execute_delegates_to_an_entry_point_that_runs() -> None:
    """The printed command is a real CLI, not a module without a __main__ guard."""
    printed = _run_cli("execute", "--lineage", "smoke").stdout.split()
    assert printed[1:] == [
        "scripts/company_enrichment_news_loop.py",
        "--evaluate", "--lineage", "smoke", "--allow-paid",
    ]
    parsed = subprocess.run(
        [sys.executable, *printed[1:-1], "--dry-run"],
        capture_output=True, text=True, cwd=REPO_ROOT,
    )
    assert parsed.returncode == 0
    assert '"enrichment_id":"news-product-launches"' in parsed.stdout
