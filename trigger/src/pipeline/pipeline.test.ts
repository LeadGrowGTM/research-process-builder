import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { extractDomainFromArticle, isStaleRound, OUTPUT_RESERVE_MS, runFundingPipeline } from "./pipeline.js";
import { SERIES_A_CONFIG } from "./round-configs.js";
import type { ExtractedData, PipelineConfig, RawResult } from "./types.js";
import { runDiscovery } from "./serper.js";
import { extractWithOpenAI, validateDomainSemantic } from "./openai.js";
import { lookupDomainMultiSignal } from "./domain-lookup.js";
import { getRecentCompanyNames, pushToSupabase, FundingWriteError } from "./supabase.js";
import { pushToWebhook } from "./webhook.js";
import { day0BlitzEnrich } from "./enrich-company.js";
import { logger } from "@trigger.dev/sdk";

import { fetchUrl, htmlToText } from "./scrape.js";

describe("extractDomainFromArticle", () => {
  it("ignores an absolute publisher link even when its path contains the company domain", () => {
    const sourceUrl = "https://publisher.com/news/acme";
    const article = htmlToText('<a href="/company/acme.com">Acme profile</a>', sourceUrl);
    expect(article).toBe("Acme profile (https://publisher.com/company/acme.com)");
    expect(extractDomainFromArticle(article, "Acme", sourceUrl)).toBeNull();
    expect(extractDomainFromArticle(`${article} Website (https://acme.com/)`, "Acme", sourceUrl)).toBe("acme.com");
  });

  it.each(["/company/acme.com", "../company/acme.com", "company/acme.com", "/company?website=acme.com"])("ignores unresolved relative link paths (%s)", (href) => {
    const sourceUrl = "https://publisher.com/news/acme";
    const article = `Profile (${href})`;
    expect(extractDomainFromArticle(article, "Acme", sourceUrl)).toBeNull();
    expect(extractDomainFromArticle(`${article} Visit acme.com`, "Acme", sourceUrl)).toBe("acme.com");
  });

  it.each(["/company/https://acme.com", "/company?website=https://acme.com"])("ignores absolute URLs embedded in relative destinations (%s)", (href) => {
    const sourceUrl = "https://publisher.com/news/acme";
    const article = `Profile (${href})`;
    expect(extractDomainFromArticle(article, "Acme", sourceUrl)).toBeNull();
    expect(extractDomainFromArticle(`${article} Website (https://acme.com/)`, "Acme", sourceUrl)).toBe("acme.com");
  });

  it.each([
    ["publisher.com", "news.publisher.com"],
    ["news.publisher.com", "publisher.com"],
    ["news.publisher.com", "profiles.publisher.com"],
    ["news.publisher.co.uk", "profiles.publisher.co.uk"],
  ])("excludes publisher domains and subdomains (%s -> %s)", (sourceHost, linkHost) => {
    const sourceUrl = `https://${sourceHost}/news/acme`;
    const article = `Profile (https://${linkHost}/company/acme.com) Website https://${linkHost}/company/acme.com Website https://${linkHost}/company/acme.com`;
    expect(extractDomainFromArticle(article, "Publisher", sourceUrl)).toBeNull();
    expect(extractDomainFromArticle(`${article} Website (https://acme.com/)`, "Acme", sourceUrl)).toBe("acme.com");
  });

  it("uses the complete hostname of an absolute company link", () => {
    expect(extractDomainFromArticle("Website (https://acme.example.com/company/other.com)", "Example", "https://publisher.com/story"))
      .toBe("acme.example.com");
  });

  it.each(["Visit acme.com", "Acme builds tools at acme.com.", "Website (https://www.acme.com/about)", "Contact hi@acme.com"])("keeps legitimate company domains (%s)", (article) => {
    expect(extractDomainFromArticle(article, "Acme", "https://publisher.com/story")).toBe("acme.com");
  });

  it("keeps other companies on the same country-code suffix", () => {
    expect(extractDomainFromArticle("Website (https://acme.co.uk/about)", "Acme", "https://news.publisher.co.uk/story"))
      .toBe("acme.co.uk");
  });
});

vi.mock("@trigger.dev/sdk", () => ({ logger: { info: vi.fn(), warn: vi.fn() } }));

