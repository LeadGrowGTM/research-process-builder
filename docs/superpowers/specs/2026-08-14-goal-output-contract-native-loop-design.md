# Goal-output contracts for research prompts

**Status:** approved by user on 2026-08-14
**Date:** 2026-08-14  
**Scope:** the canonical `research-builder` skill and the first ICP/persona prompt loop

## Problem

The current research-builder workflow starts with a discovery goal and moves directly into search-pattern and prompt generation. Its scaffold accepts a goal plus a list of output field names, while its tester only checks whether those field labels appear in returned text. This is not enough to distinguish a useful enrichment from a plausible-looking answer.

The first company-enrichment benchmark exposed the consequence. A model could produce a reasonable ICP or persona, cite valid evidence, and still be scored against an underspecified canonical string. Conversely, a weak or generic answer could receive credit for structurally mentioning a field. Before creating or optimizing any prompt, the workflow must define what a successful output actually looks like and how it will be judged.

## Decision

Add a mandatory **Goal Output Contract** gate to the canonical research-builder skill. No prompt, search pattern, or Research Flow may be generated until the operator has reviewed and accepted this contract.

The contract defines:

1. the exact structured output schema;
2. one concrete example of an acceptable output;
3. evidence requirements for every field;
4. allowed unknown, omission, and inference behavior;
5. deterministic rendering rules for any human-readable form;
6. the programmed scoring rubric;
7. the success threshold and any zero-tolerance conditions.

The existing research-process-builder autoresearch lifecycle remains authoritative. This change supplies a better target, ground truth, and evaluator to that loop; it does not create a second optimization loop.

## Canonical skill workflow

The canonical source is:

`C:/Users/mitch/Everything_CC/pipelines/gtm-orchestrator/.claude/skills/research-builder/`

The revised workflow is:

1. **Intake** — state the research question, intended user, input records, evidence sources, and operational constraints.
2. **Goal Output Contract** — define and approve the target schema, example, evidence rules, unknown behavior, renderer, rubric, and threshold.
3. **Pattern and prompt generation** — create search patterns and instructions specifically to fill the approved contract.
4. **Ground-truth preparation** — author component-level expected values and evidence links for the fixed development and holdout records.
5. **Experiment** — execute the existing native autoresearch loop against frozen evidence and ground truth.
6. **Evaluation** — score structured components, citations, unsupported claims, and readability using the approved rubric.
7. **Iteration** — mutate only the prompt, schema instructions, or permitted search patterns; do not move the ground truth or scorer during a run.
8. **Human review** — an Experiment reaching at least 90% programmed ground-truth validation becomes eligible for review, never automatic Approval.
9. **Output** — publish the reusable Research Flow only after explicit human Approval.

If an operator cannot yet define a realistic example output, the workflow returns to Intake instead of drafting a prompt.

## Contract representation

Each generated process must contain a `## goal output contract` section before `## steps`. It is human-readable Markdown containing a fenced YAML object with these keys:

```yaml
schema:
  field_name:
    type: string
    required: true
    evidence: required
example:
  field_name: A realistic acceptable value
evidence_rules:
  - Every factual field cites retained Evidence IDs.
unknown_behavior:
  - Return unknown when the retained Evidence cannot support the field.
rendering:
  template: "{field_name}"
scoring:
  field_name: 1.0
success:
  minimum_score: 0.90
  zero_tolerance:
    - unsupported factual claims
```

The precise field names vary by process, but all seven top-level contract keys are required. The example must conform to the declared schema. Scoring weights must total 1.0. Evidence rules and unknown behavior must be explicit rather than inherited from generic assistant behavior.

## Skill and script changes

### `SKILL.md`

- Insert Goal Output Contract as a mandatory phase after Intake.
- State the hard gate: do not create a prompt or pattern until the contract is accepted.
- Require one concrete example, not only a schema or list of fields.
- Route full prompt annealing through the existing research-process-builder autoresearch lifecycle.
- Require programmed validation at 90% or higher plus explicit human review before Approval.

### `REFERENCE.md`

- Document the contract format and a complete example.
- Explain component-level ground truth and locked holdout evaluation.
- Distinguish evidence-backed observations, labeled inference, and unsupported speculation.
- Explain that omission is correct when evidence is weak.

### `scaffold_process.py`

- Replace the insufficient `--output-fields`-only workflow with a required `--output-contract <path>` input.
- Read and validate the YAML contract before writing a process.
- Reject missing keys, malformed examples, weights that do not total 1.0, thresholds below 0.90, and an empty zero-tolerance list.
- Embed the approved contract verbatim in the generated process before the research steps.
- Derive the final `## output` template from the schema rather than accepting unrelated field names.
- Preserve dry-run and no-overwrite behavior.

For compatibility, `--output-fields` may remain temporarily accepted only when `--output-contract` is also supplied and the names match its schema. It must not independently authorize scaffolding.

### `test_process.py`

- Parse and validate `## goal output contract` before any network execution.
- Fail closed if the process lacks the contract or its output template diverges from the declared schema.
- Replace label-presence scoring with contract-aware structured scoring.
- Report per-field correctness, evidence support, explicit unknowns, unsupported claims, and the weighted total.
- Keep tier reporting as a diagnostic dimension, not as a substitute for output quality.

