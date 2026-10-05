import { describe, expect, it } from "vitest";
import { buildRounds, displaySource, isRaisingfiSource, needsSecondarySource, type FundingReport } from "./funding-rounds.js";

function report(overrides: Partial<FundingReport>): FundingReport {
  return {
    company: "Acme", domain: "acme.com", logo: null, round: "Seed", amount: "$5 Million", amountUsd: 5_000_000, investors: null,
    industry: "", description: "", employees: null, hq: "", founded: null, founders: [], date: "2026-03-01",
    source: "@raisingfi on X", sourceUrl: "https://x.com/raisingfi/status/1", ...overrides,
  };
}

describe("buildRounds", () => {
  it("merges several reports of one round and keeps every source", () => {
    const [company] = buildRounds([
      report({}),
      report({ date: "2026-03-04", source: "TechCrunch", sourceUrl: "https://techcrunch.com/acme", investors: "Acme Ventures" }),
      report({ date: "2026-03-10", round: "Unknown", amountUsd: 5_500_000, amount: "$5.5M", sourceUrl: "https://www.sec.gov/acme" }),
    ]);
    expect(company.earlier).toEqual([]);
    expect(company.latest).toMatchObject({ round: "Seed", date: "2026-03-01", lastReported: "2026-03-10", reports: 3, amountUsd: 5_500_000, investors: "Acme Ventures" });
    expect(company.latest.sources.map((s) => s.url)).toEqual(["https://x.com/raisingfi/status/1", "https://techcrunch.com/acme", "https://www.sec.gov/acme"]);
  });

  it("starts a new round when the company raises again, newest first", () => {
    const [company] = buildRounds([
      report({ date: "2026-09-20", round: "Series A", amountUsd: 20_000_000, amount: "$20M" }),
      report({}),
    ]);
    expect(company.latest).toMatchObject({ round: "Series A", date: "2026-09-20" });
    expect(company.earlier.map((r) => r.round)).toEqual(["Seed"]);
  });

  it("keeps a different round inside the merge window separate unless the amount matches", () => {
    const separate = buildRounds([report({}), report({ date: "2026-03-20", round: "Series A", amountUsd: 12_000_000 })])[0];
    expect(separate.earlier).toHaveLength(1);
    const merged = buildRounds([report({}), report({ date: "2026-03-20", round: "Series A", amountUsd: 5_000_000 })])[0];
    expect(merged.earlier).toHaveLength(0);
  });

  it("orders companies by their latest round and groups by name when there is no domain", () => {
    const companies = buildRounds([
      report({ company: "Old Co", domain: "", date: "2026-01-01" }),
      report({ company: "New Co", domain: "new.co", date: "2026-09-01" }),
      report({ company: "Old Co", domain: "", date: "2026-02-01", round: "Series A", amountUsd: 15_000_000 }),
    ]);
    expect(companies.map((c) => c.companyKey)).toEqual(["new.co", "old co"]);
    expect(companies[1].earlier).toHaveLength(1);
  });

  it("skips reports without a company or a date", () => {
    expect(buildRounds([report({ company: "" }), report({ date: "" })])).toEqual([]);
  });

  it("keeps a later report of an earlier round on that round", () => {
    const [company] = buildRounds([
      report({ date: "2026-01-01", sourceUrl: "https://x.com/raisingfi/status/1" }),
      report({ date: "2026-01-10", round: "Series A", amountUsd: 20_000_000, amount: "$20M", source: "TechCrunch", sourceUrl: "https://techcrunch.com/series-a" }),
      report({ date: "2026-01-20", source: "Axios", sourceUrl: "https://www.axios.com/seed" }),
    ]);
    expect(company.latest).toMatchObject({ round: "Series A", reports: 1 });
    expect(company.earlier).toHaveLength(1);
    expect(company.earlier[0].sources.map((source) => source.url)).toEqual(["https://x.com/raisingfi/status/1", "https://www.axios.com/seed"]);
    expect(company.earlier[0].lastReported).toBe("2026-01-20");
  });

  it("gives a same-day second round its own key and keeps a repeated source label", () => {
    const [company] = buildRounds([
      report({ date: "2026-03-01", source: "", sourceUrl: "https://techcrunch.com/acme" }),
      report({ date: "2026-03-01", round: "Series A", amountUsd: 20_000_000, amount: "$20M", source: "Axios", sourceUrl: "https://www.axios.com/series-a" }),
      report({ date: "2026-03-02", source: "TechCrunch", sourceUrl: "https://techcrunch.com/acme" }),
    ]);
    expect(company.earlier[0].key).toBe("acme.com|2026-03-01");
    expect(company.latest.key).toBe("acme.com|2026-03-01|2");
    expect(company.earlier.map((round) => round.round)).toEqual(["Seed"]);
    expect(company.earlier[0].sources).toEqual([{ name: "TechCrunch", url: "https://techcrunch.com/acme" }]);
  });

  it("drops unsafe report links", () => {
    const [company] = buildRounds([
      report({ sourceUrl: "https://user:pass@techcrunch.com/acme", source: "TechCrunch" }),
      report({ date: "2026-03-02", sourceUrl: "javascript:alert(1)", source: "Bad" }),
      report({ date: "2026-03-03", sourceUrl: "https://www.axios.com/acme?token=secret", source: "Axios" }),
      report({ date: "2026-03-04", sourceUrl: "https://news.example.com/acme", source: "Example" }),
    ]);
    expect(company.latest.sources.map((source) => source.url)).toEqual(["https://news.example.com/acme"]);
  });
});

