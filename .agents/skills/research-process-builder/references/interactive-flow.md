# Interactive Flow

> Reference: leadgrow-hq/company/methodology/interactive-skill-pattern.md

### Intake

1. **Ask:** "What do you want to research about companies?" (state the research goal in one sentence)
   **Default:** none — REQUIRED
   **Why:** The goal sentence drives pattern generation, scoring criteria, and output template. A vague goal produces vague patterns.

2. **Ask:** "What does a 'good result' look like? What should the output contain?" (3-5 bullet points)
   **Default:** none — REQUIRED
   **Why:** Defines the extraction spec for every search pattern. Without this, Claude can't score Quality — it doesn't know what "good" means for YOUR use case.

3. **Ask:** "Do you have ground truth examples? Companies where you already KNOW the answer, so we can validate accuracy."
   **Default:** no, but strongly encouraged. If yes, collect: company name, domain, and the known-good answer for each (3-5 companies ideal).
   **Why:** Ground truth turns the annealing loop from "does this look right?" to "did we find what we KNOW is there?" Without it, accuracy is subjective. With it, accuracy is measurable.

4. **Ask:** "What accuracy target?"
   **Default:** 90%
   **Why:** Determines when the iteration loop stops. Lower targets finish faster but produce less reliable processes.

5. **Ask:** "Do you have sample companies across size tiers? (enterprise / mid-market / startup)"
   **Default:** suggest 6-10 from existing client list + well-known companies, ensuring Tier 1 (known), Tier 2 (mid), and Tier 3 (obscure) are represented
   **Why:** Patterns that work for SpaceX break for startups. Testing across tiers is what makes the process reliable.

6. **Ask:** "Is this time-sensitive research? (e.g., recent news vs evergreen profiles)"
   **Default:** no (evergreen)
   **Why:** Time-sensitive goals add a Freshness (F) scoring dimension and require `{{current_year}}` variables in all patterns.

7. **Ask:** "Where will this process run? (Claude Code / Clay claygent / browser agent / custom)"
   **Default:** Claude Code
   **Why:** Output format differs — Clay claygents need specific field mappings, browser agents need URL patterns, Claude Code processes are freeform markdown.

### Gap Detection

| Check | Where to Look | If Missing | Severity |
|-------|--------------|------------|----------|
| Research goal is specific enough (not "learn about companies" or "find info") | User input analysis | Ask clarifying questions until goal is one-sentence specific with a clear target | BLOCKING |
| Desired output is concrete (not "useful info") | User's output description | Show examples from existing processes (e.g., find-competitors output spec), ask user to match that specificity | BLOCKING |
| Ground truth variables provided | User input | DEGRADED — can still build, but accuracy validation will be weaker. Suggest: "Can you name 3-5 companies where you already know the answer? This dramatically improves the process." | DEGRADED |
| Sample companies span 3 tiers | User input + existing client list | Auto-suggest from clients and well-known companies. Include at least one ambiguous-name company (Clay, Keep, Harvey). | Auto-resolve |
| Existing process already covers this goal | `research-process-builder/processes/` | Show the existing process, ask: "This already exists. Extend it, or build a new angle?" | BLOCKING |
| Ambiguous-name company included in samples | Sample company list | Add one automatically — ambiguous names stress-test disambiguation logic | Auto-resolve |

### Checkpoints

#### CHECKPOINT 1: Goal + Samples Confirmed

**Show:**
- Formatted research goal (one sentence)
- Desired output spec (bullet points)
- Sample companies organized by tier (Tier 1 / Tier 2 / Tier 3)
- Ground truth variables (if provided) — company name, domain, known answer
- Accuracy target
- Scoring dimensions: Quality + Consistency (+ Freshness if time-sensitive) (+ Accuracy if ground truth provided)

**Ask:** "Does this capture what you want? Any companies to swap or ground truth to add?"

**On Approve:** Proceed to pattern generation (Phase 2)
**On Reject:** Adjust goal, samples, or ground truth based on feedback

#### CHECKPOINT 2: Pattern Candidates Generated

**Show:**
15-20 generated search patterns grouped by type:
- Direct intent queries (e.g., `[name] competitors`)
- OR-combined queries (e.g., `[name] alternatives OR competitors OR "vs"`)
- Platform-specific (e.g., `site:g2.com [name]`)
- Natural language (e.g., `who competes with [name]`)
- Category-derived (e.g., `best [category] tools {{current_year}}`)
- Domain-anchored (e.g., `site:[domain] blog OR pricing`)

**Ask:** "Any patterns you know work well that I should add? Any you want to kill before testing?"

**On Approve:** Proceed to testing (Phase 3)
**On Reject:** Add/remove patterns, then proceed

#### CHECKPOINT 3: Test Results

**Show:**
Pattern-by-pattern results table:
| Pattern | Company Tested | Tier | Q | C | F? | A? | Classification |
|---------|---------------|------|---|---|----|----|---------------|
| `[name] competitors` | Clay | T2 | 5 | 4 | - | 5 | PRIMARY |
| `site:reddit.com [name]` | SpaceX | T1 | 1 | 1 | - | - | KILL |

Summary: X patterns tested, Y classified PRIMARY, Z classified KILL
Current accuracy: X%
If ground truth provided: "Found the known answer for 4/5 ground truth companies (80%)"

**Ask:** "Accuracy is X%. Target is Y%. Should I iterate with fix patterns, or is this good enough?"

**On Approve (if at target):** Proceed to assembly (Phase 6)
**On Approve (if below target):** Proceed to iteration (Phase 5)
**On Reject:** Adjust scoring or reclassify specific patterns

#### CHECKPOINT 4: Iteration Results (if needed)

**Show:**
- New fix patterns tested (targeting specific failure modes)
- Updated scores for revised patterns
- Accuracy delta: "Was X%, now Y% (improved Z%)"
- Remaining failure modes (if any)

**Ask:** "Accuracy now X%. Continue iterating, or assemble the process?"

**On Approve (assemble):** Proceed to Phase 6
**On Approve (iterate):** Generate more fix patterns, loop back

#### CHECKPOINT 5: Process File Preview

**Show:**
Complete process file structure:
- Step sequence (ordered by consistency, then quality)
- Conditional steps (Tier 1-2 only, Tier 3 fallbacks)
- Kill list with reasons
- Output template (what the agent should produce)
- Stop conditions per step
- Ground truth accuracy (if applicable): "Process found the known answer for X/Y ground truth companies"

**Ask:** "This is the final process. Save to `processes/[name].md`?"

**On Approve:** Save and output final process file
**On Reject:** Adjust steps, ordering, or output template

### Ground Truth Training Pattern

When ground truth variables are provided, the build loop gains a measurable accuracy dimension:

**Phase 3 Enhancement:** After running each pattern against a ground truth company, compare extracted results to the known answer. Score an additional dimension:
- **Accuracy (A):** 5 = found exact known answer, 3 = found partial/adjacent info, 1 = missed entirely

**Phase 4 Enhancement:** Classification factors in A score — PRIMARY requires A >= 4 in addition to Q >= 4, C >= 4

**Phase 5 Enhancement:** Iteration specifically targets ground truth misses — "Pattern X missed the known answer for Company Y because [failure mode]. Fix pattern: [new pattern targeting that failure]"

**Final Metric:** `ground_truth_accuracy = (companies where process found known answer) / (total ground truth companies) * 100`

This is the key differentiator. Without ground truth, accuracy is "Claude thinks these results look good." With ground truth, accuracy is "the process found 4/5 things we KNOW exist." The latter is what makes a process worth shipping.