vi.mock("./serper.js", () => ({ runDiscovery: vi.fn() }));
vi.mock("./scrape.js", async () => ({
  ...await vi.importActual<typeof import("./scrape.js")>("./scrape.js"),
  fetchUrl: vi.fn(),
}));
vi.mock("./openai.js", () => ({
  extractWithOpenAI: vi.fn(),
  validateDomainSemantic: vi.fn(async () => ({ status: "Correct" })),
}));
vi.mock("./domain-lookup.js", () => ({
  lookupDomainMultiSignal: vi.fn(async () => ({ domain: "not_found", source: "not_found" })),
  isDomainBlocked: vi.fn(() => false),
}));
vi.mock("./supabase.js", async () => ({
  ...await vi.importActual<typeof import("./supabase.js")>("./supabase.js"),
  isSupabaseConfigured: vi.fn(() => true),
  checkTable: vi.fn(async () => true),
  pushToSupabase: vi.fn(async (rows: unknown[]) => rows.length),
  getRecentCompanyNames: vi.fn(async () => new Set<string>()),
}));
vi.mock("./webhook.js", () => ({ pushToWebhook: vi.fn(async (rows: unknown[]) => rows.length) }));
vi.mock("./enrich-company.js", () => ({ day0BlitzEnrich: vi.fn(async () => undefined) }));

const RUN_DATE = "2026-10-05";

function raw(name: string): RawResult {
  return {
    company_name_raw: "", amount_raw: "", round_type_raw: "", query_source: "q1", snippet: "",
    title: `${name} raises $10M Series A led by Example Capital`,
    source_url: `https://www.finsmes.com/2026/10/05/${name.toLowerCase()}`,
    source_domain: "www.finsmes.com",
  };
}

function extracted(name: string, fundingDate: string | null): ExtractedData {
  return {
    company_name: name, company_domain: null, amount_raised: "$10M", round_type: "Series A", lead_investors: "Example Capital",
    round_reasoning: null, industry: null, location: null, funding_date: fundingDate,
  };
}

function config(overrides: Partial<PipelineConfig> = {}): PipelineConfig {
  return {
    roundConfig: SERIES_A_CONFIG, pipelineId: "test", tbs: "qdr:d", date: RUN_DATE,
    skipEnrich: false, maxEnrich: 100, dryRun: true, ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(fetchUrl).mockImplementation(async (url) => `Funding news. Visit ${new URL(url).pathname.split("/").pop()}.com to learn more.`);
  vi.mocked(pushToWebhook).mockImplementation(async (rows) => rows.length);
  vi.mocked(pushToSupabase).mockImplementation(async (rows) => rows.length);
  vi.stubEnv("CLAY_SERIES_A_WEBHOOK_URL", "https://clay.test/hook");
  vi.stubEnv("CLAY_SERIES_A_WEBHOOK_TOKEN", "test-token");
});
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllEnvs(); vi.unstubAllGlobals(); });

describe("isStaleRound", () => {
  it("drops rounds dated more than 45 days before the run", () => {
    expect(isStaleRound("2015-12-10", "2026-10-05")).toBe(true);
    expect(isStaleRound("2026-06-23", "2026-10-05")).toBe(true);
  });

  it("keeps recent, undated and unparseable dates", () => {
    expect(isStaleRound("2026-09-01", "2026-10-05")).toBe(false);
    expect(isStaleRound("2026-10-05", "2026-10-05")).toBe(false);
    expect(isStaleRound(null, "2026-10-05")).toBe(false);
    expect(isStaleRound("October 2026", "2026-10-05")).toBe(false);
  });
});

