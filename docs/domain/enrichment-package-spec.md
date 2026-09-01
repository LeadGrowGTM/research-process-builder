# Enrichment package spec

The installable unit of a validated Research Flow. Decision and rationale:
[adr/0004-enrichment-packages-as-installable-units.md](adr/0004-enrichment-packages-as-installable-units.md).
Reference implementation: `enrichments/news-product-launches/`.

## Layout

```
enrichments/<id>/
  <id>.md          manifest as YAML frontmatter, then the prompt body
  schema.py        pydantic InputModel / OutputModel
  run.py           describe | emit | render | execute
  variants/*.yaml  narrow overlays for adjacent use cases
```

The prompt file is named after the id so the package directory can be passed to
the GTM orchestrator's `lg_runtime.prompts.load_prompt` as its `library_path`
with no path translation.

## Manifest fields

Every key below is required; the loader refuses a package that omits one.

### Identity

| Key | Meaning |
|---|---|
| `id` | lower-kebab-case, equal to the directory name |
| `name` | short human name |
| `title` | what you get out of it, in outcome language, not a category label |
| `summary` | one sentence, used in pickers and as the catalog description |
| `description` | the longer read: what it does, what it is for, and what it is not for |
| `version` | semver for the package |
| `status` | `proposed` -> `experiment` -> `candidate` -> `approved` (or `rejected`) |
| `kind` | `lookup` (entity in, data out) or `monitoring` (date in, results out) |
| `entity` | what the id addresses, e.g. `company` |

Write `description` so it also says where the enrichment is weak. The news
package says it is not a funding database, because a reader who assumes it is
will misread an empty result as "no funding".

### Runtime

`target_model`, `temperature`, `max_tokens`, `runner` (the CLI file),
`schema_module` (the sidecar file). Both file references are checked to exist at
load time.

### Contract

`inputs.required` and `inputs.optional` are mappings of name to description -
the description is what a caller reads to know what to pass. `outputs` is a
mapping of field name to a type and description, plus any shared shape the
fields reuse.

### GTM block

Field names mirror `EnrichmentSpec` in
`src/gtm_orchestrator/stages/enrichments/registry.py` so installation is a
mechanical diff: `slug`, `provider`, `type`, `enrichment_level`,
`runtime_prompt_name`, `input_columns`, `output_columns`, `requires_tools`,
`linkedin_safe`, `cost_per_100`, `cost_estimate`. Repo-local policy travels
alongside: `tier`, `caps`, `freshness_days`, `output_visibility`.

### Evaluation

`dataset`, `scorer`, `candidate`, `dev`, `holdout`, `gate`, `approved_on`
(quoted, so YAML does not turn it into a date object), and `report`. A package
with `status: approved` must name all of them and must clear its own `gate` on
`dev`, or it fails to load. This is the gate from CLAUDE.md expressed as data:
programmed score at or above 0.90 plus the recorded human review.

### Adaptation

`adaptable`, `safe_edits`, `locked`, `revalidate_when`, `revalidate_with`. An
adaptable package must name its locked sections - the loader rejects one that
claims adaptability without saying what may not move.

## Editing a prompt before a run

The manifest exists so this is a bounded decision rather than a judgement call.

1. **Read `adaptation.locked` first.** Those sections are why the score holds.
   In the news package they are the evidence-citation rule, the
   no-general-knowledge rule, the date rules, and the different-entity rule.
   Editing any of them invalidates the recorded proof, whatever the diff looks
   like.
2. **Prefer a variant over an edit.** A variant is a file in `variants/` that
   appends a section and restates the description. It leaves the parent intact,
   it is reviewable on its own, and the loader decides whether it may inherit
   the parent's proof.
3. **Render before you spend.** `py enrichments/<id>/run.py render --variant X
   ...` prints the exact text that would be sent, subject block included. Read
   it. A prompt edit that reads fine in the diff and wrong in the render is the
   common failure.
4. **Never edit the frontmatter to keep a score.** Changing the model, the
   inputs, or the GTM contract sets `revalidation: required` and drops the
   package out of `approved`. That is the mechanism working, not an obstacle.
5. **Revalidate with the command the package names.** `adaptation.revalidate_with`
   is the loop entry that produced the original score; use it, so the new number
   is comparable to the old one.
6. **Bump `version` and record the new score** in `evaluation` before the
   package returns to `approved`.

## Variants

A variant overlay may set `title`, `summary`, `description`, `name`,
`prompt_append`, `notes`, and - at the cost of revalidation - `target_model`,
`inputs`, `gtm`. Anything else is refused.

| Overlay touches | `revalidation` | `status` |
|---|---|---|
| description fields and `prompt_append` only | `inherited` | unchanged |
| model, inputs, or GTM contract | `required` | `approved` becomes `candidate` |

`run.py execute` refuses to run a variant marked `revalidation: required`, and
`emit_registry_entry` refuses a variant entirely: variants do not become their
own catalog entries.

## Installing into the GTM orchestrator

The consumer catalog is static by ruling (D-02 / R2): no dynamic loading. An
install is four reviewed steps.

1. `py enrichments/<id>/run.py emit` and paste the rendered `EnrichmentSpec`
   into `src/gtm_orchestrator/stages/enrichments/registry.py`.
2. Copy `<id>.md` to
   `components/auto-prompt-creator/library/<runtime_prompt_name>.md`.
3. Register the sidecar schema. Until
   `lg_runtime.prompts.loader._load_sidecar_schemas` accepts a `schema_module`
   frontmatter key, copy `schema.py` to
   `lg_runtime/prompts/schemas/<runtime_prompt_name with underscores>.py`.
4. Run the registry drift test so the two TypeScript mirrors match.

`maturity` and `accuracy_pct` in the emitted entry are derived from the
package's status and holdout score. Re-emit rather than hand-editing them when a
package is re-scored.

## Migration

`resolve_prompt_path(id, repo_root)` returns the package path when the package
exists and `prompts/company-enrichment/<id>.md` otherwise, so an enrichment
migrates in one directory move. `prompt_text` strips the manifest, so the model
sees exactly the words it was scored on - the news migration is covered by a
test asserting the body is byte-identical to the pre-move file.
