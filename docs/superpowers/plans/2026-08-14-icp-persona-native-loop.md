# ICP Persona Native Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Replace the weak whole-string ICP benchmark with a typed, evidence-backed primary ICP, up to two secondary segments, outcomes, and personas, then optimize the prompt through the existing AutoresearchOrchestrator.

**Architecture:** Keep the published company Evidence and generic orchestration Module unchanged. Add an ICP-specific contract, frozen ten-company ground truth, deterministic component evaluator, prompt-aware OpenAI execution seam, and a thin native-loop composition that supplies real Inventor/checker/executor/evaluator roles to AutoresearchOrchestrator.

**Tech Stack:** Python 3, PyYAML, OpenAI Responses API through the existing adapter, pytest, append-only orchestration artifacts.

## Global Constraints

- Primary rendering is exactly {buyer} that need {need} for {object}.
- Store outcomes separately from segments.
- Return one primary ICP and zero to two secondary ICPs; omit weak secondary segments without penalty.
- Every primary/secondary/outcome/observed-persona claim cites retained Evidence IDs.
- Inferred personas must be labeled inferred and cite the Evidence supporting the inference.
- Unsupported factual claims are a hard failure even when weighted score exceeds 0.90.
- Ground truth covers saas-01 through saas-10 with development IDs saas-01,02,04,05,07,09 and locked holdout IDs saas-03,06,08,10.
- Rubric weights are buyer .25, need .20, object .20, citation .20, persona .10, readability .05.
- Freeze contract, Evidence hashes, ground truth, split, rubric, and model ID for an experiment lineage.
- Use existing AutoresearchOrchestrator; do not build a second state machine.
- A holdout score at or above 0.90 with zero hard failures halts for human review, never Approval.
- Initial live lineage uses cached Evidence only, gpt-5.6-luna, synchronous execution, no source purchases, at most three prompt candidates, and an aggregate paid cap of USD 1.00.
- Preserve docs/benchmarks-company-corpus-proposal.md untouched.

---

## File Structure

- Create scripts/company_enrichment/icp_persona_contracts.py for typed outputs and deterministic rendering.
- Create scripts/company_enrichment/icp_persona_ground_truth.py for frozen data/split/rubric validation.
- Create scripts/company_enrichment/icp_persona_evaluator.py for deterministic component scoring.
- Create scripts/company_enrichment/icp_persona_loop.py for native role composition and lineage execution.
- Create scripts/company_enrichment_icp_loop.py as the thin CLI.
- Create benchmarks/icp-persona/contract.yaml, rubric.yaml, split.yaml, and ground-truth/saas-01..10.yaml.
- Modify prompts/company-enrichment/icp-persona-analysis.md with the approved target and counterexamples.
- Modify ExperimentInput and OpenAIModelClient only enough to accept an immutable prompt/contract override.
- Add four focused test Modules and update the live report after execution.

### Task 1: Typed ICP Output Contract

**Files:**
- Create: scripts/company_enrichment/icp_persona_contracts.py
- Create: tests/company_enrichment/test_icp_persona_contracts.py
- Create: benchmarks/icp-persona/contract.yaml

**Interfaces:**
- Produces PrimaryICP, SecondaryICP, Outcome, ObservedPersona, InferredPersona, IcpPersonaOutput, load_icp_contract(path), parse_icp_output(value, retained_evidence_ids), render_segment(segment).

- [ ] **Step 1: Write contract RED tests**

~~~python
def test_renders_primary_segment_deterministically():
    value = PrimaryICP(
        buyer="Marketing agencies",
        need="automated reporting",
        object="multi-channel client campaigns",
        evidence_ids=("ev-1",),
    )
    assert render_segment(value) == (
        "Marketing agencies that need automated reporting "
        "for multi-channel client campaigns"
    )


def test_rejects_third_secondary_and_unknown_evidence():
    with pytest.raises(ValueError, match="at most two"):
        IcpPersonaOutput(primary(), (secondary(), secondary(), secondary()), (), personas())
    with pytest.raises(ValueError, match="retained Evidence"):
        parse_icp_output(payload(evidence_ids=["made-up"]), {"ev-1"})
~~~

- [ ] **Step 2: Run RED**

~~~powershell
py -m pytest tests/company_enrichment/test_icp_persona_contracts.py -q
~~~

- [ ] **Step 3: Implement frozen types and parser**

~~~python
@dataclass(frozen=True, slots=True)
class PrimaryICP:
    buyer: str
    need: str
    object: str
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class IcpPersonaOutput:
    primary_icp: PrimaryICP
    secondary_icps: tuple[SecondaryICP, ...]
    outcomes: tuple[Outcome, ...]
    observed_personas: tuple[ObservedPersona, ...]
    inferred_personas: tuple[InferredPersona, ...]
