import { beforeEach, describe, expect, it, vi } from "vitest";
import { evaluateCandidate, simulateProduction } from "./discovery-eval.js";
import { fetchUrl } from "../src/pipeline/scrape.js";
import { extractWithOpenAI } from "../src/pipeline/openai.js";
import { scoreAndFilter } from "../src/pipeline/filters.js";
import { SERIES_A_CONFIG } from "../src/pipeline/round-configs.js";
import type { ExtractedData } from "../src/pipeline/types.js";

vi.mock("../src/pipeline/scrape.js", () => ({ fetchUrl: vi.fn() }));
vi.mock("../src/pipeline/openai.js", () => ({ extractWithOpenAI: vi.fn(), validateDomainSemantic: vi.fn() }));
vi.mock("../src/pipeline/supabase.js", () => ({ getRecentCompanyNames: vi.fn(), isSupabaseConfigured: vi.fn(), checkTable: vi.fn(), pushToSupabase: vi.fn(), FundingWriteError: class extends Error {} }));
vi.mock("../src/pipeline/rapid-search.js", () => ({ webSearch: vi.fn() }));

function candidate(name = "Acme", date = "2026/10/05") {
  return scoreAndFilter([{ title: `${name} raises $10M Series A led by Example Capital`, source_url: `https://finsmes.com/${date}/${name}`,
    source_domain: "finsmes.com", company_name_raw: "", amount_raw: "", round_type_raw: "", snippet: "", query_source: "q1" }], SERIES_A_CONFIG).companies[0];
}

beforeEach(() => { vi.resetAllMocks(); });

describe("discovery production simulation", () => {
  it("defaults the production cap to 100 and honors an override", async () => {
    vi.mocked(fetchUrl).mockResolvedValue(null);
    const candidates = Array.from({ length: 101 }, (_, i) => candidate(`Acme${i}`));
    const rows = await Promise.all(candidates.map((c) => evaluateCandidate(c, SERIES_A_CONFIG)));
    const sim = simulateProduction(candidates, rows, "2026-10-05");
    expect(sim.filter((r) => r.stage === "kept")).toHaveLength(100);
    expect(sim[100].stage).toBe("cap");
    expect(simulateProduction(candidates, rows, "2026-10-05", 2).filter((r) => r.stage === "kept")).toHaveLength(2);
  });

  it.each(["fetch_failed", "extract_failed"])("retains %s fallback rows subject to the production gates", async (outcome) => {
    vi.mocked(fetchUrl).mockResolvedValue(outcome === "fetch_failed" ? null : "article");
    vi.mocked(extractWithOpenAI).mockResolvedValue(null);
    const candidates = [candidate(), candidate("Oldco", "2025/01/01"), { ...candidate("Lowco"), confidence: "low" as const }];
    const rows = await Promise.all(candidates.map((c) => evaluateCandidate(c, SERIES_A_CONFIG)));
    expect(rows.map((r) => r.outcome)).toEqual([outcome, outcome, outcome]);
    expect(simulateProduction(candidates, rows, "2026-10-05").map((r) => r.stage)).toEqual(["kept", "stale"]);
  });

  it("skips an early LOW candidate so the next valid candidate uses the enrichment slot", async () => {
    vi.mocked(fetchUrl).mockResolvedValue(null);
    const low = { ...candidate("Lowco"), confidence: "low" as const };
    const valid = candidate("Validco");
    const rows = await Promise.all([low, valid].map((c) => evaluateCandidate(c, SERIES_A_CONFIG)));

    expect(simulateProduction([low, valid], rows, "2026-10-05", 1)).toEqual([
      expect.objectContaining({ name: "Validco", stage: "kept", enrichmentOutcome: "fetch_failed" }),
    ]);
  });

  it("retries alternate article sources and still rejects an explicit wrong-round sentinel", async () => {
    const c = candidate();
    const alternate = "https://other.test/2026/10/04/acme";
    c.sources.push({ ...c.sources[0], url: alternate });
    vi.mocked(fetchUrl).mockResolvedValueOnce(null).mockResolvedValueOnce("alternate article");
    vi.mocked(extractWithOpenAI).mockResolvedValue({ company_name: SERIES_A_CONFIG.notRoundSentinel } as ExtractedData);
    const row = await evaluateCandidate(c, SERIES_A_CONFIG);
    expect(vi.mocked(fetchUrl).mock.calls.map(([url]) => url)).toEqual([c.best_source_url, alternate]);
    expect(row.url).toBe(alternate);
    expect(row.candidateUrl).toBe(c.best_source_url);
    expect(simulateProduction([c], [row], "2026-10-05")[0].stage).toBe("not_this_round");
  });
});
