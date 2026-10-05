import { afterEach, describe, expect, it, vi } from "vitest";
import { webSearch } from "./rapid-search.js";
import { searchSerper } from "./serper.js";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); vi.unstubAllEnvs(); });

describe("webSearch", () => {
  // Runs first so the module-level Google queue starts empty.
  it("starts Google calls at least 250 ms apart", async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn(async (_url: string | URL) => json({ results: [] }));
    vi.stubGlobal("fetch", fetchMock);
    const calls = [webSearch("a", { limit: 5, apiKey: "key" }), webSearch("b", { limit: 5, apiKey: "key" }), webSearch("c", { limit: 5, apiKey: "key" })];
    await vi.advanceTimersByTimeAsync(0);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(249);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(250);
    expect(fetchMock).toHaveBeenCalledTimes(3);
    await Promise.all(calls);
    await vi.runAllTimersAsync(); // drain the queue so later real-timer tests do not wait on a fake timer
  });

  it.each([429, 503])("spaces every HTTP attempt when concurrent calls first return %i", async (status) => {
    vi.useFakeTimers();
    const timestamps: number[] = [];
    const attempts = new Map<string, number>();
    vi.stubGlobal("fetch", vi.fn(async (url: string | URL) => {
      const query = new URL(String(url)).searchParams.get("query")!;
      timestamps.push(Date.now());
      const attempt = (attempts.get(query) ?? 0) + 1;
      attempts.set(query, attempt);
      return attempt === 1 ? json({}, status) : json({ results: [] });
    }));
    const calls = ["a", "b", "c", "d"].map((q) => webSearch(q, { limit: 5, apiKey: "key", fallback: false }));
    await vi.runAllTimersAsync();
    await Promise.all(calls);
    expect(timestamps).toHaveLength(status === 503 ? 8 : 4);
    for (let i = 1; i < timestamps.length; i++) expect(timestamps[i] - timestamps[i - 1]).toBeGreaterThanOrEqual(250);
  });

  it("releases the queue after rejected and aborted attempts", async () => {
    vi.useFakeTimers();
    const timestamps: number[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string | URL) => {
      timestamps.push(Date.now());
      if (new URL(String(url)).searchParams.get("query") === "bad") throw new DOMException("Aborted", "AbortError");
      return json({ results: [] });
    }));
    const bad = webSearch("bad", { limit: 5, apiKey: "key", fallback: false });
    const good = webSearch("good", { limit: 5, apiKey: "key", fallback: false });
    await vi.runAllTimersAsync();
    expect(await bad).toBeUndefined();
    expect(await good).toEqual({ results: [], provider: "google" });
    expect(timestamps).toHaveLength(3);
    for (let i = 1; i < timestamps.length; i++) expect(timestamps[i] - timestamps[i - 1]).toBeGreaterThanOrEqual(250);
  });

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

  it("asks Google for up to 100 results but keeps treg at 30", async () => {
    const fetchMock = vi.fn(async (url: string | URL, _init?: RequestInit) => new URL(String(url)).hostname.startsWith("google-")
      ? json({}, 429) : json({ output: { results: [] } }));
    vi.stubGlobal("fetch", fetchMock);
    await webSearch("hello", { limit: 250, apiKey: "key", tregToken: "treg" });
    expect(new URL(String(fetchMock.mock.calls[0][0])).searchParams.get("limit")).toBe("100");
    expect(JSON.parse(String(fetchMock.mock.calls[1][1]?.body))).toEqual({ q: "hello", limit: 30 });
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
