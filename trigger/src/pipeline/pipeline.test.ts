import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { isStaleRound, runFundingPipeline } from "./pipeline.js";
import { SERIES_A_CONFIG } from "./round-configs.js";
import type { ExtractedData, PipelineConfig, RawResult } from "./types.js";
import { runDiscovery } from "./serper.js";
import { extractWithOpenAI } from "./openai.js";
import { pushToSupabase, FundingWriteError } from "./supabase.js";
import { pushToWebhook } from "./webhook.js";
import { day0BlitzEnrich } from "./enrich-company.js";
import { logger } from "@trigger.dev/sdk";

vi.mock("@trigger.dev/sdk", () => ({ logger: { info: vi.fn(), warn: vi.fn() } }));

vi.mock("./serper.js", () => ({ runDiscovery: vi.fn() }));
vi.mock("./firecrawl.js", () => ({ fetchUrl: vi.fn(async (url: string) => `Funding news. Visit ${new URL(url).pathname.split("/").pop()}.com to learn more.`) }));
vi.mock("./openai.js", () => ({
  extractWithOpenAI: vi.fn(),
  validateDomainSemantic: vi.fn(async () => ({ status: "Correct" })),
}));
vi.mock("./domain-lookup.js", () => ({
  lookupDomainMultiSignal: vi.fn(async () => ({ domain: "not_found", source: "not_found" })),
  isDomainBlocked: vi.fn(() => false),
}));
vi.mock("./supabase.js", async () => ({
  ...await vi.importActual<typeof import("./supabase.js")>("./supabase.js"),
  isSupabaseConfigured: vi.fn(() => true),
  checkTable: vi.fn(async () => true),
  pushToSupabase: vi.fn(async (rows: unknown[]) => rows.length),
  getRecentCompanyNames: vi.fn(async () => new Set<string>()),
}));
vi.mock("./webhook.js", () => ({ pushToWebhook: vi.fn(async (rows: unknown[]) => rows.length) }));
vi.mock("./enrich-company.js", () => ({ day0BlitzEnrich: vi.fn(async () => undefined) }));

const RUN_DATE = "2026-10-05";

function raw(name: string): RawResult {
  return {
    company_name_raw: "", amount_raw: "", round_type_raw: "", query_source: "q1", snippet: "",
    title: `${name} raises $10M Series A led by Example Capital`,
    source_url: `https://www.finsmes.com/2026/10/05/${name.toLowerCase()}`,
    source_domain: "www.finsmes.com",
  };
}

function extracted(name: string, fundingDate: string | null): ExtractedData {
  return {
    company_name: name, company_domain: null, amount_raised: "$10M", round_type: "Series A", lead_investors: "Example Capital",
    round_reasoning: null, industry: null, location: null, funding_date: fundingDate,
  };
}

function config(overrides: Partial<PipelineConfig> = {}): PipelineConfig {
  return {
    roundConfig: SERIES_A_CONFIG, pipelineId: "test", tbs: "qdr:d", date: RUN_DATE,
    skipEnrich: false, maxEnrich: 100, dryRun: true, ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(pushToWebhook).mockImplementation(async (rows) => rows.length);
  vi.mocked(pushToSupabase).mockImplementation(async (rows) => rows.length);
  vi.stubEnv("CLAY_SERIES_A_WEBHOOK_URL", "https://clay.test/hook");
  vi.stubEnv("CLAY_SERIES_A_WEBHOOK_TOKEN", "test-token");
});
afterEach(() => { vi.useRealTimers(); vi.unstubAllEnvs(); });

describe("isStaleRound", () => {
  it("drops rounds dated more than 45 days before the run", () => {
    expect(isStaleRound("2015-12-10", "2026-10-05")).toBe(true);
    expect(isStaleRound("2026-06-23", "2026-10-05")).toBe(true);
  });

  it("keeps recent, undated and unparseable dates", () => {
    expect(isStaleRound("2026-09-01", "2026-10-05")).toBe(false);
    expect(isStaleRound("2026-10-05", "2026-10-05")).toBe(false);
    expect(isStaleRound(null, "2026-10-05")).toBe(false);
    expect(isStaleRound("October 2026", "2026-10-05")).toBe(false);
  });
});

