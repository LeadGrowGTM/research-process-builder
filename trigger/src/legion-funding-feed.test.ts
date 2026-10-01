import { afterEach, describe, expect, it, vi } from "vitest";
import { buildFundingFeedRows, refreshFundingFeed } from "./legion-funding-feed.js";

const config = {
  url: "https://example.supabase.co",
  key: "test-key",
  legionKv: { accountId: "acct", namespaceId: "ns", token: "cf-token" },
  now: new Date("2026-09-30T12:00:00.000Z"),
};
const KV_PATH = "/accounts/acct/storage/kv/namespaces/ns/values/feed.json";

function reply(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

afterEach(() => vi.restoreAllMocks());

describe("buildFundingFeedRows", () => {
  it("ports favicon fallback and existing https logos", () => {
    expect(buildFundingFeedRows([{ company_name: "Acme", company_domain: "acme.com" }], [], [])[0].logo)
      .toBe("https://www.google.com/s2/favicons?domain=acme.com&sz=128");
    expect(buildFundingFeedRows([{ company_name: "Acme", company_domain: "acme.com", logo_url: "https://acme.com/logo.png" }], [], [])[0].logo)
      .toBe("https://acme.com/logo.png");
  });

  it("ports safe founder projection, dropping email columns and unsafe links", () => {
    const founders = [1, 2, 3, 4].map(n => ({ name: `Founder ${n}`, title: "Founder", linkedin: "ftp://example.com/profile", email: `person${n}@example.test`, phone: "5550100" }));
    const rows = buildFundingFeedRows([{ company_name: "Acme", company_domain: "acme.com", source_url: "javascript:alert(1)", logo_url: "ftp://example.com/logo" }], [], [{ domain: "acme.com", founders }]);
    expect(rows[0].founders).toHaveLength(3);
    for (const founder of rows[0].founders) expect(Object.keys(founder).sort()).toEqual(["linkedin", "name", "title"]);
    expect(JSON.stringify(rows)).not.toContain("email");
    expect(JSON.stringify(rows)).not.toContain("@example.test");
    expect(rows[0].sourceUrl).toBe("");
    expect(rows[0].founders[0].linkedin).toBe("");
  });

  it("ports newest domain deduplication and null input handling", () => {
    const rows = buildFundingFeedRows([null, { company_name: "  " }, { company_name: "Acme", company_domain: "Acme.com", discovered_date: "2026-09-20" }, { company_name: "Acme Inc", company_domain: "acme.com", discovered_date: "2026-09-01" }], null, undefined);
    expect(rows).toHaveLength(1);
    expect(rows[0].company).toBe("Acme");
    expect(rows[0].date).toBe("2026-09-20");
    expect(rows[0].founders).toEqual([]);
  });

  it("ignores signal_companies descriptions and keeps only industry_label", () => {
    const rows = buildFundingFeedRows(
      [{ company_name: "Acme", company_domain: "acme.com" }],
      [{ domain: "acme.com", company_description: "Stale", products: "Stale", industry_label: "DevTools" }],
      [],
    );
    expect(rows[0].description).toBe("");
    expect(rows[0].industry).toBe("DevTools");
  });

  it("keeps supplied publisher labels, removes sentinels, and strips credential URLs", () => {
    const rows = buildFundingFeedRows([{ company_name: "Acme", company_domain: "acme.com", source_url: "https://sec.gov/filing", source_name: "SEC Form D", lead_investors: "not_stated", employee_range: "11-50", logo_url: "https://example.test/logo?token=fixture" }], [], []);
    expect(rows[0]).toMatchObject({ source: "SEC Form D", investors: null, employees: "11-50", logo: "https://www.google.com/s2/favicons?domain=acme.com&sz=128" });
  });
  it("uses taxonomy, discovery descriptions, and a three-founder public cap", () => {
    const rows = buildFundingFeedRows(
      [{ company_name: "Acme", company_domain: "Acme.com", round_type: "series a", amount_raised: "$12M", amount_raised_usd: "12000000", industry: "SaaS", discovered_date: "2026-09-29", source_url: "https://techcrunch.com/acme", employee_count: 42, location: "Toronto", founded_year: 2020, company_description: "Builds software", products: "Ignored product" }],
      [{ domain: "acme.com", company_description: "Stale profile text", products: "Stale product", industry_label: "AI/ML" }],
      [{ domain: "acme.com", founders: [1, 2, 3, 4].map((n) => ({ name: `Founder ${n}`, title: "CEO", linkedin: `https://linkedin.com/in/${n}` })) }],
    );
    expect(rows).toHaveLength(1);
    expect(rows[0]).toEqual(expect.objectContaining({ round: "Series A", amountUsd: 12_000_000, industry: "AI/ML", description: "Builds software", employees: 42, hq: "Toronto", founded: 2020, source: "TechCrunch" }));
    expect(rows[0].founders).toHaveLength(3);
    expect(Object.keys(rows[0])).toEqual(["company", "domain", "logo", "round", "amount", "amountUsd", "investors", "industry", "description", "employees", "hq", "founded", "founders", "date", "source", "sourceUrl"]);
  });
});

describe("refreshFundingFeed", () => {
  it("retries missing optional funding columns with core projection", async () => {
    const fetchImpl = vi.fn().mockResolvedValueOnce(reply({ code: "42703" }, 400)).mockResolvedValueOnce(reply([])).mockResolvedValue(reply({}));
    const feed = await refreshFundingFeed({ ...config, fetchImpl });
    expect(feed.count).toBe(0);
    expect(fetchImpl.mock.calls[1][0]).not.toContain("hq_location");
    expect(fetchImpl.mock.calls[1][1].headers).toMatchObject({ "Accept-Profile": "public" });
  });

  it("does not publish malformed core responses or a failed fallback", async () => {
    const invalid = vi.fn().mockResolvedValue(reply({ rows: [] }));
    await expect(refreshFundingFeed({ ...config, fetchImpl: invalid })).rejects.toThrow("invalid core response");
    expect(invalid).toHaveBeenCalledOnce();
    const failing = vi.fn().mockResolvedValue(reply({}, 400));
    await expect(refreshFundingFeed({ ...config, fetchImpl: failing })).rejects.toThrow("read failed");
    expect(failing).toHaveBeenCalledTimes(2);
  });

  it("throws on a failed core read and makes no upload", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(reply({ message: "no" }, 500));
    await expect(refreshFundingFeed({ ...config, fetchImpl })).rejects.toThrow("funding_discoveries read failed");
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });

  it("refuses to publish without Legion KV config", async () => {
    const fetchImpl = vi.fn(async (url: string) => url.includes("funding_discoveries") ? reply([]) : reply([]));
    await expect(refreshFundingFeed({ ...config, legionKv: undefined, fetchImpl })).rejects.toThrow("KV is not configured");
  });

  it("stops instead of re-enriching when the profile cache read fails", async () => {
    const fetchImpl = vi.fn(async (url: string) => {
      if (url.includes("funding_discoveries")) return reply([{ company_name: "Acme", company_domain: "acme.com" }]);
      if (url.includes("legion_company_profiles")) return reply({}, 500);
      return reply([]);
    });
    await expect(refreshFundingFeed({ ...config, fetchImpl })).rejects.toThrow("legion_company_profiles read failed");
    expect(fetchImpl.mock.calls.some(([url]) => String(url).includes("api.cloudflare.com"))).toBe(false);
  });

  it("publishes cached profiles to Legion KV with the public shape only", async () => {
    const fetchImpl = vi.fn(async (url: string, init?: RequestInit) => {
      if (url.includes("funding_discoveries")) return reply([{ company_name: "Acme", company_domain: "acme.com", round_type: "Seed", discovered_date: "2026-09-29", products: "A tool" }]);
      if (url.includes("signal_companies")) {
        expect(url).toContain("select=domain,industry_label");
        return reply([{ domain: "acme.com", industry_label: "DevTools" }]);
      }
      if (url.includes("legion_company_profiles")) {
        expect(url).toContain("select=domain,hq,employees,founders");
        return reply([{ domain: "acme.com", hq: "Austin, TX, US", employees: "20 - 99", founders: [{ name: "Ada", title: "Founder", linkedin: "https://www.linkedin.com/in/ada", email: "ada@acme.com" }] }]);
      }
      if (url.includes(KV_PATH)) {
        expect(init?.method).toBe("PUT");
        expect(init?.headers).toMatchObject({ Authorization: "Bearer cf-token" });
        const payload = JSON.parse(String(init?.body));
        expect(payload).toEqual(expect.objectContaining({ updatedAt: config.now.toISOString(), count: 1 }));
        expect(payload.rows[0]).toMatchObject({ hq: "Austin, TX, US", employees: "20 - 99", industry: "DevTools", description: "A tool" });
        expect(payload.rows[0].founders).toEqual([{ name: "Ada", title: "Founder", linkedin: "https://www.linkedin.com/in/ada" }]);
        expect(String(init?.body)).not.toContain("ada@acme.com");
        return reply({ success: true });
      }
      throw new Error(`Unexpected request ${url}`);
    });
    const feed = await refreshFundingFeed({ ...config, fetchImpl });
    expect(feed.enriched).toBe(0);
    expect(fetchImpl.mock.calls.filter(([url]) => String(url).includes(KV_PATH))).toHaveLength(1);
    expect(fetchImpl.mock.calls.map(([url]) => String(url)).join(" ")).not.toContain("/storage/v1/");
  });

  it("reports KV write failures", async () => {
    const fetchImpl = vi.fn(async (url: string) => url.includes("funding_discoveries") ? reply([{ company_name: "Acme", company_domain: "acme.com" }]) : url.includes(KV_PATH) ? reply({}, 500) : reply([]));
    await expect(refreshFundingFeed({ ...config, fetchImpl })).rejects.toThrow("KV write failed with HTTP 500");
  });
});
