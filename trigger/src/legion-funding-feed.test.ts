import { afterEach, describe, expect, it, vi } from "vitest";
import { buildFundingFeedRows, ensurePublicFeedsBucket, refreshFundingFeed } from "./legion-funding-feed.js";

const config = { url: "https://example.supabase.co", key: "test-key", now: new Date("2026-09-30T12:00:00.000Z") };

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
    const founders = [1, 2, 3, 4].map(n => ({ company_domain: "acme.com", full_name: `Founder ${n}`, title: "Founder", linkedin_url: "ftp://example.com/profile", email: `person${n}@example.test`, personal_email: "person@example.test" }));
    const rows = buildFundingFeedRows([{ company_name: "Acme", company_domain: "acme.com", source_url: "javascript:alert(1)", logo_url: "ftp://example.com/logo" }], [], founders);
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
      [1, 2, 3, 4].map((n) => ({ company_domain: "acme.com", full_name: `Founder ${n}`, title: "CEO", linkedin_url: `https://linkedin.com/in/${n}` })),
    );
    expect(rows).toHaveLength(1);
    expect(rows[0]).toEqual(expect.objectContaining({ round: "Series A", amountUsd: 12_000_000, industry: "AI/ML", description: "Builds software", employees: 42, hq: "Toronto", founded: 2020, source: "TechCrunch" }));
    expect(rows[0].founders).toHaveLength(3);
    expect(Object.keys(rows[0])).toEqual(["company", "domain", "logo", "round", "amount", "amountUsd", "investors", "industry", "description", "employees", "hq", "founded", "founders", "date", "source", "sourceUrl"]);
  });
});

