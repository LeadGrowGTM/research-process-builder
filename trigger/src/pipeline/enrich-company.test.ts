import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@trigger.dev/sdk", () => ({
  logger: { info: vi.fn(), warn: vi.fn(), error: vi.fn() },
}));

vi.mock("./supabase.js", () => ({
  patchRowBySourceUrl: vi.fn(async () => true),
}));

vi.mock("./blitz.js", async () => {
  const actual = await vi.importActual<typeof import("./blitz.js")>("./blitz.js");
  return {
    ...actual,
    blitzConfigured: vi.fn(() => false),
    blitzEnrichDomain: vi.fn(async () => null),
    blitzEnrichLinkedin: vi.fn(async () => null),
  };
});

vi.mock("./lgenrich.js", async () => {
  const actual = await vi.importActual<typeof import("./lgenrich.js")>("./lgenrich.js");
  return {
    ...actual,
    lgenrichConfigured: vi.fn(() => false),
    lgenrichDomain: vi.fn(async () => null),
  };
});

import { blitzConfigured, blitzEnrichDomain, blitzEnrichLinkedin } from "./blitz.js";
import type { BlitzCompany } from "./blitz.js";
import {
  day0BlitzEnrich,
  enrichDomainWaterfall,
  fundingPatchFromBlitz,
  fundingPatchFromLg,
  phPatchFromBlitz,
} from "./enrich-company.js";
import type { Day0Target } from "./enrich-company.js";
import { lgenrichConfigured, lgenrichDomain } from "./lgenrich.js";
import type { LgFirmographics } from "./lgenrich.js";
import { patchRowBySourceUrl } from "./supabase.js";

const target: Day0Target = {
  companyName: "Acme",
  domain: "acme.com",
  sourceUrl: "https://news.example/acme",
};

function emptyFirm(over: Partial<LgFirmographics> = {}): LgFirmographics {
  return {
    linkedin_url: null,
    name: null,
    description: null,
    employee_count: null,
    employee_count_range: null,
    follower_count: null,
    hq_city: null,
    hq_region: null,
    hq_country: null,
    industry: null,
    company_type: null,
    founded_year: null,
    ...over,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(lgenrichConfigured).mockReturnValue(false);
  vi.mocked(blitzConfigured).mockReturnValue(false);
  vi.mocked(lgenrichDomain).mockResolvedValue(null);
  vi.mocked(blitzEnrichDomain).mockResolvedValue(null);
  vi.mocked(blitzEnrichLinkedin).mockResolvedValue(null);
  vi.mocked(patchRowBySourceUrl).mockResolvedValue(true);
});

describe("funding firmographic patches", () => {
  it("maps every available Blitz firmographic without another provider call", () => {
    const company: BlitzCompany = {
      industry: "payments platform",
      hq: { city: "Toronto", country_name: "Canada" },
      employees_on_linkedin: 42,
      size: "11-50",
      founded_year: 2021,
    };

    const patch = fundingPatchFromBlitz("https://linkedin.com/company/acme", company);
    expect(patch).toMatchObject({
      industry: "Fintech",
      employee_count: 42,
      employee_range: "11-50",
      hq_location: "Toronto, Canada",
      founded_year: 2021,
      linkedin_url: "https://linkedin.com/company/acme",
    });
    expect(patch).not.toHaveProperty("company_description");
    expect(patch).not.toHaveProperty("products");
  });

  it("maps every available lgenrich firmographic", () => {
    const firmographics: LgFirmographics = {
      linkedin_url: "https://linkedin.com/company/acme",
      name: "Acme",
      description: "Acme makes payment software.",
      employee_count: 42,
      employee_count_range: "11-50",
      follower_count: 100,
      hq_city: "Toronto",
      hq_region: "Ontario",
      hq_country: "Canada",
      industry: "Fintech",
      company_type: "Privately Held",
      founded_year: 2021,
    };

    expect(fundingPatchFromLg(firmographics.linkedin_url!, firmographics)).toMatchObject({
      employee_count: 42,
      employee_range: "11-50",
      hq_location: "Toronto, Ontario, Canada",
      founded_year: 2021,
      linkedin_url: firmographics.linkedin_url,
      company_description: "Acme makes payment software.",
    });
  });

  it("does not apply funding taxonomy changes to product-launch patches", () => {
    expect(phPatchFromBlitz("https://linkedin.com/company/acme", {
      industry: "payments platform",
    }).industry).toBe("payments platform");
  });

  it("omits sentinels, contacts, person profiles, and implausible years", () => {
    const patch = fundingPatchFromBlitz("https://linkedin.com/in/jane", {
      founded_year: 0,
      employees_on_linkedin: 0,
      about: "Email jane@acme.com or call 6505550100",
      industry: "unknown",
      size: "unknown",
      hq: { city: "N/A", country_name: "US" },
    });
    expect(patch.employee_count).toBe(0);
    expect(patch.hq_location).toBe("US");
    expect(patch).not.toHaveProperty("founded_year");
    expect(patch).not.toHaveProperty("industry");
    expect(patch).not.toHaveProperty("employee_range");
    expect(patch).not.toHaveProperty("linkedin_url");
    expect(patch).not.toHaveProperty("products");
    expect(JSON.stringify(patch)).not.toMatch(/jane@acme\.com|6505550100/);
    expect(fundingPatchFromBlitz("https://linkedin.com/company/acme", { founded_year: 9999 })).not.toHaveProperty("founded_year");
  });
});

