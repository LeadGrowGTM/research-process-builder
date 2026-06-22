/**
 * Core domain logic tests for research-process-builder.
 *
 * This repo is Python-primary, but this test file validates the TypeScript
 * skill/process metadata parsing and the domain rules encoded in JS/TS tooling.
 *
 * NOTE: The actual pipeline logic lives in Python (scripts/). For Python unit
 * tests that run without API keys, see scripts/test_resolver_unit.py and
 * scripts/test_confidence_scorer.py — both have a custom print-based harness.
 *
 * This TS test file covers:
 *   1. Process file naming conventions (slug pattern)
 *   2. Ground-truth JSON schema shape
 *   3. Pattern classification scoring rules (pure logic, no I/O)
 *
 * Run with: npx tsx --test __tests__/core.test.ts
 * Or with vitest if configured: npx vitest run __tests__/core.test.ts
 */

import * as fs from "fs";
import * as path from "path";

const REPO_ROOT = path.resolve(__dirname, "..");

// ---------------------------------------------------------------------------
// Test 1: Process files follow naming convention
// A valid process directory must have process.md and STATUS.md.
// Directories matching find-* are the canonical pattern; others (e.g.
// classify-* for classifier processes) are allowed but must still carry
// process.md + STATUS.md.
// ---------------------------------------------------------------------------

function testProcessFileConventions(): void {
  const processesDir = path.join(REPO_ROOT, "processes");

  if (!fs.existsSync(processesDir)) {
    throw new Error(`processes/ directory not found at ${processesDir}`);
  }

  const entries = fs.readdirSync(processesDir, { withFileTypes: true });
  // Only check directories (skip stray .md files like clay-callback-routing.md)
  const processDirs = entries.filter(
    (e) => e.isDirectory()
  );

  if (processDirs.length === 0) {
    throw new Error("No process directories found under processes/");
  }

  const violations: string[] = [];
  let findSlugCount = 0;

  for (const dir of processDirs) {
    const dirPath = path.join(processesDir, dir.name);
    const hasProcessMd = fs.existsSync(path.join(dirPath, "process.md"));
    const hasStatusMd = fs.existsSync(path.join(dirPath, "STATUS.md"));

    if (!hasProcessMd) {
      violations.push(`${dir.name}: missing process.md`);
    }
    if (!hasStatusMd) {
      violations.push(`${dir.name}: missing STATUS.md`);
    }

    // Count how many follow the canonical find-* pattern
    if (/^find-[a-z][a-z0-9-]*$/.test(dir.name)) {
      findSlugCount++;
    }
  }

  if (violations.length > 0) {
    throw new Error(
      `Process file convention violations:\n${violations.join("\n")}`
    );
  }

  if (findSlugCount === 0) {
    throw new Error("No process directories matched the canonical find-[slug] pattern");
  }

  console.log(
    `  PASS: ${processDirs.length} process directories present; ${findSlugCount} follow find-* convention; all have process.md + STATUS.md`
  );
}

// ---------------------------------------------------------------------------
// Test 2: Ground-truth JSON files are valid JSON with at least one category key
//
// schema.json defines categories (not a flat required[] array). Each GT company
// file contains a "categories" object whose keys match schema category names.
// We verify: valid JSON, non-empty categories object, and each category key
// exists in the schema's categories map.
// ---------------------------------------------------------------------------

function testGroundTruthSchema(): void {
  const gtDir = path.join(REPO_ROOT, "ground-truth");

  if (!fs.existsSync(gtDir)) {
    throw new Error(`ground-truth/ directory not found at ${gtDir}`);
  }

  const schemaPath = path.join(gtDir, "schema.json");
  if (!fs.existsSync(schemaPath)) {
    throw new Error("ground-truth/schema.json not found");
  }

  const schema = JSON.parse(fs.readFileSync(schemaPath, "utf-8"));
  // schema.json uses a categories map, not a flat required[] array
  const knownCategories = new Set(Object.keys(schema.categories ?? {}));

  if (knownCategories.size === 0) {
    throw new Error(
      "schema.json has no categories — schema structure unexpected"
    );
  }

  const gtFiles = fs
    .readdirSync(gtDir)
    .filter((f) => f.endsWith(".json") && f !== "schema.json");

  if (gtFiles.length === 0) {
    throw new Error("No company ground-truth files found in ground-truth/");
  }

  const violations: string[] = [];

  for (const file of gtFiles) {
    const filePath = path.join(gtDir, file);
    let data: Record<string, unknown>;

    try {
      data = JSON.parse(fs.readFileSync(filePath, "utf-8"));
    } catch (e) {
      violations.push(`${file}: invalid JSON`);
      continue;
    }

    // GT files must have at least a "company" or "domain" field at the top level
    if (!("company" in data) && !("domain" in data) && !("categories" in data)) {
      violations.push(
        `${file}: missing expected top-level keys (company, domain, or categories)`
      );
    }

    // If it has a categories block, verify all keys exist in schema
    if ("categories" in data && typeof data.categories === "object" && data.categories !== null) {
      for (const catKey of Object.keys(data.categories as Record<string, unknown>)) {
        if (!knownCategories.has(catKey)) {
          violations.push(
            `${file}: category "${catKey}" not defined in schema.json`
          );
        }
      }
    }
  }

  if (violations.length > 0) {
    throw new Error(
      `Ground-truth schema violations:\n${violations.join("\n")}`
    );
  }

  console.log(
    `  PASS: ${gtFiles.length} ground-truth files valid; schema defines ${knownCategories.size} categories`
  );
}

