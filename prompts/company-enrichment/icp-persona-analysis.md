# ICP and persona analysis

## Goal

Define who this company TARGETS and the single biggest OUTCOME its target BUYER
NEEDS based on the main problem the company solves.
Use only supplied Evidence and cite every claim.
Follow the process silently and return only the schema output.

## Required sentence
Write one primary ICP as:
`{buyer} {relationship} {outcome}`
Choose exactly one relationship:
- `looking for`
- `who need`
- `in the market for`

## Process

1. Find the single main buyer problem stated or clearly supported by Evidence.
2. Choose the one buyer archetype most directly affected by that problem.
3. Translate the problem into the one concrete result that buyer wants.
4. Choose the relationship only after drafting the buyer and outcome.
5. Read the sentence aloud. If any rule fails, return to Step 1.

## Buyer rules

- Use one plural company, team, or persona archetype.
- Prefer the company or team type explicitly named by the target.
- Use one named persona only when the company type is vague or unhelpful.
- Keep the buyer to one to four words when possible.

## Outcome rules

- State one result or improved state rather than the product being purchased.
- Use one natural noun phrase containing two to five words.
- Use the buyer's plain language rather than the vendor's marketing language.
- Use at most one descriptive modifier before the outcome's main noun.
- Name the concrete thing the Evidence says the buyer struggles with. Never
  substitute a broader business benefit such as retention, revenue, or growth
  unless the Evidence states it as the buyer's own problem.

## Persona rules

- `observed_personas`: only roles the Evidence explicitly states. Cite that
  Evidence in `evidence_ids`.
- `inferred_personas`: only roles reasonably implied but never stated. Cite the
  Evidence the inference rests on in `based_on_evidence_ids`.
- Never put the same role in both arrays.
- Return an empty array for either list when the Evidence names no roles.
- Keep role phrasing short and in the buyer's own language. Never invent a
  title the Evidence does not support.

## Never do this

- Never use a comma or the word `and` anywhere in the rendered sentence.
- Never list or combine multiple buyers.
- Never combine multiple outcomes.
- Never begin the outcome with a bare verb such as save, know, prove, or prevent.
- Never begin the outcome with a verb ending in `-ing`.
- Never name a product, platform, workspace, workflow, feature, or solution.
- Never use vendor jargon or stack synonyms such as resilient plus exception-resistant.
- Never repeat a buyer word inside the outcome.
- Never copy a grammar-check phrase such as `They need` into the output.
- Compare each secondary buyer to the primary and delete it when they are the same.

## Examples

Priority: `Manufacturers looking for high-velocity product development`
Wrong: `Executives and architecture teams looking for Operational AI governance`
Right: `Enterprise IT teams looking for reliable AI operations`
Wrong: `Marketers who need know what marketing works`
Right: `Marketers who need clear campaign performance`
Wrong: `Marketing agencies looking for client retention` (generic benefit; the Evidence problem is reporting effort)
Right: `Marketing agencies looking for faster client reporting`
Add a secondary only when Evidence names a genuinely different buyer with a genuinely different outcome. Otherwise return none.
