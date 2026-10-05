import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  fetchUrl: vi.fn(),
  lunaJson: vi.fn(),
  searchSerper: vi.fn(),
  webSearch: vi.fn(),
  day0BlitzEnrich: vi.fn(),
  logger: { info: vi.fn(), warn: vi.fn(), error: vi.fn() },
}));

vi.mock("@trigger.dev/sdk", () => ({ logger: mocks.logger }));
vi.mock("./firecrawl.js", () => ({ fetchUrl: mocks.fetchUrl }));
vi.mock("./luna.js", () => ({ lunaJson: mocks.lunaJson, isLunaConfigured: () => true }));
vi.mock("./serper.js", () => ({ searchSerper: mocks.searchSerper }));
vi.mock("./rapid-search.js", () => ({ webSearch: mocks.webSearch }));
vi.mock("./enrich-company.js", () => ({ day0BlitzEnrich: mocks.day0BlitzEnrich }));

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
  const fetchMock = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    const url = String(input);
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
  return { writes, fetchMock };
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
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  vi.useRealTimers();
});

describe("runPhLaunchPipeline", () => {
  it("deduplicates today's URLs across days even when the stored lookup has no rows", async () => {
    setupPh([product("Today")], [product("Today"), product("Previous", 2)]);
    const { writes } = setupFetch();
    const { runPhLaunchPipeline } = await import("./product-launches-ph.js");
    await finish(runPhLaunchPipeline({ date: DATE }));
    expect(writes.flat().map((row) => [row.product_name, row.discovered_date])).toEqual([
      ["Today", DATE], ["Previous", PREVIOUS_DATE],
    ]);
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
    expect(mocks.day0BlitzEnrich.mock.calls.map((args) => args[2])).toEqual([start + 540_000, start + 540_000]);
  });
});

describe("runNewsLaunchPipeline", () => {
  it("writes 126 kept launches in chunks of 50, 50 and 26", async () => {
    classifyNews();
    const { writes, fetchMock } = setupFetch(126);
    const { runNewsLaunchPipeline } = await import("./product-launches-news.js");
    const result = await finish(runNewsLaunchPipeline({ date: DATE, skipSerper: true }));
    expect(result.launchCount).toBe(126);
    expect(writes.map((rows) => rows.length)).toEqual([50, 50, 26]);
    expect(writes.flat().every((row) => row.discovered_date === DATE)).toBe(true);
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
    await finish(runNewsLaunchPipeline({ date: DATE, skipSerper: true }));
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
