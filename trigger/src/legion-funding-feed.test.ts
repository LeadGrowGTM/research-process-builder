import { afterEach, describe, expect, it, vi } from "vitest";
import { buildFundingFeedRows, compactUsd, fundingSignal, refreshFundingFeed } from "./legion-funding-feed.js";

const config = {
  url: "https://example.supabase.co",
  key: "test-key",
  legionKv: { accountId: "acct", namespaceId: "ns", token: "cf-token" },
  now: new Date("2026-09-30T12:00:00.000Z"),
};
const KV_PATH = "/accounts/acct/storage/kv/namespaces/ns/values/signals.json";

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

describe("fundingSignal", () => {
  const base = buildFundingFeedRows([{ company_name: "Acme", company_domain: "acme.com", round_type: "series a", amount_raised: "$12 Million", amount_raised_usd: 12_000_000, industry: "Fintech", discovered_date: "2026-09-29", source_url: "https://techcrunch.com/acme" }], [], [])[0];

  it("maps a funding row to the shared signal shape", () => {
    expect(fundingSignal(base)).toMatchObject({
      type: "funding", company: "Acme", domain: "acme.com", headline: "Series A round of $12M",
      metric: { label: "Raised", value: "$12M", sort: 12_000_000 }, tags: ["Series A", "Fintech"], date: "2026-09-29", source: "TechCrunch",
    });
  });

  it("falls back to the raw amount text and a generic headline", () => {
    const signal = fundingSignal({ ...base, round: "Unknown", amountUsd: null, amount: "undisclosed" });
    expect(signal).toMatchObject({ headline: "Funding round of undisclosed", metric: { value: "undisclosed", sort: null }, tags: ["Fintech"] });
    expect(fundingSignal({ ...base, amountUsd: null, amount: null }).metric).toBeNull();
  });

  it("formats compact dollar amounts", () => {
    expect([compactUsd(750_000), compactUsd(1_200_000), compactUsd(12_000_000), compactUsd(1_500_000_000), compactUsd(500)]).toEqual(["$750K", "$1.2M", "$12M", "$1.5B", "$500"]);
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
        expect(payload.signals[0]).toMatchObject({ type: "funding", location: "Austin, TX, US", summary: "A tool", tags: ["Seed", "DevTools"], details: { employees: "20 - 99" } });
        expect(payload.signals[0].people).toEqual([{ name: "Ada", title: "Founder", linkedin: "https://www.linkedin.com/in/ada" }]);
        expect(payload).not.toHaveProperty("rows");
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

  it("enriches an uncached company, stores its spend, and publishes the founder", async () => {
    const upserts: Array<Record<string, unknown>> = [];
    const fetchImpl = vi.fn(async (url: string, init?: RequestInit) => {
      if (url.includes("funding_discoveries")) return reply([{ company_name: "Acme", company_domain: "acme.com" }]);
      if (url.includes("legion_company_profiles") && init?.method === "POST") {
        expect(init.headers).toMatchObject({ "Content-Profile": "leadgrow_knowledge", Prefer: "resolution=merge-duplicates" });
        upserts.push(...JSON.parse(String(init.body)));
        return reply([], 201);
      }
      if (url.includes(KV_PATH)) {
        expect(JSON.parse(String(init?.body)).signals[0].people).toEqual([{ name: "Ada Doe", title: "Co-Founder", linkedin: "https://www.linkedin.com/in/ada" }]);
        return reply({ success: true });
      }
      return reply([]);
    });
    const qe = vi.fn(async () => reply({ data: [{ first_name: "Ada", last_name: "Doe", title: "Co-Founder", email: "ada@acme.com", employee_phone: "6505550100", employee_linkedin: "https://www.linkedin.com/in/ada", city: "Austin", region_code: "TX", country_code: "US" }] }));
    vi.stubGlobal("fetch", qe);
    const feed = await refreshFundingFeed({ ...config, fetchImpl, enrichment: { quickEnrichKey: "qe", aiArkKey: "ark", quickEnrichUsdPerCredit: 0.001 } });
    vi.unstubAllGlobals();
    expect(feed).toMatchObject({ enriched: 1, costUsd: 0.001 });
    expect(upserts).toHaveLength(1);
    expect(upserts[0]).toMatchObject({ domain: "acme.com", hq: "Austin, TX, US", cost_usd: 0.001, calls: [{ provider: "quickenrich", units: 1, costUsd: 0.001 }] });
    expect(JSON.stringify(upserts)).not.toMatch(/ada@acme\.com|6505550100/);
  });

  it("reports KV write failures", async () => {
    const fetchImpl = vi.fn(async (url: string) => url.includes("funding_discoveries") ? reply([{ company_name: "Acme", company_domain: "acme.com" }]) : url.includes(KV_PATH) ? reply({}, 500) : reply([]));
    await expect(refreshFundingFeed({ ...config, fetchImpl })).rejects.toThrow("KV write failed with HTTP 500");
  });
});
