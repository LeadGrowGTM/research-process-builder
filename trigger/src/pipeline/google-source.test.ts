import { afterEach, describe, expect, it, vi } from "vitest";
import { secondaryQuery } from "./brave-source.js";
import { findGoogleSource } from "./google-source.js";
import type { FundingRound } from "./funding-rounds.js";

const round: FundingRound = {
  key: "rig.security|2026-09-30", companyKey: "rig.security", company: "Rig Security", domain: "rig.security", round: "Seed",
  amount: "$12 Million", amountUsd: 12_000_000, investors: null, date: "2026-09-30", lastReported: "2026-09-30", reports: 1,
  sources: [{ name: "@raisingfi on X", url: "https://x.com/raisingfi/status/1" }],
};

const article = {
  url: "https://www.securityweek.com/rig-security-raises-12m",
  title: "Rig Security",
  description: "raises $12 Million Seed funding",
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function queryOf(url: string): string {
  return new URL(url).searchParams.get("query") ?? "";
}

afterEach(() => vi.unstubAllGlobals());

describe("findGoogleSource", () => {
  it("maps url, title, and description, and stops when the quoted query hits", async () => {
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      const parsed = new URL(String(url));
      expect(`${parsed.origin}${parsed.pathname}`).toBe("https://google-search74.p.rapidapi.com/");
      expect(parsed.searchParams.get("query")).toBe(secondaryQuery(round));
      expect(parsed.searchParams.get("limit")).toBe("10");
      expect(parsed.searchParams.get("related_keywords")).toBe("false");
      expect(init?.method).toBe("GET");
      expect(init?.headers).toMatchObject({
        "x-rapidapi-key": "rapid-key",
        "x-rapidapi-host": "google-search74.p.rapidapi.com",
      });
      return json({ results: [article] });
    });
    vi.stubGlobal("fetch", fetchMock);

    const found = await findGoogleSource(round, "rapid-key");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(found.source?.url).toBe(article.url);
    expect(found.queries).toEqual([secondaryQuery(round)]);
  });

  it("skips X and raisingfi results and keeps the next qualifying article", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json({
      results: [
        { url: "https://x.com/raisingfi/status/9", title: "Rig Security raises $12 Million Seed funding" },
        { url: "https://raisingfi.com/rig-security", title: "Rig Security raises $12 Million Seed funding" },
        article,
      ],
    })));

    const found = await findGoogleSource(round, "rapid-key");
    expect(found.source?.url).toBe(article.url);
    expect(found.queries).toEqual([secondaryQuery(round)]);
  });

  it("tries the company site only after the quoted query misses", async () => {
    const calls: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      calls.push(queryOf(String(url)));
      if (calls.length === 1) return json({ results: [] });
      return json({
        results: [{ url: "https://rig.security/blog/seed", title: "Rig Security raises $12 Million Seed funding", description: "We announced our round." }],
      });
    }));

    const found = await findGoogleSource(round, "rapid-key");
    expect(found.source?.url).toBe("https://rig.security/blog/seed");
    expect(calls).toEqual([
      '"Rig Security" raises $12 Million Seed funding',
      "site:rig.security (raises OR funding OR announces)",
    ]);
    expect(found.queries).toEqual(calls);
  });

  it("does not run a site query when the round has no domain", async () => {
    const calls: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      calls.push(queryOf(String(url)));
      return json({ results: [] });
    }));

    const found = await findGoogleSource({ ...round, domain: "" }, "rapid-key");
    expect(found.source).toBeNull();
    expect(calls).toEqual(['"Rig Security" raises $12 Million Seed funding']);
  });

  it("rejects unrelated results for a generic company name", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json({
      results: [
        { url: "https://news.example.com/models", title: "Foundational models raise a Series B", description: "A different market." },
        { url: "https://techcrunch.com/other", title: "Another startup raises $10M pre-seed", description: "Unrelated company." },
      ],
    })));

    const generic: FundingRound = {
      ...round,
      company: "Foundational",
      domain: "",
      round: "Pre-Seed",
      amount: "$10M",
      amountUsd: 10_000_000,
    };
    const found = await findGoogleSource(generic, "rapid-key");
    expect(found.source).toBeNull();
    expect(found.queries).toHaveLength(1);
  });

  it("returns undefined on 429 and does not try the site query", async () => {
    const fetchMock = vi.fn(async () => json({ message: "slow down" }, 429));
    vi.stubGlobal("fetch", fetchMock);
    const found = await findGoogleSource(round, "rapid-key");
    expect(found.source).toBeUndefined();
    expect(found.queries).toEqual([secondaryQuery(round)]);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("returns undefined when a 200 payload has no results array", async () => {
    const fetchMock = vi.fn(async () => json({ search_term: "Rig Security" }));
    vi.stubGlobal("fetch", fetchMock);
    expect((await findGoogleSource(round, "rapid-key")).source).toBeUndefined();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
