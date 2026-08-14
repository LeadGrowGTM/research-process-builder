# Goal Output Contract Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Make an approved, evidence-aware Goal Output Contract mandatory before the canonical research-builder skill scaffolds or tests a prompt-backed Research Flow.

**Architecture:** Add a focused contract Module beside the research-builder scripts. The scaffold embeds a validated contract and derives its output template from the schema; the tester validates the same block before external calls and scores structured outputs against per-company ground truth.

**Tech Stack:** Python 3, PyYAML, argparse, pytest, Markdown/YAML skill assets.

## Global Constraints

- Canonical source: C:/Users/mitch/Everything_CC/pipelines/gtm-orchestrator/.claude/skills/research-builder/.
- Do not touch or stage the dirty prompt-creator lineage or other unrelated GTM files.
- Required keys: schema, example, evidence_rules, unknown_behavior, rendering, scoring, success.
- Weights total exactly 1.0; minimum score is at least 0.90; zero-tolerance failures are non-empty.
- No scaffold, dry run, query, or model call proceeds without a valid contract.
- Unsupported fields are omitted or explicitly unknown; generated files remain no-overwrite.

---

## File Structure

- Create .claude/skills/research-builder/scripts/goal_output_contract.py for immutable loading, validation, rendering, and scoring.
- Create .claude/skills/research-builder/tests/test_goal_output_contract.py and fixture YAML.
- Modify scaffold_process.py to require and embed a contract.
- Modify test_process.py to fail closed and score structured output.
- Modify SKILL.md and REFERENCE.md to make the gate operator-visible.

### Task 1: Contract Module

**Files:**
- Create: .claude/skills/research-builder/scripts/goal_output_contract.py
- Create: .claude/skills/research-builder/tests/test_goal_output_contract.py

**Interfaces:**
- Produces: GoalOutputContract, ContractScore, load_goal_output_contract(path), parse_goal_output_contract(text), render_goal_output_contract(contract), render_output_template(contract), score_contract_output(actual, expected, contract, retained_evidence_ids).

- [ ] **Step 1: Write failing contract tests**

~~~python
def test_contract_requires_all_sections(tmp_path):
    path = tmp_path / "contract.yaml"
    path.write_text("schema: {}\n", encoding="utf-8")
    with pytest.raises(ContractError, match="missing keys"):
        load_goal_output_contract(path)


def test_contract_rejects_bad_weights_and_threshold(tmp_path):
    path = write_contract(tmp_path, scoring={"primary_icp.buyer": 0.8}, minimum=0.89)
    with pytest.raises(ContractError, match="weights must total 1.0"):
        load_goal_output_contract(path)
~~~

- [ ] **Step 2: Run RED**

~~~powershell
uv run pytest .claude/skills/research-builder/tests/test_goal_output_contract.py -q
~~~

Expected: import/collection failure because the Module does not exist.

- [ ] **Step 3: Implement immutable loading and validation**

~~~python
@dataclass(frozen=True, slots=True)
class GoalOutputContract:
    schema: Mapping[str, object]
    example: Mapping[str, object]
    evidence_rules: tuple[str, ...]
    unknown_behavior: tuple[str, ...]
    rendering: Mapping[str, object]
    scoring: Mapping[str, Decimal]
    minimum_score: Decimal
    zero_tolerance: tuple[str, ...]


def load_goal_output_contract(path: Path) -> GoalOutputContract:
    return contract_from_mapping(yaml.safe_load(path.read_text(encoding="utf-8")))
~~~

Reject missing/unknown keys, unresolved marker text, schema/example drift, Boolean-as-number, unscored schema paths, weights other than Decimal("1.0"), a minimum below Decimal("0.90"), and empty evidence/unknown/zero-tolerance rules.

- [ ] **Step 4: Add scorer tests and implementation**

~~~python
@dataclass(frozen=True, slots=True)
class ContractScore:
    field_scores: Mapping[str, Decimal]
    weighted_total: Decimal
    invalid_evidence_ids: tuple[str, ...]
    zero_tolerance_failures: tuple[str, ...]
    passed: bool


