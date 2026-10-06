import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ lookup: vi.fn(), logger: { info: vi.fn(), warn: vi.fn(), error: vi.fn() } }));
vi.mock("@trigger.dev/sdk", () => ({ logger: mocks.logger }));
vi.mock("./domain-lookup.js", async () => ({
  ...await vi.importActual<typeof import("./domain-lookup.js")>("./domain-lookup.js"),
  lookupDomainMultiSignal: mocks.lookup,
}));

const DATE = "2026-10-05";
function job(id: number, title = "Acme", website: string | null = null) {
  return { id, title: "Technical Animator", slug: `job-${id}`, date: DATE, description: "Animation tools", job_type: "full-time", country: "Canada", city: "Toronto", company: { id: 1, title, website }, tags: [], categories: [] };
}
const json = (body: unknown) => new Response(JSON.stringify(body));

function setupFetch(jobs = [job(1), job(2)], seen = false) {
  const writes: Array<{ method: string; url: string; body: unknown }> = [];
  const fetchMock = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    const url = String(input);
    if (init?.method) {
      writes.push({ method: init.method, url, body: JSON.parse(String(init.body)) });
      return json([]);
    }
    if (url.includes("80.lv/api/jobs")) return json({ jobs: { items: url.endsWith("page=0") ? jobs : [] } });
    return json(seen ? [{ id: 1 }] : []);
  });
  vi.stubGlobal("fetch", fetchMock);
  return { writes, fetchMock };
}

beforeEach(() => {
  vi.resetModules(); vi.resetAllMocks(); vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-10-05T12:00:00Z"));
  vi.stubEnv("SUPABASE_PROJECT_URL", "https://supabase.test");
  vi.stubEnv("SUPABASE_KEY", "test-key"); vi.stubEnv("CLAY_GAME_JOB_SIGNALS_WEBHOOK", "");
  mocks.lookup.mockResolvedValue({ domain: "acme.com", confidence: "high", source: "search_validated", evidence: "Official site" });
});
afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); vi.useRealTimers(); });

describe("jobs company domains", () => {
  it.each(["unrealengine.com", "artstation.com", "jobs.lever.co"])("uses search rather than repeated job-description links to %s", async (domain) => {
    const { writes } = setupFetch([{ ...job(1), description: `Animation tools at https://${domain} and https://${domain}` }]);
    const { runJobsPipeline } = await import("./jobs-pipeline.js");
    await runJobsPipeline({ date: DATE, dryRun: false });
    expect((writes[0].body as Array<Record<string, unknown>>)[0].company_domain).toBe("acme.com");
    expect(mocks.lookup).toHaveBeenCalledTimes(1);
    expect(mocks.lookup.mock.calls[0][1].productOrService).toContain(domain);
  });

  it("leaves a tool-heavy job unresolved when search is inconclusive", async () => {
    const { writes } = setupFetch([{ ...job(1), description: "Website https://acme.com and https://acme.com" }]);
    mocks.lookup.mockResolvedValue({ domain: "wrong.test", confidence: "medium" });
    const { runJobsPipeline } = await import("./jobs-pipeline.js");
    await runJobsPipeline({ date: DATE, dryRun: false });
    expect((writes[0].body as Array<Record<string, unknown>>)[0].company_domain).toBeNull();
    expect(mocks.lookup).toHaveBeenCalledTimes(1);
  });
  it("resolves each company once and stores the domain on every new hiring row", async () => {
    const { writes } = setupFetch();
    const { runJobsPipeline } = await import("./jobs-pipeline.js");
    expect(await runJobsPipeline({ date: DATE, dryRun: false })).toMatchObject({ upserted: 2 });
    expect(mocks.lookup).toHaveBeenCalledTimes(1);
    for (const write of writes) {
      const [row] = write.body as Array<Record<string, unknown>>;
      expect(row.company_domain).toBe("acme.com");
      expect(row).not.toHaveProperty("source_url");
      expect(row).not.toHaveProperty("description");
      expect(row).not.toHaveProperty("company_identity");
    }
  });

  it("keeps valid provided websites and does no paid lookup", async () => {
    const { writes } = setupFetch([job(1, "Acme", "https://www.acme.com/team")]);
    const { runJobsPipeline } = await import("./jobs-pipeline.js");
    await runJobsPipeline({ date: DATE, dryRun: false });
    expect((writes[0].body as Array<Record<string, unknown>>)[0].company_domain).toBe("acme.com");
    expect(mocks.lookup).not.toHaveBeenCalled();
  });

  it("repairs missing domains on deduplicated stored jobs without rewriting other fields", async () => {
    const { writes } = setupFetch([job(1)], true);
    const { runJobsPipeline } = await import("./jobs-pipeline.js");
    expect(await runJobsPipeline({ date: DATE, dryRun: false })).toMatchObject({ upserted: 0 });
    expect(writes).toEqual([{ method: "PATCH", url: expect.stringContaining("company_domain.is.null"), body: { company_domain: "acme.com" } }]);
  });

  it("keeps jobs when a repair fails and makes no writes during dryRun", async () => {
    const { fetchMock, writes } = setupFetch([job(1)], true);
    const normal = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation(async (input, init) => {
      if (init?.method === "PATCH") throw new Error("connection reset");
      return normal(input, init);
    });
    const { runJobsPipeline } = await import("./jobs-pipeline.js");
    expect(await runJobsPipeline({ date: DATE, dryRun: false })).toMatchObject({ matched: 1 });
    expect(mocks.logger.warn).toHaveBeenCalledWith("Job domain repair failed", expect.objectContaining({ jobId: 1 }));
    await runJobsPipeline({ date: DATE, dryRun: true });
    expect(writes).toEqual([]);
  });

  it("bounds company searches to 20 and leaves low-confidence domains empty", async () => {
    const { writes } = setupFetch(Array.from({ length: 25 }, (_, i) => job(i + 1, `Company${i}`)));
    mocks.lookup.mockResolvedValue({ domain: "wrong.test", confidence: "low" });
    const { runJobsPipeline } = await import("./jobs-pipeline.js");
    await runJobsPipeline({ date: DATE, dryRun: false });
    expect(mocks.lookup).toHaveBeenCalledTimes(20);
    expect(writes.every((write) => (write.body as Array<Record<string, unknown>>)[0].company_domain === null)).toBe(true);
  });
});
