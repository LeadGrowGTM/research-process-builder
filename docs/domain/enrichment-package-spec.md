# Enrichment package spec

The installable unit of a validated Research Flow. Decision and rationale:
[adr/0004-enrichment-packages-as-installable-units.md](adr/0004-enrichment-packages-as-installable-units.md).
Reference implementation: `enrichments/news-product-launches/`.

## Layout

```
enrichments/<id>/
  <id>.md          manifest as YAML frontmatter, then the prompt body
  schema.py        pydantic InputModel / OutputModel
  run.py           describe | emit | body | render | execute
  variants/*.yaml  narrow overlays for adjacent use cases
```

The prompt file is named after the id: one package holds one prompt, and it is
found from the directory name alone. That name is for this repository, not for
the consumer's lookup - `lg_runtime.prompts.load_prompt` resolves
`library_path/<runtime_prompt_name>.md`, and `gtm.runtime_prompt_name` is a
catalog-side name that need not equal the id (`news-product-launches` installs
as `recent-news-summary`), so install step 2 below copies the prompt to
`library/<runtime_prompt_name>.md`.

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

`target_model`, `proof_temperature`, `proof_max_output_tokens`, `runner` (the
CLI file), `schema_module` (the sidecar file). The proof-prefixed fields record
the exact decoding configuration used to earn the evaluation scores; `null`
means that setting was not sent to the model. Both file references must be
relative files inside the package and are checked at load time. The prompt body
and runner must be non-empty, and the importable schema module must export
Pydantic `InputModel` and `OutputModel` classes.

### Contract

`inputs.required` and `inputs.optional` are mappings of package-local names to
descriptors containing `description` and `consumer_column`. Package input names
belong to the package's prompt and runner; `consumer_column` explicitly maps
each one to the consumer's column namespace. An input name cannot appear in
both the required and optional groups. Each public `outputs` field
declares a non-empty `type` and `description` plus `consumer_column`; use `null`
when a public field has no distinct consumer column. A shared shape may instead
declare a non-empty `fields` list for the public fields to reuse. The sidecar
`InputModel` must expose exactly the manifest's package-local input names with
matching requiredness, and its `OutputModel` must expose exactly the public
output names; shared shapes are definitions, not top-level output fields.
The package runner currently renders `company_name` and `domain`; declaring an
input outside that executable boundary is refused rather than silently dropped.
The portable `OutputModel` enforces Evidence closure when the consumer calls
`model_validate(..., context={"retained_evidence_ids": ids})`, where `ids` come
from the retained Evidence records supplied to the model for that run. Without
that context the sidecar validates shape only; it does not provide the
Evidence-closure guarantee.

### GTM block

Field names mirror `EnrichmentSpec` in
`src/gtm_orchestrator/stages/enrichments/registry.py` so installation is a
mechanical diff: `slug`, `provider`, `type`, `enrichment_level`,
`runtime_prompt_name`, `requires_tools`,
`linkedin_safe`, `cost_per_100`, `cost_estimate`. Repo-local policy travels
alongside: `tier`, `caps`, `freshness_days`, `output_visibility`.

The loader type-checks the mirrored keys because the emitted entry reads them
straight through: `linkedin_safe` must be a YAML boolean (a quoted `"false"` is
refused rather than silently coerced to true), `cost_per_100` a non-negative
number, the tool list a real YAML list of non-empty strings, and the text keys
non-empty strings. `input_columns` and `output_columns` are emitted in manifest
order from the field-level `consumer_column` mappings; hand-written copies in
the GTM block are refused, so package and consumer names cannot drift. A wrong
type fails at load, not at emit.

### Evaluation

`dataset`, `scorer`, `candidate`, `dev`, `holdout`, `gate`, `approved_on`
(a quoted, real `YYYY-MM-DD` calendar date), and `report`. A package with
`status: approved` must name all of them, use a gate of at least 0.90, and clear
that gate on both `dev` and `holdout`, or it fails to load. Scores and the gate
must be finite numbers from zero to one. This is the gate from CLAUDE.md
expressed as data: programmed score at or above 0.90 plus the recorded human
review.

### Adaptation

