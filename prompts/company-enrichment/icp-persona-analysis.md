# ICP and persona analysis

Return a structured ICP and persona analysis using only the supplied Evidence.
Every factual component must cite one or more supplied `evidence_id` values.
Do not use general knowledge, stereotypes, or plausible guesses to fill gaps.

## ICP segments

Each segment has exactly three meaningfully distinct components:

- `buyer`: the specific customer group supported by Evidence;
- `need`: the job or problem that group needs addressed;
- `object`: the concrete workflow, asset, or use case the need applies to.

The deterministic human-readable form is:

`{buyer} that need {need} for {object}`

Keep outcomes separate from the segment. An outcome explains why the buyer
cares, but it is not a substitute for a specific buyer, need, or object.

Return exactly one primary ICP. Return zero, one, or two secondary ICPs. A
secondary ICP is valid only when the Evidence explicitly supports both a
distinct buyer group and its use case. A product capability alone does not
support a secondary segment. Never fill the secondary list merely to reach two.

## Outcomes

Return outcomes only when the Evidence explicitly states or directly supports
them. Cite each outcome independently. Do not fold outcome language such as
"grow revenue" or "save time" into the deterministic segment sentence.

## Personas

Use `observed_personas` only for roles directly named or unambiguously observed
in the Evidence, and cite that direct Evidence with `evidence_ids`.

Use `inferred_personas` only for a role inferred from an evidence-backed segment
or responsibility. Keep it explicitly in the inferred collection and cite the
Evidence supporting the inference with `based_on_evidence_ids`. Never present an
inferred role as observed.

## Missing support

Omit unsupported optional items by returning an empty collection. Empty
secondary, outcome, observed-persona, or inferred-persona collections are
correct when Evidence is insufficient. Do not invent specificity. If Evidence
cannot support the required primary buyer, need, and object, do not substitute a
generic buyer or capability.

## Complete good example

```json
{
  "primary_icp": {
    "buyer": "Marketing agencies",
    "need": "automated reporting",
    "object": "multi-channel client campaigns",
    "evidence_ids": ["evidence-001"]
  },
  "secondary_icps": [
    {
      "buyer": "SEO agencies",
      "need": "automated reporting",
      "object": "organic-search performance across client accounts",
      "evidence_ids": ["evidence-002"]
    }
  ],
  "outcomes": [
    {
      "text": "save reporting time",
      "evidence_ids": ["evidence-001"]
    }
  ],
  "observed_personas": [
    {
      "role": "Agency owner",
      "evidence_ids": ["evidence-003"]
    }
  ],
  "inferred_personas": [
    {
      "role": "Client reporting lead",
      "based_on_evidence_ids": ["evidence-001", "evidence-002"]
    }
  ]
}
```

The primary segment renders exactly as: `Marketing agencies that need automated
reporting for multi-channel client campaigns`.

## Bad examples

- Generic buyer: `Businesses that need efficiency for growth`. "Businesses" is
  not a specific evidence-backed buyer, and the need and object are vague.
- Unsupported secondary: adding "Enterprise sales teams" because the product
  has dashboards, when no Evidence names that buyer and use case.
- Unlabeled inference: placing "Client reporting lead" in
  `observed_personas` when no Evidence directly names that role. It must remain
  in `inferred_personas` with `based_on_evidence_ids`, or be omitted.

Return only the structured output required by the supplied schema.