~~~

Require non-empty normalized text, unique secondary buyer/use-case tuples, at most two secondaries, Evidence closure, observed role evidence, and inferred based_on_evidence_ids. Keep outcomes outside rendering.

- [ ] **Step 4: Add and validate contract.yaml**

~~~yaml
version: "1.0"
rendering: "{buyer} that need {need} for {object}"
secondary_limit: 2
unsupported_claim_policy: hard_fail
unknown_policy: omit_optional_or_return_unknown
~~~

- [ ] **Step 5: Run GREEN and commit**

~~~powershell
py -m pytest tests/company_enrichment/test_icp_persona_contracts.py -q
git add scripts/company_enrichment/icp_persona_contracts.py tests/company_enrichment/test_icp_persona_contracts.py benchmarks/icp-persona/contract.yaml
git commit -m "feat: define structured icp persona outputs"
~~~

### Task 2: Frozen Ten-Company Ground Truth

**Files:**
- Create: scripts/company_enrichment/icp_persona_ground_truth.py
- Create: tests/company_enrichment/test_icp_persona_ground_truth.py
- Create: benchmarks/icp-persona/split.yaml
- Create: benchmarks/icp-persona/rubric.yaml
- Create: benchmarks/icp-persona/ground-truth/saas-01.yaml through saas-10.yaml

**Interfaces:**
- Produces IcpGroundTruthRecord, IcpDataset, load_icp_dataset(root, dossiers), dataset_hash.
- Consumes published dossier Evidence only.

- [ ] **Step 1: Write dataset RED tests**

~~~python
def test_dataset_is_exact_ten_with_locked_six_four_split(dossiers):
    dataset = load_icp_dataset(ROOT, dossiers)
    assert set(dataset.records) == {f"saas-{index:02d}" for index in range(1, 11)}
    assert dataset.development_ids == (
        "saas-01", "saas-02", "saas-04", "saas-05", "saas-07", "saas-09",
    )
    assert dataset.holdout_ids == ("saas-03", "saas-06", "saas-08", "saas-10")


def test_every_expected_component_closes_over_dossier_evidence(dossiers):
    dataset = load_icp_dataset(ROOT, dossiers)
    for record in dataset.records.values():
        assert record.all_evidence_ids <= {
            item.evidence_id for item in dossiers[record.company_id].evidence
        }
~~~

- [ ] **Step 2: Run RED**

~~~powershell
py -m pytest tests/company_enrichment/test_icp_persona_ground_truth.py -q
~~~

- [ ] **Step 3: Implement strict loader and immutable hashes**

~~~python
@dataclass(frozen=True, slots=True)
class IcpDataset:
    records: Mapping[str, IcpGroundTruthRecord]
    development_ids: tuple[str, ...]
    holdout_ids: tuple[str, ...]
    rubric: IcpRubric
    dataset_hash: str
~~~

Reject missing/extra SaaS IDs, overlap, split drift, Evidence IDs absent from the matching dossier, more than two secondaries, empty acceptable aliases, weights not totaling 1.0, or threshold other than 0.90. Hash canonical file bytes plus referenced dossier Evidence hashes.

- [ ] **Step 4: Author human-reviewed records**

Use these exact primary targets, with acceptable aliases and field-specific Evidence IDs selected from each published dossier:

~~~yaml
saas-01: Marketing agencies | automated reporting | multi-channel client campaigns
saas-02: Enterprises | composable workflow automation | business processes and AI orchestration
saas-03: B2B sales teams | shared deal workspaces | complex buying journeys
saas-04: Manufacturers | product cost and manufacturability analysis | design and sourcing decisions
saas-05: Regulated enterprises and government agencies | governed data archiving | compliance and AI readiness
saas-06: Procurement teams | predictive sourcing automation | supplier negotiations
saas-07: HR leaders | continuous performance management | enterprise workforces
saas-08: Enterprise IT teams | incident intelligence and automation | IT operations
saas-09: Marketing teams | branded link and QR management | digital campaigns
saas-10: Commercial real-estate lenders | automated loan administration | construction finance workflows
~~~

For saas-01 include the two approved secondaries only where the retained excerpts explicitly support them: SEO agencies reporting organic-search performance across client accounts, and paid-media agencies reporting cross-channel advertising performance for clients. For every other company author zero to two secondaries only from explicit buyer/use-case language; omission is valid.

- [ ] **Step 5: Verify data and commit**

