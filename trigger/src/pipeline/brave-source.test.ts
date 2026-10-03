import { afterEach, describe, expect, it, vi } from "vitest";
import { findSecondarySource, pickSecondarySource, secondaryQuery } from "./brave-source.js";
import type { FundingRound } from "./funding-rounds.js";

const round: FundingRound = {
  key: "rig.security|2026-09-30", companyKey: "rig.security", company: "Rig Security", domain: "rig.security", round: "Seed",
  amount: "$12 Million", amountUsd: 12_000_000, investors: null, date: "2026-09-30", lastReported: "2026-09-30", reports: 1,
  sources: [{ name: "@raisingfi on X", url: "https://x.com/raisingfi/status/1" }],
};

afterEach(() => vi.unstubAllGlobals());

describe("pickSecondarySource", () => {
  it("skips social posts and unrelated pages, takes the first funding article naming the company", () => {
    const picked = pickSecondarySource(round, [
      { url: "https://x.com/raisingfi/status/1", title: "Rig Security raises $12M" },
      { url: "https://www.linkedin.com/posts/rig", title: "Rig Security raises $12M seed" },
      { url: "https://example.com/careers", title: "Rig Security careers" },
      { url: "http://insecure.example.com/rig", title: "Rig Security raises $12M" },
      { url: "https://www.securityweek.com/rig-security-raises-12m", title: "Rig Security Raises $12 Million in <strong>Seed</strong> Funding" },
    ]);
    expect(picked?.url).toBe("https://www.securityweek.com/rig-security-raises-12m");
    expect(picked?.name).toBeTruthy();
  });

  it("returns null when nothing qualifies", () => {
    expect(pickSecondarySource(round, [{ url: "https://news.example.com/a", title: "Another company raises $5M" }])).toBeNull();
  });

  it("rejects a different company, a different round, and links that cannot be published", () => {
    const picked = pickSecondarySource(round, [
      { url: "https://news.example.com/rigor", title: "Rigorous Systems raises $12M seed" },
      { url: "https://news.example.com/series-b", title: "Rig Security raises $40M Series B" },
      { url: "https://user:pass@news.example.com/rig", title: "Rig Security raises $12M seed" },
      { url: "https://news.example.com/rig?api_key=secret", title: "Rig Security raises $12M seed" },
      { url: "https://news.example.com/extra", title: "extra.co raises $12M seed funding" },
    ]);
    expect(picked).toBeNull();
    expect(pickSecondarySource({ ...round, company: "A", domain: "a.co" }, [
      { url: "https://news.example.com/extra", title: "extra.co raises $12M seed funding" },
    ])).toBeNull();
  });

  it("matches a round amount only as its own number, not as digits inside a larger amount", () => {
    const coverage = {
      company: "Coverage Robotics",
      domain: "coverage-robotics.example",
      round: "Series A",
      amount: null,
      amountUsd: 2_000_000,
    };
    const article = (title: string) => [{
      url: "https://news.example/coverage-robotics",
      title,
      description: "Venture investment announced today.",
    }];
    expect(pickSecondarySource(coverage, article("Coverage Robotics raises $12000000 in new funding"))).toBeNull();
    expect(pickSecondarySource(coverage, article("Coverage Robotics raises $12,000,000 in new funding"))).toBeNull();
    expect(pickSecondarySource(coverage, article("Coverage Robotics raises $20M in new funding"))).toBeNull();
    expect(pickSecondarySource(coverage, article("Coverage Robotics raises $2M in new funding"))?.url).toBe("https://news.example/coverage-robotics");
    expect(pickSecondarySource(coverage, article("Coverage Robotics raises $2 million in new funding"))?.url).toBe("https://news.example/coverage-robotics");
    expect(pickSecondarySource(coverage, article("Coverage Robotics raises $2,000,000 in new funding"))?.url).toBe("https://news.example/coverage-robotics");
    expect(pickSecondarySource(coverage, article("Coverage Robotics raises USD 2 million in new funding"))?.url).toBe("https://news.example/coverage-robotics");
    expect(pickSecondarySource(coverage, article("Coverage Robotics raises 2000000 in new funding"))?.url).toBe("https://news.example/coverage-robotics");
  });

  it("accepts this round by amount, and a short name only when the domain matches", () => {
    expect(pickSecondarySource(round, [
      { url: "https://www.axios.com/2026/09/30/rig-security-seed", title: "Rig Security raises $12M" },
    ])?.url).toBe("https://www.axios.com/2026/09/30/rig-security-seed");
    expect(pickSecondarySource({ ...round, company: "AI", domain: "rig.security" }, [
      { url: "https://rig.security/blog/seed", title: "We raised $12M seed funding" },
    ])?.url).toBe("https://rig.security/blog/seed");
  });

  it("builds a quoted query with amount and round", () => {
    expect(secondaryQuery(round)).toBe('"Rig Security" raises $12 Million Seed funding');
  });
});

describe("findSecondarySource", () => {
  it("queries Brave with the subscription token and reads news then web results", async () => {
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) => new Response(JSON.stringify({
      news: { results: [{ url: "https://techcrunch.com/rig-security", title: "Rig Security raises $12M seed round" }] },
      web: { results: [] },
    }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const found = await findSecondarySource(round, "brave-key");
    expect(found?.url).toBe("https://techcrunch.com/rig-security");
    const [url, init] = fetchMock.mock.calls[0];
    expect(new URL(url).hostname).toBe("api.search.brave.com");
    expect(init?.headers).toMatchObject({ "X-Subscription-Token": "brave-key" });
  });

  it("returns undefined on provider failure so the round is retried later", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 401 })));
    expect(await findSecondarySource(round, "bad-key")).toBeUndefined();
  });

  it("returns null for an explicit empty result and undefined when the payload has no result list", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ web: { results: [] }, news: { results: [] } }), { status: 200 })));
    expect(await findSecondarySource(round, "brave-key")).toBeNull();
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ message: "unavailable" }), { status: 200 })));
    expect(await findSecondarySource(round, "brave-key")).toBeUndefined();
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ web: { results: {} } }), { status: 200 })));
    expect(await findSecondarySource(round, "brave-key")).toBeUndefined();
  });
});
