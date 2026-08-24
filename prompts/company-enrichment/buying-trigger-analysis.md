---
name: buying-trigger-analysis
version: 0.2.0
target_model: gpt-4.1-mini
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
    campaign_idea_1: Marketing agencies hiring client reporting specialists
    campaign_idea_2: Marketing agencies launching new channel services
  unacceptable_output_example:
    campaign_idea_1: Marketing agencies struggling with reporting capacity
    campaign_idea_2: Marketing agencies needing better efficiency
  acceptance_gates:
    typed_schema_accuracy: 1.0
    fallback_accuracy: 1.0
    semantic_accuracy: 0.9
provisional: true
provisional_reason: Second candidate in human-guided prompt annealing.
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

Good: Marketing agencies hiring client reporting specialists
Good: Marketing agencies launching new channel services
Good: Manufacturers posting more cost engineering roles
Good: Lenders announcing expansion into construction loans
Bad: Marketing agencies struggling with reporting capacity
Bad: Businesses undergoing digital transformation

Return only the requested structured output.