describe("runFundingPipeline freshness gate", () => {
  it("uses the canonical extracted company name for free article-link resolution", async () => {
    vi.mocked(runDiscovery).mockResolvedValue([raw("Project Alpha")]);
    vi.mocked(fetchUrl).mockResolvedValue("Funding announcement. Company site https://acme.dev/team");
    vi.mocked(extractWithOpenAI).mockResolvedValue(extracted("Acme", RUN_DATE));
    const result = await runFundingPipeline(config());
    expect(result.companies[0].company_domain).toBe("acme.dev");
    expect(lookupDomainMultiSignal).not.toHaveBeenCalled();
  });

  it("does not store low-confidence search domains on funding rows", async () => {
    vi.mocked(runDiscovery).mockResolvedValue([raw("Alpha")]);
    vi.mocked(fetchUrl).mockResolvedValue("A funding announcement without a website");
    vi.mocked(extractWithOpenAI).mockResolvedValue(extracted("Alpha", RUN_DATE));
    vi.mocked(lookupDomainMultiSignal).mockResolvedValueOnce({ domain: "wrong.test", confidence: "low", source: "search_validated", evidence: "Ambiguous company" });
    const result = await runFundingPipeline(config());
    expect(result.companies[0].company_domain).toBe("not_found");
    expect(validateDomainSemantic).not.toHaveBeenCalled();
  });
  it("drops an old round reported in a freshly dated article", async () => {
    vi.mocked(runDiscovery).mockResolvedValue([raw("Acme")]);
    vi.mocked(extractWithOpenAI).mockResolvedValue(extracted("Acme", "2015-12-10"));
    const result = await runFundingPipeline(config());
    expect(result.companyCount).toBe(0);
  });

  it("falls back to the article URL date when the round date is missing", async () => {
    vi.mocked(runDiscovery).mockResolvedValue([raw("Acme")]);
    vi.mocked(extractWithOpenAI).mockResolvedValue(extracted("Acme", null));
    const result = await runFundingPipeline(config());
    expect(result.companies.map((c) => [c.company_name, c.funding_date])).toEqual([["Acme", "2026-10-05"]]);
  });
});

describe("runFundingPipeline extraction gate", () => {
  it("does not deliver a candidate when every source has no article text", async () => {
    const candidate = raw("Alpha");
    const fallback = { ...candidate, source_url: `${candidate.source_url}?fallback=1` };
    vi.mocked(runDiscovery).mockResolvedValue([candidate, fallback]);
    vi.mocked(fetchUrl).mockResolvedValue(null);

    const result = await runFundingPipeline(config({ dryRun: false }));

    expect(result.companyCount).toBe(0);
    expect(fetchUrl).toHaveBeenCalledTimes(2);
    expect(extractWithOpenAI).not.toHaveBeenCalled();
    expect(pushToSupabase).not.toHaveBeenCalled();
    expect(pushToWebhook).not.toHaveBeenCalled();
    expect(logger.info).toHaveBeenCalledWith("Not shipped (no extraction): Alpha (no article text)");
  });

  it("does not deliver a candidate when extraction fails", async () => {
    vi.mocked(runDiscovery).mockResolvedValue([raw("Alpha")]);
    vi.mocked(extractWithOpenAI).mockResolvedValue(null);

    const result = await runFundingPipeline(config({ dryRun: false }));

    expect(result.companyCount).toBe(0);
    expect(pushToSupabase).not.toHaveBeenCalled();
    expect(pushToWebhook).not.toHaveBeenCalled();
    expect(logger.info).toHaveBeenCalledWith("Not shipped (no extraction): Alpha (extraction failed)");
  });

  it("does not deliver an extraction with a blank company name", async () => {
    vi.mocked(runDiscovery).mockResolvedValue([raw("Alpha")]);
    vi.mocked(extractWithOpenAI).mockResolvedValue(extracted("  ", RUN_DATE));

    const result = await runFundingPipeline(config({ dryRun: false }));

    expect(result.companyCount).toBe(0);
    expect(pushToSupabase).not.toHaveBeenCalled();
    expect(pushToWebhook).not.toHaveBeenCalled();
  });

  it("retries a candidate on the next run after extraction fails", async () => {
    vi.mocked(runDiscovery).mockResolvedValue([raw("Alpha")]);
    vi.mocked(extractWithOpenAI)
      .mockResolvedValueOnce(null)
      .mockResolvedValueOnce(extracted("Alpha", RUN_DATE));
    vi.mocked(getRecentCompanyNames).mockResolvedValue(new Set<string>());

    await runFundingPipeline(config({ dryRun: false, skipKnownCompanies: true }));
    const second = await runFundingPipeline(config({ dryRun: false, skipKnownCompanies: true }));

    expect(extractWithOpenAI).toHaveBeenCalledTimes(2);
    expect(second.companyCount).toBe(1);
    expect(pushToSupabase).toHaveBeenCalledTimes(1);
    expect(pushToWebhook).toHaveBeenCalledTimes(1);
  });

  it("does not deliver a post-extraction sentinel", async () => {
    vi.mocked(runDiscovery).mockResolvedValue([raw("Alpha")]);
    vi.mocked(extractWithOpenAI).mockResolvedValue(extracted(SERIES_A_CONFIG.notRoundSentinel, RUN_DATE));

    const result = await runFundingPipeline(config({ dryRun: false }));

    expect(result.companyCount).toBe(0);
    expect(pushToSupabase).not.toHaveBeenCalled();
    expect(pushToWebhook).not.toHaveBeenCalled();
    expect(logger.info).toHaveBeenCalledWith("Filtered post-extraction: Alpha");
  });

  it("delivers the extracted company name", async () => {
    vi.mocked(runDiscovery).mockResolvedValue([raw("Search Result Name")]);
    vi.mocked(extractWithOpenAI).mockResolvedValue(extracted("Primer", RUN_DATE));

    const result = await runFundingPipeline(config({ dryRun: false }));

    expect(result.companies.map((company) => company.company_name)).toEqual(["Primer"]);
    expect(vi.mocked(pushToSupabase).mock.calls[0][0][0].company_name).toBe("Primer");
    expect(vi.mocked(pushToWebhook).mock.calls[0][0][0].company_name).toBe("Primer");
  });
});