~~~powershell
py -m pytest tests/company_enrichment/test_icp_persona_ground_truth.py -q
git add scripts/company_enrichment/icp_persona_ground_truth.py tests/company_enrichment/test_icp_persona_ground_truth.py benchmarks/icp-persona
git commit -m "data: add evidence backed saas icp ground truth"
~~~

### Task 3: Prompt-Aware Model Execution

**Files:**
- Modify: scripts/company_enrichment/experiment_runner.py:49-83
- Modify: scripts/company_enrichment/openai_model_client.py:208-326,405-490
- Modify: prompts/company-enrichment/icp-persona-analysis.md
- Modify: tests/company_enrichment/test_openai_model_client.py
- Create: tests/company_enrichment/test_icp_persona_prompt_execution.py

**Interfaces:**
- Extend ExperimentInput with prompt_id: str = "", prompt_text: str = "", output_contract: Mapping[str, object] | None = None.
- Existing callers with empty overrides retain byte-for-byte legacy prompt/schema behavior.
- ICP override returns structured values inside the existing icp and personas FieldAssertions.

- [ ] **Step 1: Write compatibility and structured-schema RED tests**

~~~python
def test_legacy_request_body_is_unchanged():
    request = legacy_request()
    assert client._body(request) == LEGACY_BODY


def test_icp_override_uses_candidate_prompt_and_nested_schema():
    request = icp_request(prompt_id="candidate-1", prompt_text="Return structured ICPs")
    body = client._body(request)
    assert "candidate-1" in client._request_digest(request)
    assert body["input"].startswith("Return structured ICPs")
    assert body["text"]["format"]["schema"]["properties"]["primary_icp"]
~~~

- [ ] **Step 2: Run RED**

~~~powershell
py -m pytest tests/company_enrichment/test_icp_persona_prompt_execution.py tests/company_enrichment/test_openai_model_client.py -q
~~~

- [ ] **Step 3: Add immutable prompt/schema overrides**

~~~python
@dataclass(frozen=True, slots=True)
class ExperimentInput:
    enrichment_id: str
    company_id: str
    requested_model_id: str
    dossier: CompanyDossier
    prompt_id: str = ""
    prompt_text: str = ""
    output_contract: Mapping[str, object] | None = None
~~~

Include prompt ID, prompt text hash, and contract hash in sync/Batch digests and provider artifacts. Never serialize secrets. Preserve exact requested/resolved model identity and existing retry/idempotency behavior.

- [ ] **Step 4: Replace the four-line prompt with the approved contract**

The prompt must state the deterministic segment form, outcomes separation, secondary limit, Evidence-only behavior, observed/inferred distinction, omission behavior, a complete good example, and bad examples for generic buyers, unsupported secondary segments, and unlabeled inferred personas.

- [ ] **Step 5: Verify no regression and commit**

~~~powershell
py -m pytest tests/company_enrichment/test_icp_persona_prompt_execution.py tests/company_enrichment/test_openai_model_client.py tests/company_enrichment/test_experiment_runner.py -q
git add scripts/company_enrichment/experiment_runner.py scripts/company_enrichment/openai_model_client.py prompts/company-enrichment/icp-persona-analysis.md tests/company_enrichment
git commit -m "feat: execute versioned icp prompt contracts"
~~~

### Task 4: Component Evaluator

**Files:**
- Create: scripts/company_enrichment/icp_persona_evaluator.py
- Create: tests/company_enrichment/test_icp_persona_evaluator.py

**Interfaces:**
- Produces ComponentScore, CompanyIcpScore, IcpPersonaEvaluation, evaluate_icp_persona(outputs, dataset, dossier_evidence, split).
- Does not call a model.

- [ ] **Step 1: Write rubric and hard-failure RED tests**

~~~python
def test_component_weights_match_approved_rubric():
    assert IcpRubric.default().weights == {
        "buyer": Decimal(".25"), "need": Decimal(".20"),
        "object": Decimal(".20"), "citation": Decimal(".20"),
        "persona": Decimal(".10"), "readability": Decimal(".05"),
    }


def test_unsupported_secondary_fails_even_above_ninety():
    result = evaluate_icp_persona(
        outputs={"saas-01": output_with_secondary("Banks", ["ev-valid"])},
        dataset=dataset_with_otherwise_perfect_gt(),
        dossiers=dossiers,
        split="holdout",
    )
    assert result.score > Decimal(".90")
    assert result.passed is False
    assert result.hard_failures == ("saas-01:unsupported_secondary:0",)
~~~

- [ ] **Step 2: Run RED**

~~~powershell
py -m pytest tests/company_enrichment/test_icp_persona_evaluator.py -q
~~~

