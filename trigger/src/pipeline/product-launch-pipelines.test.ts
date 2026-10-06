import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  fetchUrl: vi.fn(),
  lunaJson: vi.fn(),
  searchSerper: vi.fn(),
  webSearch: vi.fn(),
  day0BlitzEnrich: vi.fn(),
  lookupDomain: vi.fn(),
  logger: { info: vi.fn(), warn: vi.fn(), error: vi.fn() },
}));

vi.mock("@trigger.dev/sdk", () => ({ logger: mocks.logger }));
vi.mock("./scrape.js", () => ({ fetchUrl: mocks.fetchUrl }));
vi.mock("./luna.js", () => ({ lunaJson: mocks.lunaJson, isLunaConfigured: () => true }));
vi.mock("./serper.js", () => ({ searchSerper: mocks.searchSerper }));
vi.mock("./rapid-search.js", () => ({ webSearch: mocks.webSearch }));
vi.mock("./enrich-company.js", () => ({ day0BlitzEnrich: mocks.day0BlitzEnrich }));
vi.mock("./domain-lookup.js", async () => ({
  ...await vi.importActual<typeof import("./domain-lookup.js")>("./domain-lookup.js"),
  lookupDomainMultiSignal: mocks.lookupDomain,
}));

const DATE = "2026-10-05";
const PREVIOUS_DATE = "2026-10-04";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status });
}

function product(name: string, rank = 1) {
  return {
    rank, product_name: name, company_name: name, tagline: "Software for businesses", score: 50,
    ph_url: `https://www.producthunt.com/products/${name.toLowerCase()}`,
    categories: [], maker_website: `https://${name.toLowerCase()}.test` as string | null, linkedin_url: null,
  };
}

function lunaResult(data: unknown) {
  return { data, costUsd: 0.01, usage: { inputTokens: 100, cachedInputTokens: 0, outputTokens: 50 }, serviceTier: "flex" };
}

function setupPh(today = [product("Today")], previous = [product("Previous")]) {
  mocks.fetchUrl.mockImplementation(async (url: string) => url);
  mocks.lunaJson.mockImplementation(async (options) => {
    if (options.name === "ph_leaderboard") {
      return lunaResult({ products: options.userPrompt.includes("/2026/10/5") ? today : previous, error: null });
    }
    const products = options.userPrompt.includes("product_name=Today") ? today : previous;
    return lunaResult({ classifications: products.map((p) => ({
      rank: p.rank, company_name: p.company_name, product_name: p.product_name,
      launch_type: "new_product", is_ai: false, classification_reasoning: "A first launch",
    })) });
  });
}

function setupFetch(hits: number = 0) {
  const writes: Array<Array<Record<string, unknown>>> = [];
  const patches: Array<{ url: string; body: Record<string, unknown> }> = [];
  const fetchMock = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    const url = String(input);
    if (init?.method === "PATCH") {
      patches.push({ url, body: JSON.parse(String(init.body)) });
      return json([]);
    }
    if (init?.method === "POST") {
      writes.push(JSON.parse(String(init.body)));
      return new Response(null, { status: 201 });
    }
    if (url.includes("supabase.test")) return json([]);
    if (url.includes("hn.algolia.com") && url.includes("tags=show_hn")) {
      return json({ hits: Array.from({ length: hits }, (_, i) => ({
        objectID: String(i), title: `Company${i} launches Product${i}`, url: `https://company${i}.test/launch`,
      })) });
    }
    if (url.includes("hn.algolia.com")) return json({ hits: [] });
    return new Response("", { status: 200 });
  });
  vi.stubGlobal("fetch", fetchMock);
  return { writes, patches, fetchMock };
}

function classifyNews() {
  mocks.lunaJson.mockImplementation(async (options) => lunaResult({
    results: [...options.userPrompt.matchAll(/\[(\d+)\] TITLE: Company(\d+)/g)].map((m) => ({
      idx: Number(m[1]), is_launch: true, company_name: `Company${m[2]}`, product_name: `Product${m[2]}`,
      launch_type: "new_product", is_ai: false, reason: null,
    })),
  }));
}

function fakeAbortTimeouts() {
  vi.spyOn(AbortSignal, "timeout").mockImplementation((ms) => {
    const controller = new AbortController();
    setTimeout(() => controller.abort(new DOMException("Timed out", "TimeoutError")), ms);
    return controller.signal;
  });
}