## First execution: ICP and persona enrichment

The first use of the new gate is the existing company-enrichment task. Its job is not to decide whether a company is funded. It converts retained company Evidence into usable ICP segments, personas, and outcomes.

### Structured goal output

```yaml
primary_icp:
  buyer: Marketing agencies
  need: automated reporting
  object: multi-channel client campaigns
  evidence_ids:
    - evidence-001
secondary_icps:
  - buyer: SEO agencies
    need: automated reporting
    object: organic-search performance across client accounts
    evidence_ids:
      - evidence-002
  - buyer: Paid-media agencies
    need: automated reporting
    object: cross-channel advertising performance for clients
    evidence_ids:
      - evidence-003
outcomes:
  - value: save reporting time
    evidence_ids:
      - evidence-001
  - value: prove campaign ROI to clients
    evidence_ids:
      - evidence-003
personas:
  observed:
    - role: Agency owner
      segment: Marketing agencies
      evidence_ids:
        - evidence-001
  inferred:
    - role: Client reporting lead
      responsibility: assembling and presenting client campaign reports
      based_on_evidence_ids:
        - evidence-001
        - evidence-003
```

### Human-readable rendering

Each ICP segment is rendered deterministically as:

`{buyer} that need {need} for {object}`

The primary example becomes:

> Marketing agencies that need automated reporting for multi-channel client campaigns.

Outcomes remain separate from the segment sentence. They explain why the buyer cares without making the segment itself vague or overloaded.

### Evidence and omission rules

- The primary ICP requires direct retained Evidence for buyer, need, and object.
- Return at most two secondary ICPs.
- A secondary ICP requires explicit evidence for a distinct buyer group and its use case. A mere product capability or analyst guess is insufficient.
- Omit a secondary segment when its support is weak. Omission is not penalized.
- Observed personas require direct role evidence.
- Inferred personas are allowed only when labeled `inferred`, tied to a supported segment and responsibility, and linked to the Evidence that supports the inference.
- Outcomes require explicit source support and remain separate from ICP rendering.
- Unsupported factual claims are a zero-tolerance failure.
- Unknown or empty collections are preferred to invented specificity.

### Ground truth

Create component-level ground truth for all ten SaaS companies. Each record stores acceptable normalized values and supporting Evidence IDs for buyer, need, object, secondary segments, outcomes, and personas.

Use six records as the development set and four as a locked holdout. The loop may inspect development failures but may not expose or mutate holdout answers. Ground truth, evidence snapshots, scoring weights, and split membership remain frozen for an experiment lineage.

### Programmed rubric

Score each company as follows:

| Dimension | Weight |
|---|---:|
| Buyer accuracy and specificity | 0.25 |
| Need accuracy and specificity | 0.20 |
| Object/use-case accuracy | 0.20 |
| Citation support and entailment | 0.20 |
| Persona quality and inference labeling | 0.10 |
| Readability and deterministic rendering | 0.05 |

Secondary segments and outcomes are evaluated inside the relevant component dimensions. A supported omission receives no penalty. An unsupported secondary segment, outcome, or persona triggers the zero-tolerance condition regardless of weighted score.

### Native loop execution

Use the existing `AutoresearchOrchestrator` roles and state transitions:

1. capture the current prompt as the baseline;
2. let the Inventor propose one bounded prompt/schema mutation;
3. run the in-bounds and novelty checkers;
4. execute against the fixed retained Evidence;
5. evaluate development and locked holdout outputs with the component rubric;
6. let the deterministic Gate advance, retry, roll back, or halt;
7. retain append-only artifacts for every attempt;
8. halt when holdout score is at least 0.90 and zero unsupported claims are present;
9. present the winning prompt, outputs, deltas, and evidence to a human reviewer.

The loop may change prompt wording, ordering, examples, and schema instructions. It may not change company Evidence, ground truth, rubric weights, holdout membership, or the 90% threshold within the experiment.

## Failure handling

- Missing or malformed Goal Output Contract: stop before prompt generation.
- Contract/output schema mismatch: stop before research execution.
- Evidence does not support a field: return unknown or omit the optional item.
- Fewer than two defensible secondary segments: return the supported number; do not fill quota.
- Holdout score below 0.90: remain an Experiment and iterate or halt with findings.
- Any unsupported claim: fail the attempt even when weighted score exceeds 0.90.
- Programmed pass without human review: remain Candidate, not Approval.

## Verification

Implementation is complete only when:

1. the skill refuses to draft a prompt without an approved goal-output contract;
2. the scaffold rejects incomplete contracts and embeds valid ones;
3. the tester rejects schema drift and produces component-level scores;
4. the ICP/persona ground truth covers ten SaaS companies with a locked holdout;
5. the native loop runs append-only experiments without changing frozen evaluation inputs;
6. the winning result reaches at least 90% on holdout with zero unsupported claims;
7. two secondary segments appear only where evidence supports them;
8. no process or prompt is promoted without explicit human review.

## Non-goals

- Building a second autoresearch or Karpathy loop.
- Using exact full-sentence equality as the quality metric.
- Treating funding status as the ICP/persona benchmark target.
- Forcing two secondary segments when evidence does not support them.
- Automatically approving a prompt because programmed validation passes.
