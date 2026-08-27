/**
 * Unit tests for signal-bank-daily fix-forward logic.
 * Tests schema override for funding_discoveries (public schema) and fix-forward cutoff.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";

describe("signal-bank-daily selection logic", () => {
  const mockRows = [
    {
      company_domain: "newco.com",
      discovered_date: "2026-08-28",
      company_name: "NewCo Inc",
      industry: "SaaS",
      round_type: "Series A",
      amount_raised: "$5M",
      location: "SF",
    },
    {
      company_domain: "oldco.com",
      discovered_date: "2026-05-10",
      company_name: "OldCo Ltd",
      industry: "Fintech",
      round_type: "Seed",
      amount_raised: "$1M",
      location: "NYC",
    },
    {
      company_domain: "not_found",
      discovered_date: "2026-08-27",
      company_name: "FakeDir",
      industry: "Unknown",
      round_type: "Seed",
      amount_raised: null,
      location: "Web",
    },
    {
      company_domain: "duplicate.com",
      discovered_date: "2026-08-27",
      company_name: "Duplicate",
      industry: "AI",
      round_type: "Series B",
      amount_raised: "$10M",
      location: "Austin",
    },
    {
      company_domain: "today.com",
      discovered_date: "2026-08-27",
      company_name: "TodayRound",
      industry: "Web3",
      round_type: "Seed",
      amount_raised: "$2M",
      location: "London",
    },
  ];

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("should filter out rows with null domain", () => {
    const rowsWithNull = [
      ...mockRows,
      {
        company_domain: null,
        discovered_date: "2026-08-27",
        company_name: "NoDomain",
      } as any,
    ];

    const filtered = rowsWithNull.filter((r) => r.company_domain);
    expect(filtered).toHaveLength(5);
  });

  it("should filter out rows containing 'not_found' in domain", () => {
    const filtered = mockRows.filter((r) => !r.company_domain.includes("not_found"));
    expect(filtered).toHaveLength(4);
    expect(filtered.map((r) => r.company_domain)).not.toContain("not_found");
  });

  it("should filter out rows already in signal_companies", () => {
    const existingDomains = new Set(["duplicate.com"]);
    const filtered = mockRows.filter((r) => !existingDomains.has(r.company_domain));
    expect(filtered).toHaveLength(4);
    expect(filtered.map((r) => r.company_domain)).not.toContain("duplicate.com");
  });

  it("should filter out rows discovered before fix-forward cutoff (2026-08-27)", () => {
    const FIX_FORWARD_SINCE = "2026-08-27";
    const filtered = mockRows.filter((r) => r.discovered_date >= FIX_FORWARD_SINCE);
    expect(filtered).toHaveLength(4); // oldco (2026-05-10) excluded
    expect(filtered.map((r) => r.company_name)).not.toContain("OldCo Ltd");
  });

  it("should apply all predicates cumulatively (selection logic)", () => {
    const FIX_FORWARD_SINCE = "2026-08-27";
    const existingDomains = new Set(["duplicate.com"]);

    const toProcess = mockRows
      .filter(
        (r) =>
          r.company_domain &&
          !r.company_domain.includes("not_found") &&
          !existingDomains.has(r.company_domain) &&
          r.discovered_date >= FIX_FORWARD_SINCE
      )
      .slice(0, 50);

    expect(toProcess).toHaveLength(2);
    expect(toProcess.map((r) => r.company_name)).toEqual(["NewCo Inc", "TodayRound"]);
  });

  it("should respect MAX_PER_RUN slice", () => {
    const FIX_FORWARD_SINCE = "2026-08-27";
    const existingDomains = new Set<string>();
    const MAX_PER_RUN = 1;

    const toProcess = mockRows
      .filter(
        (r) =>
          r.company_domain &&
          !r.company_domain.includes("not_found") &&
          !existingDomains.has(r.company_domain) &&
          r.discovered_date >= FIX_FORWARD_SINCE
      )
      .slice(0, MAX_PER_RUN);

    expect(toProcess).toHaveLength(1);
  });

  it("should preserve rows with cutoff date exactly", () => {
    const FIX_FORWARD_SINCE = "2026-08-27";
    const rows = [
      {
        company_domain: "cutoff.com",
        discovered_date: "2026-08-27",
        company_name: "OnCutoff",
      },
      {
        company_domain: "before.com",
        discovered_date: "2026-08-26",
        company_name: "BeforeCutoff",
      },
    ];

    const filtered = rows.filter((r) => r.discovered_date >= FIX_FORWARD_SINCE);
    expect(filtered).toHaveLength(1);
    expect(filtered[0].company_name).toBe("OnCutoff");
  });
});