describe("firmographic waterfall", () => {
  it("does not treat a sentinel description as a completed profile", async () => {
    vi.mocked(lgenrichConfigured).mockReturnValue(true);
    vi.mocked(lgenrichDomain).mockResolvedValue({
      linkedin_url: "https://linkedin.com/company/acme",
      trusted: true,
      firmographics: emptyFirm({ description: "unknown", linkedin_url: "https://linkedin.com/company/acme" }),
    });
    vi.mocked(blitzEnrichLinkedin).mockResolvedValue({ employees_on_linkedin: 9, name: "Acme" });

    const hit = await enrichDomainWaterfall("funding_discoveries", target, "acme.com");
    expect(hit?.provider).toBe("lgenrich+blitz");
    expect(hit?.patch.employee_count).toBe(9);
    expect(hit?.present).toContain("headcount");
    expect(hit?.omitted).toContain("products");
    expect(hit?.patch).not.toHaveProperty("products");
    expect(blitzEnrichLinkedin).toHaveBeenCalledWith("https://linkedin.com/company/acme");
  });

  it("stops once lgenrich already returned usable firmographics", async () => {
    vi.mocked(lgenrichConfigured).mockReturnValue(true);
    vi.mocked(lgenrichDomain).mockResolvedValue({
      linkedin_url: "https://linkedin.com/company/acme",
      trusted: true,
      firmographics: emptyFirm({ employee_count: 4, description: "Acme makes payment software." }),
    });

    const hit = await enrichDomainWaterfall("funding_discoveries", target, "acme.com");
    expect(hit?.provider).toBe("lgenrich");
    expect(hit?.patch.employee_count).toBe(4);
    expect(blitzEnrichLinkedin).not.toHaveBeenCalled();
  });

  it("keeps a funding company LinkedIn when firmographics are empty and leaves product launches unstamped", async () => {
    vi.mocked(lgenrichConfigured).mockReturnValue(true);
    vi.mocked(lgenrichDomain).mockResolvedValue({
      linkedin_url: "https://www.linkedin.com/company/acme",
      trusted: true,
      firmographics: emptyFirm({ description: "unknown" }),
    });

    const funding = await enrichDomainWaterfall("funding_discoveries", target, "acme.com");
    expect(funding?.patch).toEqual({ linkedin_url: "https://www.linkedin.com/company/acme" });
    expect(await enrichDomainWaterfall("product_launches", target, "acme.com")).toBeNull();
  });

  it("drops a person profile instead of storing it as the company", async () => {
    vi.mocked(lgenrichConfigured).mockReturnValue(true);
    vi.mocked(lgenrichDomain).mockResolvedValue({
      linkedin_url: "https://linkedin.com/in/jane",
      trusted: true,
      firmographics: emptyFirm({ description: "unknown" }),
    });

    expect(await enrichDomainWaterfall("funding_discoveries", target, "acme.com")).toBeNull();
    expect(blitzEnrichLinkedin).not.toHaveBeenCalled();
  });

  it("does not write a Blitz profile when the company name does not match", async () => {
    vi.mocked(blitzConfigured).mockReturnValue(true);
    vi.mocked(blitzEnrichDomain).mockResolvedValue({
      linkedin_url: "https://linkedin.com/company/other",
      company: { name: "Other Labs", employees_on_linkedin: 12 },
    });

    expect(await enrichDomainWaterfall("funding_discoveries", target, "acme.com")).toBeNull();
    expect(patchRowBySourceUrl).not.toHaveBeenCalled();
  });
});

describe("day0 coverage", () => {
  it("reports providers unavailable and does not mark fields omitted when nothing was attempted", async () => {
    const out = await day0BlitzEnrich("funding_discoveries", [target]);
    expect(out).toMatchObject({ attempted: 0, enriched: 0, providers: "unavailable" });
    expect(out.coverage.recordsAttempted).toBe(0);
    expect(out.coverage.omitted.description).toBe(0);
    expect(patchRowBySourceUrl).not.toHaveBeenCalled();
  });

  it("counts a miss as omitted fields and does not patch", async () => {
    vi.mocked(blitzConfigured).mockReturnValue(true);
    const out = await day0BlitzEnrich("funding_discoveries", [target]);
    expect(out).toMatchObject({ attempted: 1, enriched: 0, providers: "available" });
    expect(out.coverage.recordsWritten).toBe(0);
    expect(out.coverage.omitted.description).toBe(1);
    expect(out.coverage.present.headcount).toBe(0);
    expect(patchRowBySourceUrl).not.toHaveBeenCalled();
  });

  it("writes only present fields and keeps field coverage separate from records written", async () => {
    vi.mocked(blitzConfigured).mockReturnValue(true);
    vi.mocked(blitzEnrichDomain).mockResolvedValue({
      linkedin_url: "https://linkedin.com/company/acme",
      company: { name: "Acme", employees_on_linkedin: 12 },
    });

    const out = await day0BlitzEnrich("funding_discoveries", [target]);
    expect(out.coverage.recordsWritten).toBe(1);
    expect(out.coverage.present.headcount).toBe(1);
    expect(out.coverage.present.company_linkedin).toBe(1);
    expect(out.coverage.omitted.description).toBe(1);
    expect(out.coverage.omitted.products).toBe(1);
    const body = vi.mocked(patchRowBySourceUrl).mock.calls[0][2] as Record<string, unknown>;
    expect(body).toEqual(expect.objectContaining({ employee_count: 12, enriched_by: "blitz" }));
    expect(body).not.toHaveProperty("company_description");
    expect(body).not.toHaveProperty("products");
    expect(body).not.toHaveProperty("founded_year");
  });
});
