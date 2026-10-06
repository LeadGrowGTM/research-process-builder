import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  lookup: vi.fn(), scrape: vi.fn(), validate: vi.fn(), logger: { info: vi.fn(), warn: vi.fn(), error: vi.fn() },
}));
vi.mock("@trigger.dev/sdk", () => ({ logger: mocks.logger, task: (options: unknown) => options }));
vi.mock("./pipeline/domain-lookup.js", async () => ({
  ...await vi.importActual<typeof import("./pipeline/domain-lookup.js")>("./pipeline/domain-lookup.js"),
  lookupDomainMultiSignal: mocks.lookup,
}));
vi.mock("./pipeline/scrape.js", () => ({ fetchUrl: mocks.scrape }));
vi.mock("./pipeline/openai.js", () => ({ validateDomainSemantic: mocks.validate }));

const stored = (id: number, company_name = "Acme") => ({ id, company_name, company_domain: null, source_url: "https://publisher.test/story", article_text: "Funding article without a website" });
const hit = (domain = "acme.com", confidence = "high") => ({ domain, confidence, source: "search_validated", evidence: "Official site" });
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });

function setupFetch(rows: unknown[]) {
  const writes: Array<{ url: string; patch: Record<string, unknown> }> = [];
  const mock = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    if (init?.method === "PATCH") {
      writes.push({ url: String(input), patch: JSON.parse(String(init.body)) });
      return json([{ id: 1 }]);
    }
    return json(rows);
  });
  vi.stubGlobal("fetch", mock);
  return { mock, writes };
}

beforeEach(() => {
  vi.resetModules(); vi.resetAllMocks();
  vi.useFakeTimers(); vi.setSystemTime(new Date("2026-10-05T12:00:00Z"));
  vi.stubEnv("SUPABASE_PROJECT_URL", "https://supabase.test");
  vi.stubEnv("SUPABASE_SERVICE_ROLE_KEY", "test-key");
  mocks.lookup.mockResolvedValue(hit());
  mocks.scrape.mockResolvedValue(null);
  mocks.validate.mockResolvedValue({ status: "Correct" });
});
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); vi.unstubAllEnvs(); });

