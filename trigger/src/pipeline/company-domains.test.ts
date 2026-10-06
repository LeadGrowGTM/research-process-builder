import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { companyDomainBudget, fillCompanyDomains, launchDomainBatches, type CompanyDomainResolution } from "./company-domains.js";
import { logger } from "@trigger.dev/sdk";
import { lookupDomainMultiSignal } from "./domain-lookup.js";

vi.mock("@trigger.dev/sdk", () => ({ logger: { info: vi.fn(), warn: vi.fn(), error: vi.fn() } }));
vi.mock("./domain-lookup.js", async () => ({
  ...await vi.importActual<typeof import("./domain-lookup.js")>("./domain-lookup.js"),
  lookupDomainMultiSignal: vi.fn(),
}));

const row = (company_name = "Acme") => ({ company_name, source_url: "https://publisher.test/story" });
const hit = (domain = "acme.com", confidence: "high" | "medium" | "low" = "high") => ({ domain, confidence, source: "search_validated" as const, evidence: "Official website in search" });

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(lookupDomainMultiSignal).mockResolvedValue(hit());
});
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

describe("fillCompanyDomains", () => {
  it("rejects long-tail publishers and substring lookalikes before searching", async () => {
    vi.mocked(lookupDomainMultiSignal).mockResolvedValue(hit("not_found", "low"));
    const rows = await fillCompanyDomains([
      { ...row("Foo Labs"), source_url: "https://foolabs-news.com/story" },
      { ...row("Foo Labs"), article_text: "Website https://foolabs-news.com" },
      { ...row("Foo Labs"), article_text: "Website https://foo.com" },
      { ...row("Foo Labs"), article_text: "Website https://foolabs.example.com" },
      { ...row("Foo Labs"), source_url: "https://foolabs.com/story" },
    ].map((r, i) => ({ ...r, source_url: `${r.source_url}?row=${i}` })), companyDomainBudget(Date.now() + 120_000));
    expect(rows.map((r) => r.company_domain)).toEqual([null, null, null, null, null]);
    expect(lookupDomainMultiSignal).toHaveBeenCalledTimes(5);
  });

  it("matches the normalized registrable label on company subdomains and country-code suffixes", async () => {
    const rows = await fillCompanyDomains([
      { ...row("Foo Labs"), article_text: "Website https://app.foo-labs.co.uk/about" },
    ], companyDomainBudget(Date.now() + 120_000));
    expect(rows[0].company_domain).toBe("foo-labs.co.uk");
    expect(lookupDomainMultiSignal).not.toHaveBeenCalled();
  });
  it("keeps same-name launch companies on different sources separate when context is absent", async () => {
    vi.mocked(lookupDomainMultiSignal).mockResolvedValueOnce(hit("acme.one")).mockResolvedValueOnce(hit("acme.two"));
    const rows = await fillCompanyDomains([
      { ...row(), source_url: "https://publisher.test/story-one" },
      { ...row(), source_url: "https://publisher.test/story-two" },
    ], companyDomainBudget(Date.now() + 120_000));
    expect(rows.map((r) => r.company_domain)).toEqual(["acme.one", "acme.two"]);
    expect(lookupDomainMultiSignal).toHaveBeenCalledTimes(2);
  });
  it("separates same-name companies using their industry and location context", async () => {
    vi.mocked(lookupDomainMultiSignal).mockResolvedValueOnce(hit("acme.health")).mockResolvedValueOnce(hit("acme.build"));
    const rows = await fillCompanyDomains([
      { ...row(), industry: "Healthcare", location: "Canada" },
      { ...row(), industry: "Construction", location: "US" },
      { ...row(), industry: "Healthcare", location: "Canada" },
    ], companyDomainBudget(Date.now() + 120_000));
    expect(rows.map((r) => r.company_domain)).toEqual(["acme.health", "acme.build", "acme.health"]);
    expect(lookupDomainMultiSignal).toHaveBeenCalledTimes(2);
  });
  it("uses existing domains, maker websites, article links and known company websites without API calls", async () => {
    const budget = companyDomainBudget(Date.now() + 120_000);
    const rows = await fillCompanyDomains([
      { ...row(), company_domain: "https://www.acme.com/about" },
      { ...row("Maker"), maker_website: "https://maker.io" },
      { ...row("ArticleCo"), article_text: "Website (https://articleco.ai/)" },
      { ...row("FirstParty"), company_website: "https://firstparty.dev/launch" },
    ], budget);
    expect(rows.map((r) => r.company_domain)).toEqual(["acme.com", "maker.io", "articleco.ai", "firstparty.dev"]);
    expect(lookupDomainMultiSignal).not.toHaveBeenCalled();
    expect(budget.remainingLookups).toBe(20);
  });

  it("reuses a successful domain for the same company and accepts only high-confidence search matches", async () => {
    vi.mocked(lookupDomainMultiSignal).mockResolvedValueOnce(hit()).mockResolvedValueOnce(hit("wrong.test", "medium")).mockResolvedValueOnce(hit("linkedin.com"));
    const rows = await fillCompanyDomains([row(), row(" ACME "), row("Ambiguous"), row("Blocked")], companyDomainBudget(Date.now() + 120_000));
    expect(rows.map((r) => r.company_domain)).toEqual(["acme.com", "acme.com", null, null]);
    expect(lookupDomainMultiSignal).toHaveBeenCalledTimes(3);
  });

  it("reports stored, article, search and cached candidates without adding database columns", async () => {
    const resolutions: CompanyDomainResolution[] = [];
    const rows = await fillCompanyDomains([
      { ...row("Stored"), maker_website: "https://legal.pilot5.ai" },
      { ...row("ArticleCo"), article_text: "Website https://app.articleco.ai" },
      row(), row(),
    ], companyDomainBudget(Date.now() + 120_000), true, { onResolution: (result) => resolutions.push(result) });
    expect(resolutions).toEqual([
      { source: "stored", lookupDomain: "pilot5.ai", confidence: "high", rejectedReason: null },
      { source: "article", lookupDomain: "app.articleco.ai", confidence: "high", rejectedReason: null },
      { source: "search", lookupDomain: "acme.com", confidence: "high", rejectedReason: null },
      { source: "cache", lookupDomain: "acme.com", confidence: "high", rejectedReason: null },
    ]);
    expect(rows.every((r) => !Object.hasOwn(r, "resolution") && !Object.hasOwn(r, "rejectedReason"))).toBe(true);
  });

  it("reports rejected searches, cached misses, invalid domains and exhausted lookup budgets", async () => {
    vi.mocked(lookupDomainMultiSignal).mockResolvedValueOnce(hit("ara.so", "medium")).mockResolvedValueOnce(hit("linkedin.com"));
    const resolutions: CompanyDomainResolution[] = [];
    const rows = await fillCompanyDomains([row("Reason"), row("Reason"), row("Blocked"), row("Other")], companyDomainBudget(Date.now() + 120_000, 2), true, { onResolution: (result) => resolutions.push(result) });
    expect(rows.every((r) => r.company_domain === null)).toBe(true);
    expect(resolutions).toEqual([
      { source: "search", lookupDomain: "ara.so", confidence: "medium", rejectedReason: "confidence_not_high" },
      { source: "cache", lookupDomain: "ara.so", confidence: "medium", rejectedReason: "confidence_not_high" },
      { source: "search", lookupDomain: "linkedin.com", confidence: "high", rejectedReason: "no_usable_domain" },
      { source: "none", lookupDomain: null, confidence: null, rejectedReason: "lookup_budget_exhausted" },
    ]);
  });

  it.each([
    ["Fundraising News", "non_company_name"],
    [" FUNDRAISING   NEWS ", "non_company_name"],
    ["Funding News", "non_company_name"],
    ["Newsroom", "non_company_name"],
    ["This headline has seven separate company words", "company_name_too_long"],
    ["这家公司获得融资，计划拓展市场。", "cjk_sentence_punctuation"],
    ["", "missing_company_name"],
  ])("skips lookup for %s and logs why", async (name, rejectedReason) => {
    const budget = companyDomainBudget(Date.now() + 120_000);
    const rows = await fillCompanyDomains([row(name)], budget);
    expect(rows[0].company_domain).toBeNull();
    expect(lookupDomainMultiSignal).not.toHaveBeenCalled();
    expect(budget.remainingLookups).toBe(20);
    expect(logger.info).toHaveBeenCalledWith("Company domain lookup skipped", { company: name, source: "none", lookupDomain: null, confidence: null, rejectedReason });
  });

  it.each(["Fundraising Labs", "Newsroom AI", "One Two Three Four Five Six", "量子科技"])("keeps plausible company name %s eligible for lookup", async (name) => {
    const rows = await fillCompanyDomains([row(name)], companyDomainBudget(Date.now() + 120_000));
    expect(rows[0].company_domain).toBe("acme.com");
    expect(lookupDomainMultiSignal).toHaveBeenCalledOnce();
  });

  it("bounds lookups, caches misses, passes context and never invents a fallback domain", async () => {
    vi.mocked(lookupDomainMultiSignal).mockRejectedValue(new Error("provider unavailable"));
    const budget = companyDomainBudget(Date.now() + 120_000, 1);
    const rows = await fillCompanyDomains([{ ...row(), industry: "Healthcare", location: "Canada", description: "Medical records software" }, row("Acme"), row("Other")], budget);
    expect(rows.every((r) => r.company_domain === null)).toBe(true);
    expect(lookupDomainMultiSignal).toHaveBeenCalledTimes(1);
    expect(vi.mocked(lookupDomainMultiSignal).mock.calls[0]).toEqual(["Acme", { industry: "Healthcare", location: "Canada", productOrService: "Medical records software" }, "https://publisher.test/story", expect.any(Number)]);
    expect(budget.remainingLookups).toBe(0);
  });

  it("still extracts free Evidence after the search deadline but skips searches and late results", async () => {
    vi.useFakeTimers();
    const budget = companyDomainBudget(Date.now() + 5_000);
    vi.mocked(lookupDomainMultiSignal).mockImplementation(async () => { vi.advanceTimersByTime(5_001); return hit(); });
    const rows = await fillCompanyDomains([row(), { ...row("Free"), maker_website: "https://free.dev" }, row("Late")], budget);
    expect(rows.map((r) => r.company_domain)).toEqual([null, "free.dev", null]);
    expect(lookupDomainMultiSignal).toHaveBeenCalledTimes(1);
  });

  it("rejects publisher, social and shortener domains before trying the existing search resolver", async () => {
    vi.mocked(lookupDomainMultiSignal).mockResolvedValue(hit("not_found", "low"));
    const rows = await fillCompanyDomains([
      { ...row(), maker_website: "https://t.co/abc", article_text: "Website https://publisher.test/company/acme.com" },
      { ...row("Other"), company_website: "https://www.linkedin.com/company/other" },
    ], companyDomainBudget(Date.now() + 120_000));
    expect(rows.map((r) => r.company_domain)).toEqual([null, null]);
    expect(lookupDomainMultiSignal).toHaveBeenCalledTimes(2);
  });
});

describe("launchDomainBatches", () => {
  it("keeps homogeneous PostgREST keys and omits all domains to preserve previously stored domains", () => {
    const batches = launchDomainBatches([{ ...row(), company_domain: "acme.com" }, { ...row("Unknown"), company_domain: null }]);
    expect(batches).toEqual([[row(), row("Unknown")]]);
    expect(batches[0].every((r) => !Object.hasOwn(r, "company_domain"))).toBe(true);
  });
});
