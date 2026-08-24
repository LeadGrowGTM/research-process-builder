# Prompt annealing

Use this process to build or improve any LLM prompt whose outputs can be
reviewed against fixed examples.

## 1. Define the output before the prompt

- Write the exact structured output contract first.
- Show the human-readable rendering of that contract.
- Define field-level length limits and total output limits.
- Define required omissions and explicit unknown behavior.
- Define what counts as unsupported or invalid.
- Get human acceptance before generating prompt candidates.

## 2. Freeze the experiment

- Keep one fixed development set across prompt versions.
- Keep a separate holdout sealed until selecting a development winner.
- Send one independent case per API call when cross-case context could leak.
- Give the model only normal inputs and retained Evidence.
- Never send benchmark answers or scorer aliases to the model.
- Change one variable at a time: prompt wording or model, never both.
- When comparing models, reuse the exact same prompt hash and case set.

## 3. Build for the required degree of control

- Do not assume the shortest prompt performs best.
- Start concise, then add boundaries when failures show the task is fragile.
- For strict grammar or classification, a 50 to 60 line prompt can outperform
  a compressed prompt when those lines specify real failure modes.
- Use ordered steps when the task requires selection before rendering.
- Put the final self-review after generation and send failures back to Step 1.
- Use positive examples to show the target shape.
- Use wrong-to-right examples drawn from actual failure classes.
- Do not use a holdout answer as an example. If an evaluation case becomes an
  example, mark it contaminated and remove it from unbiased scoring.

## 4. Prefer explicit negative boundaries

List what the model must not do when each prohibition maps to a real observed
failure. Common prompt failures include:

- combining multiple buyers;
- combining multiple outcomes;
- repeating buyer words inside the outcome;
- describing the product rather than the buyer result;
- copying vendor jargon from Evidence;
- starting noun-phrase fields with bare verbs or gerunds;
- leaking a grammar-check phrase into the output;
- exceeding word limits;
- adding an unsupported optional item;
- adding a secondary for the same buyer under a renamed label;
- using commas or conjunctions when the contract requires one idea.

Avoid generic warnings. Each negative rule should be mechanically checkable or
illustrated by a concrete wrong-to-right pair.

## 5. Anneal against real outputs

For every iteration:

1. State one mutation and the failure it should repair.
2. Record the exact prompt text, ID, hash, word count, and character count.
3. Run the same development cases.
4. Record requested model, resolved model, latency, and cost.
5. Show every primary output, secondary output, and omission.
6. Score mechanical adherence and semantic quality separately.
7. Ask the human reviewer for concrete reactions.
8. Convert those reactions into a general rule or example.
9. Preserve the prior version so regressions can be traced.
10. Test the development winner once on the sealed holdout.

Offline mutation tables are hypotheses, not validation. A projected repair does
not graduate until the real target model produces outputs with that prompt.

## 6. Evaluate honestly

- Mechanical scoring checks schema, grammar, length, citations, and omissions.
- Human review checks meaning, naturalness, buyer language, and usefulness.
- Never claim semantic improvement from a format score alone.
- Inspect regressions by prompt version and by individual case.
- Track whether the prompt is selecting one fact or synthesizing several facts.
- Treat repeated rule violations as evidence that the prompt needs a structural
  constraint, example, or schema guard rather than more abstract prose.

## 7. Report every round

Every iteration handoff must include:

- exact prompt mutation;
- exact requested and resolved model;
- prompt line, word, and character counts;
- every rendered output;
- secondary outputs or explicit none;
- per-case failures;
- mechanical score and prior-round delta;
- qualitative human assessment;
- iteration cost and cumulative cost;
- a direct request for reviewer feedback.

## 8. Save and graduate

- Save the best prompt to its canonical prompt file.
- Retain the lineage of prompts, outputs, scores, and model metadata.
- Record any contaminated examples.
- A high offline score does not create Approval.
- A high holdout score does not create Approval by itself.
- Require programmed validation at the repository threshold followed by
  explicit human review before promotion.
- Re-anneal when live quality drops or a new failure class appears.
