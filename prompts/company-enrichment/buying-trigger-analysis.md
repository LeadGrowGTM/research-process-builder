---
name: buying-trigger-analysis
version: 0.3.0
target_model: gpt-5.6-luna
temperature: 0.2
max_tokens: 700
input_schema:
  company_id: string
  evidence: array
output_schema:
  campaign_idea_1: string
  campaign_idea_2: string
goal_output:
  success_definition: >-
    Return two distinct evidence-backed campaign ideas based on public signals
    that can be found and verified from public sources.
  fallback_behavior: >-
    Put an unsupported output field in unknowns rather than inventing a second
    public signal.
  good_output_example:
    campaign_idea_1: Restaurant groups posting payroll manager roles
    campaign_idea_2: Restaurant groups announcing new location openings
  unacceptable_output_example:
    campaign_idea_1: Restaurant groups struggling with payroll complexity
    campaign_idea_2: Restaurant groups needing better efficiency
  acceptance_gates:
    typed_schema_accuracy: 1.0
    fallback_accuracy: 1.0
    semantic_accuracy: 0.9
provisional: true
provisional_reason: Third candidate; examples moved off benchmark companies after v3 parroting.
---

# Public buying signal analysis

## Goal

Create two distinct campaign ideas based on public signals we could use to find
companies likely to need the outcome described in the supplied Evidence.

A signal must be visible and verifiable in a public source. Define the signal
to research. Do not claim that it is currently happening at the company.

## Process

1. Identify the main buyer explicitly supported by Evidence.
2. Identify the main problem the company solves for that buyer.
3. List public events or artifacts that would make that problem more urgent.
4. Choose two signals that can be found through public research at scale.
5. Rewrite each as one plain signal and check every rule.

## Public signal test

A researcher must be able to answer yes or no from a public source.
Good sources include job posts company news service pages ads filings leadership
changes acquisitions certifications location openings or product launches.

## Rules

- Return one assertion for campaign_idea_1 and one for campaign_idea_2.
- Each value must name one public signal in 12 words or fewer.
- Start with the buyer archetype when it improves clarity.
- Make the two signals meaningfully different rather than paraphrases.
- Describe an observable event action posting page or announcement.
- Use conversational language at an eighth-grade reading level.
- Use the buyer's words when Evidence provides them.
- Cite Evidence that explains why the signal matters to this company's offer.
- Never claim the signal is currently happening.
- Never describe a private feeling pain or internal state.
- Never use struggling needing facing pressure complexity risk or demand alone.
- Never use vague signals such as growth transformation innovation or scaling.
- Never name the company's product platform feature workflow or solution.
- Never combine signals with a comma or the word and.
- If Evidence supports only one signal put the other field in unknowns.

## Examples

Good: Restaurant groups posting payroll manager roles
Good: Clinics announcing new patient scheduling systems
Good: Freight carriers posting fleet maintenance roles
Good: Retailers announcing expansion into new regions
Bad: Restaurant groups struggling with payroll complexity
Bad: Businesses undergoing digital transformation

Return only the requested structured output.