describe("runFundingPipeline enrichment deadline", () => {
  it("stops stalled multi-source fetches at the enrichment deadline and preserves delivery reserve", async () => {
    vi.useFakeTimers();
    const start = Date.now();
    const deadlineAt = start + 20 * 60_000;
    const enrichUntil = deadlineAt - OUTPUT_RESERVE_MS;
    const alpha = raw("Alpha");
    const fallbacks = [1, 2].map((i) => ({ ...alpha, source_url: `${alpha.source_url}?fallback=${i}` }));
    vi.mocked(runDiscovery).mockImplementationOnce(() => new Promise((resolve) => {
      setTimeout(() => resolve([alpha, ...fallbacks, raw("Bravo")]), enrichUntil - start - 10_000);
    }));
    const scrape = await vi.importActual<typeof import("./scrape.js")>("./scrape.js");
    vi.mocked(fetchUrl).mockImplementation(scrape.fetchUrl);
    vi.stubEnv("SPIDER_API_KEY", "spider-test-key");
    vi.spyOn(AbortSignal, "timeout").mockImplementation((ms) => {
      const controller = new AbortController();
      setTimeout(() => controller.abort(), ms);
      return controller.signal;
    });
    const fetchMock = vi.fn<typeof fetch>(async (url, init) => {
      if (String(url) === raw("Bravo").source_url) {
        return new Response(`<main>${"Bravo funding news. Visit bravo.com to learn more. ".repeat(10)}</main>`, {
          headers: { "Content-Type": "text/html" },
        });
      }
      if (String(url) === alpha.source_url) {
        return new Promise((resolve) => setTimeout(() => resolve(new Response("Not found", { status: 404 })), 2_000));
      }
      return new Promise((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")), { once: true });
      });
    });
    vi.stubGlobal("fetch", fetchMock);
    vi.mocked(extractWithOpenAI).mockImplementation(async (_text, name) => extracted(name, RUN_DATE));
    let finished = false;
    const run = runFundingPipeline(config({ dryRun: false, deadlineAt })).then((result) => {
      finished = true;
      return result;
    });

    await vi.advanceTimersByTimeAsync(enrichUntil - start - 1);
    expect(finished).toBe(false);
    await vi.advanceTimersByTimeAsync(1);
    expect(finished).toBe(true);
    const result = await run;
    expect(result.stats.durationMs).toBe(enrichUntil - start);
    expect(deadlineAt - Date.now()).toBe(OUTPUT_RESERVE_MS);
    expect(result.companies.map((company) => company.company_name)).toEqual(["Bravo"]);
    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([alpha.source_url, raw("Bravo").source_url, fallbacks[0].source_url]);
    expect(vi.mocked(fetchUrl).mock.calls.map(([url]) => url)).toEqual([alpha.source_url, raw("Bravo").source_url, fallbacks[0].source_url]);
    expect(fetchMock.mock.calls[2][1]?.signal?.aborted).toBe(true);
    expect(vi.mocked(fetchUrl).mock.calls.every(([, options]) => options?.deadlineAt === enrichUntil)).toBe(true);
    expect(extractWithOpenAI).toHaveBeenCalledTimes(1);
    expect(lookupDomainMultiSignal).not.toHaveBeenCalled();
    expect(vi.mocked(pushToWebhook).mock.calls[0][0].map((company) => company.company_name)).toEqual(["Bravo"]);
    expect(vi.mocked(pushToSupabase).mock.calls[0][0].map((company) => company.company_name)).toEqual(["Bravo"]);
    expect(day0BlitzEnrich).toHaveBeenCalledTimes(1);
  });

  it.each(["extraction", "domain lookup", "semantic validation"])("bounds a stalled %s at the enrichment deadline", async (stage) => {
    vi.useFakeTimers();
    const start = Date.now();
    const enrichUntil = start + 1_000;
    vi.mocked(runDiscovery).mockResolvedValue([raw("Alpha")]);
    vi.mocked(extractWithOpenAI).mockResolvedValue(extracted("Alpha", RUN_DATE));
    let resolveOperation!: () => void;
    if (stage === "extraction") {
      vi.mocked(extractWithOpenAI).mockImplementationOnce(() => new Promise((resolve) => {
        resolveOperation = () => resolve(extracted("Alpha", RUN_DATE));
      }));
    } else if (stage === "domain lookup") {
      vi.mocked(fetchUrl).mockResolvedValueOnce("Funding news without a company website.");
      vi.mocked(lookupDomainMultiSignal).mockImplementationOnce(() => new Promise((resolve) => {
        resolveOperation = () => resolve({ domain: "alpha.com", source: "search_validated", confidence: "high", evidence: "Official site" });
      }));
    } else {
      vi.mocked(validateDomainSemantic).mockImplementationOnce(() => new Promise((resolve) => {
        resolveOperation = () => resolve({ status: "Correct", correctDomain: "alpha.com", correctCompanyName: "Alpha", reason: "Official site" });
      }));
    }
    let finished = false;
    const run = runFundingPipeline(config({ dryRun: false, deadlineAt: enrichUntil + OUTPUT_RESERVE_MS })).then((result) => {
      finished = true;
      return result;
    });

    await vi.advanceTimersByTimeAsync(999);
    expect(finished).toBe(false);
    await vi.advanceTimersByTimeAsync(1);
    expect(finished).toBe(true);
    const result = await run;
    expect(result.companyCount).toBe(0);
    expect(result.stats.durationMs).toBe(1_000);
    expect(vi.mocked(extractWithOpenAI).mock.calls[0][4]).toBe(enrichUntil);
    if (stage === "domain lookup") expect(vi.mocked(lookupDomainMultiSignal).mock.calls[0][3]).toBe(enrichUntil);
    if (stage === "semantic validation") expect(vi.mocked(validateDomainSemantic).mock.calls[0][4]).toBe(enrichUntil);
    resolveOperation();
    await vi.advanceTimersByTimeAsync(0);
    if (stage === "extraction") expect(lookupDomainMultiSignal).not.toHaveBeenCalled();
    if (stage !== "semantic validation") expect(validateDomainSemantic).not.toHaveBeenCalled();
    expect(pushToWebhook).not.toHaveBeenCalled();
    expect(pushToSupabase).not.toHaveBeenCalled();
  });

  it("skips model and domain calls when an article finishes at the enrichment deadline", async () => {
    vi.useFakeTimers();
    const enrichUntil = Date.now() + 1_000;
    vi.mocked(runDiscovery).mockResolvedValue([raw("Alpha")]);
    vi.mocked(fetchUrl).mockImplementationOnce(async () => {
      vi.setSystemTime(enrichUntil);
      return "Alpha funding news. Visit alpha.com.";
    });

    const result = await runFundingPipeline(config({ deadlineAt: enrichUntil + OUTPUT_RESERVE_MS }));
    expect(result.companyCount).toBe(0);
    expect(extractWithOpenAI).not.toHaveBeenCalled();
    expect(lookupDomainMultiSignal).not.toHaveBeenCalled();
    expect(validateDomainSemantic).not.toHaveBeenCalled();
  });

  it.each(["extraction", "semantic validation"])("caps stalled %s HTTP calls to the remaining enrichment time", async (stage) => {
    vi.useFakeTimers();
    const start = Date.now();
    const enrichUntil = start + 1_000;
    vi.mocked(runDiscovery).mockResolvedValue([raw("Alpha")]);
    const openai = await vi.importActual<typeof import("./openai.js")>("./openai.js");
    if (stage === "extraction") vi.mocked(extractWithOpenAI).mockImplementationOnce(openai.extractWithOpenAI);
    else {
      vi.mocked(extractWithOpenAI).mockResolvedValue(extracted("Alpha", RUN_DATE));
      vi.mocked(validateDomainSemantic).mockImplementationOnce(openai.validateDomainSemantic);
    }
    vi.stubEnv("OPENAI_API_KEY", "test-key");
    const timeout = vi.spyOn(AbortSignal, "timeout").mockImplementation((ms) => {
      const controller = new AbortController();
      setTimeout(() => controller.abort(), ms);
      return controller.signal;
    });
    const fetchMock = vi.fn<typeof fetch>((_url, init) => new Promise((_resolve, reject) => {
      init?.signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")), { once: true });
    }));
    vi.stubGlobal("fetch", fetchMock);

    const run = runFundingPipeline(config({ deadlineAt: enrichUntil + OUTPUT_RESERVE_MS }));
    await vi.advanceTimersByTimeAsync(1_000);
    const result = await run;
    expect(result.companyCount).toBe(0);
    expect(result.stats.durationMs).toBe(1_000);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][1]?.signal?.aborted).toBe(true);
    expect(timeout.mock.calls.map(([ms]) => ms)).toEqual([1_000]);
  });
});