`adaptable`, `safe_edits`, `locked`, `revalidate_when`, `revalidate_with`. The
loader requires all five, type-checks `adaptable` as a YAML boolean and the
guidance fields as lists of text, and requires an adaptable package to name its
locked sections and revalidation command.

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
   --company-name X --domain x.com` prints the prompt assembled the way the live
   client assembles it: the body, then `Company ID`, `Subject company`,
   `Enrichment`, `Requested fields`, `Evidence`. The company id and the Evidence
   are not known before a run, so those two lines carry an explicit placeholder
   rather than being dropped. Read it. A prompt edit that reads fine in the diff
   and wrong in the render is the common failure. `render` is for reading only;
   `body` is the mode whose output is safe to hand to a run as `--prompt`.
4. **Never edit the frontmatter to keep a score.** Changing the model, the
   inputs, or the GTM contract sets `revalidation: required` and drops the
   package out of `approved`. That is the mechanism working, not an obstacle.
5. **Revalidate with the command the package names.** `adaptation.revalidate_with`
   is the loop entry that produced the original score; use it, so the new number
   is comparable to the old one. For the news package that is
   `py scripts/company_enrichment_news_loop.py --evaluate --lineage <name>
   --model gpt-5.6-luna --allow-paid`, which `py
   enrichments/news-product-launches/run.py execute --lineage <name>
   --allow-paid` delegates to.
6. **Bump `version` and record the new score** in `evaluation` before the
   package returns to `approved`.

## Variants

A variant overlay may set `title`, `summary`, `description`, `name`,
`prompt_append`, `notes`, and - at the cost of revalidation - `target_model`,
`inputs`, `gtm`. It may also declare `variant`, which must equal the file stem
it is selected by; a mismatch is refused. Anything else is refused, as is an
overlay that sets nothing beyond `variant` and `notes`. `prompt_append` must be
non-empty text; booleans, lists, mappings, and whitespace-only values are refused.

| Overlay touches | `revalidation` | `status` |
|---|---|---|
| description fields and `prompt_append` only | `inherited` | unchanged |
| model, inputs, or GTM contract | `required` | `approved` becomes `candidate` |

An overlay is held to the same safety checks as the manifest it overlays: no
`${` environment interpolation, and no secret-bearing key anywhere in the
mapping.

An overlay must also merge into a manifest that would have loaded on its own -
the loader re-runs the full manifest validation over the merged result, so an
overlay cannot reach a state (an empty `inputs.required`, a non-mapping `gtm`)
that a base manifest is refused for.

Every variant shares the package's single sidecar schema. An `inputs` overlay
may refine descriptions or consumer-column mappings, but it must preserve the
parent's required and optional input names without adding, removing, or moving
them between groups. Input descriptors are merged field by field, so a variant
may name only the input and descriptor field it refines; sibling inputs and
unchanged descriptor fields remain inherited. A use case that needs different
inputs is a different enrichment or a v2 of the parent.

`run.py execute` revalidates the parent package against its sealed benchmark
corpus, so it refuses `--variant` along with the other subject flags. Revalidate
a variant by materialising it first: `py enrichments/<id>/run.py body --variant
X > output/X.md`, then pass `--prompt output/X.md` to the command in
`adaptation.revalidate_with`. Read the variant's effective `target_model` from
`run.py describe --variant X` and replace the command's `--model` value with
that model; the inherited command names the parent's proof model and must not be
used unchanged for a model-changing variant. Use `body`, not `render` -
`render` includes the live-assembly sections the run appends for itself, and
passing those as a prompt sends the model a second, placeholder-filled copy of
them.
`emit_registry_entry` refuses a variant entirely:
variants do not become their own catalog entries. It also refuses a package with
`status: rejected` - a rejected enrichment has no catalog entry to install.

## Installing into the GTM orchestrator

The consumer catalog is static by ruling (D-02 / R2): no dynamic loading. An
install is four reviewed steps.

1. `py enrichments/<id>/run.py emit` and paste the rendered `EnrichmentSpec`
   into `src/gtm_orchestrator/stages/enrichments/registry.py`.
2. Copy `<id>.md` - the whole file, manifest frontmatter included - to
   `components/auto-prompt-creator/library/<runtime_prompt_name>.md`. The
   consumer strips the frontmatter itself:
   `lg_runtime.prompts.frontmatter.parse_prompt_file` returns
   `(frontmatter, body)` and `loader.load_prompt` builds the `Prompt` from the
   body alone, so the manifest never reaches the model on that side either.
   The consumer loader reserves `tool_use` and `conversation`; package loading
   rejects those keys before installation and the consumer ignores every other
   manifest key, which is why the rest of the manifest travels safely in the
   same file. Do not paste a `body` render here - the file is copied
   whole so the installed artifact and the package stay one reviewable object.
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
