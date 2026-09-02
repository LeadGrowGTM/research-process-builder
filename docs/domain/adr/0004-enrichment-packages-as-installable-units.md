# Enrichment packages as the installable unit of a validated Research Flow

## Status

Proposed

## Context

A validated Research Flow currently exists as four unlinked pieces: a policy
definition (`enrichments/p0/<id>.yaml`), an annealed prompt
(`prompts/company-enrichment/<id>.md`), a runtime binding that lives only in
Python (`SignalSpec` in `scripts/company_enrichment/signal_loop.py`), and its
proof, split between a benchmark directory and a report. Nothing links them, so
what a consumer needs in order to run an approved enrichment - inputs, outputs,
the model it was proved on, the score it earned, and which parts of the prompt
may be edited - can only be reconstructed by reading source.

The consumer is the GTM orchestration landscape. Its enrichment catalog,
`src/gtm_orchestrator/stages/enrichments/registry.py`, is deliberately static:
"Adding one = add a CATALOG entry; no plugin surface, no dynamic loading"
(D-02, restated as ruling R2 of the 2026-07-19 enrichment-library restructure).
Prompts it runs live in `components/auto-prompt-creator/library/<name>.md` and
are loaded by `lg_runtime.prompts.load_prompt`, which resolves
`library_path/<name>.md` and binds a sidecar pydantic `InputModel`/`OutputModel`
by module convention. A "plug-in" therefore cannot mean runtime discovery on the
consumer side. It has to mean an artifact that installs as a reviewable diff.

The existing `definitions.py` registry cannot carry this. `load_definition`
rejects any status but `proposed` and any priority but `P0`, and `load_registry`
requires exactly the eight `EXPECTED_P0_IDS`, so the five approved enrichments -
including buying-trigger, which has no definition file at all - are structurally
unrepresentable.

## Considered Options

1. **Self-describing package directory, installed by generated diff.** One
   directory per enrichment holds the prompt with its manifest as frontmatter,
   the sidecar schema, a CLI, and variant overlays. The manifest mirrors the
   consumer's `EnrichmentSpec` field names, so installing is a rendered catalog
   entry plus a prompt-file copy that a human reviews.
2. **Extend `enrichments/p0/*.yaml` in place.** Cheapest change, but it leaves
   the prompt, schema, proof, and policy in four locations, keeps the frozen-ID
   registry, and gives a consumer nothing to install.
3. **Dynamic plug-in loading in the orchestrator.** A registry that imports
   packages at runtime. It contradicts D-02/R2, moves catalog membership out of
   review, and makes a stage's available enrichments depend on a filesystem the
   orchestrator does not own.

## Decision

Choose the self-describing package directory. Its external Interface is:

```python
load_package(root: Path, *, variant: str | None = None) -> EnrichmentPackage
emit_registry_entry(package: EnrichmentPackage) -> str
```

A package is `enrichments/<id>/` containing `<id>.md` (prompt body plus manifest
frontmatter), `schema.py`, `run.py`, and optional `variants/*.yaml`. The prompt
file is named after the id so one package holds one prompt, found from the
directory name alone. The consumer's loader resolves
`library_path/<runtime_prompt_name>.md`, and the runtime prompt name is a
catalog-side name that need not equal the id, so installing copies the prompt to
`library/<runtime_prompt_name>.md`.

The manifest is the contract: identity and lifecycle status, a `title` stating
what a reader gets out of it, a `summary` and a longer `description`, declared
`inputs` and `outputs`, the `target_model` and decoding settings it was proved
on, an `evaluation` block naming dataset, scores, gate, and approval date, a
`gtm` block whose keys mirror `EnrichmentSpec`, and an `adaptation` block naming
the prompt sections that may be edited, the ones that stay locked, and what
forces revalidation.

The manifest never reaches the model. `prompt_text` returns the body only, so
packaging an already-approved prompt leaves the scored text byte-identical and
its score intact.

Adaptation is expressed as variants rather than forks. A variant overlay may
restate the descriptive fields and append a section to the prompt; doing only
that marks it `revalidation: inherited` and it keeps the parent's proof. Touching
the model, the declared inputs, or the GTM contract marks it
`revalidation: required` and drops an approved package to `candidate`, so no
consumer can spend a score the variant did not earn. A variant never installs as
its own catalog entry.

Installation stays a reviewed diff: `emit_registry_entry` renders the consumer's
`EnrichmentSpec` - including `maturity` derived from package status and
`accuracy_pct` derived from the holdout score - to be pasted into `registry.py`
and reconciled by its existing drift test.

Migration is per enrichment. `resolve_prompt_path(id, repo_root)` returns the
package path when the package exists and the flat prompt path otherwise, so an
enrichment moves in one directory move with no caller changes.

## Consequences

A consumer can answer "what does this give me, what does it cost, how good is
it, and may I edit the prompt" from one file, and the answer is checked: an
approved package that does not clear its own stated gate fails to load. Proof
and description travel with the prompt instead of decaying in a report.

This adds a second registry alongside `definitions.py` until the P0 definitions
migrate into manifests; that duplication is intentional and temporary, and the
frozen `EXPECTED_P0_IDS` set is what it eventually replaces. Shipping the
sidecar schema inside the package needs one upstream change in
`lg_runtime.prompts.loader._load_sidecar_schemas`, which today hardcodes
`lg_runtime.prompts.schemas.<name>`; until that lands, the schema is copied to
the consumer at install time. Package status and catalog `maturity` can still
diverge if a catalog entry is hand-edited rather than re-emitted.