- [ ] **Step 3: Implement deterministic component scoring**

Use normalized acceptable aliases for buyer. Use deterministic token F1 with threshold 0.80 for need, object, responsibility, and outcomes. Citation credit requires the output Evidence IDs to be retained by the dossier and approved for that ground-truth component. Observed personas require role match and Evidence; inferred personas require the inferred label, responsibility match, and based-on Evidence. Readability requires exact equality with render_segment after terminal punctuation normalization.

- [ ] **Step 4: Add development/holdout isolation tests**

~~~python
def test_holdout_report_omits_expected_answers_from_inventor_view():
    evaluation = evaluate_icp_persona(outputs, dataset, dossiers, split="holdout")
    assert set(evaluation.inventor_feedback) == {"buyer", "need", "object", "citation", "persona", "readability"}
    assert "saas-03" not in canonical_json(evaluation.inventor_feedback)
    assert "expected" not in canonical_json(evaluation.inventor_feedback)
~~~

- [ ] **Step 5: Run GREEN and commit**

~~~powershell
py -m pytest tests/company_enrichment/test_icp_persona_evaluator.py -q
git add scripts/company_enrichment/icp_persona_evaluator.py tests/company_enrichment/test_icp_persona_evaluator.py
git commit -m "feat: score icp outputs by evidence backed components"
~~~

### Task 5: Native Autoresearch Composition

**Files:**
- Create: scripts/company_enrichment/icp_persona_loop.py
- Create: scripts/company_enrichment_icp_loop.py
- Create: tests/company_enrichment/test_icp_persona_loop.py

**Interfaces:**
- Produces PromptCandidate, PromptCandidateQueue, build_icp_roles(...)->RoleRunners, IcpPersonaLoop.run(lineage_id, allow_paid=False, resume=False)->IcpLoopSummary.
- Calls AutoresearchOrchestrator.run(request) once per prompt attempt; no duplicate state machine.
- Owns one persistent company-enrichment BudgetLedger scope capped at USD 1.00 for the entire lineage; each native run receives only the remaining cap.

- [ ] **Step 1: Write native-role and isolation RED tests**

~~~python
def test_loop_uses_native_orchestrator(monkeypatch, loop):
    calls = []
    monkeypatch.setattr(AutoresearchOrchestrator, "run", lambda self, request: calls.append(request) or summary())
    loop.run("icp-v1")
    assert len(calls) == 1
    assert calls[0].approval_threshold == 0.90


def test_inventor_cannot_receive_holdout_answers(role_envelopes):
    inventor = role_envelopes[Role.INVENTOR].payload
    assert "execution_inputs" not in inventor
    assert "ground_truth" not in canonical_json(inventor)
~~~

- [ ] **Step 2: Run RED**

~~~powershell
py -m pytest tests/company_enrichment/test_icp_persona_loop.py -q
~~~

- [ ] **Step 3: Implement bounded candidate queue and roles**

Candidate queue:
1. baseline current prompt;
2. schema-first prompt emphasizing field contracts/evidence closure;
3. example-and-counterexample prompt emphasizing supported secondary segments and inferred-persona labels.

The in-bounds checker accepts only prompt/example/order changes and rejects contract, evidence, ground-truth, split, rubric, threshold, or model changes. Novelty checks candidate content hashes. Executor runs all ten frozen dossiers on gpt-5.6-luna using cached Evidence only. Evaluator writes full development and sealed holdout reports, then returns a bounded EvaluationResult without holdout answers.

The executor returns one research_orchestration.Evidence per company. Its excerpt is bounded canonical JSON containing company_id, prompt_id, structured output, cited Evidence IDs, resolved model, latency, and actual model cost; source_url is the first cited retained source URL. The evaluator validates and decodes these envelopes instead of reading mutable side state.

For Gate semantics, EvaluationResult.passed means the evaluation completed deterministically. A zero-tolerance claim therefore returns passed=True with score=0 and reason_code=unsupported_claim; quality remains encoded in score so the pure Gate can roll back or advance to the next bounded candidate instead of treating a valid evaluation artifact as a runner failure.

- [ ] **Step 4: Compose each attempt through RunRequest**

~~~python
request = RunRequest(
    "1.0", run_id, "Improve evidence-backed ICP/persona extraction.",
    ("cached_evidence_only", "frozen_ground_truth", "fixed_model", "no_source_purchase"),
    {"overall": float(best_holdout_score)},
    BudgetLimits(max_llm_calls=10, max_cost=float(remaining_cap), max_stages=5),
    0.90,
    execution_inputs=input_manifest,
    rubric="icp_persona_components_v1",
)
summary = AutoresearchOrchestrator(ArtifactStore(run_dir), roles).run(request)
~~~

