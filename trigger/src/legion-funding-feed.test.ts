import { createHash } from "node:crypto";
import { afterEach, describe, expect, it, vi } from "vitest";
import { buildFundingFeedRows, canonicalFeedDocument, compactUsd, companySignal, fundingClientFromEnv, fundingSignal, refreshFundingFeed, SEARCH_STRATEGY_VERSION, SECONDARY_NONE_RECHECK_BEFORE, signalsVersion, writerClientFromEnv } from "./legion-funding-feed.js";
import { buildRounds, type FundingReport } from "./pipeline/funding-rounds.js";

const source = { url: "https://example.supabase.co", key: "test-key" };
const config = {
  url: source.url,
  key: source.key,
  sources: {
    productLaunches: source,
    jobSignals: source,
  },
  legionKv: { accountId: "acct", namespaceId: "ns", token: "cf-token" },
  now: new Date("2026-09-30T12:00:00.000Z"),
};
const KV_PATH = "%2Fp1.json";
const KV_CURRENT = "/values/signals%2Fcurrent.json";
const CURRENT_KEY = "signals/current.json";

function reply(body: unknown, status = 200, extra: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json", ...extra } });
}

function provenEmpty(): Response {
  return reply([], 200, { "content-range": "*/0" });
}

function isAdditionalTable(url: string): boolean {
  return /\/rest\/v1\/(?:product_launches|game_signals|game_job_signals)\?/.test(url);
}

function fallback(url: string, init?: RequestInit): Response {
  if (isAdditionalTable(url)) return provenEmpty();
  if (url.includes("api.cloudflare.com")) {
    const method = init?.method ?? "GET";
    if (method === "GET") return reply({}, 404);
    if (method === "PUT") return reply({ success: true });
  }
  return reply([]);
}