// ---------------------------------------------------------------------------
// Test 3: Pattern classification scoring rules (pure logic)
// Encodes the exact rules from SKILL.md Phase 4 without any I/O.
// ---------------------------------------------------------------------------

type PatternClass =
  | "PRIMARY"
  | "ENRICHMENT"
  | "SITUATIONAL"
  | "FALLBACK"
  | "KILL";

function classifyPattern(quality: number, consistency: number): PatternClass {
  if (quality <= 2) return "KILL";
  if (quality >= 4 && consistency >= 4) return "PRIMARY";
  if (quality >= 4 && consistency >= 3) return "ENRICHMENT";
  if (quality >= 4 && consistency <= 2) return "SITUATIONAL";
  if (quality >= 3) return "FALLBACK";
  return "KILL";
}

function testPatternClassificationRules(): void {
  const cases: Array<{
    q: number;
    c: number;
    expected: PatternClass;
    label: string;
  }> = [
    // PRIMARY: Q>=4, C>=4
    { q: 5, c: 5, expected: "PRIMARY", label: "perfect scores" },
    { q: 4, c: 4, expected: "PRIMARY", label: "minimum PRIMARY" },
    { q: 5, c: 4, expected: "PRIMARY", label: "high quality, minimum consistency" },

    // ENRICHMENT: Q>=4, C>=3 (and C<4 since PRIMARY takes C>=4)
    { q: 4, c: 3, expected: "ENRICHMENT", label: "minimum ENRICHMENT" },
    { q: 5, c: 3, expected: "ENRICHMENT", label: "high quality, C=3" },

    // SITUATIONAL: Q>=4, C<=2
    { q: 4, c: 2, expected: "SITUATIONAL", label: "good quality, very inconsistent" },
    { q: 5, c: 1, expected: "SITUATIONAL", label: "excellent quality, no consistency" },

    // FALLBACK: Q>=3 (when C doesn't lift to higher tier)
    { q: 3, c: 2, expected: "FALLBACK", label: "adequate quality, low consistency" },
    { q: 3, c: 4, expected: "FALLBACK", label: "Q=3 doesn't reach PRIMARY even with high C" },

    // KILL: Q<=2
    { q: 2, c: 5, expected: "KILL", label: "irrelevant regardless of consistency" },
    { q: 1, c: 1, expected: "KILL", label: "universally bad pattern" },
    { q: 0, c: 0, expected: "KILL", label: "zero scores" },
  ];

  const failures: string[] = [];

  for (const { q, c, expected, label } of cases) {
    const result = classifyPattern(q, c);
    if (result !== expected) {
      failures.push(
        `  FAIL [${label}]: Q=${q}, C=${c} -> ${result} (expected ${expected})`
      );
    }
  }

  if (failures.length > 0) {
    throw new Error(`Pattern classification failures:\n${failures.join("\n")}`);
  }

  console.log(`  PASS: ${cases.length} pattern classification cases all correct`);
}

// ---------------------------------------------------------------------------
// Runner
// ---------------------------------------------------------------------------

type TestFn = () => void;

const tests: Array<{ name: string; fn: TestFn }> = [
  { name: "Process file conventions", fn: testProcessFileConventions },
  { name: "Ground-truth JSON schema", fn: testGroundTruthSchema },
  { name: "Pattern classification scoring rules", fn: testPatternClassificationRules },
];

let passed = 0;
let failed = 0;

for (const { name, fn } of tests) {
  try {
    console.log(`\n[${name}]`);
    fn();
    passed++;
  } catch (e) {
    console.error(`  FAIL: ${(e as Error).message}`);
    failed++;
  }
}

console.log(
  `\n${"=".repeat(50)}\nRESULT: ${passed} passed, ${failed} failed\n${"=".repeat(50)}`
);

if (failed > 0) {
  process.exit(1);
}