describe("signal-domain-backfill", () => {
  it.each(["Unclear", "Wrong"])("does not write a funding domain when semantic validation is %s without a correction", async (status) => {
    mocks.validate.mockResolvedValue({ status, correctDomain: "not_found" });
    const { writes } = setupFetch([stored(1)]);
    const { runSignalDomainBackfill } = await import("./signal-domain-backfill.js");
    const result = await runSignalDomainBackfill({ table: "funding_discoveries", dryRun: false });
    expect(mocks.validate).toHaveBeenCalledWith(stored(1).source_url, "Acme", "acme.com", stored(1).article_text, expect.any(Number));
    expect(result).toMatchObject({ resolved: 0, updated: 0 });
    expect(writes).toEqual([]);
  });

  it("writes a semantic correction consistently to the funding domain, website and logo", async () => {
    mocks.validate.mockResolvedValue({ status: "Wrong", correctDomain: "https://www.correct.test/about" });
    const { writes } = setupFetch([stored(1)]);
    const { runSignalDomainBackfill } = await import("./signal-domain-backfill.js");
    await runSignalDomainBackfill({ table: "funding_discoveries", dryRun: false });
    expect(writes[0].patch).toEqual({ company_domain: "correct.test", website_url: "https://correct.test", logo_url: "https://www.google.com/s2/favicons?domain=correct.test&sz=128" });
  });

  it("never writes a semantic result returned after the budget deadline", async () => {
    mocks.validate.mockImplementation(async () => { vi.advanceTimersByTime(540_000); return { status: "Correct" }; });
    const { writes } = setupFetch([stored(1)]);
    const { runSignalDomainBackfill } = await import("./signal-domain-backfill.js");
    await runSignalDomainBackfill({ table: "funding_discoveries", dryRun: false });
    expect(writes).toEqual([]);
  });
  it("preserves curated funding website and logo values", async () => {
    const { writes } = setupFetch([{ ...stored(1), website_url: "https://acme.com/about", logo_url: "https://images.test/acme.png" }]);
    const { runSignalDomainBackfill } = await import("./signal-domain-backfill.js");
    await runSignalDomainBackfill({ table: "funding_discoveries", dryRun: false });
    expect(writes[0].patch).toEqual({ company_domain: "acme.com" });
  });

  it("normalizes malformed optional fields instead of aborting the row loop", async () => {
    setupFetch([{ ...stored(1), article_text: 123, description: {}, industry: [], location: 42 }, stored(2, "Other")]);
    const { runSignalDomainBackfill } = await import("./signal-domain-backfill.js");
    const result = await runSignalDomainBackfill({ table: "funding_discoveries" });
    expect(result.processed).toBe(2);
  });
  it("defaults to dryRun, scopes reads to missing domains in the last 90 days, and logs every proposal", async () => {
    const { mock, writes } = setupFetch([stored(1), stored(2, "Other")]);
    const { runSignalDomainBackfill } = await import("./signal-domain-backfill.js");
    const result = await runSignalDomainBackfill({ table: "funding_discoveries", limit: 2 });
    expect(result).toMatchObject({ dryRun: true, processed: 2, resolved: 2, updated: 0 });
    const url = new URL(String(mock.mock.calls[0][0]));
    expect(url.searchParams.get("discovered_date")).toBe("gte.2026-07-07");
    expect(url.searchParams.get("and")).toBe("(discovered_date.lte.2026-10-05)");
    expect(url.searchParams.get("or")).toContain("company_domain.is.null");
    expect(url.searchParams.get("limit")).toBe("2");
    expect(writes).toEqual([]);
    expect(mocks.logger.info.mock.calls.filter(([message]) => message === "Signal domain proposal")).toHaveLength(2);
    expect(mocks.scrape).not.toHaveBeenCalled();
  });

  it("writes a confident funding domain and its website/logo atomically, without overwriting concurrent resolutions", async () => {
    const { writes } = setupFetch([{ ...stored(1), article_text: "Website (https://acme.com)" }]);
    const { runSignalDomainBackfill } = await import("./signal-domain-backfill.js");
    const result = await runSignalDomainBackfill({ table: "funding_discoveries", dryRun: false });
    expect(result).toMatchObject({ updated: 1, remainingLookups: 20 });
    expect(writes[0].patch).toEqual({ company_domain: "acme.com", website_url: "https://acme.com", logo_url: "https://www.google.com/s2/favicons?domain=acme.com&sz=128" });
    expect(new URL(writes[0].url).searchParams.get("or")).toContain("company_domain.is.null");
    expect(mocks.lookup).not.toHaveBeenCalled();
  });

  it("uses maker websites for products and writes only the company_domain column", async () => {
    const { writes } = setupFetch([{ ...stored(1), maker_website: "https://www.acme.com/", article_text: null }]);
    const { runSignalDomainBackfill } = await import("./signal-domain-backfill.js");
    await runSignalDomainBackfill({ table: "product_launches", dryRun: false });
    expect(writes[0].patch).toEqual({ company_domain: "acme.com" });
    expect(mocks.scrape).not.toHaveBeenCalled();
  });

  it("uses the existing bounded scraper when stored Evidence is missing and caches repeated company lookups", async () => {
    setupFetch([{ ...stored(1), article_text: null }, { ...stored(2), article_text: null }]);
    mocks.scrape.mockResolvedValue("Website https://acme.com");
    const { runSignalDomainBackfill } = await import("./signal-domain-backfill.js");
    const result = await runSignalDomainBackfill({ table: "funding_discoveries" });
    expect(result.resolved).toBe(2);
    expect(mocks.scrape).toHaveBeenCalledTimes(1);
    expect(mocks.scrape).toHaveBeenCalledWith("https://publisher.test/story", { maxChars: 20_000, deadlineAt: Date.now() + 30_000 });
    expect(mocks.lookup).not.toHaveBeenCalled();
  });

  it("caps row and search budgets and preserves ambiguous rows", async () => {
    const { mock, writes } = setupFetch(Array.from({ length: 70 }, (_, i) => stored(i + 1, `Company${i}`)));
    mocks.lookup.mockResolvedValue(hit("ambiguous.test", "medium"));
    const { runSignalDomainBackfill } = await import("./signal-domain-backfill.js");
    const result = await runSignalDomainBackfill({ table: "product_launches", limit: 100, dryRun: false });
    expect(new URL(String(mock.mock.calls[0][0])).searchParams.get("limit")).toBe("50");
    expect(mocks.lookup).toHaveBeenCalledTimes(20);
    expect(result).toMatchObject({ processed: 50, resolved: 0, updated: 0, remainingLookups: 0 });
    expect(writes).toEqual([]);
  });

  it("stops at the deadline and never patches a late resolution", async () => {
    const { writes } = setupFetch([stored(1), stored(2, "Other")]);
    mocks.lookup.mockImplementation(async () => { vi.advanceTimersByTime(540_000); return hit(); });
    const { runSignalDomainBackfill } = await import("./signal-domain-backfill.js");
    const result = await runSignalDomainBackfill({ table: "funding_discoveries", dryRun: false });
    expect(result).toMatchObject({ processed: 1, resolved: 0, stoppedEarly: true });
    expect(writes).toEqual([]);
  });

  it("reports failed writes and continues to later rows, and does not count a raced empty PATCH as an update", async () => {
    const { mock } = setupFetch([stored(1), stored(2, "Other")]);
    const normal = mock.getMockImplementation()!;
    let attempt = 0;
    mock.mockImplementation(async (url, init) => init?.method === "PATCH" ? (++attempt === 1 ? json({}, 503) : json([])) : normal(url, init));
    const { runSignalDomainBackfill } = await import("./signal-domain-backfill.js");
    expect(await runSignalDomainBackfill({ table: "funding_discoveries", dryRun: false })).toMatchObject({ processed: 2, failed: 1, updated: 0 });
  });

  it("rejects unsupported tables and invalid limits before any I/O", async () => {
    const { mock } = setupFetch([]);
    const { runSignalDomainBackfill } = await import("./signal-domain-backfill.js");
    await expect(runSignalDomainBackfill({ table: "secrets" as "product_launches" })).rejects.toThrow("Unsupported");
    await expect(runSignalDomainBackfill({ table: "product_launches", limit: -1 })).rejects.toThrow("positive integer");
    await expect(runSignalDomainBackfill({ table: "product_launches", dryRun: "false" as unknown as boolean })).rejects.toThrow("boolean");
    expect(mock).not.toHaveBeenCalled();
  });
});