function versionedWrites(writes: string[]): string[] {
  return writes.map((item) => item.replace(/^signals\/[0-9a-f]{64}\//, "signals/"));
}

function storedPage(bodies: Map<string, string>, page: number): Record<string, any> {
  const suffix = `/p${page}.json`;
  const keys = [...bodies.keys()].filter((key) => key.endsWith(suffix) && /^signals\/[0-9a-f]{64}\/p\d+\.json$/.test(key));
  if (keys.length !== 1) throw new Error(`expected one signals page ${page}, saw ${keys.length}`);
  return JSON.parse(bodies.get(keys[0])!);
}

function storedRows(url: string, rows: unknown[]): Response {
  const offset = Number(/offset=(\d+)/.exec(url)?.[1] ?? 0);
  return reply(rows.slice(offset));
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

  it("keeps a public author query and drops a token on the same URL", () => {
    const rows = buildFundingFeedRows(
      [{
        company_name: "Acme",
        company_domain: "acme.com",
        source_url: "https://news.example/round?author=editor&utm_medium=social",
        logo_url: "https://news.example/logo?author=desk",
      }],
      [],
      [{ domain: "acme.com", founders: [{ name: "Ada", title: "Founder", linkedin: "https://www.linkedin.com/in/ada?author=desk" }] }],
    );
    expect(rows[0].sourceUrl).toBe("https://news.example/round?author=editor&utm_medium=social");
    expect(rows[0].logo).toBe("https://news.example/logo?author=desk");
    expect(rows[0].founders[0].linkedin).toBe("https://www.linkedin.com/in/ada?author=desk");
    const stripped = buildFundingFeedRows(
      [{ company_name: "Acme", company_domain: "acme.com", source_url: "https://news.example/round?author=editor&token=abc" }],
      [],
      [],
    );
    expect(stripped[0].sourceUrl).toBe("");
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

describe("feed text cleanup", () => {
  it("decodes HTML entities from source text", () => {
    const [row] = buildFundingFeedRows([{ company_name: "Acme &amp; Co", company_domain: "acme.com", industry: "AI, Software &amp; SaaS" }], [], []);
    expect(row).toMatchObject({ company: "Acme & Co", industry: "AI, Software & SaaS" });
  });

  it("decodes double-escaped and numeric entities and leaves plain text", () => {
    const [row] = buildFundingFeedRows(
      [{
        company_name: "O&#39;Brien Robotics",
        company_domain: "obrien.com",
        industry: "AI, Robotics &amp;amp; Automation",
        company_description: "Plain robotics text",
        location: "Austin",
        lead_investors: "North &amp; Star",
        amount_raised: "$4M",
        round_type: "seed",
        discovered_date: "2026-09-29",
      }],
      [],
      [{ domain: "obrien.com", founders: [{ name: "Pat O&#39;Brien", title: "CEO &amp;amp; Founder", linkedin: "https://www.linkedin.com/in/pat" }] }],
    );
    expect(row).toMatchObject({
      company: "O'Brien Robotics",
      industry: "AI, Robotics & Automation",
      description: "Plain robotics text",
      hq: "Austin",
      investors: "North & Star",
    });
    expect(row.founders[0]).toMatchObject({ name: "Pat O'Brien", title: "CEO & Founder" });
    const signal = fundingSignal(row);
    expect(signal.tags).toContain("AI, Robotics & Automation");
    expect(signal.company).toBe("O'Brien Robotics");
    expect(signal.summary).toBe("Plain robotics text");
    expect(signal.location).toBe("Austin");
    expect(signal.headline).toBe("Seed round of $4M");
    expect(signal.people[0]).toMatchObject({ name: "Pat O'Brien", title: "CEO & Founder" });
    expect(signal.details.investors).toBe("North & Star");
  });

  it("drops link-shortener domains and their logos", () => {
    const [row] = buildFundingFeedRows([{ company_name: "Reflection AI", company_domain: "t.co", logo_url: "https://www.google.com/s2/favicons?domain=t.co&sz=128" }], [], []);
    expect(row).toMatchObject({ domain: "", logo: null });
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

describe("companySignal sources", () => {
  function fundingReport(overrides: Partial<FundingReport> = {}): FundingReport {
    return {
      company: "Acme", domain: "acme.com", logo: null, round: "Seed", amount: "$4M", amountUsd: 4_000_000,
      investors: "Northstar", industry: "Fintech", description: "Builds payments", employees: 12, hq: "Austin", founded: 2020, founders: [],
      date: "2026-01-10", source: "@raisingfi on X", sourceUrl: "https://x.com/raisingfi/status/1", ...overrides,
    };
  }

  it("publishes no source when the only report is raisingfi and Brave found nothing", () => {
    const [company] = buildRounds([fundingReport()]);
    const signal = companySignal(company, new Map());
    expect(signal).toMatchObject({ source: null, sourceUrl: "", headline: "Seed round of $4M", summary: "Builds payments", tags: ["Seed", "Fintech"] });
    expect(signal.details).toEqual({ investors: "Northstar", employees: 12, founded: 2020 });
    expect(JSON.stringify(signal)).not.toMatch(/raisingfi|x\.com|twitter\.com/i);
  });

  it("uses a non-X Brave secondary for the latest round and the same rule for earlier rounds", () => {
    const [company] = buildRounds([
      fundingReport({ date: "2026-01-10", round: "Seed", amount: "$4M", amountUsd: 4_000_000, sourceUrl: "https://x.com/raisingfi/status/1" }),
      fundingReport({ date: "2026-05-01", round: "Series A", amount: "$12M", amountUsd: 12_000_000, sourceUrl: "https://twitter.com/raisingfi/status/2" }),
      fundingReport({ date: "2026-05-02", round: "Series A", amount: "$12M", amountUsd: 12_000_000, source: "TechCrunch", sourceUrl: "https://techcrunch.com/acme-a" }),
      fundingReport({ date: "2026-09-20", round: "Series B", amount: "$20M", amountUsd: 20_000_000, sourceUrl: "https://x.com/raisingfi/status/3" }),
    ]);
    const seedKey = company.earlier[1].key;
    const shown = companySignal(company, new Map([
      [company.latest.key, { name: "Axios", url: "https://www.axios.com/acme-b" }],
      [seedKey, { name: "Reuters", url: "https://www.reuters.com/acme-seed" }],
    ]));
    expect(shown).toMatchObject({ source: "Axios", sourceUrl: "https://www.axios.com/acme-b" });
    expect(shown.earlier[0]).toMatchObject({ round: "Series A", source: "TechCrunch", sourceUrl: "https://techcrunch.com/acme-a" });
    expect(shown.earlier[1]).toMatchObject({ round: "Seed", source: "Reuters", sourceUrl: "https://www.reuters.com/acme-seed" });

    const hidden = companySignal(company, new Map([
      [company.latest.key, { name: "@raisingfi on X", url: "https://x.com/raisingfi/status/9" }],
      [seedKey, { name: "Wire", url: "https://twitter.com/someone/status/4" }],
    ]));
    expect(hidden).toMatchObject({ source: null, sourceUrl: "" });
    expect(hidden.earlier[0]).toMatchObject({ source: "TechCrunch", sourceUrl: "https://techcrunch.com/acme-a" });
    expect(hidden.earlier[1]).toMatchObject({ source: null, sourceUrl: "" });
    expect(JSON.stringify(shown)).not.toMatch(/raisingfi|x\.com|twitter\.com/i);
    expect(JSON.stringify(hidden)).not.toMatch(/raisingfi|x\.com|twitter\.com/i);
  });
});

describe("refreshFundingFeed", () => {
  it("retries missing optional funding columns with core projection", async () => {
    const fetchImpl = vi.fn(async (url: string, init?: RequestInit) => {
      if (url.includes("funding_discoveries") && url.includes("hq_location")) return reply({ code: "42703" }, 400);
      if (url.includes("funding_discoveries")) return provenEmpty();
      return fallback(url, init);
    });
    const feed = await refreshFundingFeed({ ...config, fetchImpl });
    expect(feed.count).toBe(0);
    const retryCall = fetchImpl.mock.calls[1];
    expect(String(retryCall?.[0])).not.toContain("hq_location");
    expect(retryCall?.[1]?.headers).toMatchObject({ "Accept-Profile": "public" });
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
    const fetchImpl = vi.fn(async (url: string) => url.includes("funding_discoveries") ? provenEmpty() : reply([]));
    await expect(refreshFundingFeed({ ...config, legionKv: undefined, fetchImpl })).rejects.toThrow("KV is not configured");
  });

  it("stops instead of re-enriching when the profile cache read fails", async () => {
    const fetchImpl = vi.fn(async (url: string, init?: RequestInit) => {
      if (url.includes("funding_discoveries")) return storedRows(url, [{ company_name: "Acme", company_domain: "acme.com" }]);
      if (url.includes("legion_company_profiles")) return reply({}, 500);
      return fallback(url, init);
    });
    await expect(refreshFundingFeed({ ...config, fetchImpl })).rejects.toThrow("legion_company_profiles read failed");
    expect(fetchImpl.mock.calls.some(([url]) => String(url).includes("api.cloudflare.com"))).toBe(false);
  });

  it("publishes cached profiles to Legion KV with the public shape only", async () => {
    const fetchImpl = vi.fn(async (url: string, init?: RequestInit) => {
      if (url.includes("funding_discoveries")) return storedRows(url, [{ company_name: "Acme", company_domain: "acme.com", round_type: "Seed", discovered_date: "2026-09-29", products: "A tool" }]);
      if (url.includes("signal_companies")) {
        expect(url).toContain("select=domain,industry_label");
        return storedRows(url, [{ domain: "acme.com", industry_label: "DevTools" }]);
      }
      if (url.includes("legion_company_profiles")) {
        expect(url).toContain("select=domain,hq,employees,founders");
        return storedRows(url, [{ domain: "acme.com", hq: "Austin, TX, US", employees: "20 - 99", founders: [{ name: "Ada", title: "Founder", linkedin: "https://www.linkedin.com/in/ada", email: "ada@acme.com" }] }]);
      }
      if (url.includes("legion_funding_rounds")) return reply([]);
      if (isAdditionalTable(url)) return provenEmpty();
      if (url.includes(KV_CURRENT)) return init?.method === "PUT" ? reply({ success: true }) : reply({}, 404);
      if (url.includes("api.cloudflare.com") && (init?.method ?? "GET") === "GET") return reply({}, 404);
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
    expect(fetchImpl.mock.calls.filter(([url, init]) => String(url).includes(KV_PATH) && (init as RequestInit | undefined)?.method === "PUT")).toHaveLength(1);
    expect(fetchImpl.mock.calls.map(([url]) => String(url)).join(" ")).not.toContain("/storage/v1/");
  });

  it("enriches an uncached company, stores its spend, and publishes the founder", async () => {
    const upserts: Array<Record<string, unknown>> = [];
    const fetchImpl = vi.fn(async (url: string, init?: RequestInit) => {
      if (url.includes("funding_discoveries")) return storedRows(url, [{ company_name: "Acme", company_domain: "acme.com", discovered_date: "2026-09-29" }]);
      if (url.includes("legion_company_profiles") && init?.method === "POST") {
        expect(init.headers).toMatchObject({ "Content-Profile": "leadgrow_knowledge", Prefer: "resolution=merge-duplicates" });
        upserts.push(...JSON.parse(String(init.body)));
        return reply([], 201);
      }
      if (url.includes("api.cloudflare.com") && (init?.method ?? "GET") !== "PUT") return reply({}, 404);
      if (url.includes(KV_PATH) && init?.method === "PUT") {
        expect(JSON.parse(String(init.body)).signals[0].people).toEqual([{ name: "Ada Doe", title: "Co-Founder", linkedin: "https://www.linkedin.com/in/ada" }]);
        return reply({ success: true });
      }
      return fallback(url, init);
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

  it("publishes raised-again companies first, with earlier rounds and a Brave source in place of raisingfi", async () => {
    const patches: Array<{ url: string; body: Record<string, unknown> }> = [];
    let page1: Record<string, any> | null = null;
    const fetchImpl = vi.fn(async (url: string, init?: RequestInit) => {
      if (url.includes("funding_discoveries")) return storedRows(url, [
        { company_name: "Acme", company_domain: "acme.com", round_type: "Series A", amount_raised_usd: 20_000_000, discovered_date: "2026-09-20", source_url: "https://x.com/raisingfi/status/2", source_name: "@raisingfi on X" },
        { company_name: "Beta", company_domain: "beta.io", round_type: "Seed", amount_raised_usd: 3_000_000, discovered_date: "2026-08-01", source_url: "https://techcrunch.com/beta", source_name: "TechCrunch" },
        { company_name: "Acme", company_domain: "acme.com", round_type: "Seed", amount_raised_usd: 4_000_000, discovered_date: "2026-01-10", source_url: "https://techcrunch.com/acme-seed", source_name: "TechCrunch" },
      ]);
      if (url.includes("legion_funding_rounds") && init?.method === "PATCH") {
        patches.push({ url, body: JSON.parse(String(init.body)) });
        return reply([], 200);
      }
      if (url.includes(KV_PATH) && init?.method === "PUT") {
        page1 = JSON.parse(String(init.body));
        return reply({ success: true });
      }
      if (url.includes(KV_CURRENT)) return init?.method === "PUT" ? reply({ success: true }) : reply({}, 404);
      return fallback(url, init);
    });
    vi.stubGlobal("fetch", vi.fn(async () => reply({ news: { results: [{ url: "https://www.axios.com/acme-series-a", title: "Acme raises $20M Series A" }] } })));
    const result = await refreshFundingFeed({ ...config, fetchImpl, brave: { apiKey: "brave", perRun: 10, usdPerQuery: 0.005 } });
    vi.unstubAllGlobals();

    expect(result).toMatchObject({
      count: 2, rounds: 3, raisedAgain: 1, braveLookups: 1, braveFound: 1, pages: 1,
      coverage: { sourceRows: 3, duplicateRows: 0, excludedNoCompany: 0, excludedUndated: 0, mergedReports: 3, distinctRounds: 3, companySignals: 2, publishedSignals: 2 },
    });
    expect(patches).toHaveLength(1);
    expect(decodeURIComponent(patches[0].url)).toContain("round_key=eq.acme.com|2026-09-20");
    expect(patches[0].body).toMatchObject({ secondary_status: "found", secondary_source: { url: "https://www.axios.com/acme-series-a" } });
    expect(page1).toMatchObject({ count: 2, pages: 1, page: 1, types: { funding: 2 } });
    const [acme, beta] = page1!.signals;
    expect(acme).toMatchObject({ company: "Acme", raisedAgain: true, source: "axios.com", sourceUrl: "https://www.axios.com/acme-series-a", metric: { value: "$20M" } });
    expect(acme.earlier).toEqual([expect.objectContaining({ round: "Seed", value: "$4M", date: "2026-01-10", source: "TechCrunch", sourceUrl: "https://techcrunch.com/acme-seed" })]);
    expect(JSON.stringify(page1)).not.toMatch(/raisingfi|x\.com|twitter\.com/i);
    expect(beta).toMatchObject({ company: "Beta", raisedAgain: false, earlier: [] });
  });

  it("re-checks a none row from before the strategy cutoff and leaves a later none row", async () => {
    expect(SEARCH_STRATEGY_VERSION).toBe(2);
    expect(SECONDARY_NONE_RECHECK_BEFORE).toBe("2026-10-05T16:00:00Z");
    const patches: string[] = [];
    const fetchImpl = vi.fn(async (url: string, init?: RequestInit) => {
      if (url.includes("funding_discoveries")) return storedRows(url, [
        { company_name: "Acme", company_domain: "acme.com", round_type: "Series A", amount_raised_usd: 20_000_000, discovered_date: "2026-09-20", source_url: "https://x.com/raisingfi/status/2", source_name: "@raisingfi on X" },
        { company_name: "Beta", company_domain: "beta.io", round_type: "Seed", amount_raised_usd: 3_000_000, discovered_date: "2026-08-01", source_url: "https://x.com/raisingfi/status/3", source_name: "@raisingfi on X" },
      ]);
      if (url.includes("legion_funding_rounds") && (init?.method ?? "GET") === "GET") {
        expect(url).toContain("secondary_checked_at");
        return ranged([
          { round_key: "acme.com|2026-09-20", secondary_status: "none", secondary_source: null, secondary_checked_at: "2026-10-05T15:59:59Z" },
          { round_key: "beta.io|2026-08-01", secondary_status: "none", secondary_source: null, secondary_checked_at: SECONDARY_NONE_RECHECK_BEFORE },
        ], 0, 2);
      }
      if (url.includes("legion_funding_rounds") && init?.method === "PATCH") {
        patches.push(decodeURIComponent(url));
        return reply([], 200);
      }
      if (url.includes(KV_CURRENT)) return init?.method === "PUT" ? reply({ success: true }) : reply({}, 404);
      return fallback(url, init);
    });
    const search = vi.fn(async (url: string, init?: RequestInit) => {
      const parsed = new URL(url);
      expect(`${parsed.origin}${parsed.pathname}`).toBe("https://google-search74.p.rapidapi.com/");
      expect(parsed.searchParams.get("limit")).toBe("10");
      expect(init?.headers).toMatchObject({ "x-rapidapi-key": "rapid-key", "x-rapidapi-host": "google-search74.p.rapidapi.com" });
      return reply({
        results: [{ url: "https://www.axios.com/acme-series-a", title: "Acme raises $20M Series A", description: "Acme announced funding." }],
      });
    });
    vi.stubGlobal("fetch", search);
    const result = await refreshFundingFeed({
      ...config,
      fetchImpl,
      brave: { apiKey: "brave", perRun: 10, usdPerQuery: 0.005 },
      google: { apiKey: "rapid-key", perRun: 10, usdPerQuery: 0 },
    });
    vi.unstubAllGlobals();

    expect(search).toHaveBeenCalledTimes(1);
    expect(patches).toEqual([expect.stringContaining("round_key=eq.acme.com|2026-09-20")]);
    expect(result).toMatchObject({ searchLookups: 1, searchFound: 1, braveLookups: 0, braveFound: 0, costUsd: 0 });
  });

  it("reports KV write failures", async () => {
    const fetchImpl = vi.fn(async (url: string, init?: RequestInit) => {
      if (url.includes("funding_discoveries")) return storedRows(url, [{ company_name: "Acme", company_domain: "acme.com", discovered_date: "2026-09-29" }]);
      if (url.includes("api.cloudflare.com") && (init?.method ?? "GET") === "GET") return reply({}, 404);
      if (url.includes("api.cloudflare.com") && init?.method === "PUT" && url.includes(KV_PATH)) return reply({}, 500);
      return fallback(url, init);
    });
    await expect(refreshFundingFeed({ ...config, fetchImpl })).rejects.toThrow("KV write failed with HTTP 500");
    const writes = writesOf(fetchImpl);
    expect(writes).toContain("post");
    expect(writes).not.toContain("delete");
    expect(writes.filter((item) => item.startsWith("signals/"))).toEqual([expect.stringMatching(/^signals\/[0-9a-f]{64}\/p1\.json$/)]);
  });
});

function discovery(id: string, company: string, domain: string, date: string, extra: Record<string, unknown> = {}) {
  return {
    id, company_name: company, company_domain: domain, round_type: "Seed", amount_raised_usd: 1_000_000,
    discovered_date: date, source_url: `https://example.com/${id}`, source_name: "TechCrunch", ...extra,
  };
}

function ranged(body: unknown[], start: number, total: number): Response {
  const end = start + body.length - 1;
  return reply(body, 200, { "content-range": `${start}-${end}/${total}` });
}

function writesOf(fetchImpl: ReturnType<typeof vi.fn>): string[] {
  return fetchImpl.mock.calls.flatMap(([url, init]) => {
    const method = (init as RequestInit | undefined)?.method;
    if (method === "DELETE") return ["delete"];
    if (method === "POST") return ["post"];
    if (method === "PUT" && String(url).includes("api.cloudflare.com")) {
      return [decodeURIComponent(String(url).split("/values/")[1] ?? "")];
    }
    return [];
  });
}

const MANY = Array.from({ length: 501 }, (_, i) => discovery(String(i + 1), `Company ${i}`, `c${i}.example`, "2026-09-01"));

function manyFeed(fail: "none" | "p2" | "p1" | "current", head?: unknown) {
  const bodies = new Map<string, string>();
  const fetchImpl = vi.fn(async (url: string, init?: RequestInit) => {
    if (url.includes("funding_discoveries")) return storedRows(url, MANY);
    if (isAdditionalTable(url)) return provenEmpty();
    if (init?.method === "PUT" && url.includes("api.cloudflare.com")) {
      const key = decodeURIComponent(url.split("/values/")[1] ?? "");
      bodies.set(key, String(init.body));
      if (fail === "p2" && key.endsWith("/p2.json")) return reply({}, 500);
      if (fail === "p1" && key.endsWith("/p1.json")) return reply({}, 500);
      if (fail === "current" && key === CURRENT_KEY) return reply({}, 500);
      return reply({ success: true });
    }
    if (url.includes("api.cloudflare.com") && (init?.method ?? "GET") === "GET") {
      if (url.includes(KV_CURRENT) && head !== undefined) return reply(head);
      return reply({}, 404);
    }
    return reply([]);
  });
  return { fetchImpl, bodies };
}

describe("funding read coverage", () => {
  it("follows a server page smaller than the request and keeps the earlier round from the last page", async () => {
    const rows = [
      discovery("a1", "Acme", "acme.com", "2026-09-20", { round_type: "Series A", amount_raised_usd: 20_000_000, source_url: "https://techcrunch.com/acme-a" }),
      discovery("b", "Beta", "beta.example", "2026-08-01"),
      discovery("g", "Gamma", "gamma.example", "2026-07-01", { round_type: "Series B", amount_raised_usd: 8_000_000, source_url: "https://www.sec.gov/Archives/edgar/data/1/gamma", source_name: "SEC Form D" }),
      discovery("d", "Delta", "delta.example", "2026-06-01"),
      discovery("a2", "Acme", "acme.com", "2026-01-10", { amount_raised_usd: 4_000_000, source_url: "https://techcrunch.com/acme-seed" }),
    ];
    const offsets: number[] = [];
    const posts: Array<Record<string, unknown>> = [];
    let page1: Record<string, any> | null = null;
    const fetchImpl = vi.fn(async (url: string, init?: RequestInit) => {
      if (url.includes("funding_discoveries")) {
        const offset = Number(/offset=(\d+)/.exec(url)?.[1]);
        offsets.push(offset);
        return ranged(rows.slice(offset, offset + 2), offset, rows.length);
      }
      if (url.includes("legion_funding_rounds") && init?.method === "POST") {
        posts.push(...JSON.parse(String(init.body)));
        return reply([], 201);
      }
      if (url.includes(KV_PATH) && init?.method === "PUT") {
        page1 = JSON.parse(String(init.body));
        return reply({ success: true });
      }
      return fallback(url, init);
    });
    const result = await refreshFundingFeed({ ...config, fetchImpl });
    expect(offsets).toEqual([0, 2, 4]);
    expect(result.coverage).toMatchObject({ sourceRows: 5, duplicateRows: 0, excludedNoCompany: 0, excludedUndated: 0, mergedReports: 5, distinctRounds: 5, companySignals: 4, publishedSignals: 4 });
    expect(result.count).toBe(4);
    expect(posts.map((row) => row.round_key)).toContain("acme.com|2026-01-10");
    expect(page1).toMatchObject({ count: 4, pages: 1, types: { funding: 4 } });
    const signals = page1!.signals as Array<Record<string, any>>;
    expect(signals.map((signal) => signal.company)).toEqual(["Acme", "Beta", "Gamma", "Delta"]);
    expect(signals[0]).not.toHaveProperty("id");
    expect(signals[0].earlier).toEqual([expect.objectContaining({ round: "Seed", value: "$4M", date: "2026-01-10", source: "TechCrunch" })]);
    expect(signals.find((signal) => signal.company === "Gamma")).toMatchObject({ type: "funding", source: "SEC Form D" });
  });

  it("does not upsert, publish, or prune when a later page ends before the announced total", async () => {
    const rows = [discovery("a", "Acme", "acme.com", "2026-09-20"), discovery("b", "Beta", "beta.example", "2026-08-01")];
    const fetchImpl = vi.fn(async (url: string) => {
      if (!url.includes("funding_discoveries")) return reply([]);
      const offset = Number(/offset=(\d+)/.exec(url)?.[1]);
      return offset === 0 ? ranged(rows, 0, 5) : reply([]);
    });
    await expect(refreshFundingFeed({ ...config, fetchImpl })).rejects.toThrow("announced total");
    expect(writesOf(fetchImpl)).toEqual([]);
  });

  it("does not publish when a later page repeats an earlier range start", async () => {
    const rows = [discovery("a", "Acme", "acme.com", "2026-09-20"), discovery("b", "Beta", "beta.example", "2026-08-01"), discovery("c", "Gamma", "gamma.example", "2026-07-01")];
    const fetchImpl = vi.fn(async (url: string) => {
      if (!url.includes("funding_discoveries")) return reply([]);
      const offset = Number(/offset=(\d+)/.exec(url)?.[1]);
      const body = rows.slice(offset, offset + 2);
      return offset === 0 ? ranged(body, 0, 5) : ranged(body, 0, 5);
    });
    await expect(refreshFundingFeed({ ...config, fetchImpl })).rejects.toThrow("page boundary mismatch");
    expect(writesOf(fetchImpl)).toEqual([]);
  });

  it("does not publish when a later page repeats only identities already read", async () => {
    const acme = discovery("a", "Acme", "acme.com", "2026-09-20");
    const rows = [acme, discovery("b", "Beta", "beta.example", "2026-08-01"), acme];
    const fetchImpl = vi.fn(async (url: string) => {
      if (!url.includes("funding_discoveries")) return reply([]);
      const offset = Number(/offset=(\d+)/.exec(url)?.[1]);
      const body = rows.slice(offset, offset + 2);
      return ranged(body, offset, rows.length);
    });
    await expect(refreshFundingFeed({ ...config, fetchImpl })).rejects.toThrow("repeated source identities");
    expect(writesOf(fetchImpl)).toEqual([]);
  });

  it("keeps a new identity that shares a page with a duplicate", async () => {
    const acme = discovery("a", "Acme", "acme.com", "2026-09-20");
    const rows = [acme, discovery("b", "Beta", "beta.example", "2026-08-01"), acme, discovery("g", "Gamma", "gamma.example", "2026-07-01")];
    let page1: Record<string, any> | null = null;
    const fetchImpl = vi.fn(async (url: string, init?: RequestInit) => {
      if (url.includes("funding_discoveries")) {
        const offset = Number(/offset=(\d+)/.exec(url)?.[1]);
        const body = rows.slice(offset, offset + 2);
        return ranged(body, offset, rows.length);
      }
      if (url.includes(KV_PATH) && init?.method === "PUT") {
        page1 = JSON.parse(String(init.body));
        return reply({ success: true });
      }
      return fallback(url, init);
    });
    const result = await refreshFundingFeed({ ...config, fetchImpl });
    expect(result.coverage).toMatchObject({ sourceRows: 3, duplicateRows: 1, mergedReports: 3, distinctRounds: 3, companySignals: 3, publishedSignals: 3 });
    expect(page1!.signals.map((signal: { company: string }) => signal.company)).toEqual(["Acme", "Beta", "Gamma"]);
  });

  it("counts blank and undated rows as exclusions and publishes the dated company once", async () => {
    const rows = [
      discovery("a", "Acme", "acme.com", "2026-09-01"),
      discovery("a", "Acme", "acme.com", "2026-09-01"),
      discovery("blank", "   ", "", "2026-09-02"),
      discovery("undated", "No Date Co", "nodate.example", "not-a-date"),
    ];
    const posts: unknown[][] = [];
    let page1: Record<string, any> | null = null;
    const fetchImpl = vi.fn(async (url: string, init?: RequestInit) => {
      if (url.includes("funding_discoveries")) return storedRows(url, rows);
      if (url.includes("legion_funding_rounds") && init?.method === "POST") {
        posts.push(JSON.parse(String(init.body)));
        return reply([], 201);
      }
      if (url.includes(KV_PATH) && init?.method === "PUT") {
        page1 = JSON.parse(String(init.body));
        return reply({ success: true });
      }
      return fallback(url, init);
    });
    const result = await refreshFundingFeed({ ...config, fetchImpl });
    expect(result.coverage).toEqual({
      sourceRows: 3, duplicateRows: 1, excludedNoCompany: 1, excludedUndated: 1,
      mergedReports: 1, distinctRounds: 1, companySignals: 1, publishedSignals: 1,
    });
    expect(posts).toEqual([expect.any(Array)]);
    expect(posts[0]).toHaveLength(1);
    expect(page1).toMatchObject({ count: 1, types: { funding: 1 } });
    expect(page1!.signals.map((signal: { company: string }) => signal.company)).toEqual(["Acme"]);
  });

  it("does not publish an empty funding body that has no proven total", async () => {
    const fetchImpl = vi.fn(async () => reply([]));
    await expect(refreshFundingFeed({ ...config, fetchImpl })).rejects.toThrow("unproven empty read");
    expect(writesOf(fetchImpl)).toEqual([]);
    expect(fetchImpl).toHaveBeenCalledOnce();
  });

  it("continues a headerless short page until an empty page and publishes every row", async () => {
    const rows = [
      discovery("a", "Acme", "acme.com", "2026-09-20"),
      discovery("b", "Beta", "beta.example", "2026-08-01"),
      discovery("g", "Gamma", "gamma.example", "2026-07-01"),
    ];
    const offsets: number[] = [];
    let page1: { signals: Array<{ company: string }> } | null = null;
    const fetchImpl = vi.fn(async (url: string, init?: RequestInit) => {
      if (url.includes("funding_discoveries")) {
        const offset = Number(/offset=(\d+)/.exec(url)?.[1] ?? 0);
        offsets.push(offset);
        if (offset === 0) return reply(rows.slice(0, 2));
        if (offset === 2) return reply(rows.slice(2));
        return reply([]);
      }
      if (url.includes(KV_PATH) && init?.method === "PUT") {
        page1 = JSON.parse(String(init.body));
        return reply({ success: true });
      }
      return fallback(url, init);
    });
    const result = await refreshFundingFeed({ ...config, fetchImpl });
    expect(offsets).toEqual([0, 2, 3]);
    expect(result.coverage).toMatchObject({ sourceRows: 3, companySignals: 3, publishedSignals: 3 });
    expect(page1!.signals.map((signal) => signal.company)).toEqual(["Acme", "Beta", "Gamma"]);
  });

  it("does not publish when a headerless short page repeats the same rows", async () => {
    const rows = [discovery("a", "Acme", "acme.com", "2026-09-20"), discovery("b", "Beta", "beta.example", "2026-08-01")];
    const fetchImpl = vi.fn(async (url: string) => url.includes("funding_discoveries") ? reply(rows) : reply([]));
    await expect(refreshFundingFeed({ ...config, fetchImpl })).rejects.toThrow(/repeated source identities/);
    expect(writesOf(fetchImpl)).toEqual([]);
  });
});

describe("signal page publication", () => {
  it("writes versioned pages last-to-first and prunes only after the current head succeeds", async () => {
    const { fetchImpl, bodies } = manyFeed("none");
    const result = await refreshFundingFeed({ ...config, fetchImpl });
    const writes = writesOf(fetchImpl).filter((item) => item !== "post");
    expect(versionedWrites(writes)).toEqual(["signals/p2.json", "signals/p1.json", "signals/current.json", "delete"]);
    expect(writes).not.toContain("signals/meta.json");
    expect(writes[0]).toMatch(/^signals\/[0-9a-f]{64}\/p2\.json$/);
    expect(writes[1]).toMatch(/^signals\/[0-9a-f]{64}\/p1\.json$/);
    expect(writes[0].split("/")[1]).toBe(result.version);
    expect(writes[1].split("/")[1]).toBe(result.version);
    expect(result).toMatchObject({ count: 501, pages: 2, pagesWritten: 2 });
    expect(result.coverage).toMatchObject({ sourceRows: 501, companySignals: 501, publishedSignals: 501, distinctRounds: 501, mergedReports: 501 });
    expect(result.families).toEqual({
      funding: { sourceRows: 501, duplicateRows: 0, excluded: 0, merged: 0, published: 501 },
      productLaunches: { sourceRows: 0, duplicateRows: 0, excluded: 0, merged: 0, published: 0 },
      jobSignals: { sourceRows: 0, duplicateRows: 0, excluded: 0, merged: 0, published: 0 },
    });
    const page1 = storedPage(bodies, 1);
    const page2 = storedPage(bodies, 2);
    expect(page1).toMatchObject({ count: 501, pageSize: 500, pages: 2, page: 1, types: { funding: 501 }, version: result.version });
    expect(page1.signals).toHaveLength(500);
    expect(Object.keys(page2).sort()).toEqual(["page", "pages", "signals", "version"]);
    expect(page2).toMatchObject({ page: 2, pages: 2, version: result.version });
    expect(page2.signals).toHaveLength(1);
    const published = [...page1.signals, ...page2.signals];
    expect(createHash("sha256").update(canonicalFeedDocument(published)).digest("hex")).toBe(result.version);
    expect(signalsVersion(result.signals)).toBe(result.version);
    const changed = result.signals.map((signal, index) => index === 0 ? { ...signal, headline: `${signal.headline} changed` } : signal);
    expect(signalsVersion(changed)).not.toBe(result.version);
  });

  it("leaves page 1 in place when the last page write fails", async () => {
    const { fetchImpl, bodies } = manyFeed("p2");
    await expect(refreshFundingFeed({ ...config, fetchImpl })).rejects.toThrow("KV write failed with HTTP 500");
    const writes = writesOf(fetchImpl);
    expect(writes).toContain("post");
    expect(versionedWrites(writes.filter((item) => item !== "post"))).toEqual(["signals/p2.json"]);
    expect(writes.filter((item) => item !== "post")[0]).toMatch(/^signals\/[0-9a-f]{64}\/p2\.json$/);
    expect(bodies.has("signals/current.json")).toBe(false);
    expect([...bodies.keys()].some((key) => key.endsWith("/p1.json"))).toBe(false);
  });

  it("does not prune when page 1 fails after the tail page", async () => {
    const { fetchImpl, bodies } = manyFeed("p1");
    await expect(refreshFundingFeed({ ...config, fetchImpl })).rejects.toThrow("KV write failed with HTTP 500");
    const writes = writesOf(fetchImpl);
    expect(writes).toContain("post");
    expect(versionedWrites(writes.filter((item) => item !== "post"))).toEqual(["signals/p2.json", "signals/p1.json"]);
    expect(bodies.has("signals/current.json")).toBe(false);
  });

  it("does not prune when the current head write fails after the pages", async () => {
    const { fetchImpl } = manyFeed("current");
    await expect(refreshFundingFeed({ ...config, fetchImpl })).rejects.toThrow("KV write failed with HTTP 500");
    const writes = writesOf(fetchImpl);
    expect(writes).toContain("post");
    expect(versionedWrites(writes.filter((item) => item !== "post"))).toEqual(["signals/p2.json", "signals/p1.json", "signals/current.json"]);
  });

  it("updates the manifest scan time without rewriting an unchanged version", async () => {
    const first = manyFeed("none");
    const firstResult = await refreshFundingFeed({ ...config, fetchImpl: first.fetchImpl });
    const page1 = storedPage(first.bodies, 1);
    expect(page1.updatedAt).toBe(config.now.toISOString());
    const meta = JSON.parse(first.bodies.get("signals/current.json")!);
    const second = manyFeed("none", meta);
    const later = new Date("2026-10-01T12:00:00.000Z");
    const secondResult = await refreshFundingFeed({ ...config, now: later, fetchImpl: second.fetchImpl });
    expect(secondResult.version).toBe(firstResult.version);
    expect(secondResult.pagesWritten).toBe(0);
    expect(writesOf(second.fetchImpl).filter((item) => item !== "post")).toEqual(["signals/current.json", "delete"]);
    expect([...second.bodies.keys()]).toEqual(["signals/current.json"]);
    expect(JSON.parse(second.bodies.get("signals/current.json")!)).toMatchObject({
      version: firstResult.version,
      updatedAt: later.toISOString(),
      count: 501,
      pages: 2,
    });
    const kvGets = second.fetchImpl.mock.calls.filter(([url, init]) => String(url).includes("api.cloudflare.com") && ((init as RequestInit | undefined)?.method ?? "GET") === "GET");
    expect(kvGets).toHaveLength(1);
    expect(String(kvGets[0][0])).toContain("signals%2Fcurrent.json");
  });
});

describe("people coverage cache", () => {
  it("does not store a profile when every people field is unavailable", async () => {
    const fetchImpl = vi.fn(async (url: string, init?: RequestInit) => {
      if (url.includes("funding_discoveries")) return storedRows(url, [{ company_name: "Acme", company_domain: "acme.com", discovered_date: "2026-09-29" }]);
      return fallback(url, init);
    });
    vi.stubGlobal("fetch", vi.fn(async () => reply({ error: "down" }, 503)));
    const feed = await refreshFundingFeed({ ...config, fetchImpl, enrichment: { quickEnrichKey: "qe", aiArkKey: "ark", quickEnrichUsdPerCredit: 0.001 } });
    vi.unstubAllGlobals();
    const profilePosts = fetchImpl.mock.calls.filter(([url, init]) => String(url).includes("legion_company_profiles") && (init as RequestInit | undefined)?.method === "POST");
    expect(profilePosts).toHaveLength(0);
    expect(feed.enriched).toBe(0);
    expect(feed.costUsd).toBeGreaterThan(0);
    expect(feed.coverage.publishedSignals).toBe(1);
  });

  it("stores a confirmed-empty profile when every people field was read", async () => {
    const upserts: Array<Record<string, unknown>> = [];
    const fetchImpl = vi.fn(async (url: string, init?: RequestInit) => {
      if (url.includes("funding_discoveries")) return storedRows(url, [{ company_name: "Acme", company_domain: "acme.com", discovered_date: "2026-09-29" }]);
      if (url.includes("legion_company_profiles") && init?.method === "POST") {
        upserts.push(...JSON.parse(String(init.body)));
        return reply([], 201);
      }
      return fallback(url, init);
    });
    vi.stubGlobal("fetch", vi.fn(async (url: string) => String(url).includes("quickenrich") ? reply({ data: [] }) : reply({ content: [] })));
    const feed = await refreshFundingFeed({ ...config, fetchImpl, enrichment: { quickEnrichKey: "qe", aiArkKey: "ark", quickEnrichUsdPerCredit: 0.001 } });
    vi.unstubAllGlobals();
    expect(feed.enriched).toBe(1);
    expect(upserts).toEqual([expect.objectContaining({ domain: "acme.com", hq: null, employees: null, founders: [] })]);
  });
});

const CAP = 200;
const COMPANY_COUNT = 1201;
const CAPPED = Array.from({ length: COMPANY_COUNT }, (_, i) => discovery(String(i + 1), `Company ${i}`, `c${i}.example`, "2026-09-01"));

function cappedRead(mode: "range" | "headerless" | "fail-mid" | "unmet-total") {
  const offsets: number[] = [];
  const limits: number[] = [];
  const bodies = new Map<string, string>();
  let mutated = false;
  const fetchImpl = vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    if (method !== "GET") mutated = true;
    if (url.includes("funding_discoveries")) {
      if (mutated) throw new Error("funding read continued after a mutation");
      const offset = Number(/offset=(\d+)/.exec(url)?.[1] ?? 0);
      offsets.push(offset);
      limits.push(Number(/limit=(\d+)/.exec(url)?.[1] ?? 0));
      if (mode === "fail-mid" && offset === CAP) return reply({ message: "no" }, 500);
      const body = CAPPED.slice(offset, offset + CAP);
      if (mode === "headerless") return offset >= CAPPED.length ? reply([]) : reply(body);
      if (mode === "unmet-total") return offset === 0 ? ranged(body, 0, COMPANY_COUNT) : reply([]);
      return ranged(body, offset, CAPPED.length);
    }
    if (method === "PUT" && url.includes("api.cloudflare.com")) {
      bodies.set(decodeURIComponent(url.split("/values/")[1] ?? ""), String(init?.body));
      return reply({ success: true });
    }
    if (isAdditionalTable(url)) return provenEmpty();
    if (url.includes("api.cloudflare.com") && method === "GET") return reply({}, 404);
    return reply([]);
  });
  return { fetchImpl, offsets, limits, bodies };
}

function publishedCompanies(bodies: Map<string, string>): string[] {
  const keys = [...bodies.keys()].filter((key) => /^signals\/[0-9a-f]{64}\/p\d+\.json$/.test(key));
  keys.sort((left, right) => Number(/p(\d+)\.json$/.exec(left)![1]) - Number(/p(\d+)\.json$/.exec(right)![1]));
  return keys.flatMap((key) => JSON.parse(bodies.get(key)!).signals.map((signal: { company: string }) => signal.company));
}

describe("server page cap of 200 against a 1000-row request", () => {
  it("publishes all 1201 companies when Content-Range names the total", async () => {
    const read = cappedRead("range");
    const result = await refreshFundingFeed({ ...config, fetchImpl: read.fetchImpl });
    expect(read.limits.every((limit) => limit === 1000)).toBe(true);
    expect(read.offsets).toEqual([0, 200, 400, 600, 800, 1000, 1200]);
    expect(result.coverage).toMatchObject({
      sourceRows: 1201, duplicateRows: 0, excludedNoCompany: 0, excludedUndated: 0,
      mergedReports: 1201, distinctRounds: 1201, companySignals: 1201, publishedSignals: 1201,
    });
    expect(result).toMatchObject({ count: 1201, pages: 3, pagesWritten: 3 });
    const names = publishedCompanies(read.bodies);
    expect(names).toHaveLength(1201);
    expect(new Set(names).size).toBe(1201);
    const page1 = storedPage(read.bodies, 1);
    const page3 = storedPage(read.bodies, 3);
    expect(page1).toMatchObject({ count: 1201, pageSize: 500, pages: 3, page: 1, types: { funding: 1201 }, version: result.version });
    expect(page1.signals).toHaveLength(500);
    expect(page3).toMatchObject({ page: 3, pages: 3, version: result.version });
    expect(Object.keys(page3).sort()).toEqual(["page", "pages", "signals", "version"]);
    expect(page3.signals).toHaveLength(201);
    expect(page1.signals.every((signal: { type: string }) => signal.type === "funding")).toBe(true);
    const writes = writesOf(read.fetchImpl).filter((item) => item !== "post");
    expect(versionedWrites(writes)).toEqual(["signals/p3.json", "signals/p2.json", "signals/p1.json", "signals/current.json", "delete"]);
    expect(writes[0].split("/")[1]).toBe(result.version);
  });

  it("publishes 1201 capped funding rows with the other families on their own clients", async () => {
    const hosts: Record<string, string> = {};
    const keys: Record<string, string> = {};
    const bodies = new Map<string, string>();
    const offsets: number[] = [];
    const fetchImpl = vi.fn(async (url: string, init?: RequestInit) => {
      const host = new URL(url).host;
      const apikey = ((init?.headers ?? {}) as Record<string, string>).apikey ?? "";
      if (url.includes("funding_discoveries")) {
        hosts.funding = host;
        keys.funding = apikey;
        const offset = Number(/offset=(\d+)/.exec(url)?.[1] ?? 0);
        offsets.push(offset);
        return ranged(CAPPED.slice(offset, offset + CAP), offset, CAPPED.length);
      }
      if (url.includes("/rest/v1/product_launches?")) {
        hosts.product = host;
        keys.product = apikey;
        return ranged([{ company_name: "Launch", product_name: "Widget", source: "news", source_url: "https://example.com/widget", discovered_date: "2026-09-02" }], 0, 1);
      }
      if (url.includes("/rest/v1/game_signals?")) {
        hosts.game = host;
        keys.game = apikey;
        return ranged([{ signal_type: "game_announcement", developer: "Pixel", game_title: "Side", source_url: "https://example.com/side", article_date: "2026-08-02" }], 0, 1);
      }
      if (url.includes("/rest/v1/game_job_signals?")) {
        hosts.job = host;
        keys.job = apikey;
        return ranged([{ job_id: 9, job_title: "Rigger", company_name: "Zed", job_url: "https://80.lv/jobs/9", date_posted: "2026-07-02" }], 0, 1);
      }
      if (init?.method === "PUT" && url.includes("api.cloudflare.com")) {
        bodies.set(decodeURIComponent(url.split("/values/")[1] ?? ""), String(init.body));
        return reply({ success: true });
      }
      if (url.includes("api.cloudflare.com")) return reply({}, 404);
      return reply([]);
    });
    const result = await refreshFundingFeed({
      ...config,
      url: "https://funding.example",
      key: "funding-key",
      sources: {
        productLaunches: { url: "https://product.example", key: "product-key" },
        jobSignals: { url: "https://jobs.example", key: "job-key" },
      },
      fetchImpl,
    });
    expect(offsets).toEqual([0, 200, 400, 600, 800, 1000, 1200]);
    expect(hosts).toEqual({ funding: "funding.example", product: "product.example", job: "jobs.example" });
    expect(keys).toEqual({ funding: "funding-key", product: "product-key", job: "job-key" });
    expect(hosts.game).toBeUndefined();
    expect(result.families).toEqual({
      funding: { sourceRows: 1201, duplicateRows: 0, excluded: 0, merged: 0, published: 1201 },
      productLaunches: { sourceRows: 1, duplicateRows: 0, excluded: 0, merged: 0, published: 1 },
      jobSignals: { sourceRows: 1, duplicateRows: 0, excluded: 0, merged: 0, published: 1 },
    });
    expect(result.coverage).toMatchObject({
      sourceRows: 1201, duplicateRows: 0, excludedNoCompany: 0, excludedUndated: 0,
      mergedReports: 1201, distinctRounds: 1201, companySignals: 1201, publishedSignals: 1203,
    });
    expect(result).toMatchObject({ count: 1203, pages: 3, pagesWritten: 3 });
    expect(result.signals[0]).toMatchObject({ type: "product-launch", company: "Launch", date: "2026-09-02" });
    expect(result.signals.at(-1)).toMatchObject({ type: "hiring", company: "Zed", date: "2026-07-02" });
    const page1 = storedPage(bodies, 1);
    expect(page1).toMatchObject({ count: 1203, pages: 3, page: 1, types: { funding: 1201, "product-launch": 1, hiring: 1 }, version: result.version });
    expect(page1.types).not.toHaveProperty("gaming");
    expect(page1.signals.some((signal: { type: string }) => signal.type === "gaming")).toBe(false);
    expect(page1.signals).toHaveLength(500);
    expect(storedPage(bodies, 3).signals).toHaveLength(203);
    expect(JSON.parse(bodies.get(CURRENT_KEY)!).version).toBe(result.version);
    expect(bodies.has("signals/meta.json")).toBe(false);
    expect(writesOf(fetchImpl).filter((item) => item !== "post")).toEqual([
      expect.stringMatching(/^signals\/[0-9a-f]{64}\/p3\.json$/),
      expect.stringMatching(/^signals\/[0-9a-f]{64}\/p2\.json$/),
      expect.stringMatching(/^signals\/[0-9a-f]{64}\/p1\.json$/),
      CURRENT_KEY,
      "delete",
    ]);
  });

  it("publishes all 1201 companies when short pages omit Content-Range", async () => {
    const read = cappedRead("headerless");
    const result = await refreshFundingFeed({ ...config, fetchImpl: read.fetchImpl });
    expect(read.limits.every((limit) => limit === 1000)).toBe(true);
    expect(read.offsets).toEqual([0, 200, 400, 600, 800, 1000, 1200, 1201]);
    expect(result.coverage).toMatchObject({ sourceRows: 1201, companySignals: 1201, publishedSignals: 1201, distinctRounds: 1201 });
    const names = publishedCompanies(read.bodies);
    expect(new Set(names).size).toBe(1201);
    expect(names).toHaveLength(1201);
  });

  it("does not mutate when the second 200-row page fails", async () => {
    const read = cappedRead("fail-mid");
    await expect(refreshFundingFeed({ ...config, fetchImpl: read.fetchImpl })).rejects.toThrow("funding_discoveries read failed with HTTP 500");
    expect(read.offsets).toEqual([0, 200]);
    expect(writesOf(read.fetchImpl)).toEqual([]);
    expect(read.bodies.has(CURRENT_KEY)).toBe(false);
  });

  it("does not mutate when a later empty page leaves the announced total unmet", async () => {
    const read = cappedRead("unmet-total");
    await expect(refreshFundingFeed({ ...config, fetchImpl: read.fetchImpl })).rejects.toThrow("ended before announced total of 1201");
    expect(read.offsets).toEqual([0, 200]);
    expect(writesOf(read.fetchImpl)).toEqual([]);
  });
});

function memoryFeed(initialRows: ReturnType<typeof discovery>[]) {
  const store = new Map<string, string>();
  const control: {
    rows: typeof initialRows;
    failTable: string;
    onPut: ((key: string) => void) | null;
    failPut: ((key: string) => boolean) | null;
  } = {
    rows: initialRows,
    failTable: "",
    onPut: null,
    failPut: null,
  };
  const fetchImpl = vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    if (url.includes("funding_discoveries")) return storedRows(url, control.rows);
    if (isAdditionalTable(url)) {
      if (control.failTable && url.includes(`/rest/v1/${control.failTable}?`)) return reply({ message: "down" }, 500);
      return provenEmpty();
    }
    if (url.includes("api.cloudflare.com")) {
      const key = decodeURIComponent(url.split("/values/")[1] ?? "");
      if (method === "DELETE") return reply({}, 500);
      if (method === "GET") {
        const body = store.get(key);
        if (body === undefined) return new Response("missing", { status: 404 });
        return new Response(body, { status: 200, headers: { "Content-Type": "application/json" } });
      }
      if (method === "PUT") {
        if (control.failPut?.(key)) return reply({}, 500);
        control.onPut?.(key);
        store.set(key, String(init?.body ?? ""));
        return reply({ success: true });
      }
    }
    return reply([]);
  });
  return { fetchImpl, store, control };
}

function pageKey(store: Map<string, string>, version: string, page: number): string {
  return `signals/${version}/p${page}.json`;
}

describe("immutable feed snapshots", () => {
  const acme = discovery("a", "Acme", "acme.com", "2026-09-20");
  const beta = discovery("b", "Beta", "beta.example", "2026-08-01");

  it("keeps the page 1 creation header when the same version is scanned again", async () => {
    const feed = memoryFeed([acme]);
    const first = await refreshFundingFeed({ ...config, fetchImpl: feed.fetchImpl });
    const key = pageKey(feed.store, first.version, 1);
    const bytes = feed.store.get(key);
    const later = new Date("2026-10-01T15:00:00.000Z");
    const start = feed.fetchImpl.mock.calls.length;
    const second = await refreshFundingFeed({ ...config, now: later, fetchImpl: feed.fetchImpl });
    const during = feed.fetchImpl.mock.calls.slice(start);
    const kvGets = during.filter(([url, init]) => String(url).includes("api.cloudflare.com") && ((init as RequestInit | undefined)?.method ?? "GET") === "GET");
    expect(second.version).toBe(first.version);
    expect(second.pagesWritten).toBe(0);
    expect(feed.store.get(key)).toBe(bytes);
    expect(JSON.parse(bytes!).updatedAt).toBe(config.now.toISOString());
    expect(JSON.parse(feed.store.get("signals/current.json")!).updatedAt).toBe(later.toISOString());
    expect(kvGets.map(([url]) => String(url))).toEqual([expect.stringContaining("signals%2Fcurrent.json")]);
    expect(createHash("sha256").update(canonicalFeedDocument(second.signals)).digest("hex")).toBe(second.version);
  });

  it("publishes a new version beside the old one and leaves the old page bytes in place", async () => {
    const feed = memoryFeed([acme]);
    const first = await refreshFundingFeed({ ...config, fetchImpl: feed.fetchImpl });
    const oldKey = pageKey(feed.store, first.version, 1);
    const oldBytes = feed.store.get(oldKey);
    let observed = false;
    feed.control.onPut = (key) => {
      if (!key.endsWith("/p1.json") || key.includes(first.version)) return;
      expect(JSON.parse(feed.store.get("signals/current.json")!).version).toBe(first.version);
      expect(feed.store.get(oldKey)).toBe(oldBytes);
      observed = true;
    };
    feed.control.rows = [acme, beta];
    const second = await refreshFundingFeed({ ...config, now: new Date("2026-10-01T15:00:00.000Z"), fetchImpl: feed.fetchImpl });
    expect(observed).toBe(true);
    expect(second.version).not.toBe(first.version);
    expect(feed.store.get(oldKey)).toBe(oldBytes);
    expect(JSON.parse(feed.store.get("signals/current.json")!).version).toBe(second.version);
    expect(feed.store.has(pageKey(feed.store, second.version, 1))).toBe(true);
    const kvDeletes = feed.fetchImpl.mock.calls.filter(([url, init]) => String(url).includes("api.cloudflare.com") && (init as RequestInit | undefined)?.method === "DELETE");
    expect(kvDeletes).toEqual([]);
  });

  it("refuses a corrupted older page without writing, pruning, or moving meta", async () => {
    const feed = memoryFeed([acme]);
    const first = await refreshFundingFeed({ ...config, fetchImpl: feed.fetchImpl });
    feed.control.rows = [acme, beta];
    await refreshFundingFeed({ ...config, now: new Date("2026-10-01T15:00:00.000Z"), fetchImpl: feed.fetchImpl });
    const oldKey = pageKey(feed.store, first.version, 1);
    const corrupted = JSON.parse(feed.store.get(oldKey)!);
    corrupted.signals[0].company = "Tampered";
    feed.store.set(oldKey, JSON.stringify(corrupted));
    const meta = feed.store.get("signals/current.json");
    feed.control.rows = [acme];
    const start = feed.fetchImpl.mock.calls.length;
    await expect(refreshFundingFeed({ ...config, now: new Date("2026-10-02T15:00:00.000Z"), fetchImpl: feed.fetchImpl })).rejects.toThrow("does not match its content");
    const during = feed.fetchImpl.mock.calls.slice(start);
    expect(during.some(([, init]) => (init as RequestInit | undefined)?.method === "PUT")).toBe(false);
    expect(during.some(([, init]) => (init as RequestInit | undefined)?.method === "DELETE")).toBe(false);
    expect(feed.store.get("signals/current.json")).toBe(meta);
    expect(feed.store.get(oldKey)).toContain("Tampered");
  });

  it("refuses a manifest whose fields disagree with the content version", async () => {
    const feed = memoryFeed([acme]);
    const first = await refreshFundingFeed({ ...config, fetchImpl: feed.fetchImpl });
    const meta = JSON.parse(feed.store.get("signals/current.json")!);
    meta.count = first.count + 1;
    const forged = JSON.stringify(meta);
    feed.store.set("signals/current.json", forged);
    const start = feed.fetchImpl.mock.calls.length;
    await expect(refreshFundingFeed({ ...config, fetchImpl: feed.fetchImpl })).rejects.toThrow("signals manifest disagrees with its content version");
    const during = feed.fetchImpl.mock.calls.slice(start);
    expect(during.some(([, init]) => (init as RequestInit | undefined)?.method === "PUT")).toBe(false);
    expect(during.some(([, init]) => (init as RequestInit | undefined)?.method === "DELETE")).toBe(false);
    expect(feed.store.get("signals/current.json")).toBe(forged);
  });

  it("keeps the last manifest when a required family read fails", async () => {
    const feed = memoryFeed([acme]);
    await refreshFundingFeed({ ...config, fetchImpl: feed.fetchImpl });
    const meta = feed.store.get("signals/current.json");
    const keys = [...feed.store.keys()];
    feed.control.failTable = "product_launches";
    const start = feed.fetchImpl.mock.calls.length;
    await expect(refreshFundingFeed({ ...config, fetchImpl: feed.fetchImpl })).rejects.toThrow("product_launches read failed with HTTP 500");
    const during = feed.fetchImpl.mock.calls.slice(start);
    expect(during.some(([, init]) => ["POST", "PUT", "DELETE"].includes((init as RequestInit | undefined)?.method ?? ""))).toBe(false);
    expect(feed.store.get("signals/current.json")).toBe(meta);
    expect([...feed.store.keys()]).toEqual(keys);
  });

  it("reports a KV read failure without writing pages or pruning", async () => {
    const fetchImpl = vi.fn(async (url: string, init?: RequestInit) => {
      if (url.includes("funding_discoveries")) return storedRows(url, [acme]);
      if (isAdditionalTable(url)) return provenEmpty();
      if (url.includes("api.cloudflare.com") && (init?.method ?? "GET") === "GET") return reply({}, 500);
      if (url.includes("api.cloudflare.com") && init?.method === "PUT") return reply({ success: true });
      return reply([]);
    });
    await expect(refreshFundingFeed({ ...config, fetchImpl })).rejects.toThrow("KV read failed with HTTP 500");
    expect(writesOf(fetchImpl)).toEqual(["post"]);
  });

  it("preserves the prior current head and snapshot when a new version page fails", async () => {
    const feed = memoryFeed([acme]);
    const legacy = "{\"version\":\"legacy-head\"}";
    feed.store.set("signals/meta.json", legacy);
    const first = await refreshFundingFeed({ ...config, fetchImpl: feed.fetchImpl });
    const head = feed.store.get(CURRENT_KEY);
    const oldKey = pageKey(feed.store, first.version, 1);
    const oldBytes = feed.store.get(oldKey);
    expect(feed.store.get("signals/meta.json")).toBe(legacy);
    expect(feed.fetchImpl.mock.calls.some(([url]) => decodeURIComponent(String(url)).includes("signals/meta.json"))).toBe(false);
    feed.control.rows = Array.from({ length: 501 }, (_, index) => discovery(String(index + 1), `Next ${index}`, `n${index}.example`, "2026-09-01"));
    feed.control.failPut = (key) => key.endsWith("/p1.json") && !key.includes(first.version);
    const start = feed.fetchImpl.mock.calls.length;
    await expect(refreshFundingFeed({ ...config, now: new Date("2026-10-03T12:00:00.000Z"), fetchImpl: feed.fetchImpl })).rejects.toThrow("KV write failed with HTTP 500");
    const during = feed.fetchImpl.mock.calls.slice(start);
    expect(during.some(([, init]) => (init as RequestInit | undefined)?.method === "DELETE")).toBe(false);
    expect(during.some(([url]) => decodeURIComponent(String(url)).includes("signals/meta.json"))).toBe(false);
    expect(feed.store.get(CURRENT_KEY)).toBe(head);
    expect(JSON.parse(head!).version).toBe(first.version);
    expect(feed.store.get(oldKey)).toBe(oldBytes);
    expect(feed.store.get("signals/meta.json")).toBe(legacy);
    const orphan = [...feed.store.keys()].filter((key) => key.endsWith("/p2.json") && !key.includes(first.version));
    expect(orphan).toHaveLength(1);
    expect([...feed.store.keys()].some((key) => key.endsWith("/p1.json") && !key.includes(first.version))).toBe(false);
  });
});

describe("additional producer reads", () => {
  function fundingOnly(url: string): Response | null {
    if (!url.includes("funding_discoveries")) return null;
    return storedRows(url, [discovery("a", "Acme", "acme.com", "2026-09-20")]);
  }

  it("pages product_launches by the rows returned and keeps the writer order", async () => {
    const products = [0, 1, 2].map((index) => ({
      company_name: `Launch ${index}`,
      product_name: `P${index}`,
      source: "news",
      source_url: `https://example.com/p/${index}`,
      discovered_date: `2026-03-0${index + 1}`,
    }));
    const offsets: number[] = [];
    const fetchImpl = vi.fn(async (url: string, init?: RequestInit) => {
      const funding = fundingOnly(url);
      if (funding) return funding;
      if (url.includes("/rest/v1/product_launches?")) {
        expect(url).toContain("order=source_url.asc");
        expect(url).toContain("limit=1000");
        expect(url).not.toContain("classification_reasoning");
        expect(url).not.toContain("query_source");
        expect(url).not.toContain("%2C");
        const headers = (init?.headers ?? {}) as Record<string, string>;
        expect(headers["Accept-Profile"]).toBe("public");
        expect(headers.Prefer).toBe("count=exact");
        const offset = Number(/offset=(\d+)/.exec(url)?.[1] ?? 0);
        offsets.push(offset);
        return ranged(products.slice(offset, offset + 2), offset, products.length);
      }
      if (isAdditionalTable(url)) return provenEmpty();
      return fallback(url, init);
    });
    const result = await refreshFundingFeed({ ...config, fetchImpl });
    expect(offsets).toEqual([0, 2]);
    expect(result.families.productLaunches).toEqual({ sourceRows: 3, duplicateRows: 0, excluded: 0, merged: 0, published: 3 });
    expect(result.families).not.toHaveProperty("gameSignals");
    expect(result.families.jobSignals.published).toBe(0);
    expect(result.coverage).toMatchObject({ companySignals: 1, publishedSignals: 4 });
    expect(result.count).toBe(4);
    expect(result.signals.map((signal) => signal.company)).toEqual(["Acme", "Launch 2", "Launch 1", "Launch 0"]);
  });

  it("retains a repeated product source_url and publishes the later source date", async () => {
    const products = [
      { company_name: "Acme", product_name: "Old", source: "news", source_url: "https://example.com/same", discovered_date: "2026-01-01" },
      { company_name: "Acme", product_name: "New", source: "news", source_url: "https://example.com/same", discovered_date: "2026-06-01" },
    ];
    const fetchImpl = vi.fn(async (url: string, init?: RequestInit) => {
      const funding = fundingOnly(url);
      if (funding) return funding;
      if (url.includes("/rest/v1/product_launches?")) return ranged(products, 0, products.length);
      if (isAdditionalTable(url)) return provenEmpty();
      return fallback(url, init);
    });
    const result = await refreshFundingFeed({ ...config, fetchImpl });
    expect(result.families.productLaunches).toEqual({ sourceRows: 2, duplicateRows: 1, excluded: 0, merged: 1, published: 1 });
    expect(result.signals.find((signal) => signal.type === "product-launch")).toMatchObject({
      company: "Acme",
      headline: "Product launch: New",
      date: "2026-06-01",
    });
  });

  it("does not publish when game_job_signals fails", async () => {
    const fetchImpl = vi.fn(async (url: string, init?: RequestInit) => {
      const funding = fundingOnly(url);
      if (funding) return funding;
      if (url.includes("/rest/v1/game_job_signals?")) {
        expect(url).toContain("order=job_id.asc");
        return reply({ message: "down" }, 500);
      }
      if (isAdditionalTable(url)) return provenEmpty();
      if (init?.method === "PUT") return reply({ success: true });
      return reply([]);
    });
    await expect(refreshFundingFeed({ ...config, fetchImpl })).rejects.toThrow("game_job_signals read failed with HTTP 500");
    expect(writesOf(fetchImpl)).toEqual([]);
    expect(String(fetchImpl.mock.calls[0][0])).toContain("funding_discoveries");
  });

  it("does not publish when product_launches rejects the select", async () => {
    const fetchImpl = vi.fn(async (url: string) => {
      const funding = fundingOnly(url);
      if (funding) return funding;
      if (url.includes("/rest/v1/product_launches?")) return reply({ message: "column" }, 400);
      if (isAdditionalTable(url)) return provenEmpty();
      return reply([]);
    });
    await expect(refreshFundingFeed({ ...config, fetchImpl })).rejects.toThrow("product_launches read failed with HTTP 400");
    expect(writesOf(fetchImpl)).toEqual([]);
  });

  it("publishes product launches and hiring and drops game_signals rows", async () => {
    const fetchImpl = vi.fn(async (url: string, init?: RequestInit) => {
      const funding = fundingOnly(url);
      if (funding) return funding;
      if (url.includes("/rest/v1/product_launches?")) {
        return ranged([
          { company_name: "Acme", product_name: "Widget", source: "news", source_url: "https://example.com/widget", discovered_date: "2026-09-01" },
          { company_name: "Acme", product_name: "Other", source: "news", source_url: "https://example.com/other", discovered_date: "2026-08-15" },
        ], 0, 2);
      }
      if (url.includes("/rest/v1/game_signals?")) {
        expect(url).toContain("order=source_url.asc");
        return ranged([{
          signal_type: "game_announcement",
          developer: "Pixel",
          game_title: "Side",
          source_url: "https://example.com/side",
          article_date: "2026-07-01",
        }], 0, 1);
      }
      if (url.includes("/rest/v1/game_job_signals?")) {
        expect(url).toContain("order=job_id.asc");
        return ranged([{
          job_id: 9,
          job_title: "Rigger",
          company_name: "Zed",
          job_url: "https://80.lv/jobs/9",
          date_posted: "2026-06-01",
        }], 0, 1);
      }
      return fallback(url, init);
    });
    const result = await refreshFundingFeed({ ...config, fetchImpl });
    expect(fetchImpl.mock.calls.some(([url]) => String(url).includes("/rest/v1/game_signals?"))).toBe(false);
    expect(result.families.productLaunches).toEqual({ sourceRows: 2, duplicateRows: 0, excluded: 0, merged: 0, published: 2 });
    expect(result.families).not.toHaveProperty("gameSignals");
    expect(result.families.jobSignals).toEqual({ sourceRows: 1, duplicateRows: 0, excluded: 0, merged: 0, published: 1 });
    expect(result.coverage).toMatchObject({ companySignals: 1, publishedSignals: 4 });
    expect(result.signals.map((signal) => [signal.type, signal.company, signal.date])).toEqual([
      ["funding", "Acme", "2026-09-20"],
      ["product-launch", "Acme", "2026-09-01"],
      ["product-launch", "Acme", "2026-08-15"],
      ["hiring", "Zed", "2026-06-01"],
    ]);
    expect(result.signals.find((signal) => signal.type === "hiring")?.tags).toContain("Gaming and animation");
    expect(JSON.stringify(result.signals)).not.toContain("@");
    const page1 = storedPage(new Map(fetchImpl.mock.calls.flatMap(([url, init]) => {
      if ((init as RequestInit | undefined)?.method !== "PUT" || !String(url).includes("api.cloudflare.com")) return [];
      const key = decodeURIComponent(String(url).split("/values/")[1] ?? "");
      return key.endsWith("/p1.json") ? [[key, String((init as RequestInit).body)]] : [];
    })), 1);
    expect(page1.types).toEqual({ funding: 1, "product-launch": 2, hiring: 1 });
    expect(page1.types).not.toHaveProperty("gaming");
    expect(page1.signals.some((signal: { type: string }) => signal.type === "gaming")).toBe(false);
    expect(result.version).toBe(createHash("sha256").update(canonicalFeedDocument(result.signals)).digest("hex"));
    expect(page1.version).toBe(result.version);
  });
});

describe("writer source clients", () => {
  it("follows each writer key order and drops a non-http project URL", () => {
    expect(writerClientFromEnv({
      SUPABASE_PROJECT_URL: "https://project.example",
      SUPABASE_URL: "https://other.example",
      SUPABASE_KEY: "writer-key",
      SUPABASE_SERVICE_ROLE_KEY: "service-key",
      SUPABASE_ANON_KEY: "anon-key",
    })).toEqual({ url: "https://project.example", key: "writer-key" });
    expect(writerClientFromEnv({ SUPABASE_URL: "https://fallback.example", SUPABASE_SERVICE_ROLE_KEY: "service-key" }))
      .toEqual({ url: "https://fallback.example", key: "service-key" });
    expect(writerClientFromEnv({ SUPABASE_URL: "https://anon.example", SUPABASE_ANON_KEY: "anon-key" }))
      .toEqual({ url: "https://anon.example", key: "anon-key" });
    expect(writerClientFromEnv({ SUPABASE_PROJECT_URL: "project.example", SUPABASE_KEY: "writer-key" }))
      .toEqual({ url: "", key: "writer-key" });
  });

  it("keeps funding on the service role and ignores the anon key", () => {
    expect(fundingClientFromEnv({
      SUPABASE_PROJECT_URL: "https://project.example",
      SUPABASE_URL: "https://other.example",
      SUPABASE_KEY: "writer-key",
      SUPABASE_SERVICE_ROLE_KEY: "service-key",
      SUPABASE_ANON_KEY: "anon-key",
    })).toEqual({ url: "https://project.example", key: "service-key" });
    expect(fundingClientFromEnv({ SUPABASE_URL: "not-http", SUPABASE_KEY: "writer-key", SUPABASE_ANON_KEY: "anon-key" }))
      .toEqual({ url: "not-http", key: "writer-key" });
    expect(fundingClientFromEnv({ SUPABASE_ANON_KEY: "anon-key" })).toEqual({ url: "", key: "" });
  });

  it("does not read or publish when a writer client is missing", async () => {
    const fetchImpl = vi.fn(async () => reply([]));
    await expect(refreshFundingFeed({
      ...config,
      sources: { ...config.sources, jobSignals: { url: "https://jobs.example", key: "" } },
      fetchImpl,
    })).rejects.toThrow("game_job_signals source is not configured");
    expect(fetchImpl).not.toHaveBeenCalled();
  });
});