describe("runFundingPipeline delivery deadline", () => {
  it("writes all rows to Supabase even when Clay rejects one", async () => {
    vi.mocked(runDiscovery).mockResolvedValue(["Alpha", "Bravo", "Charlie"].map(raw));
    vi.mocked(extractWithOpenAI).mockImplementation(async (_text, name) => extracted(name, RUN_DATE));
    vi.mocked(pushToWebhook).mockImplementation(async (rows) => rows[0].company_name === "Bravo" ? 0 : 1);

    await runFundingPipeline(config({ dryRun: false }));

    expect(vi.mocked(pushToSupabase).mock.calls.map(([rows]) => rows[0].company_name)).toEqual(["Alpha", "Bravo", "Charlie"]);
    expect(pushToSupabase).toHaveBeenCalledTimes(3);
    expect(vi.mocked(pushToSupabase).mock.invocationCallOrder[0]).toBeLessThan(Math.min(...vi.mocked(pushToWebhook).mock.invocationCallOrder));
    expect(logger.warn).toHaveBeenCalledWith(expect.stringContaining("Clay rejected"), { names: ["Bravo"] });
    expect(logger.info).toHaveBeenCalledWith(expect.stringContaining("Webhook: 2/3 sent; Supabase: 3/3 upserted"));
    expect(vi.mocked(day0BlitzEnrich).mock.calls[0][1].map((t) => t.companyName)).toEqual(["Alpha", "Bravo", "Charlie"]);
  });

  it("delivers without Clay and dedups rows written to Supabase on the next run", async () => {
    vi.stubEnv("CLAY_SERIES_A_WEBHOOK_URL", "   ");
    vi.stubEnv("CLAY_SERIES_A_WEBHOOK_TOKEN", "");
    vi.mocked(runDiscovery).mockResolvedValue([raw("Alpha"), raw("Bravo")]);
    vi.mocked(extractWithOpenAI).mockImplementation(async (_text, name) => extracted(name, RUN_DATE));
    const written = new Set<string>();
    vi.mocked(pushToSupabase).mockImplementation(async (rows) => {
      written.add(rows[0].company_name.toLowerCase());
      return 1;
    });
    vi.mocked(getRecentCompanyNames).mockImplementation(async () => new Set(written));

    await runFundingPipeline(config({ dryRun: false, skipKnownCompanies: true }));

    expect(pushToWebhook).not.toHaveBeenCalled();
    expect(vi.mocked(pushToSupabase).mock.calls.map(([rows]) => rows[0].company_name)).toEqual(["Alpha", "Bravo"]);
    expect(vi.mocked(day0BlitzEnrich).mock.calls[0][1].map((t) => t.companyName)).toEqual(["Alpha", "Bravo"]);
    expect(logger.info).toHaveBeenCalledWith(expect.stringContaining("Webhook: 0/2 sent; Supabase: 2/2 upserted"));
    expect(logger.info).toHaveBeenCalledWith(expect.stringContaining("Clay: off"));
    expect(logger.info).toHaveBeenCalledWith(expect.stringContaining("Clay delivery off for Series A"));

    await runFundingPipeline(config({ dryRun: false, skipKnownCompanies: true }));
    expect(pushToSupabase).toHaveBeenCalledTimes(2);
    expect(day0BlitzEnrich).toHaveBeenCalledTimes(1);
  });

  it("fails before discovery when Clay has a URL but no token", async () => {
    vi.stubEnv("CLAY_SERIES_A_WEBHOOK_TOKEN", "   ");
    await expect(runFundingPipeline(config({ dryRun: false }))).rejects.toThrow("CLAY_SERIES_A_WEBHOOK_TOKEN");
    expect(runDiscovery).not.toHaveBeenCalled();
    expect(pushToSupabase).not.toHaveBeenCalled();
    expect(pushToWebhook).not.toHaveBeenCalled();
  });

  it("keeps an unconfirmed Supabase row eligible for the next run", async () => {
    vi.stubEnv("CLAY_SERIES_A_WEBHOOK_URL", "");
    vi.mocked(runDiscovery).mockResolvedValue([raw("Alpha"), raw("Bravo")]);
    vi.mocked(extractWithOpenAI).mockImplementation(async (_text, name) => extracted(name, RUN_DATE));
    const written = new Set<string>();
    let firstBravo = true;
    vi.mocked(getRecentCompanyNames).mockImplementation(async () => new Set(written));
    vi.mocked(pushToSupabase).mockImplementation(async ([record]) => {
      if (record.company_name === "Bravo" && firstBravo) {
        firstBravo = false;
        throw new FundingWriteError(0);
      }
      written.add(record.company_name.toLowerCase());
      return 1;
    });

    await expect(runFundingPipeline(config({ dryRun: false, skipKnownCompanies: true }))).rejects.toThrow("funding write failed");
    expect(written).toEqual(new Set(["alpha"]));
    expect(day0BlitzEnrich).not.toHaveBeenCalled();

    await runFundingPipeline(config({ dryRun: false, skipKnownCompanies: true }));
    expect(vi.mocked(pushToSupabase).mock.calls.map(([rows]) => rows[0].company_name)).toEqual(["Alpha", "Bravo", "Bravo"]);
  });

  it("returns by the deadline even when Supabase never resolves", async () => {
    vi.useFakeTimers();
    vi.mocked(runDiscovery).mockResolvedValue([raw("Alpha")]);
    vi.mocked(extractWithOpenAI).mockResolvedValue(extracted("Alpha", RUN_DATE));
    vi.mocked(pushToSupabase).mockImplementationOnce(() => new Promise(() => {}));
    const deadlineAt = Date.now() + 6 * 60_000;
    let finished = false;
    const run = runFundingPipeline(config({ dryRun: false, deadlineAt })).then(() => { finished = true; });

    await vi.advanceTimersByTimeAsync(0);
    expect(pushToSupabase).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(6 * 60_000);
    expect(finished).toBe(true);
    await run;
    expect(day0BlitzEnrich).not.toHaveBeenCalled();
    expect(vi.mocked(pushToSupabase).mock.calls[0][3]?.aborted).toBe(true);
  });

  it("logs confirmed partial writes when Supabase fails after its first row", async () => {
    vi.mocked(runDiscovery).mockResolvedValue(["Alpha", "Bravo"].map(raw));
    vi.mocked(extractWithOpenAI).mockImplementation(async (_text, name) => extracted(name, RUN_DATE));
    vi.mocked(pushToSupabase).mockRejectedValueOnce(new FundingWriteError(1));
    await expect(runFundingPipeline(config({ dryRun: false }))).rejects.toThrow("funding write failed");
    expect(logger.info).toHaveBeenCalledWith(expect.stringContaining("Webhook: 0/2 sent; Supabase: 1/2 upserted"));
  });

  it("keeps confirmed partial Supabase counts when the deadline aborts the next row", async () => {
    vi.useFakeTimers();
    vi.mocked(runDiscovery).mockResolvedValue(["Alpha", "Bravo"].map(raw));
    vi.mocked(extractWithOpenAI).mockImplementation(async (_text, name) => extracted(name, RUN_DATE));
    vi.mocked(pushToSupabase).mockImplementationOnce(async (_rows, _date, _table, signal, onUpsert) => {
      onUpsert?.(1);
      return new Promise((_resolve, reject) => signal?.addEventListener("abort", () => reject(new FundingWriteError(1)), { once: true }));
    });
    const run = runFundingPipeline(config({ dryRun: false, deadlineAt: Date.now() + 6 * 60_000 }));
    await vi.advanceTimersByTimeAsync(6 * 60_000);
    await run;
    expect(logger.info).toHaveBeenCalledWith(expect.stringContaining("Webhook: 0/2 sent; Supabase: 1/2 upserted"));
    expect(day0BlitzEnrich).not.toHaveBeenCalled();
  });

  it("does not start Clay or Blitz without their operation budgets", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    const start = Date.now();
    vi.mocked(runDiscovery).mockResolvedValue([raw("Alpha")]);
    vi.mocked(pushToSupabase).mockImplementationOnce(async () => { vi.setSystemTime(start + 51_000); return 1; });
    await runFundingPipeline(config({ dryRun: false, skipEnrich: true, deadlineAt: start + 60_000 }));
    expect(pushToSupabase).toHaveBeenCalledTimes(1);
    expect(pushToWebhook).not.toHaveBeenCalled();
    expect(day0BlitzEnrich).not.toHaveBeenCalled();
  });

  it("bounds a stalled table check and a stalled Clay send", async () => {
    vi.useFakeTimers();
    const { checkTable } = await import("./supabase.js");
    vi.mocked(runDiscovery).mockResolvedValue([raw("Alpha")]);
    vi.mocked(checkTable).mockImplementationOnce(() => new Promise(() => {}));
    const deadlineAt = Date.now() + 60_000;
    const checking = runFundingPipeline(config({ dryRun: false, skipEnrich: true, deadlineAt }));
    await vi.advanceTimersByTimeAsync(60_000);
    await checking;
    expect(pushToWebhook).not.toHaveBeenCalled();

    vi.mocked(pushToWebhook).mockImplementationOnce(() => new Promise(() => {}));
    const sending = runFundingPipeline(config({ dryRun: false, skipEnrich: true, deadlineAt: Date.now() + 60_000 }));
    await vi.advanceTimersByTimeAsync(60_000);
    await sending;
    expect(pushToSupabase).toHaveBeenCalledTimes(1);
  });

  it("gives Blitz the shared deadline and returns when Blitz never resolves", async () => {
    vi.useFakeTimers();
    vi.mocked(runDiscovery).mockResolvedValue([raw("Alpha")]);
    vi.mocked(extractWithOpenAI).mockResolvedValue(extracted("Alpha", RUN_DATE));
    vi.mocked(day0BlitzEnrich).mockImplementationOnce(() => new Promise(() => {}));
    const deadlineAt = Date.now() + 6 * 60_000;
    let finished = false;
    const run = runFundingPipeline(config({ dryRun: false, deadlineAt })).then(() => { finished = true; });
    await vi.advanceTimersByTimeAsync(0);
    expect(day0BlitzEnrich).toHaveBeenCalledTimes(1);
    const signal = vi.mocked(day0BlitzEnrich).mock.calls[0][3];
    expect(signal?.aborted).toBe(false);
    await vi.advanceTimersByTimeAsync(6 * 60_000);
    expect(finished).toBe(true);
    expect(signal?.aborted).toBe(true);
    await run;
  });

  it("stops between chunks at the deadline and leaves the rest unwritten", async () => {
    const names = ["Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot"];
    vi.mocked(runDiscovery).mockResolvedValue(names.map(raw));
    vi.mocked(extractWithOpenAI).mockImplementation(async (_text, name) => extracted(name, RUN_DATE));
    vi.stubEnv("CLAY_SERIES_A_WEBHOOK_URL", "https://clay.test/hook");
    vi.stubEnv("CLAY_SERIES_A_WEBHOOK_TOKEN", "test-token");
    vi.useFakeTimers({ toFake: ["Date"] });
    const deadlineAt = Date.now() + 6 * 60_000;
    // The first Supabase write lands after the deadline, so the second chunk must not start.
    vi.mocked(pushToSupabase).mockImplementationOnce(async (rows) => { vi.setSystemTime(deadlineAt + 1); return rows.length; });

    const result = await runFundingPipeline(config({ dryRun: false, deadlineAt }));

    expect(result.companyCount).toBe(6);
    expect(vi.mocked(pushToWebhook)).not.toHaveBeenCalled();
    expect(vi.mocked(pushToSupabase)).toHaveBeenCalledTimes(1);
    expect(vi.mocked(pushToSupabase).mock.calls[0][0].map((r) => r.company_name)).toEqual(names.slice(0, 1));
  });
});