def render_output_template(contract: GoalOutputContract) -> str:
    return yaml.safe_dump(schema_template(contract.schema), sort_keys=False).rstrip()
~~~

Normalize strings with Unicode NFKC, collapsed whitespace, and casefold. Unsupported evidence IDs are a hard failure. An allowed unknown is correct only when expected ground truth also marks that path unknown.

- [ ] **Step 5: Run GREEN and commit**

~~~powershell
uv run pytest .claude/skills/research-builder/tests/test_goal_output_contract.py -q
git add .claude/skills/research-builder/scripts/goal_output_contract.py .claude/skills/research-builder/tests/test_goal_output_contract.py
git commit -m "feat: validate research goal output contracts"
~~~

### Task 2: Fail-Closed Scaffold

**Files:**
- Modify: .claude/skills/research-builder/scripts/scaffold_process.py:32-127
- Modify: .claude/skills/research-builder/tests/test_goal_output_contract.py

**Interfaces:**
- Consumes Task 1 APIs.
- Produces build_process_md(name, goal, keywords, contract, extra_inputs) -> str and main(argv: list[str] | None = None) -> int; CLI requires --output-contract PATH.

- [ ] **Step 1: Write scaffold RED tests**

~~~python
def test_scaffold_requires_contract_even_for_dry_run():
    with pytest.raises(SystemExit):
        scaffold_main(["--name", "buyers", "--goal", "Find buyers", "--dry-run"])


def test_scaffold_embeds_contract_before_steps(valid_contract):
    content = build_process_md(
        "find-buyers", "Find buyers", ["buyer"],
        load_goal_output_contract(valid_contract), [],
    )
    assert content.index("## goal output contract") < content.index("## steps")
    assert "primary_icp:" in content.split("## output", 1)[1]
~~~

- [ ] **Step 2: Run RED**

~~~powershell
uv run pytest .claude/skills/research-builder/tests/test_goal_output_contract.py -q
~~~

Expected: failures expose the old output-fields-only signature.

- [ ] **Step 3: Require, validate, and embed the contract**

~~~python
parser.add_argument("--output-contract", type=Path, required=True)
contract = load_goal_output_contract(args.output_contract)
content = build_process_md(args.name, args.goal, keywords, contract, args.extra_inputs)
~~~

Place the contract after the goal and before inputs/steps. Derive output from the schema and remove the implicit source field. Retain --output-fields only as a deprecated exact-schema assertion; it cannot authorize scaffolding alone.

- [ ] **Step 4: Verify and commit**

~~~powershell
uv run pytest .claude/skills/research-builder/tests/test_goal_output_contract.py -q
uv run python .claude/skills/research-builder/scripts/scaffold_process.py --name contract-smoke --goal "Find supported buyer evidence" --output-contract .claude/skills/research-builder/tests/fixtures/valid-contract.yaml --dry-run
git add .claude/skills/research-builder/scripts/scaffold_process.py .claude/skills/research-builder/tests
git commit -m "feat: gate research scaffolds on target output"
~~~

### Task 3: Contract-Aware Tester

**Files:**
- Modify: .claude/skills/research-builder/scripts/test_process.py:56-180,184-386
- Create: .claude/skills/research-builder/tests/fixtures/valid-contract.yaml
- Create: .claude/skills/research-builder/tests/fixtures/ground-truth.yaml

**Interfaces:**
- Consumes embedded contract, --ground-truth PATH, retained Evidence IDs, and Task 1 scorer.
- Produces parse_process(path) -> {steps, inputs, contract, output_schema}, main(argv: list[str] | None = None) -> int, and field-level scores.

- [ ] **Step 1: Write preflight and schema-drift RED tests**

~~~python
def test_tester_stops_before_external_call_without_contract(monkeypatch, process_without_contract):
    monkeypatch.setattr(module, "run_searches_batch", pytest.fail)
    assert module.main([
        "--process-path", str(process_without_contract),
        "--ground-truth", "gt.yaml",
    ]) == 2