Before each attempt, the outer driver reserves the OpenAIModelClient conservative estimate in one append-only lineage BudgetLedger capped at 1.00; after the executor artifact is durable it reconciles the sum of actual model costs. The native executor BudgetCharge uses the same estimate and the RunRequest receives only the remaining outer cap. Resume reuses the owned reservation and completed provider artifacts.

The outer driver advances to the next candidate only after native Gate action advance, reuses the best prompt on rollback, and stops on human_review_required, budget exhaustion, invalid artifacts, or candidate exhaustion. A supported but sub-threshold score continues; a zero-tolerance attempt scores zero and cannot win.

- [ ] **Step 5: Add resume/artifact tests**

Assert interruption after any role resumes at the first missing native stage, never repeats completed model cases, and preserves contract/dataset/split/rubric/model hashes. Assert no Candidate/Approval object is written by the loop.

- [ ] **Step 6: Verify and commit**

~~~powershell
py -m pytest tests/company_enrichment/test_icp_persona_loop.py tests/test_autoresearch_orchestrator.py tests/test_autoresearch_artifacts.py tests/test_autoresearch_gate.py -q
git add scripts/company_enrichment/icp_persona_loop.py scripts/company_enrichment_icp_loop.py tests/company_enrichment/test_icp_persona_loop.py
git commit -m "feat: run icp prompts through native autoresearch"
~~~

### Task 6: Baseline, Live Loop, and Human Review Pack

**Files:**
- Create ignored artifacts under runs/company-enrichment/icp-persona/<lineage>/
- Modify: docs/reports/company-corpus-live-run.md
- Create: docs/reports/icp-persona-loop-v1.md

**Interfaces:**
- Consumes Tasks 1-5 and the existing OPENAI_API_KEY loaded through the approved CLI environment.
- Produces append-only baseline/candidate outputs, component reports, native orchestration journals, and a human review summary.

- [ ] **Step 1: Run all mechanical gates**

~~~powershell
py -m pytest tests/company_enrichment/test_icp_persona_contracts.py tests/company_enrichment/test_icp_persona_ground_truth.py tests/company_enrichment/test_icp_persona_prompt_execution.py tests/company_enrichment/test_icp_persona_evaluator.py tests/company_enrichment/test_icp_persona_loop.py -q
py -m pytest tests/company_enrichment -q
py -m pytest tests -q
git diff --check
~~~

- [ ] **Step 2: Dry-run and inspect the frozen manifest**

~~~powershell
py scripts/company_enrichment_icp_loop.py --lineage icp-persona-v1 --dry-run
~~~

Expected JSON: ten SaaS IDs, exact six/four split, three candidate prompt hashes, fixed gpt-5.6-luna, cached Evidence only, source purchases zero, USD 1.00 aggregate cap, Approval false.

- [ ] **Step 3: Execute the approved paid loop through the CLI**

~~~powershell
lg run --profile prod -- py scripts/company_enrichment_icp_loop.py --lineage icp-persona-v1 --allow-paid
~~~

Stop immediately on a nonzero exit, any source purchase, prompt/GT hash drift, cost-cap breach, or unsupported-claim hard failure. Resume only with:

~~~powershell
lg run --profile prod -- py scripts/company_enrichment_icp_loop.py --lineage icp-persona-v1 --allow-paid --resume
~~~

- [ ] **Step 4: Verify loop outcomes**

Read-only audit must prove: exact ten-company coverage per completed candidate, no duplicate model case, no source purchase, exact requested/resolved model IDs, total cost <= USD 1.00, immutable input hashes, development and sealed holdout scores, and zero unsupported claims for any result eligible for review.

- [ ] **Step 5: Write the human review report**

Report the baseline and each candidate prompt hash, per-dimension development/holdout score, company-level failures, rendered primary/secondary segments, outcomes, persona labels, citations, cost, Gate decision, and why the winner is or is not eligible for human review. Do not mark Approval.

- [ ] **Step 6: Request independent review and commit**

Run a fresh reviewer against final artifacts. On PASS:

~~~powershell
git add prompts/company-enrichment/icp-persona-analysis.md benchmarks/icp-persona scripts/company_enrichment scripts/company_enrichment_icp_loop.py tests/company_enrichment docs/reports
git diff --cached --name-only
git commit -m "feat: graduate evidence backed icp persona prompt"
~~~

Exclude ignored run artifacts and docs/benchmarks-company-corpus-proposal.md.
