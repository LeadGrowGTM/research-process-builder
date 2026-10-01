/**
 * Unit tests for signal-bank-daily fix-forward logic.
 * Tests schema override for funding_discoveries (public schema) and fix-forward cutoff.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { resolveDiscoveryProfilePatch, resolveFounderCap } from "./signal-bank-daily.js";

vi.mock("@trigger.dev/sdk", () => ({ schedules: { task: (config: unknown) => config }, logger: { info: vi.fn(), warn: vi.fn(), error: vi.fn() } }));
vi.mock("./modules/workflow-gate.js", () => ({ workflowGate: async () => ({ active: true }) }));
vi.mock("./pipeline/luna.js", () => ({ lunaJson: vi.fn() }));
vi.mock("./pipeline/founders.js", () => ({ isFoundersConfigured: () => false, runFoundersForCompany: vi.fn(), logCostRecorder: () => ({ record: () => {} }), MAX_FOUNDER_PROVIDER_CALLS_PER_RUN: 500 }));

afterEach(() => { vi.resetAllMocks(); vi.unstubAllEnvs(); vi.unstubAllGlobals(); });

describe("resolveFounderCap", () => {
  it("defaults to 50 and bounds per-run overrides", () => {
    expect(resolveFounderCap(undefined, undefined)).toBe(50);
    expect(resolveFounderCap("12", "40")).toBe(12);
    expect(resolveFounderCap(100, undefined)).toBe(50);
    expect(resolveFounderCap(-3, undefined)).toBe(0);
    expect(resolveFounderCap("bad", undefined)).toBe(50);
  });
});

describe("resolveDiscoveryProfilePatch", () => {
  it("includes non-empty values for all matching discoveries", () => {
    expect(
      resolveDiscoveryProfilePatch(
        { company_description: "Builds tools", products: "A widget" }
      )
    ).toEqual({ company_description: "Builds tools", products: "A widget" });
  });

  it("omits empty values so existing discovery values are preserved", () => {
    expect(
      resolveDiscoveryProfilePatch(
        { company_description: "New text", products: "   " }
      )
    ).toEqual({ company_description: "New text" });
  });

  it("includes non-empty replacement values", () => {
    expect(
      resolveDiscoveryProfilePatch(
        { company_description: "Other", products: "Other" }
      )
    ).toEqual({ company_description: "Other", products: "Other" });
  });

  it("returns an empty patch when there is nothing new", () => {
    expect(
      resolveDiscoveryProfilePatch(
        { company_description: null, products: " " }
      )
    ).toEqual({});
  });
});

describe("signal bank profile writes", () => {
  it.each(["Payment software", null])("updates discoveries by domain without empty fields (products: %s)", async (products) => {
    vi.resetModules();
    vi.stubEnv("SUPABASE_PROJECT_URL", "https://project.test");
    vi.stubEnv("SUPABASE_SERVICE_ROLE_KEY", "fixture-key");
    vi.stubEnv("OPENAI_API_KEY", "fixture-key");
    vi.stubEnv("FIRECRAWL_API_KEY", "");
    const { signalBankDaily } = await import("./signal-bank-daily.js");
    const { lunaJson } = await import("./pipeline/luna.js");
    vi.mocked(lunaJson).mockImplementation(async (options) => ({ data: options.name === "company_profile" ? { company_description: null, products } : { industry: "Fintech", icp_fit: "strong", company_size: "SMB", reasoning: "B2B software", decision_makers: [], pain_points: [] }, usage: { inputTokens: 0, cachedInputTokens: 0, outputTokens: 0 }, serviceTier: "flex", costUsd: 0 }));
    const discovery = { company_name: "Acme", company_domain: "acme.com", industry: "Fintech", round_type: "Series A", discovered_date: "2026-09-30", company_description: "Acme makes payment software." };
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method === "PATCH" || init?.method === "POST") return new Response(null, { status: 204 });
      if (url.includes("funding_discoveries")) return new Response(JSON.stringify([discovery, { ...discovery, company_description: null, products: "Existing product" }]));
      return new Response("[]");
    });
    vi.stubGlobal("fetch", fetchMock);
    const runTask = signalBankDaily as unknown as { run: (payload: { timestamp: Date }) => Promise<unknown> };
    await runTask.run({ timestamp: new Date("2026-09-30T00:00:00Z") });
    const writes = fetchMock.mock.calls.filter(([, init]) => init?.method === "POST");
    expect(writes).toHaveLength(1);
    const company = JSON.parse(String(writes[0][1]?.body))[0];
    expect(company).toMatchObject({ domain: "acme.com", icp_fit: "strong" });
    expect(company).not.toHaveProperty("company_description");
    expect(company).not.toHaveProperty("products");
    const patches = fetchMock.mock.calls.filter(([, init]) => init?.method === "PATCH");
    expect(patches).toHaveLength(1);
    expect(patches[0][0]).toBe("https://project.test/rest/v1/funding_discoveries?company_domain=eq.acme.com");
    expect(patches[0][1]?.headers).toMatchObject({ "Accept-Profile": "public", "Content-Profile": "public" });
    expect(JSON.parse(String(patches[0][1]?.body))).toEqual({ company_description: discovery.company_description, ...(products ? { products } : {}) });
  });
});

describe("signal-bank-daily selection logic", () => {
  const mockRows = [
    {
      company_domain: "newco.com",
      discovered_date: "2026-08-28",
      company_name: "NewCo Inc",
      industry: "SaaS",
      round_type: "Series A",
      amount_raised: "$5M",
      location: "SF",
    },
    {
      company_domain: "oldco.com",
      discovered_date: "2026-05-10",
      company_name: "OldCo Ltd",
      industry: "Fintech",
      round_type: "Seed",
      amount_raised: "$1M",
      location: "NYC",
    },
    {
      company_domain: "not_found",
      discovered_date: "2026-08-27",
      company_name: "FakeDir",
      industry: "Unknown",
      round_type: "Seed",
      amount_raised: null,
      location: "Web",
    },
    {
      company_domain: "duplicate.com",
      discovered_date: "2026-08-27",
      company_name: "Duplicate",
      industry: "AI",
      round_type: "Series B",
      amount_raised: "$10M",
      location: "Austin",
    },
    {
      company_domain: "today.com",
      discovered_date: "2026-08-27",
      company_name: "TodayRound",
      industry: "Web3",
      round_type: "Seed",
      amount_raised: "$2M",
      location: "London",
    },
  ];

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("should filter out rows with null domain", () => {
    const rowsWithNull = [
      ...mockRows,
      {
        company_domain: null,
        discovered_date: "2026-08-27",
        company_name: "NoDomain",
      } as any,
    ];

    const filtered = rowsWithNull.filter((r) => r.company_domain);
    expect(filtered).toHaveLength(5);
  });

  it("should filter out rows containing 'not_found' in domain", () => {
    const filtered = mockRows.filter((r) => !r.company_domain.includes("not_found"));
    expect(filtered).toHaveLength(4);
    expect(filtered.map((r) => r.company_domain)).not.toContain("not_found");
  });

  it("should filter out rows already in signal_companies", () => {
    const existingDomains = new Set(["duplicate.com"]);
    const filtered = mockRows.filter((r) => !existingDomains.has(r.company_domain));
    expect(filtered).toHaveLength(4);
    expect(filtered.map((r) => r.company_domain)).not.toContain("duplicate.com");
  });

  it("should filter out rows discovered before fix-forward cutoff (2026-08-27)", () => {
    const FIX_FORWARD_SINCE = "2026-08-27";
    const filtered = mockRows.filter((r) => r.discovered_date >= FIX_FORWARD_SINCE);
    expect(filtered).toHaveLength(4); // oldco (2026-05-10) excluded
    expect(filtered.map((r) => r.company_name)).not.toContain("OldCo Ltd");
  });

  it("should apply all predicates cumulatively (selection logic)", () => {
    const FIX_FORWARD_SINCE = "2026-08-27";
    const existingDomains = new Set(["duplicate.com"]);

    const toProcess = mockRows
      .filter(
        (r) =>
          r.company_domain &&
          !r.company_domain.includes("not_found") &&
          !existingDomains.has(r.company_domain) &&
          r.discovered_date >= FIX_FORWARD_SINCE
      )
      .slice(0, 50);

    expect(toProcess).toHaveLength(2);
    expect(toProcess.map((r) => r.company_name)).toEqual(["NewCo Inc", "TodayRound"]);
  });

  it("should respect MAX_PER_RUN slice", () => {
    const FIX_FORWARD_SINCE = "2026-08-27";
    const existingDomains = new Set<string>();
    const MAX_PER_RUN = 1;

    const toProcess = mockRows
      .filter(
        (r) =>
          r.company_domain &&
          !r.company_domain.includes("not_found") &&
          !existingDomains.has(r.company_domain) &&
          r.discovered_date >= FIX_FORWARD_SINCE
      )
      .slice(0, MAX_PER_RUN);

    expect(toProcess).toHaveLength(1);
  });

  it("should preserve rows with cutoff date exactly", () => {
    const FIX_FORWARD_SINCE = "2026-08-27";
    const rows = [
      {
        company_domain: "cutoff.com",
        discovered_date: "2026-08-27",
        company_name: "OnCutoff",
      },
      {
        company_domain: "before.com",
        discovered_date: "2026-08-26",
        company_name: "BeforeCutoff",
      },
    ];

    const filtered = rows.filter((r) => r.discovered_date >= FIX_FORWARD_SINCE);
    expect(filtered).toHaveLength(1);
    expect(filtered[0].company_name).toBe("OnCutoff");
  });
});