def test_parse_process_rejects_output_schema_drift(valid_process):
    valid_process.write_text(
        valid_process.read_text().replace("buyer:", "account:"),
        encoding="utf-8",
    )
    with pytest.raises(ContractError, match="output schema"):
        parse_process(valid_process)
~~~

- [ ] **Step 2: Run RED**

~~~powershell
uv run pytest .claude/skills/research-builder/tests/test_goal_output_contract.py -q
~~~

- [ ] **Step 3: Add typed ground-truth loading**

~~~python
def load_ground_truth(path: Path) -> dict[str, Mapping[str, object]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if set(data) != {"version", "companies"}:
        raise ContractError("ground truth must contain version and companies")
    return dict(data["companies"])
~~~

Each company must contain expected, unknown, and retained_evidence_ids. Reject missing requested companies, unexpected paths, or empty Evidence closure before query construction.

- [ ] **Step 4: Replace label-presence scoring**

~~~python
score = score_contract_output(
    actual=parse_structured_output(output_text),
    expected=ground_truth[company]["expected"],
    contract=sections["contract"],
    retained_evidence_ids=set(ground_truth[company]["retained_evidence_ids"]),
)
~~~

Print per-field correctness, invalid citations, zero-tolerance failures, and weighted total. Never update the validation header on unsupported claims.

- [ ] **Step 5: Verify and commit**

~~~powershell
uv run pytest .claude/skills/research-builder/tests/test_goal_output_contract.py -q
uv run pytest .claude/skills/prompt-creator/tests/test_goal_output_contract.py -q
git add .claude/skills/research-builder/scripts/test_process.py .claude/skills/research-builder/tests
git commit -m "feat: score research outputs against declared goals"
~~~

### Task 4: Main Skill Documentation and Forward Test

**Files:**
- Modify: .claude/skills/research-builder/SKILL.md:20-48
- Modify: .claude/skills/research-builder/REFERENCE.md:3-53,108-133

**Interfaces:**
- Documents Tasks 1-3 and routes full annealing to research-process-builder.

- [ ] **Step 1: Add the mandatory phase to SKILL.md**

~~~markdown
### Phase 2: Goal Output Contract — mandatory gate

Before creating a prompt or search pattern, define and obtain acceptance for the schema, one realistic good output, evidence and unknown rules, deterministic rendering, weighted scoring at >= 0.90, and zero-tolerance failures. If the example cannot be approved, return to Intake. Do not scaffold.
~~~

Renumber later phases. State that programmed validation creates only a Candidate; explicit human review is required for Approval.

Normalize SKILL.md frontmatter to the validator's allowed top-level keys: name and description. Move version, maturity, and trigger details into a metadata mapping or the document body so quick_validate.py passes without losing operator guidance.

- [ ] **Step 2: Add complete examples and commands to REFERENCE.md**

~~~yaml
schema:
  primary_icp:
    buyer: string
    need: string
    object: string
    evidence_ids: list[string]
example:
  primary_icp:
    buyer: Marketing agencies
    need: automated reporting
    object: multi-channel client campaigns
    evidence_ids: [ev-001]
~~~

Document observed versus inferred personas, supported omission, frozen development/holdout data, and exact scaffold/test commands.

- [ ] **Step 3: Validate and forward-test**

~~~powershell
py -m pytest .claude/skills/research-builder/tests/test_goal_output_contract.py -q
py -m pytest .claude/skills/prompt-creator/tests/test_goal_output_contract.py -q
py C:\Users\mitch\.codex\skills\.system\skill-creator\scripts\quick_validate.py .claude/skills/research-builder
git diff --check
git status --short
~~~

Invoke the revised skill on “Build a process that identifies agencies needing automated reporting.” Confirm it stops at the contract until accepted, then rejects an unsupported secondary segment.

- [ ] **Step 4: Commit only research-builder files**

~~~powershell
git add .claude/skills/research-builder/SKILL.md .claude/skills/research-builder/REFERENCE.md .claude/skills/research-builder/scripts .claude/skills/research-builder/tests
git diff --cached --name-only
git commit -m "docs: require goal output before research prompts"
~~~

Expected staged files are confined to .claude/skills/research-builder/.
