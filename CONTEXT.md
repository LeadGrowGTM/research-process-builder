# Research-Process Builder

This context names the concepts used to make research repeatable, evaluable, and reviewable. These definitions are the canonical language for resumable autoresearch.

## Language

**Research Flow**:
A validated, portable sequence that produces research evidence for a stated goal.
_Avoid_: Research process, pipeline

**Search Flow**:
A Research Flow that begins with a query and returns ranked source candidates.
_Avoid_: Search pipeline, query workflow

**Site Extraction Flow**:
A Research Flow that begins with known URLs and extracts evidence deterministically before any optional LLM interpretation.
_Avoid_: Scraping pipeline, extraction workflow

**Source Adapter**:
An adapter at the provider seam that performs read-only discovery or extraction and returns provider-neutral Evidence.
_Avoid_: Provider client, integration

**Experiment**:
One proposed change to a Research Flow, identified by a stable content-derived key.
_Avoid_: Mutation, iteration

**Evidence**:
Bounded, source-attributed observations used to evaluate an Experiment.
_Avoid_: Results, findings

**Approval**:
The lifecycle state reached only after at least 90% ground-truth validation and explicit human review.
_Avoid_: Auto-approval, promotion
