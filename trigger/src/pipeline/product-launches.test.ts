import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanLeaderboard, fetchLeaderboard } from "./product-launches-ph.js";
import { PLACEHOLDER_COMPANY_RE, SERPER_QUERIES, SOCIAL_DOMAIN_RE, fetchHNAlgolia, runSerperQueries } from "./product-launches-news.js";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); vi.useRealTimers(); });

describe("cleanLeaderboard", () => {
  it("drops image and blank lines and keeps product rows", () => {
    const md = "# Best of Product Hunt\n\n![Logo](https://ph-files.imgix.net/a.png?w=48)\n\n[1\\. Acme](https://www.producthunt.com/products/acme)Tagline\n![Promoted](https://x/y)";
    expect(cleanLeaderboard(md)).toBe("# Best of Product Hunt\n[1\\. Acme](https://www.producthunt.com/products/acme)Tagline");
  });
});

describe("fetchLeaderboard", () => {
  it("retries a failed fetch instead of returning zero launches", async () => {
    vi.useFakeTimers();
    vi.stubEnv("OPENAI_API_KEY", "test-key");
    vi.stubEnv("SPIDER_API_KEY", "test-key");
    const page = "Leaderboard product details ".repeat(50);
    let pageCalls = 0;
    const fetchMock = vi.fn(async (url: string | URL) => {
      const u = String(url);
      if (u.includes("openai.com")) {
        return json({ choices: [{ message: { content: JSON.stringify({ products: [{ rank: 1, product_name: "Acme", company_name: null, tagline: null, score: 50, ph_url: "https://www.producthunt.com/products/acme", categories: [], maker_website: null }], error: null }) } }] });
      }
      pageCalls++;
      return pageCalls === 1 ? new Response("blocked", { status: 403 }) : json([{ status: 200, content: page }]);
    });
    vi.stubGlobal("fetch", fetchMock);
    const pending = fetchLeaderboard("2026-10-02");
    await vi.advanceTimersByTimeAsync(10_000);
    const products = await pending;
    expect(pageCalls).toBe(2);
    expect(products.map((p) => p.product_name)).toEqual(["Acme"]);
  });

  it("gives up with an empty list after the last attempt", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("fetch", vi.fn(async () => new Response("blocked", { status: 403 })));
    const pending = fetchLeaderboard("2026-10-02");
    await vi.advanceTimersByTimeAsync(60_000);
    expect(await pending).toEqual([]);
  });
});

describe("fetchHNAlgolia", () => {
  it("maps hits, falling back to the item page for text posts, and applies the point floor", async () => {
    const fetchMock = vi.fn(async (_url: string | URL) => json({ hits: [
      { objectID: "1", title: "Show HN: Acme - docs for agents", url: "https://acme.dev/" },
      { objectID: "2", title: "Ask-style post", url: null },
      { objectID: "3", url: "https://untitled.test" },
    ] }));
    vi.stubGlobal("fetch", fetchMock);
    const items = await fetchHNAlgolia("show_hn", "hn_show", 3, 36);
    expect(items.map((i) => [i.source_url, i.source_domain, i.query_source])).toEqual([
      ["https://acme.dev/", "acme.dev", "hn_show"],
      ["https://news.ycombinator.com/item?id=2", "news.ycombinator.com", "hn_show"],
    ]);
    const requested = decodeURIComponent(String(fetchMock.mock.calls[0][0]));
    expect(requested).toContain("tags=show_hn");
    expect(requested).toContain("points>=3");
  });

  it("returns nothing on an HTTP error", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json({}, 503)));
    expect(await fetchHNAlgolia("front_page", "hn_front", 0, 36)).toEqual([]);
  });
});

describe("runSerperQueries", () => {
  it("keeps going when one query fails and tags results with the query id", async () => {
    const queries = SERPER_QUERIES.slice(0, 2);
    vi.useFakeTimers();
    vi.stubGlobal("fetch", vi.fn(async (url: string | URL) => decodeURIComponent(String(url)).includes("businesswire.com")
      ? json({}, 503)
      : json({ results: [{ url: "https://www.example.com/a", title: "A", description: "d" }] })));
    const pending = runSerperQueries("qdr:d", queries);
    await vi.advanceTimersByTimeAsync(60_000);
    const out = await pending;
    expect(out.map((o) => o.query_source)).toEqual([queries[1].id]);
    expect(out[0].source_domain).toBe("example.com");
  });
});

describe("launch filters", () => {
  it("flags social hosts and placeholder company names", () => {
    for (const host of ["facebook.com", "www.instagram.com", "www3.skool.com", "x.com", "m.youtube.com"]) expect(SOCIAL_DOMAIN_RE.test(host)).toBe(true);
    for (const host of ["businesswire.com", "linkedin.example.com", "box.com", "github.com"]) expect(SOCIAL_DOMAIN_RE.test(host)).toBe(false);
    for (const name of ["Unknown", "show hn", "", "N/A"]) expect(PLACEHOLDER_COMPANY_RE.test(name)).toBe(true);
    expect(PLACEHOLDER_COMPANY_RE.test("Acme")).toBe(false);
  });
});
