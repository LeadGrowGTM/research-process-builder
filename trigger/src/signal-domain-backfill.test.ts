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
  it("accepts a medium funding lookup only after article semantic validation", async () => {
    mocks.lookup.mockResolvedValue(hit("namespace.so", "medium"));
    const { writes } = setupFetch([stored(1, "Namespace")]);
    const { runSignalDomainBackfill } = await import("./signal-domain-backfill.js");
    const result = await runSignalDomainBackfill({ table: "funding_discoveries", dryRun: false });
    expect(mocks.validate).toHaveBeenCalledWith(stored(1).source_url, "Namespace", "namespace.so", stored(1).article_text, expect.any(Number));
    expect(result).toMatchObject({ resolved: 1, updated: 1 });
    expect(writes[0].patch.company_domain).toBe("namespace.so");
    expect(result.proposals[0]).toMatchObject({ source: "search", lookupDomain: "namespace.so", confidence: "medium", rejectedReason: null });
  });

  it.each([
    ["Unclear", "high"], ["Wrong", "high"], ["Unclear", "medium"], ["Wrong", "medium"],
  ])("does not write a funding domain when semantic validation is %s for a %s lookup without a correction", async (status, confidence) => {
    mocks.lookup.mockResolvedValue(hit("acme.com", confidence));
    mocks.validate.mockResolvedValue({ status, correctDomain: "not_found" });
    const { writes } = setupFetch([stored(1)]);
    const { runSignalDomainBackfill } = await import("./signal-domain-backfill.js");
    const result = await runSignalDomainBackfill({ table: "funding_discoveries", dryRun: false });
    expect(mocks.validate).toHaveBeenCalledWith(stored(1).source_url, "Acme", "acme.com", stored(1).article_text, expect.any(Number));
    expect(result).toMatchObject({ resolved: 0, updated: 0 });
    expect(result.proposals[0].rejectedReason).toBe(status === "Wrong" ? "semantic_wrong" : "semantic_unclear");
    expect(writes).toEqual([]);
  });

  it.each(["high", "medium", "low"])("accepts only high funding lookups without article text (%s)", async (confidence) => {
    mocks.lookup.mockResolvedValue(hit("firecrawl.dev", confidence));
    const { writes } = setupFetch([{ ...stored(1, "Firecrawl"), article_text: null }]);
    const { runSignalDomainBackfill } = await import("./signal-domain-backfill.js");
    const result = await runSignalDomainBackfill({ table: "funding_discoveries", dryRun: false });
    expect(result.resolved).toBe(confidence === "high" ? 1 : 0);
    expect(writes).toHaveLength(confidence === "high" ? 1 : 0);
    expect(mocks.validate).not.toHaveBeenCalled();
    expect(result.proposals[0]).toMatchObject({ lookupDomain: "firecrawl.dev", confidence, rejectedReason: confidence === "high" ? null : confidence === "medium" ? "medium_requires_article_validation" : "confidence_not_high" });
  });

  it("rejects a low funding lookup even when article text is present", async () => {
    mocks.lookup.mockResolvedValue(hit("wrong.test", "low"));
    const { writes } = setupFetch([stored(1)]);
    const { runSignalDomainBackfill } = await import("./signal-domain-backfill.js");
    const result = await runSignalDomainBackfill({ table: "funding_discoveries", dryRun: false });
    expect(result.proposals[0]).toMatchObject({ domain: null, confidence: "low", rejectedReason: "confidence_not_high" });
    expect(mocks.validate).not.toHaveBeenCalled();
    expect(writes).toEqual([]);
  });

  it("does not reuse a validated medium lookup when the next funding row has no article", async () => {
    mocks.lookup.mockResolvedValue(hit("acme.com", "medium"));
    const { writes } = setupFetch([stored(1), { ...stored(2), article_text: null }, stored(3)]);
    const { runSignalDomainBackfill } = await import("./signal-domain-backfill.js");
    const result = await runSignalDomainBackfill({ table: "funding_discoveries", dryRun: false });
    expect(result).toMatchObject({ resolved: 2, updated: 2 });
    expect(result.proposals[1]).toMatchObject({ domain: null, source: "cache", lookupDomain: "acme.com", confidence: "medium", rejectedReason: "medium_requires_article_validation" });
    expect(result.proposals[2]).toMatchObject({ domain: "acme.com", source: "cache", confidence: "medium", rejectedReason: null });
    expect(mocks.validate).toHaveBeenCalledTimes(2);
    expect(mocks.lookup).toHaveBeenCalledOnce();
    expect(writes).toHaveLength(2);
  });

  it("revalidates a cached medium candidate once a funding row has article text", async () => {
    mocks.lookup.mockResolvedValue(hit("acme.com", "medium"));
    const { writes } = setupFetch([{ ...stored(1), article_text: null }, stored(2)]);
    const { runSignalDomainBackfill } = await import("./signal-domain-backfill.js");
    const result = await runSignalDomainBackfill({ table: "funding_discoveries", dryRun: false });
    expect(result).toMatchObject({ resolved: 1, updated: 1 });
    expect(result.proposals[1]).toMatchObject({ domain: "acme.com", source: "cache", confidence: "medium", rejectedReason: null });
    expect(mocks.validate).toHaveBeenCalledOnce();
    expect(mocks.lookup).toHaveBeenCalledOnce();
    expect(writes).toHaveLength(1);
  });

  it("logs a failed semantic validation and continues to later funding rows", async () => {
    mocks.validate.mockRejectedValueOnce(new Error("validator unavailable"));
    const { writes } = setupFetch([stored(1), stored(2, "Other")]);
    const { runSignalDomainBackfill } = await import("./signal-domain-backfill.js");
    const result = await runSignalDomainBackfill({ table: "funding_discoveries", dryRun: false });
    expect(result).toMatchObject({ processed: 2, resolved: 1 });
    expect(result.proposals[0]).toMatchObject({ domain: null, rejectedReason: "semantic_validation_failed" });
    expect(writes).toHaveLength(1);
  });

  it.each([
    ["Unclear", "high"], ["Wrong", "high"], ["error", "high"],
    ["Unclear", "medium"], ["Wrong", "medium"], ["error", "medium"],
  ])("revalidates a cached %s semantic rejection for a %s candidate with later article evidence", async (status, confidence) => {
    mocks.lookup.mockResolvedValue(hit("acme.com", confidence));
    if (status === "error") mocks.validate.mockRejectedValueOnce(new Error("validator unavailable"));
    else mocks.validate.mockResolvedValueOnce({ status, correctDomain: "not_found" });
    const betterArticle = "Acme funding article with clearer company details";
    const { writes } = setupFetch([stored(1), { ...stored(2), article_text: null }, { ...stored(3), article_text: betterArticle }]);
    const { runSignalDomainBackfill } = await import("./signal-domain-backfill.js");
    const result = await runSignalDomainBackfill({ table: "funding_discoveries", dryRun: false });
    expect(result.proposals.map((proposal) => proposal.domain)).toEqual([null, null, "acme.com"]);
    expect(result.proposals[2]).toMatchObject({ source: "cache", confidence, rejectedReason: null });
    expect(mocks.validate).toHaveBeenCalledTimes(2);
    expect(mocks.validate).toHaveBeenLastCalledWith(stored(1).source_url, "Acme", "acme.com", betterArticle, expect.any(Number));
    expect(mocks.lookup).toHaveBeenCalledOnce();
    expect(writes).toHaveLength(1);
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

  it.each([
    ["Crosswalk", "mcp.crosswalk.to", "crosswalk.to"],
    ["Pilot5", "legal.pilot5.ai", "pilot5.ai"],
    ["OpenAI", "ads.openai.com", "openai.com"],
  ])("writes registrable launch domains for %s", async (name, host, domain) => {
    const { writes } = setupFetch([{ ...stored(1, name), maker_website: `https://${host}` }]);
    const { runSignalDomainBackfill } = await import("./signal-domain-backfill.js");
    const result = await runSignalDomainBackfill({ table: "product_launches", dryRun: false });
    expect(writes[0].patch).toEqual({ company_domain: domain });
    expect(result.proposals[0]).toMatchObject({ domain, source: "stored", confidence: "high", rejectedReason: null });
    expect(mocks.lookup).not.toHaveBeenCalled();
  });

  it("keeps product searches high-only even with article text", async () => {
    mocks.lookup.mockResolvedValue(hit("ara.so", "medium"));
    const { writes } = setupFetch([stored(1, "Reason")]);
    const { runSignalDomainBackfill } = await import("./signal-domain-backfill.js");
    const result = await runSignalDomainBackfill({ table: "product_launches", dryRun: false });
    expect(result.proposals[0]).toMatchObject({ domain: null, source: "search", lookupDomain: "ara.so", confidence: "medium", rejectedReason: "confidence_not_high" });
    expect(mocks.validate).not.toHaveBeenCalled();
    expect(writes).toEqual([]);
  });

  it("skips junk names before scraping or lookup and logs one detailed proposal per row", async () => {
    setupFetch(["Fundraising News", "Fundraising News", "Newsroom", "这家公司获得融资，计划拓展市场。", "This headline has seven separate company words"].map((name, i) => ({ ...stored(i + 1, name), article_text: null })));
    const { runSignalDomainBackfill } = await import("./signal-domain-backfill.js");
    const result = await runSignalDomainBackfill({ table: "funding_discoveries" });
    expect(result).toMatchObject({ processed: 5, resolved: 0, remainingLookups: 20 });
    expect(mocks.lookup).not.toHaveBeenCalled();
    expect(mocks.scrape).not.toHaveBeenCalled();
    expect(result.proposals.map((proposal) => proposal.rejectedReason)).toEqual(["non_company_name", "non_company_name", "non_company_name", "cjk_sentence_punctuation", "company_name_too_long"]);
    const logs = mocks.logger.info.mock.calls.filter(([message]) => message === "Signal domain proposal");
    expect(logs).toHaveLength(5);
    result.proposals.forEach((proposal, i) => expect(logs[i][1]).toEqual({ table: "funding_discoveries", dryRun: true, ...proposal }));
    expect(result.proposals.every((proposal) => proposal.source === "none" && proposal.lookupDomain === null && proposal.confidence === null)).toBe(true);
  });

  it("preserves candidate details on lookup failures, cached misses and deadline rejections", async () => {
    const { writes } = setupFetch([stored(1), stored(2), stored(3, "Late")]);
    mocks.lookup.mockRejectedValueOnce(new Error("provider unavailable"));
    mocks.lookup.mockImplementationOnce(async () => { vi.advanceTimersByTime(540_000); return hit("late.test", "high"); });
    const { runSignalDomainBackfill } = await import("./signal-domain-backfill.js");
    const result = await runSignalDomainBackfill({ table: "product_launches", dryRun: false });
    expect(result.proposals).toMatchObject([
      { domain: null, source: "search", lookupDomain: null, confidence: null, rejectedReason: "lookup_failed" },
      { domain: null, source: "cache", lookupDomain: null, confidence: null, rejectedReason: "lookup_failed" },
      { domain: null, source: "search", lookupDomain: "late.test", confidence: "high", rejectedReason: "deadline_exceeded" },
    ]);
    expect(writes).toEqual([]);
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