function delayedResponse(init?: RequestInit, delayMs?: number, response?: Response): Promise<Response> {
  return new Promise((resolve, reject) => {
    const signal = init?.signal;
    const abort = () => { clearTimeout(timer); reject(signal?.reason); };
    const timer = delayMs === undefined ? undefined : setTimeout(() => {
      signal?.removeEventListener("abort", abort);
      resolve(response!);
    }, delayMs);
    if (signal?.aborted) abort();
    else signal?.addEventListener("abort", abort, { once: true });
  });
}

async function finish<T>(pending: Promise<T>): Promise<T> {
  void pending.catch(() => {});
  await vi.runAllTimersAsync();
  return pending;
}

beforeEach(() => {
  vi.resetModules();
  vi.resetAllMocks();
  vi.useFakeTimers();
  vi.setSystemTime(new Date(`${DATE}T13:00:00Z`));
  vi.stubEnv("SUPABASE_PROJECT_URL", "https://supabase.test");
  vi.stubEnv("SUPABASE_KEY", "test-key");
  vi.stubEnv("RAPID_API_KEY", "test-key");
  mocks.day0BlitzEnrich.mockResolvedValue({ enriched: 0 });
  mocks.searchSerper.mockResolvedValue([]);
  mocks.webSearch.mockResolvedValue(undefined);
  mocks.lookupDomain.mockResolvedValue({ domain: "not_found", confidence: "low" });
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  vi.useRealTimers();
});

describe.each(["ph", "news"])("%s launch domain preservation", (source) => {
  it.each([null, "", "verified.test", "new"])("only fills a missing stored domain (%s)", async (storedDomain) => {
    if (source === "ph") setupPh([{ ...product("Today"), maker_website: "https://replacement.test" }], []);
    else classifyNews();
    mocks.lookupDomain.mockResolvedValue({ domain: "replacement.test", confidence: "high" });
    const { fetchMock, writes, patches } = setupFetch(1);
    const sourceUrl = source === "ph" ? product("Today").ph_url : "https://company0.test/launch";
    let stored: Record<string, unknown> | undefined = storedDomain === "new" ? undefined : { source_url: sourceUrl, company_domain: storedDomain };
    const normalFetch = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation(async (input, init) => {
      if (init?.method === "POST") {
        for (const row of JSON.parse(String(init.body))) stored = { ...stored, ...row };
      }
      if (init?.method === "PATCH") {
        const query = new URL(String(input)).searchParams;
        expect(query.get("source_url")).toBe(`eq.${sourceUrl}`);
        expect(query.get("or")).toBe("(company_domain.is.null,company_domain.eq.)");
        if (stored && (stored.company_domain == null || stored.company_domain === "")) Object.assign(stored, JSON.parse(String(init.body)));
      }
      return normalFetch(input, init);
    });
    if (source === "ph") {
      const { runPhLaunchPipeline } = await import("./product-launches-ph.js");
      await finish(runPhLaunchPipeline({ date: DATE }));
    } else {
      const { runNewsLaunchPipeline } = await import("./product-launches-news.js");
      await finish(runNewsLaunchPipeline({ date: DATE, skipSerper: true }));
    }
    expect(writes.flat().every((row) => !Object.hasOwn(row, "company_domain"))).toBe(true);
    expect(patches).toHaveLength(1);
    expect(stored?.company_domain).toBe(storedDomain === "verified.test" ? "verified.test" : "replacement.test");
  });

  it("keeps a domain resolved concurrently between the upsert and repair", async () => {
    if (source === "ph") setupPh([{ ...product("Today"), maker_website: "https://replacement.test" }], []);
    else classifyNews();
    mocks.lookupDomain.mockResolvedValue({ domain: "replacement.test", confidence: "high" });
    const { fetchMock } = setupFetch(1);
    const normalFetch = fetchMock.getMockImplementation()!;
    let domain: unknown = null;
    fetchMock.mockImplementation(async (input, init) => {
      if (init?.method === "POST") domain = "concurrent.test";
      if (init?.method === "PATCH") {
        expect(new URL(String(input)).searchParams.get("or")).toBe("(company_domain.is.null,company_domain.eq.)");
        if (domain == null || domain === "") domain = JSON.parse(String(init.body)).company_domain;
      }
      return normalFetch(input, init);
    });
    if (source === "ph") {
      const { runPhLaunchPipeline } = await import("./product-launches-ph.js");
      await finish(runPhLaunchPipeline({ date: DATE }));
    } else {
      const { runNewsLaunchPipeline } = await import("./product-launches-news.js");
      await finish(runNewsLaunchPipeline({ date: DATE, skipSerper: true }));
    }
    expect(domain).toBe("concurrent.test");
  });
});

