import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@trigger.dev/sdk", () => ({ schedules: { task: (config: unknown) => config }, logger: { info: vi.fn(), warn: vi.fn(), error: vi.fn() } }));
vi.mock("./modules/workflow-gate.js", () => ({ workflowGate: async () => ({ active: true }) }));
vi.mock("./pipeline/luna.js", () => ({ lunaJson: vi.fn() }));
vi.mock("./pipeline/founders.js", () => ({ isFoundersConfigured: () => false, runFoundersForCompany: vi.fn(), logCostRecorder: () => ({ record: () => {} }), MAX_FOUNDER_PROVIDER_CALLS_PER_RUN: 500 }));

afterEach(() => { vi.resetAllMocks(); vi.unstubAllEnvs(); vi.unstubAllGlobals(); });

async function task() {
  vi.resetModules();
  vi.stubEnv("SUPABASE_PROJECT_URL", "https://project.test");
  vi.stubEnv("SUPABASE_SERVICE_ROLE_KEY", "fixture-key");
  vi.stubEnv("OPENAI_API_KEY", "fixture-key");
  vi.stubEnv("FIRECRAWL_API_KEY", "");
  const { signalBankDaily } = await import("./signal-bank-daily.js");
  return signalBankDaily as unknown as { run: (payload: { timestamp: Date }) => Promise<unknown> };
}

describe("signal bank execution", () => {
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