describe("runFundingPipeline freshness gate", () => {
  it("drops an old round reported in a freshly dated article", async () => {
    vi.mocked(runDiscovery).mockResolvedValue([raw("Acme")]);
    vi.mocked(extractWithOpenAI).mockResolvedValue(extracted("Acme", "2015-12-10"));
    const result = await runFundingPipeline(config());
    expect(result.companyCount).toBe(0);
  });

  it("falls back to the article URL date when the round date is missing", async () => {
    vi.mocked(runDiscovery).mockResolvedValue([raw("Acme")]);
    vi.mocked(extractWithOpenAI).mockResolvedValue(extracted("Acme", null));
    const result = await runFundingPipeline(config());
    expect(result.companies.map((c) => [c.company_name, c.funding_date])).toEqual([["Acme", "2026-10-05"]]);
  });
});

describe("runFundingPipeline delivery deadline", () => {
  it("only writes Clay-acknowledged rows and reports partial Supabase counts", async () => {
    vi.mocked(runDiscovery).mockResolvedValue(["Alpha", "Bravo", "Charlie"].map(raw));
    vi.mocked(extractWithOpenAI).mockImplementation(async (_text, name) => extracted(name, RUN_DATE));
    vi.mocked(pushToWebhook).mockImplementation(async (rows) => rows[0].company_name === "Bravo" ? 0 : 1);
    vi.mocked(pushToSupabase).mockResolvedValueOnce(1);

    await runFundingPipeline(config({ dryRun: false }));

    expect(vi.mocked(pushToSupabase).mock.calls[0][0].map((r) => r.company_name)).toEqual(["Alpha", "Charlie"]);
    expect(pushToSupabase).toHaveBeenCalledTimes(1);
    expect(vi.mocked(pushToSupabase).mock.invocationCallOrder[0]).toBeGreaterThan(Math.max(...vi.mocked(pushToWebhook).mock.invocationCallOrder));
    expect(logger.warn).toHaveBeenCalledWith(expect.stringContaining("Clay rejected"), { names: ["Bravo"] });
    expect(logger.info).toHaveBeenCalledWith(expect.stringContaining("Webhook: 2/3 sent; Supabase: 1/2 upserted"));
    expect(vi.mocked(day0BlitzEnrich).mock.calls[0][1].map((t) => t.companyName)).toEqual(["Alpha", "Charlie"]);
  });

  it("returns by the deadline even when Supabase never resolves", async () => {
    vi.useFakeTimers();
    vi.mocked(runDiscovery).mockResolvedValue([raw("Alpha")]);
    vi.mocked(extractWithOpenAI).mockResolvedValue(extracted("Alpha", RUN_DATE));
    vi.mocked(pushToSupabase).mockImplementationOnce(() => new Promise(() => {}));
    const deadlineAt = Date.now() + 6 * 60_000;
    let finished = false;
    const run = runFundingPipeline(config({ dryRun: false, deadlineAt })).then(() => { finished = true; });

    await vi.advanceTimersByTimeAsync(0);
    expect(pushToSupabase).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(6 * 60_000);
    expect(finished).toBe(true);
    await run;
    expect(day0BlitzEnrich).not.toHaveBeenCalled();
    expect(vi.mocked(pushToSupabase).mock.calls[0][3]?.aborted).toBe(true);
  });

  it("logs confirmed partial writes when Supabase fails after its first row", async () => {
    vi.mocked(runDiscovery).mockResolvedValue(["Alpha", "Bravo"].map(raw));
    vi.mocked(extractWithOpenAI).mockImplementation(async (_text, name) => extracted(name, RUN_DATE));
    vi.mocked(pushToSupabase).mockRejectedValueOnce(new FundingWriteError(1));
    await expect(runFundingPipeline(config({ dryRun: false }))).rejects.toThrow("funding write failed");
    expect(logger.info).toHaveBeenCalledWith(expect.stringContaining("Webhook: 2/2 sent; Supabase: 1/2 upserted"));
  });

  it("keeps confirmed partial Supabase counts when the deadline aborts the next row", async () => {
    vi.useFakeTimers();
    vi.mocked(runDiscovery).mockResolvedValue(["Alpha", "Bravo"].map(raw));
    vi.mocked(extractWithOpenAI).mockImplementation(async (_text, name) => extracted(name, RUN_DATE));
    vi.mocked(pushToSupabase).mockImplementationOnce(async (_rows, _date, _table, signal, onUpsert) => {
      onUpsert?.(1);
      return new Promise((_resolve, reject) => signal?.addEventListener("abort", () => reject(new FundingWriteError(1)), { once: true }));
    });
    const run = runFundingPipeline(config({ dryRun: false, deadlineAt: Date.now() + 6 * 60_000 }));
    await vi.advanceTimersByTimeAsync(6 * 60_000);
    await run;
    expect(logger.info).toHaveBeenCalledWith(expect.stringContaining("Webhook: 2/2 sent; Supabase: 1/2 upserted"));
    expect(day0BlitzEnrich).not.toHaveBeenCalled();
  });

  it("does not start Supabase or Blitz without their operation budgets", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    const start = Date.now();
    vi.mocked(runDiscovery).mockResolvedValue([raw("Alpha")]);
    vi.mocked(pushToWebhook).mockImplementationOnce(async () => { vi.setSystemTime(start + 51_000); return 1; });
    await runFundingPipeline(config({ dryRun: false, skipEnrich: true, deadlineAt: start + 60_000 }));
    expect(pushToSupabase).not.toHaveBeenCalled();
    expect(day0BlitzEnrich).not.toHaveBeenCalled();
  });

  it("bounds a stalled table check and a stalled Clay send", async () => {
    vi.useFakeTimers();
    const { checkTable } = await import("./supabase.js");
    vi.mocked(runDiscovery).mockResolvedValue([raw("Alpha")]);
    vi.mocked(checkTable).mockImplementationOnce(() => new Promise(() => {}));
    const deadlineAt = Date.now() + 60_000;
    const checking = runFundingPipeline(config({ dryRun: false, skipEnrich: true, deadlineAt }));
    await vi.advanceTimersByTimeAsync(60_000);
    await checking;
    expect(pushToWebhook).not.toHaveBeenCalled();

    vi.mocked(pushToWebhook).mockImplementationOnce(() => new Promise(() => {}));
    const sending = runFundingPipeline(config({ dryRun: false, skipEnrich: true, deadlineAt: Date.now() + 60_000 }));
    await vi.advanceTimersByTimeAsync(60_000);
    await sending;
    expect(pushToSupabase).not.toHaveBeenCalled();
  });

  it("gives Blitz the shared deadline and returns when Blitz never resolves", async () => {
    vi.useFakeTimers();
    vi.mocked(runDiscovery).mockResolvedValue([raw("Alpha")]);
    vi.mocked(extractWithOpenAI).mockResolvedValue(extracted("Alpha", RUN_DATE));
    vi.mocked(day0BlitzEnrich).mockImplementationOnce(() => new Promise(() => {}));
    const deadlineAt = Date.now() + 6 * 60_000;
    let finished = false;
    const run = runFundingPipeline(config({ dryRun: false, deadlineAt })).then(() => { finished = true; });
    await vi.advanceTimersByTimeAsync(0);
    expect(day0BlitzEnrich).toHaveBeenCalledTimes(1);
    const signal = vi.mocked(day0BlitzEnrich).mock.calls[0][3];
    expect(signal?.aborted).toBe(false);
    await vi.advanceTimersByTimeAsync(6 * 60_000);
    expect(finished).toBe(true);
    expect(signal?.aborted).toBe(true);
    await run;
  });

  it("stops between chunks at the deadline and leaves the rest unwritten", async () => {
    const names = ["Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot"];
    vi.mocked(runDiscovery).mockResolvedValue(names.map(raw));
    vi.mocked(extractWithOpenAI).mockImplementation(async (_text, name) => extracted(name, RUN_DATE));
    vi.stubEnv("CLAY_SERIES_A_WEBHOOK_URL", "https://clay.test/hook");
    vi.stubEnv("CLAY_SERIES_A_WEBHOOK_TOKEN", "test-token");
    vi.useFakeTimers({ toFake: ["Date"] });
    const deadlineAt = Date.now() + 6 * 60_000;
    // The first Supabase write lands after the deadline, so the second chunk must not start.
    vi.mocked(pushToSupabase).mockImplementationOnce(async (rows) => { vi.setSystemTime(deadlineAt + 1); return rows.length; });

    const result = await runFundingPipeline(config({ dryRun: false, deadlineAt }));

    expect(result.companyCount).toBe(6);
    expect(vi.mocked(pushToWebhook)).toHaveBeenCalledTimes(5);
    expect(vi.mocked(pushToSupabase)).toHaveBeenCalledTimes(1);
    expect(vi.mocked(pushToSupabase).mock.calls[0][0].map((r) => r.company_name)).toEqual(names.slice(0, 5));
  });
});