describe("runPhLaunchPipeline", () => {
  it("caps company searches at 90 seconds so enrichment and the previous day still run", async () => {
    setupPh([product("Today"), ...Array.from({ length: 4 }, (_, i) => ({ ...product(`Unknown${i}`, i + 2), maker_website: null }))]);
    const lookupTimes: number[] = [];
    mocks.lookupDomain.mockImplementation(async (_name, _clues, _url, deadlineAt) => {
      lookupTimes.push(Date.now());
      vi.advanceTimersByTime(Math.min(60_000, deadlineAt - Date.now()));
      return { domain: "not_found", confidence: "low" };
    });
    const { writes } = setupFetch();
    const { runPhLaunchPipeline } = await import("./product-launches-ph.js");
    await finish(runPhLaunchPipeline({ date: DATE }));
    expect(mocks.lookupDomain.mock.calls.map((args) => args[3] - lookupTimes[0])).toEqual([60_000, 90_000]);
    expect(mocks.day0BlitzEnrich).toHaveBeenCalledTimes(2);
    expect(writes.flat().map((r) => r.discovered_date)).toContain(PREVIOUS_DATE);
  });

  it("keeps an empty classified company name instead of replacing it with the product name", async () => {
    setupPh([{ ...product("Today"), company_name: "" }], []);
    const { writes } = setupFetch();
    const { runPhLaunchPipeline } = await import("./product-launches-ph.js");
    await finish(runPhLaunchPipeline({ date: DATE }));
    expect(writes.flat()[0].company_name).toBe("");
  });

  it("keeps the persistence reserve when it is earlier than the 90-second domain cap", async () => {
    setupPh([product("Today"), { ...product("Unknown", 2), maker_website: null }], []);
    const start = Date.now();
    const normalLuna = mocks.lunaJson.getMockImplementation()!;
    mocks.lunaJson.mockImplementation(async (options) => {
      if (options.name === "ph_classification") vi.advanceTimersByTime(450_000);
      return normalLuna(options);
    });
    mocks.lookupDomain.mockImplementation(async (_name, _clues, _url, deadlineAt) => {
      vi.advanceTimersByTime(deadlineAt - Date.now());
      return { domain: "official.test", confidence: "high" };
    });
    const { writes } = setupFetch();
    const { runPhLaunchPipeline } = await import("./product-launches-ph.js");
    const result = await finish(runPhLaunchPipeline({ date: DATE }));
    expect(mocks.lookupDomain.mock.calls[0][3]).toBe(start + 500_000);
    expect(result.launchCount).toBe(2);
    expect(writes.flat()).toHaveLength(2);
  });

  it("bounds slow domain repairs so enrichment and the previous day still run", async () => {
    setupPh([product("Today"), product("Other", 2)]);
    const { fetchMock, writes, patches } = setupFetch();
    const normalFetch = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation(async (input, init) => {
      if (init?.method === "PATCH") vi.advanceTimersByTime(15_000);
      return normalFetch(input, init);
    });
    const { runPhLaunchPipeline } = await import("./product-launches-ph.js");
    await finish(runPhLaunchPipeline({ date: DATE }));
    expect(writes.flat()).toHaveLength(3);
    expect(patches).toHaveLength(2);
    expect(mocks.day0BlitzEnrich).toHaveBeenCalledTimes(2);
  });
  it("persists maker domains and resolves a missing website with the existing resolver", async () => {
    setupPh([product("Today"), { ...product("Unknown", 2), maker_website: null }], []);
    mocks.lookupDomain.mockResolvedValue({ domain: "official.test", confidence: "high" });
    const { writes, patches } = setupFetch();
    const { runPhLaunchPipeline } = await import("./product-launches-ph.js");
    await finish(runPhLaunchPipeline({ date: DATE }));
    expect(patches.map((patch) => patch.body.company_domain)).toEqual(["today.test", "official.test"]);
    expect(writes.flat().every((row) => !Object.hasOwn(row, "company_domain"))).toBe(true);
    expect(mocks.lookupDomain).toHaveBeenCalledTimes(1);
    expect(writes.flat()[1].maker_website).toBeNull();
    expect(mocks.day0BlitzEnrich.mock.calls[0][1].map((target: { domain: string }) => target.domain)).toEqual(["https://today.test"]);
  });
  it("deduplicates today's URLs across days even when the stored lookup has no rows", async () => {
    setupPh([product("Today")], [product("Today"), product("Previous", 2)]);
    const { writes } = setupFetch();
    const { runPhLaunchPipeline } = await import("./product-launches-ph.js");
    await finish(runPhLaunchPipeline({ date: DATE }));
    expect(writes.flat().map((row) => [row.product_name, row.discovered_date])).toEqual([
      ["Today", DATE], ["Previous", PREVIOUS_DATE],
    ]);
  });

  it.each(["null", "throw"])("recovers an overlapping launch from the previous day when today's classification fails with %s", async (failure) => {
    setupPh([product("Today")], [product("Today")]);
    const normalLuna = mocks.lunaJson.getMockImplementation()!;
    let classificationCalls = 0;
    mocks.lunaJson.mockImplementation(async (options) => {
      if (options.name === "ph_classification" && classificationCalls++ === 0) {
        if (failure === "throw") throw new Error("Luna unavailable");
        return null;
      }
      return normalLuna(options);
    });
    const { writes } = setupFetch();
    const { runPhLaunchPipeline } = await import("./product-launches-ph.js");
    const result = await finish(runPhLaunchPipeline({ date: DATE }));
    expect(result.launchCount).toBe(1);
    expect(classificationCalls).toBe(2);
    expect(writes.flat().map((row) => [row.source_url, row.discovered_date])).toEqual([
      [product("Today").ph_url, PREVIOUS_DATE],
    ]);
  });

  it.each(["http", "transport"])("recovers an overlapping launch after a %s write batch failure and deduplicates the successful batch", async (failure) => {
    setupPh([
      product("Today"),
      ...Array.from({ length: 49 }, (_, i) => product(`Other${i}`, i + 2)),
      product("Persisted", 51),
    ], [product("Today"), product("Persisted", 2)]);
    const { writes, fetchMock } = setupFetch();
    const normalFetch = fetchMock.getMockImplementation()!;
    const batches: Array<Array<Record<string, unknown>>> = [];
    fetchMock.mockImplementation(async (input, init) => {
      if (init?.method === "POST") {
        batches.push(JSON.parse(String(init.body)));
        if (batches.length === 1) {
          if (failure === "transport") throw new Error("write unavailable");
          return json({}, 503);
        }
      }
      return normalFetch(input, init);
    });
    const { runPhLaunchPipeline } = await import("./product-launches-ph.js");
    const result = await finish(runPhLaunchPipeline({ date: DATE }));
    expect(result.launchCount).toBe(2);
    expect(batches.map((batch) => batch.length)).toEqual([50, 1, 1]);
    expect(writes.flat().map((row) => [row.source_url, row.discovered_date])).toEqual([
      [product("Persisted").ph_url, DATE], [product("Today").ph_url, PREVIOUS_DATE],
    ]);
    const classifications = mocks.lunaJson.mock.calls.filter(([opts]) => opts.name === "ph_classification");
    expect(classifications).toHaveLength(2);
    expect(classifications[1][0].userPrompt).toContain("product_name=Today");
    expect(classifications[1][0].userPrompt).not.toContain("product_name=Persisted");
  });

  it("recovers an overlapping launch filtered by today's low score when the previous day meets the floor", async () => {
    setupPh([{ ...product("Today"), score: 4 }], [product("Today")]);
    const { writes } = setupFetch();
    const { runPhLaunchPipeline } = await import("./product-launches-ph.js");
    const result = await finish(runPhLaunchPipeline({ date: DATE }));
    expect(result.launchCount).toBe(1);
    expect(writes.flat()).toEqual([
      expect.objectContaining({ source_url: product("Today").ph_url, discovered_date: PREVIOUS_DATE, score: 50 }),
    ]);
    expect(mocks.lunaJson.mock.calls.filter(([opts]) => opts.name === "ph_classification")).toHaveLength(1);
  });

  it.each(["http", "transport", "invalid-json"])("skips the previous-day pass on a %s stored lookup failure", async (failure) => {
    setupPh();
    const { writes, fetchMock } = setupFetch();
    const normalFetch = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation(async (input, init) => {
      if (String(input).includes("select=source_url")) {
        if (failure === "transport") throw new Error("lookup unavailable");
        if (failure === "invalid-json") return new Response("not json");
        return json({}, 503);
      }
      return normalFetch(input, init);
    });
    const { runPhLaunchPipeline } = await import("./product-launches-ph.js");
    const result = await finish(runPhLaunchPipeline({ date: DATE }));
    expect(result.launchCount).toBe(1);
    expect(writes.flat().map((row) => row.product_name)).toEqual(["Today"]);
    expect(mocks.lunaJson.mock.calls.filter(([opts]) => opts.name === "ph_classification")).toHaveLength(1);
  });

  it.each(["null", "throw"])("keeps the previous day independent when today's Luna classification fails with %s", async (failure) => {
    setupPh();
    const normalLuna = mocks.lunaJson.getMockImplementation()!;
    mocks.lunaJson.mockImplementation(async (options) => {
      if (options.name === "ph_classification" && options.userPrompt.includes("product_name=Today")) {
        if (failure === "throw") throw new Error("Luna unavailable");
        return null;
      }
      return normalLuna(options);
    });
    const { writes } = setupFetch();
    const { runPhLaunchPipeline } = await import("./product-launches-ph.js");
    const result = await finish(runPhLaunchPipeline({ date: DATE }));
    expect(result.launchCount).toBe(1);
    expect(writes.flat().map((row) => [row.product_name, row.discovered_date])).toEqual([["Previous", PREVIOUS_DATE]]);
    expect(mocks.logger.warn).toHaveBeenCalledWith("PH leaderboard day failed", expect.objectContaining({ dateStr: DATE, status: "failed" }));
  });

  it("records Luna spend from extraction and classification for both leaderboard days", async () => {
    setupPh();
    setupFetch();
    const { runPhLaunchPipeline, phSpend } = await import("./product-launches-ph.js");
    await finish(runPhLaunchPipeline({ date: DATE }));
    expect(phSpend.usd).toBeCloseTo(0.04);
    expect(phSpend.inputTokens).toBe(400);
    expect(phSpend.outputTokens).toBe(200);
  });

  it("never invents PH classifications omitted by Luna", async () => {
    setupPh([product("Today"), product("Missing", 2)], []);
    const normalLuna = mocks.lunaJson.getMockImplementation()!;
    mocks.lunaJson.mockImplementation(async (options) => options.name === "ph_classification"
      ? lunaResult({ classifications: [{ rank: 1, product_name: "Today", company_name: "Today", launch_type: "new_product", is_ai: false, classification_reasoning: "A first launch" }] })
      : normalLuna(options));
    const { writes } = setupFetch();
    const { runPhLaunchPipeline } = await import("./product-launches-ph.js");
    await finish(runPhLaunchPipeline({ date: DATE }));
    expect(writes.flat().map((row) => row.product_name)).toEqual(["Today"]);
  });

  it("excludes stored URLs and keeps each remaining launch dated by its leaderboard day", async () => {
    setupPh([product("Today")], [product("Stored"), product("Previous", 2)]);
    const { writes, fetchMock } = setupFetch();
    const normalFetch = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation(async (input, init) => String(input).includes("select=source_url")
      ? json([{ source_url: product("Stored").ph_url }]) : normalFetch(input, init));
    const { runPhLaunchPipeline } = await import("./product-launches-ph.js");
    await finish(runPhLaunchPipeline({ date: DATE }));
    expect(writes.flat().map((row) => [row.source_url, row.discovered_date])).toEqual([
      [product("Today").ph_url, DATE], [product("Previous").ph_url, PREVIOUS_DATE],
    ]);
  });

  it("bounds real flex-to-standard extraction retries by the absolute deadline", async () => {
    fakeAbortTimeouts();
    vi.stubEnv("OPENAI_API_KEY", "test-key");
    const actual = await vi.importActual<typeof import("./luna.js")>("./luna.js");
    mocks.lunaJson.mockImplementation(actual.lunaJson);
    mocks.fetchUrl.mockResolvedValue("Leaderboard content");
    const requests: Array<{ time: number; tier: string }> = [];
    vi.stubGlobal("fetch", vi.fn(async (_input, init) => {
      requests.push({ time: Date.now(), tier: JSON.parse(String(init.body)).service_tier });
      return delayedResponse(init);
    }));
    const start = Date.now();
    const { runPhLaunchPipeline } = await import("./product-launches-ph.js");
    const result = await finish(runPhLaunchPipeline({ date: DATE }));
    expect(result.launchCount).toBe(0);
    expect(result.stats.durationMs).toBe(485_000);
    expect(requests.map((r) => [r.time - start, r.tier])).toEqual([
      [0, "flex"], [120_000, "default"], [245_000, "flex"], [365_000, "default"],
    ]);
    expect(mocks.fetchUrl).toHaveBeenCalledTimes(2);
    expect(mocks.lunaJson.mock.calls.every(([opts]) => opts.deadlineAt === start + 490_000)).toBe(true);
  });

  it("reserves persistence after a classification consumes its deadline and skips optional work", async () => {
    setupPh();
    const start = Date.now();
    const normalLuna = mocks.lunaJson.getMockImplementation()!;
    mocks.lunaJson.mockImplementation(async (options) => {
      if (options.name === "ph_classification") {
        await new Promise((resolve) => setTimeout(resolve, options.deadlineAt - Date.now()));
      }
      return normalLuna(options);
    });
    const { writes, fetchMock } = setupFetch();
    const normalFetch = fetchMock.getMockImplementation()!;
    const writeTimes: number[] = [];
    fetchMock.mockImplementation(async (input, init) => {
      if (init?.method === "POST") {
        writeTimes.push(Date.now());
        await new Promise((resolve) => setTimeout(resolve, 14_000));
      }
      return normalFetch(input, init);
    });
    const { runPhLaunchPipeline } = await import("./product-launches-ph.js");
    const result = await finish(runPhLaunchPipeline({ date: DATE }));
    expect(result.launchCount).toBe(1);
    expect(result.stats.durationMs).toBe(534_000);
    expect(writeTimes).toEqual([start + 520_000]);
    expect(writes.flat()[0].discovered_date).toBe(DATE);
    expect(fetchMock.mock.calls).toHaveLength(1);
    expect(mocks.day0BlitzEnrich).not.toHaveBeenCalled();
    expect(mocks.fetchUrl.mock.calls.filter(([url]) => url.includes("leaderboard"))).toHaveLength(1);
  });

  it("skips product and homepage fetches when only classification and persistence time remain", async () => {
    setupPh([{ ...product("Today"), maker_website: null }], []);
    const normalLuna = mocks.lunaJson.getMockImplementation()!;
    mocks.lunaJson.mockImplementation(async (options) => {
      if (options.name === "ph_leaderboard") await new Promise((resolve) => setTimeout(resolve, 450_000));
      return normalLuna(options);
    });
    const { writes } = setupFetch();
    const { runPhLaunchPipeline } = await import("./product-launches-ph.js");
    const result = await finish(runPhLaunchPipeline({ date: DATE }));
    expect(result.launchCount).toBe(1);
    expect(mocks.fetchUrl).toHaveBeenCalledTimes(1);
    expect(writes.flat()[0].maker_website).toBeNull();
  });

  it("passes the same run deadline to enrichment for both days", async () => {
    setupPh();
    setupFetch();
    const start = Date.now();
    const { runPhLaunchPipeline } = await import("./product-launches-ph.js");
    await finish(runPhLaunchPipeline({ date: DATE }));
    expect(mocks.day0BlitzEnrich.mock.calls.map((args) => args[4])).toEqual([start + 540_000, start + 540_000]);
  });
});