describe("ensurePublicFeedsBucket", () => {
  it("skips creation when the bucket already exists", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(reply({}));
    await ensurePublicFeedsBucket({ ...config, fetchImpl });
    expect(fetchImpl).toHaveBeenCalledOnce();
    expect(String(fetchImpl.mock.calls[0][0])).toContain("/storage/v1/bucket/public-feeds");
    expect(fetchImpl.mock.calls[0][1]).toMatchObject({ headers: expect.objectContaining({ apikey: "test-key", Authorization: "Bearer test-key" }) });
  });

  it("creates the bucket on 404 with the public-feeds payload", async () => {
    const fetchImpl = vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        expect(String(url)).toContain("/storage/v1/bucket");
        expect(JSON.parse(String(init?.body))).toEqual({ id: "public-feeds", name: "public-feeds", public: true });
        return reply({});
      }
      return reply({ message: "not found" }, 404);
    });
    await ensurePublicFeedsBucket({ ...config, fetchImpl });
    expect(fetchImpl).toHaveBeenCalledTimes(2);
  });

  it("tolerates a 409 create race and surfaces real failures", async () => {
    const conflict = vi.fn().mockResolvedValueOnce(reply({}, 404)).mockResolvedValueOnce(reply({}, 409));
    await ensurePublicFeedsBucket({ ...config, fetchImpl: conflict });
    expect(conflict).toHaveBeenCalledTimes(2);
    const checkFailed = vi.fn().mockResolvedValue(reply({}, 500));
    await expect(ensurePublicFeedsBucket({ ...config, fetchImpl: checkFailed })).rejects.toThrow("check failed with HTTP 500");
    const createFailed = vi.fn().mockResolvedValueOnce(reply({}, 404)).mockResolvedValueOnce(reply({}, 500));
    await expect(ensurePublicFeedsBucket({ ...config, fetchImpl: createFailed })).rejects.toThrow("create failed with HTTP 500");
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

  it("does not upload malformed core responses or a failed fallback", async () => {
    const invalid = vi.fn().mockResolvedValue(reply({ rows: [] }));
    await expect(refreshFundingFeed({ ...config, fetchImpl: invalid })).rejects.toThrow("invalid core response");
    expect(invalid).toHaveBeenCalledOnce();
    const failing = vi.fn().mockResolvedValue(reply({}, 400));
    await expect(refreshFundingFeed({ ...config, fetchImpl: failing })).rejects.toThrow("read failed");
    expect(failing).toHaveBeenCalledTimes(2);
  });

  it("degrades malformed optional results and reports upload failures", async () => {
    const fetchImpl = vi.fn(async (url: string) => url.includes("funding_discoveries") ? reply([{ company_name: "Acme", company_domain: "acme.com" }]) : url.includes("/storage/v1/object/") ? reply({}, 500) : reply({}));
    await expect(refreshFundingFeed({ ...config, fetchImpl })).rejects.toThrow("upload failed with HTTP 500");
    expect(fetchImpl.mock.calls.filter(([url]) => url.includes("/storage/v1/object/"))).toHaveLength(1);
  });
  it("throws on a failed core read and makes no upload", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(reply({ message: "no" }, 500));
    await expect(refreshFundingFeed({ ...config, fetchImpl })).rejects.toThrow("funding_discoveries read failed");
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });

  it("degrades when optional profile reads fail and uploads the core feed", async () => {
    const fetchImpl = vi.fn(async (url: string, _init?: RequestInit) => {
      if (url.includes("funding_discoveries")) return reply([{ company_name: "Acme", company_domain: "acme.com", round_type: "Seed", discovered_date: "2026-09-29", source_url: "https://example.com/acme" }]);
      if (url.includes("signal_companies") || url.includes("founder_contacts_public")) return reply({}, 404);
      if (url.includes("/storage/")) return reply({});
      throw new Error(`Unexpected request ${url}`);
    });
    const feed = await refreshFundingFeed({ ...config, fetchImpl });
    expect(feed.rows[0].description).toBe("");
    expect(feed.rows[0].founders).toEqual([]);
    const calls = fetchImpl.mock.calls.map(([url]) => String(url));
    expect(calls.some((url) => url.includes("/storage/v1/object/public-feeds/funding/feed.json"))).toBe(true);
  });

  it("reads descriptions from funding_discoveries and only industry_label from signal_companies", async () => {
    const fetchImpl = vi.fn(async (url: string) => {
      if (url.includes("funding_discoveries")) return reply([{ company_name: "Acme", company_domain: "acme.com", round_type: "Seed", discovered_date: "2026-09-29", company_description: "Discovery text", products: "Discovery product" }]);
      if (url.includes("signal_companies")) {
        expect(url).toContain("select=domain,industry_label");
        return reply([{ domain: "acme.com", industry_label: "DevTools" }]);
      }
      if (url.includes("founder_contacts_public")) return reply([]);
      if (url.includes("/storage/v1/bucket")) return reply({});
      if (url.includes("/storage/")) return reply({});
      throw new Error(`Unexpected request ${url}`);
    });
    const feed = await refreshFundingFeed({ ...config, fetchImpl });
    expect(feed.rows[0]).toMatchObject({ description: "Discovery text", industry: "DevTools" });
    const signalCall = String(fetchImpl.mock.calls.find(([url]) => String(url).includes("signal_companies"))?.[0] ?? "");
    expect(signalCall).not.toContain("company_description");
    expect(signalCall).not.toContain("products");
    const bucketCalls = fetchImpl.mock.calls.map(([url]) => String(url)).filter((url) => url.includes("/storage/v1/bucket"));
    const uploadCalls = fetchImpl.mock.calls.map(([url]) => String(url)).filter((url) => url.includes("/storage/v1/object/"));
    expect(bucketCalls.length).toBeGreaterThan(0);
    expect(uploadCalls).toHaveLength(1);
  });

  it("uses explicit profile projections and uploads the serialized public shape", async () => {
    const fetchImpl = vi.fn(async (url: string, init?: RequestInit) => {
      if (url.includes("funding_discoveries")) return reply([{ company_name: "Acme", company_domain: "acme.com", round_type: "Seed", discovered_date: "2026-09-29", products: "A tool" }]);
      if (url.includes("signal_companies")) return reply([{ domain: "acme.com", industry_label: "DevTools" }]);
      if (url.includes("founder_contacts_public")) return reply([{ company_domain: "acme.com", full_name: "Ada", title: "Founder", linkedin_url: "https://linkedin.com/in/ada" }]);
      if (url.includes("/storage/v1/bucket/")) return reply({});
      if (url.includes("/storage/v1/object/")) {
        expect(init?.method).toBe("POST");
        expect(init?.headers).toEqual(expect.objectContaining({ "Content-Type": "application/json", "Cache-Control": "max-age=900", "x-upsert": "true" }));
        const payload = JSON.parse(String(init?.body));
        expect(payload).toEqual(expect.objectContaining({ updatedAt: config.now.toISOString(), count: 1 }));
        expect(payload.rows[0].founders[0]).toEqual({ name: "Ada", title: "Founder", linkedin: "https://linkedin.com/in/ada" });
        expect(payload.rows[0].description).toBe("A tool");
        return reply({});
      }
      throw new Error(`Unexpected request ${url}`);
    });
    await refreshFundingFeed({ ...config, fetchImpl });
    const requests = fetchImpl.mock.calls.map(([url]) => String(url));
    expect(requests.find((url) => url.includes("founder_contacts_public"))).toContain("select=company_domain,full_name,title,linkedin_url");
    expect(requests.join(" ")).not.toContain("select=*");
  });
});
