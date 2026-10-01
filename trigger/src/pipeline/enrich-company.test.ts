import { describe, expect, it } from "vitest";
import {
  fundingPatchFromBlitz,
  fundingPatchFromLg,
  phPatchFromBlitz,
} from "./enrich-company.js";
import type { BlitzCompany } from "./blitz.js";
import type { LgFirmographics } from "./lgenrich.js";

describe("funding firmographic patches", () => {
  it("maps every available Blitz firmographic without another provider call", () => {
    const company: BlitzCompany = {
      industry: "payments platform",
      hq: { city: "Toronto", country_name: "Canada" },
      employees_on_linkedin: 42,
      size: "11-50",
      founded_year: 2021,
    };

    expect(fundingPatchFromBlitz("https://linkedin.com/company/acme", company)).toMatchObject({
      industry: "Fintech",
      employee_count: 42,
      employee_range: "11-50",
      hq_location: "Toronto, Canada",
      founded_year: 2021,
      linkedin_url: "https://linkedin.com/company/acme",
    });
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
    });
  });

  it("does not apply funding taxonomy changes to product-launch patches", () => {
    expect(phPatchFromBlitz("https://linkedin.com/company/acme", {
      industry: "payments platform",
    }).industry).toBe("payments platform");
  });
});