describe("runNewsLaunchPipeline", () => {
  it("never stores a publisher lookalike from the launch source URL", async () => {
    mocks.lunaJson.mockResolvedValue(lunaResult({ results: [{ idx: 1, is_launch: true, company_name: "Foo Labs", product_name: "Product", launch_type: "new_product", is_ai: false, reason: null }] }));
    const { writes, patches, fetchMock } = setupFetch(1);
    const normalFetch = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation(async (input, init) => String(input).includes("tags=show_hn")
      ? json({ hits: [{ objectID: "1", title: "Foo Labs launches Product", url: "https://foolabs-news.com/story" }] })
      : normalFetch(input, init));
    const { runNewsLaunchPipeline } = await import("./product-launches-news.js");
    await finish(runNewsLaunchPipeline({ date: DATE, skipSerper: true }));
    expect(writes.flat()[0].company_name).toBe("Foo Labs");
    expect(writes.flat()[0]).not.toHaveProperty("company_domain");
    expect(patches).toEqual([]);
    expect(mocks.lookupDomain).toHaveBeenCalledTimes(1);
  });

  it("resolves missing news domains and preserves existing values when the lookup is inconclusive", async () => {
    classifyNews();
    mocks.lookupDomain.mockResolvedValueOnce({ domain: "official.test", confidence: "high" }).mockResolvedValueOnce({ domain: "not_found", confidence: "low" });
    const { writes, patches, fetchMock } = setupFetch(2);
    const normal = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation(async (url, init) => {
      const response = await normal(url, init);
      if (String(url).includes("tags=show_hn")) {
        return json({ hits: [0, 1].map((i) => ({ objectID: String(i), title: `Company${i} launches Product${i}`, url: `https://publisher.test/article${i}` })) });
      }
      return response;
    });
    const { runNewsLaunchPipeline } = await import("./product-launches-news.js");
    await finish(runNewsLaunchPipeline({ date: DATE, skipSerper: true }));
    expect(writes.flat()).toEqual([
      expect.objectContaining({ company_name: "Company0" }),
      expect.objectContaining({ company_name: "Company1" }),
    ]);
    expect(writes.flat().every((row) => !Object.hasOwn(row, "company_domain"))).toBe(true);
    expect(patches.map((patch) => patch.body)).toEqual([{ company_domain: "official.test" }]);
    expect(mocks.lookupDomain).toHaveBeenCalledTimes(2);
  });
  it("writes 126 kept launches in chunks of 50, 50 and 26", async () => {
    classifyNews();
    const { writes, fetchMock } = setupFetch(126);
    const { runNewsLaunchPipeline } = await import("./product-launches-news.js");
    const result = await finish(runNewsLaunchPipeline({ date: DATE, skipSerper: true }));
    expect(result.launchCount).toBe(126);
    expect(writes.map((rows) => rows.length)).toEqual([50, 50, 26]);
    expect(writes.flat().every((row) => row.discovered_date === DATE)).toBe(true);
    // PostgREST rejects the whole batch (PGRST204) for a column product_launches does not have (migration 003).
    expect(Object.keys(writes.flat()[0]).sort()).toEqual([
      "company_name", "description", "discovered_date", "is_ai", "launch_type",
      "pipeline_version", "product_name", "source", "source_url",
    ]);
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "POST").map(([, init]) => init?.headers)).toEqual([
      expect.objectContaining({ Prefer: "resolution=merge-duplicates" }),
      expect.objectContaining({ Prefer: "resolution=merge-duplicates" }),
      expect.objectContaining({ Prefer: "resolution=merge-duplicates" }),
    ]);
  });

  it("drops null or placeholder companies and only falls back to a title for a named company", async () => {
    const { writes } = setupFetch(4);
    mocks.lunaJson.mockResolvedValue(lunaResult({ results: [null, "Unknown", " ", "Acme"].map((company_name, i) => ({
      idx: i + 1, is_launch: true, company_name, product_name: null,
      launch_type: "new_product", is_ai: false, reason: null,
    })) }));
    const { runNewsLaunchPipeline } = await import("./product-launches-news.js");
    const result = await finish(runNewsLaunchPipeline({ date: DATE, skipSerper: true }));
    expect(result.launchCount).toBe(1);
    expect(writes.flat()).toEqual([expect.objectContaining({ company_name: "Acme", product_name: "Company3 launches Product3" })]);
  });

  it.each(["null", "throw"])("persists independent classification batches after a Luna %s failure", async (failure) => {
    classifyNews();
    const normalLuna = mocks.lunaJson.getMockImplementation()!;
    mocks.lunaJson.mockImplementationOnce(async () => {
      if (failure === "throw") throw new Error("Luna unavailable");
      return null;
    }).mockImplementation(normalLuna);
    const { writes } = setupFetch(60);
    const { runNewsLaunchPipeline, classifySpend } = await import("./product-launches-news.js");
    const result = await finish(runNewsLaunchPipeline({ date: DATE, skipSerper: true }));
    expect(result.launchCount).toBe(30);
    expect(writes.flat().map((row) => row.company_name)).toEqual(Array.from({ length: 30 }, (_, i) => `Company${i + 30}`));
    expect(classifySpend.usd).toBeCloseTo(0.01);
  });

  it("bounds sequential real Luna fallbacks and still persists earlier successful batches", async () => {
    fakeAbortTimeouts();
    vi.stubEnv("OPENAI_API_KEY", "test-key");
    const actual = await vi.importActual<typeof import("./luna.js")>("./luna.js");
    mocks.lunaJson.mockImplementation(actual.lunaJson);
    const { writes, fetchMock } = setupFetch(126);
    const normalFetch = fetchMock.getMockImplementation()!;
    const apiTiers: string[] = [];
    const writeTimes: number[] = [];
    fetchMock.mockImplementation(async (input, init) => {
      if (String(input).includes("openai.com")) {
        const body = JSON.parse(String(init?.body));
        apiTiers.push(body.service_tier);
        if (body.service_tier === "flex") return delayedResponse(init);
        const results = [...body.messages[1].content.matchAll(/\[(\d+)\] TITLE: Company(\d+)/g)].map((m) => ({
          idx: Number(m[1]), is_launch: true, company_name: `Company${m[2]}`, product_name: `Product${m[2]}`,
          launch_type: "new_product", is_ai: false, reason: null,
        }));
        return delayedResponse(init, 89_000, json({ choices: [{ message: { content: JSON.stringify({ results }) } }] }));
      }
      if (init?.method === "POST") {
        writeTimes.push(Date.now());
        await new Promise((resolve) => setTimeout(resolve, 14_000));
      }
      return normalFetch(input, init);
    });
    const start = Date.now();
    const { runNewsLaunchPipeline } = await import("./product-launches-news.js");
    const result = await finish(runNewsLaunchPipeline({ date: DATE, skipSerper: true }));
    expect(result.launchCount).toBe(60);
    expect(result.stats).toMatchObject({ rawResults: 126, afterClassify: 60, durationMs: 518_000 });
    expect(apiTiers).toEqual(["flex", "default", "flex", "default", "flex", "default"]);
    expect(mocks.lunaJson).toHaveBeenCalledTimes(3);
    expect(mocks.lunaJson.mock.calls.every(([opts]) => opts.deadlineAt === start + 490_000)).toBe(true);
    expect(writes.map((rows) => rows.length)).toEqual([50, 10]);
    expect(writeTimes).toEqual([start + 490_000, start + 504_000]);
  });

  it("stops slow searches before they consume classification and persistence time", async () => {
    setupFetch();
    mocks.searchSerper.mockImplementation(async () => {
      await new Promise((resolve) => setTimeout(resolve, 31_000));
      return [];
    });
    const { runNewsLaunchPipeline } = await import("./product-launches-news.js");
    const result = await finish(runNewsLaunchPipeline({ date: DATE }));
    expect(mocks.searchSerper).toHaveBeenCalledTimes(15);
    expect(result.stats.durationMs).toBe(468_500);
    expect(mocks.lunaJson).not.toHaveBeenCalled();
  });

  it("keeps later write chunks independent when the first upsert fails", async () => {
    classifyNews();
    const { writes, fetchMock } = setupFetch(126);
    const normalFetch = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation(async (input, init) => {
      const response = await normalFetch(input, init);
      return init?.method === "POST" && writes.length === 1 ? json({}, 503) : response;
    });
    const { runNewsLaunchPipeline } = await import("./product-launches-news.js");
    const result = await finish(runNewsLaunchPipeline({ date: DATE, skipSerper: true }));
    expect(result.launchCount).toBe(76);
    expect(result.stats.afterClassify).toBe(126);
    expect(writes.map((rows) => rows.length)).toEqual([50, 50, 26]);
    expect(mocks.logger.info).toHaveBeenCalledWith("Stage 3: pushed 76 rows to product_launches");
  });

  it("runs the shipped 17 search queries sequentially with at least 250 ms spacing", async () => {
    setupFetch();
    const times: number[] = [];
    mocks.searchSerper.mockImplementation(async () => { times.push(Date.now()); return []; });
    const { runNewsLaunchPipeline, SERPER_QUERIES } = await import("./product-launches-news.js");
    await finish(runNewsLaunchPipeline({ date: DATE }));
    expect(SERPER_QUERIES).toHaveLength(17);
    expect(times).toHaveLength(17);
    expect(times.slice(1).every((time, i) => time - times[i] >= 250)).toBe(true);
  });
});
