import { afterEach, describe, expect, it, vi } from "vitest";
import { AI_ARK_COST_USD, findCompanyPeople, parseQuickEnrichRows } from "./legion-people.js";

const cfg = { quickEnrichKey: "qe", aiArkKey: "ark", quickEnrichUsdPerCredit: 0 };

function reply(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function qeRow(first: string, title: string, extra: Record<string, unknown> = {}) {
  return {
    first_name: first, last_name: "Doe", title, email: `${first}@acme.com`, employee_phone: "6505550100",
    employee_linkedin: `https://www.linkedin.com/in/${first.toLowerCase()}`, city: "Austin", region_code: "TX",
    country_code: "US", employee_count: "20 - 99", ...extra,
  };
}

afterEach(() => vi.unstubAllGlobals());

describe("parseQuickEnrichRows", () => {
  it("keeps founders, skips founding staff, and drops contact data", () => {
    const out = parseQuickEnrichRows([qeRow("Keith", "Co-Founder and CEO"), qeRow("Matt", "Founding Engineer"), qeRow("Pete", "Co-Founder & VP, Engineering")]);
    expect(out.founders.map((f) => f.name)).toEqual(["Keith Doe", "Pete Doe"]);
    expect(out.hq).toBe("Austin, TX, US");
    expect(out.employees).toBe("20 - 99");
    expect(JSON.stringify(out)).not.toMatch(/@acme\.com|6505550100/);
  });

  it("treats N/A as missing and caps at three founders", () => {
    const rows = [1, 2, 3, 4].map((n) => qeRow(`F${n}`, "Founder", { city: "N/A" }));
    const out = parseQuickEnrichRows(rows);
    expect(out.founders).toHaveLength(3);
    expect(out.hq).toBeNull();
  });
});

describe("findCompanyPeople", () => {
  it("stops after QuickEnrich when it finds founders", async () => {
    const fetchMock = vi.fn(async (_url: string) => reply({ data: [qeRow("Ada", "Founder")] }));
    vi.stubGlobal("fetch", fetchMock);
    const out = await findCompanyPeople("acme.com", cfg);
    expect(out.founders).toHaveLength(1);
    expect(out.sources).toEqual(["quickenrich"]);
    expect(out.calls).toEqual([{ provider: "quickenrich", units: 1, costUsd: 0 }]);
    const url = new URL(String(fetchMock.mock.calls[0][0]));
    expect(url.pathname).toBe("/api/employees/dataset-search");
    expect(url.searchParams.get("title")).toBe("Founder");
    expect(url.searchParams.get("company_url")).toBe("acme.com");
  });

  it("falls back to an unfiltered page for HQ, then AI Ark for founders", async () => {
    const fetchMock = vi.fn(async (url: string) => {
      if (url.includes("quickenrich") && url.includes("title=")) return reply({ data: [] });
      if (url.includes("quickenrich")) return reply({ data: [qeRow("Sam", "Engineer")] });
      return reply({ content: [{ profile: { first_name: "Ada", last_name: "Lovelace", title: "Co-Founder" }, link: { linkedin: "https://linkedin.com/in/ada" } }] });
    });
    vi.stubGlobal("fetch", fetchMock);
    const out = await findCompanyPeople("acme.com", cfg);
    expect(out.hq).toBe("Austin, TX, US");
    expect(out.founders).toEqual([{ name: "Ada Lovelace", title: "Co-Founder", linkedin: "https://www.linkedin.com/in/ada" }]);
    expect(out.sources).toEqual(["quickenrich", "aiark"]);
    expect(out.calls.map((c) => c.provider)).toEqual(["quickenrich", "quickenrich", "aiark"]);
    expect(out.calls[2].costUsd).toBe(AI_ARK_COST_USD);
    expect(out.coverage).toEqual({ hq: "present", employees: "present", founders: "present" });
  });

  it("keeps an employee range and records QuickEnrich when size is the only company field", async () => {
    const fetchMock = vi.fn(async (url: string) => {
      if (url.includes("title=")) return reply({ data: [] });
      return reply({ data: [{ title: "Engineer", employee_count: "10001-10005", city: "N/A", email: "sam@acme.com" }] });
    });
    vi.stubGlobal("fetch", fetchMock);
    const out = await findCompanyPeople("acme.com", { ...cfg, aiArkKey: "" });
    expect(out.employees).toBe("10001-10005");
    expect(out.hq).toBeNull();
    expect(out.sources).toEqual(["quickenrich"]);
    expect(out.calls.map((call) => call.provider)).toEqual(["quickenrich", "quickenrich"]);
    expect(out.coverage).toEqual({ hq: "absent", employees: "present", founders: "unavailable" });
    expect(JSON.stringify(out)).not.toMatch(/sam@acme\.com/);
  });

  it("does not treat an empty QuickEnrich page as proof that founders are absent", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => reply({ data: [] })));
    const out = await findCompanyPeople("acme.com", { ...cfg, aiArkKey: "" });
    expect(out.founders).toEqual([]);
    expect(out.coverage).toEqual({ hq: "absent", employees: "absent", founders: "unavailable" });
  });

  it("marks founders absent only after AI Ark returns an empty page", async () => {
    const fetchMock = vi.fn(async (url: string) => url.includes("quickenrich") ? reply({ data: [] }) : reply({ content: [] }));
    vi.stubGlobal("fetch", fetchMock);
    const out = await findCompanyPeople("acme.com", cfg);
    expect(out.coverage.founders).toBe("absent");
    expect(out.calls[2]).toEqual({ provider: "aiark", units: 1, costUsd: AI_ARK_COST_USD });
  });

  it("keeps every field unavailable when both reads fail", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) => reply({ error: "down" }, url.includes("quickenrich") ? 500 : 503)));
    const out = await findCompanyPeople("acme.com", cfg);
    expect(out.founders).toEqual([]);
    expect(out.hq).toBeNull();
    expect(out.employees).toBeNull();
    expect(out.sources).toEqual([]);
    expect(out.coverage).toEqual({ hq: "unavailable", employees: "unavailable", founders: "unavailable" });
    expect(out.calls.map((call) => call.provider)).toEqual(["quickenrich", "quickenrich", "aiark"]);
  });
});
