import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@trigger.dev/sdk", () => ({ schedules: { task: (config: unknown) => config }, logger: { info: vi.fn(), warn: vi.fn(), error: vi.fn() } }));
vi.mock("./modules/workflow-gate.js", () => ({ workflowGate: async () => ({ active: true }) }));
vi.mock("./pipeline/luna.js", () => ({ lunaJson: vi.fn() }));
vi.mock("./pipeline/founders.js", () => ({ isFoundersConfigured: () => false, runFoundersForCompany: vi.fn(), logCostRecorder: () => ({ record: () => {} }), MAX_FOUNDER_PROVIDER_CALLS_PER_RUN: 500 }));

beforeEach(() => { vi.stubEnv("SPIDER_API_KEY", ""); });
afterEach(() => { vi.resetAllMocks(); vi.restoreAllMocks(); vi.unstubAllEnvs(); vi.unstubAllGlobals(); });

async function task() {
  vi.resetModules();
  vi.stubEnv("SUPABASE_PROJECT_URL", "https://project.test");
  vi.stubEnv("SUPABASE_SERVICE_ROLE_KEY", "fixture-key");
  vi.stubEnv("OPENAI_API_KEY", "fixture-key");
  const { signalBankDaily } = await import("./signal-bank-daily.js");
  return signalBankDaily as unknown as { run: (payload: { timestamp: Date }) => Promise<unknown> };
}

describe("signal bank execution", () => {
  it.each([12, 220])("scrapes full homepage text without a key and caps evidence at 8000 characters (repeats: %s)", async (repeats) => {
    const runTask = await task();
    const scrape = vi.spyOn(await import("./pipeline/scrape.js"), "scrapePage");
    const { lunaJson } = await import("./pipeline/luna.js");
    vi.mocked(lunaJson).mockImplementation(async (options) => ({
      data: options.name === "company_profile"
        ? { company_description: "Acme builds payment software.", products: "Payment software" }
        : { industry: "Fintech", icp_fit: "strong", company_size: "SMB", reasoning: "B2B software", decision_makers: [], pain_points: [] },
      usage: { inputTokens: 0, cachedInputTokens: 0, outputTokens: 0 }, serviceTier: "flex", costUsd: 0,
    }));
    const homepage = "Acme builds payment software for retailers. ".repeat(repeats).trim();
    const evidence = `Company navigation\n${homepage}\nCompany footer`.slice(0, 8_000);
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (url === "https://acme.com") return new Response(`<script>script junk</script><nav>Company navigation</nav><main>${homepage}</main><footer>Company footer</footer>`, { headers: { "Content-Type": "text/html" } });
      if (init?.method === "PATCH" || init?.method === "POST") return new Response(null, { status: 204 });
      if (url.includes("funding_discoveries")) return new Response(JSON.stringify([{ company_name: "Acme", company_domain: "acme.com", discovered_date: "2026-09-30" }]));
      return new Response("[]");
    });
    vi.stubGlobal("fetch", fetchMock);
    expect(await runTask.run({ timestamp: new Date("2026-09-30T00:00:00Z") })).toMatchObject({ scraped: 1 });
    expect(await scrape.mock.results[0].value).toMatchObject({ content: evidence, provider: "direct", costUsd: 0 });
    expect(fetchMock.mock.calls.filter(([url]) => url === "https://acme.com")).toHaveLength(1);
    expect(fetchMock.mock.calls.some(([url]) => url.includes("spider.cloud"))).toBe(false);
    const prompts = vi.mocked(lunaJson).mock.calls.map(([options]) => options);
    expect(prompts.find(options => options.name === "icp_classification")?.userPrompt).toContain(`Homepage: ${evidence.slice(0, 1000)}`);
    expect(prompts.find(options => options.name === "company_profile")?.userPrompt).toContain(`Evidence:\n${evidence.slice(0, 4000)}`);
    for (const prompt of prompts) expect(prompt.userPrompt).not.toContain("script junk");
    const companyCall = fetchMock.mock.calls.find(([url, init]) => url.includes("signal_companies") && init?.method === "POST");
    expect(JSON.parse(String(companyCall?.[1]?.body))[0]).toMatchObject({ homepage_scraped: true, homepage_analysis: { homepage_summary: evidence.slice(0, 500) } });
  });

  it("writes factual profile fields to public funding discoveries and classification to the knowledge schema", async () => {
    const runTask = await task();
    const { lunaJson } = await import("./pipeline/luna.js");
    vi.mocked(lunaJson).mockImplementation(async (options) => ({ data: options.name === "company_profile" ? { company_description: "Unsupported rewrite", products: "Payment software" } : { industry: "Fintech", icp_fit: "strong", company_size: "SMB", reasoning: "B2B software", decision_makers: [], pain_points: [] }, usage: { inputTokens: 0, cachedInputTokens: 0, outputTokens: 0 }, serviceTier: "flex", costUsd: 0 }));
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (url.includes("funding_discoveries")) return new Response(JSON.stringify([{ company_name: "Acme", company_domain: "acme.com", industry: "Fintech", round_type: "Series A", amount_raised: "$5M", discovered_date: "2026-09-30", company_description: "Acme makes payment software." }]));
      if (init?.method === "POST") return new Response(null, { status: 201 });
      return new Response("[]");
    });
    vi.stubGlobal("fetch", fetchMock);
    await runTask.run({ timestamp: new Date("2026-09-30T00:00:00Z") });
    const funding = fetchMock.mock.calls.find(([url]) => url.includes("funding_discoveries"));
    expect(funding?.[1]?.headers).toMatchObject({ "Accept-Profile": "public" });
    const write = fetchMock.mock.calls.find(([, init]) => init?.method === "POST");
    expect(write?.[1]?.headers).toMatchObject({ "Content-Profile": "leadgrow_knowledge" });
    const company = JSON.parse(String(write?.[1]?.body))[0];
    expect(company).toMatchObject({ icp_fit: "strong", round_type: "Series A" });
    expect(company).not.toHaveProperty("company_description");
    expect(company).not.toHaveProperty("products");
    const patch = fetchMock.mock.calls.find(([, init]) => init?.method === "PATCH");
    expect(patch?.[0]).toBe("https://project.test/rest/v1/funding_discoveries?company_domain=eq.acme.com");
    expect(patch?.[1]?.headers).toMatchObject({ "Accept-Profile": "public", "Content-Profile": "public" });
    expect(JSON.parse(String(patch?.[1]?.body))).toEqual({ company_description: "Acme makes payment software.", products: "Payment software" });
  });

  it("fails when the core funding read is unavailable", async () => {
    const runTask = await task();
    vi.stubGlobal("fetch", vi.fn(async (url: string) => url.includes("funding_discoveries") ? new Response("failed", { status: 500 }) : new Response("[]")));
    await expect(runTask.run({ timestamp: new Date() })).rejects.toThrow("funding_discoveries read failed");
  });
});
