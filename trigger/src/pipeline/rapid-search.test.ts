import { afterEach, describe, expect, it, vi } from "vitest";
import { webSearch } from "./rapid-search.js";
import { searchSerper } from "./serper.js";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); vi.unstubAllEnvs(); });

describe("webSearch", () => {
  it("returns Google results", async () => {
    vi.stubGlobal("fetch", vi.fn(async (_url: string | URL) => json({ results: [{ url: "https://a.test", title: "A", description: "desc" }] })));
    expect(await webSearch("hello", { limit: 5, apiKey: "key" })).toEqual({ results: [{ url: "https://a.test", title: "A", snippet: "desc" }], provider: "google" });
  });

  it("falls back to treg on a primary 429", async () => {
    const fetchMock = vi.fn(async (url: string | URL, _init?: RequestInit) => new URL(String(url)).hostname.startsWith("google-")
      ? json({}, 429) : json({ output: { results: [{ link: "https://b.test", title: "B", snippet: "fallback" }] } }));
    vi.stubGlobal("fetch", fetchMock);
    expect(await webSearch("hello", { limit: 5, apiKey: "key", tregToken: "treg" }))
      .toEqual({ results: [{ url: "https://b.test", title: "B", snippet: "fallback" }], provider: "treg" });
    const [url, init] = fetchMock.mock.calls[1];
    expect(String(url)).toBe("https://treg.to/call/treg.google.serp.organic");
    expect(JSON.parse(String(init?.body))).toEqual({ q: "hello", limit: 5 });
  });

  it("has no fallback without a treg token", async () => {
    vi.stubEnv("TREG_TOKEN", "");
    const fetchMock = vi.fn(async (_url: string | URL) => json({}, 503));
    vi.stubGlobal("fetch", fetchMock);
    expect(await webSearch("hello", { limit: 5, apiKey: "key" })).toBeUndefined();
    expect(fetchMock.mock.calls.every(([url]) => new URL(String(url)).hostname.startsWith("google-"))).toBe(true);
  });

  it("does not fall back for a successful empty result list", async () => {
    const fetchMock = vi.fn(async (_url: string | URL) => json({ results: [] }));
    vi.stubGlobal("fetch", fetchMock);
    expect(await webSearch("hello", { limit: 5, apiKey: "key" })).toEqual({ results: [], provider: "google" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("returns undefined when both providers fail", async () => {
    vi.stubGlobal("fetch", vi.fn(async (_url: string | URL) => json({}, 503)));
    expect(await webSearch("hello", { limit: 5, apiKey: "key", tregToken: "treg" })).toBeUndefined();
  });

  it("appends after to the query", async () => {
    const fetchMock = vi.fn(async (_url: string | URL) => json({ results: [] }));
    vi.stubGlobal("fetch", fetchMock);
    await webSearch("hello", { limit: 5, after: "2026-09-01", apiKey: "key" });
    expect(new URL(String(fetchMock.mock.calls[0][0])).searchParams.get("query")).toBe("hello after:2026-09-01");
  });
});

describe("searchSerper compatibility", () => {
  it("maps qdr freshness to an after date", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-10-05T12:00:00Z"));
    const fetchMock = vi.fn(async (_url: string | URL) => json({ results: [] }));
    vi.stubGlobal("fetch", fetchMock);
    await searchSerper("hello", 50, "qdr:w");
    expect(new URL(String(fetchMock.mock.calls[0][0])).searchParams.get("query")).toBe("hello after:2026-09-28");
  });
});