describe("source choice", () => {
  it("recognises raisingfi and X posts", () => {
    expect(isRaisingfiSource({ name: "@raisingfi on X", url: "https://x.com/raisingfi/status/1" })).toBe(true);
    expect(isRaisingfiSource({ name: "", url: "https://twitter.com/someone/status/2" })).toBe(true);
    expect(isRaisingfiSource({ name: "TechCrunch", url: "https://techcrunch.com/a" })).toBe(false);
  });

  it("prefers a non-X report, then a non-X secondary, and never raisingfi", () => {
    const [onlyRaisingfi] = buildRounds([report({})]);
    expect(needsSecondarySource(onlyRaisingfi.latest)).toBe(true);
    expect(displaySource(onlyRaisingfi.latest)).toBeNull();
    expect(displaySource(onlyRaisingfi.latest, { name: "Axios", url: "https://www.axios.com/acme" })).toEqual({ name: "Axios", url: "https://www.axios.com/acme" });
    expect(displaySource(onlyRaisingfi.latest, { name: "Still X", url: "https://x.com/other/status/9" })).toBeNull();
    expect(displaySource(onlyRaisingfi.latest, { name: "Wire", url: "https://twitter.com/someone/status/4" })).toBeNull();
    expect(displaySource(onlyRaisingfi.latest, { name: "@raisingfi on X", url: "https://www.reuters.com/acme" })).toBeNull();
    expect(displaySource(onlyRaisingfi.latest, { name: "Leak", url: "https://www.axios.com/acme?api_key=secret" })).toBeNull();

    const [withNews] = buildRounds([report({}), report({ date: "2026-03-02", source: "TechCrunch", sourceUrl: "https://techcrunch.com/acme" })]);
    expect(needsSecondarySource(withNews.latest)).toBe(false);
    expect(displaySource(withNews.latest, { name: "Axios", url: "https://www.axios.com/acme" })?.name).toBe("TechCrunch");

    const [again] = buildRounds([
      report({ date: "2026-01-10", round: "Seed", amount: "$4M", amountUsd: 4_000_000, sourceUrl: "https://x.com/raisingfi/status/1" }),
      report({ date: "2026-05-01", round: "Series A", amount: "$12M", amountUsd: 12_000_000, sourceUrl: "https://twitter.com/raisingfi/status/2" }),
      report({ date: "2026-05-02", round: "Series A", amount: "$12M", amountUsd: 12_000_000, source: "TechCrunch", sourceUrl: "https://techcrunch.com/acme-a" }),
      report({ date: "2026-09-20", round: "Series B", amount: "$20M", amountUsd: 20_000_000, sourceUrl: "https://x.com/raisingfi/status/3" }),
    ]);
    expect(displaySource(again.latest)).toBeNull();
    expect(displaySource(again.latest, { name: "Axios", url: "https://www.axios.com/acme-b" })?.url).toBe("https://www.axios.com/acme-b");
    expect(displaySource(again.earlier[0])?.url).toBe("https://techcrunch.com/acme-a");
    expect(displaySource(again.earlier[1])).toBeNull();
    expect(displaySource(again.earlier[1], { name: "Reuters", url: "https://www.reuters.com/acme-seed" })?.name).toBe("Reuters");
    expect(displaySource(again.earlier[1], { name: "Still X", url: "https://x.com/someone/status/9" })).toBeNull();
  });
});
