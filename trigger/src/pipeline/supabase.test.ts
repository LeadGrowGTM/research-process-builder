import { afterEach, describe, expect, it, vi } from "vitest";
import { fundingRowFromRecord } from "./supabase.js";
import type { EnrichedRecord } from "./types.js";

function record(overrides: Partial<EnrichedRecord> = {}): EnrichedRecord {
  return {
    company_name: "Acme",
    company_domain: "acme.com",
    amount_raised: "$5M",
    round_type: "Series A",
    source_url: "https://www.techcrunch.com/acme-raises",
    lead_investors: "not_stated",
    round_reasoning: "",
    industry: "payments platform",
    location: "Toronto, Canada",
    article_text: "article",
    source_count: 1,
    score: 10,
    discovered_by: "test",
    discovered_by_pipeline: "series-a",
    ...overrides,
  };
}

describe("fundingRowFromRecord", () => {
  it("normalizes taxonomy and null sentinels at the persistence boundary", () => {
    const row = fundingRowFromRecord(record(), "2026-09-30");
    expect(row.round_type).toBe("Series A");
    expect(row.industry).toBe("Fintech");
    expect(row.lead_investors).toBeNull();
    expect(row.round_reasoning).toBeNull();
    expect(row.location).toBe("Toronto, Canada");
  });

  it("derives source and logo labels without provider calls", () => {
    const row = fundingRowFromRecord(record(), "2026-09-30");
    expect(row.source_name).toBe("TechCrunch");
    expect(row.logo_url).toBe(
      "https://www.google.com/s2/favicons?domain=acme.com&sz=128"
    );
  });
});

describe("funding storage failures", () => {
  afterEach(() => { vi.unstubAllEnvs(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

  async function storage() {
    vi.resetModules();
    vi.stubEnv("SUPABASE_PROJECT_URL", "https://project.test");
    vi.stubEnv("SUPABASE_SERVICE_ROLE_KEY", "fixture-key");
    return import("./supabase.js");
  }

  it("counts failed RaisingFi writes and never logs server bodies or transport details", async () => {
    const { pushRaisingFiRows } = await storage();
    const log = vi.spyOn(console, "error").mockImplementation(() => {});
    const fetchMock = vi.fn().mockResolvedValueOnce(new Response("fixture-contact@example.test fixture-private-token", { status: 500 })).mockRejectedValueOnce(new Error("fixture-contact@example.test fixture-private-token"));
    vi.stubGlobal("fetch", fetchMock);
    const result = await pushRaisingFiRows([{ company_name: "Acme", discovered_date: "2026-09-30", source_url: "https://x.com/raisingfi/status/1" }, { company_name: "Other", discovered_date: "2026-09-30", source_url: "https://x.com/raisingfi/status/2" }], "funding_discoveries");
    expect(result).toMatchObject({ attempted: 2, upserted: 0 });
    expect(result.errors).toHaveLength(2);
    expect(JSON.stringify(log.mock.calls)).not.toContain("@example.test");
    expect(JSON.stringify(log.mock.calls)).not.toContain("fixture-private-token");
    expect(fetchMock.mock.calls[0][1].headers).toMatchObject({ "Content-Profile": "public" });
  });

  it("fails funding ingestion on a failed dedup read or failed upsert", async () => {
    const { pushToSupabase } = await storage();
    const fetchMock = vi.fn().mockResolvedValueOnce(new Response("unavailable", { status: 500 }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(pushToSupabase([record()], "2026-09-30", "funding_discoveries")).rejects.toThrow("dedup read failed");
    fetchMock.mockResolvedValueOnce(new Response("[]")).mockResolvedValueOnce(new Response("[]")).mockResolvedValueOnce(new Response("failed", { status: 500 }));
    await expect(pushToSupabase([record()], "2026-09-30", "funding_discoveries")).rejects.toThrow("funding write failed");
  });

  it("preserves the confirmed count when a later funding write fails", async () => {
    const { pushToSupabase } = await storage();
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response("[]"))
      .mockResolvedValueOnce(new Response("", { status: 201 }))
      .mockResolvedValueOnce(new Response("[]"))
      .mockResolvedValueOnce(new Response("failed", { status: 500 }));
    vi.stubGlobal("fetch", fetchMock);
    const rows = [record({ company_domain: "not_enriched" }), record({ company_name: "Other", company_domain: "not_enriched", source_url: "https://news.test/other" })];
    const onUpsert = vi.fn();
    await expect(pushToSupabase(rows, "2026-09-30", "funding_discoveries", undefined, onUpsert)).rejects.toMatchObject({ upserted: 1 });
    expect(onUpsert).toHaveBeenCalledExactlyOnceWith(1);
  });

  it("starts no funding write after its caller aborts during a read", async () => {
    const { pushToSupabase } = await storage();
    const controller = new AbortController();
    const fetchMock = vi.fn(async (_url: string, init: RequestInit) => {
      expect(init.signal?.aborted).toBe(false);
      controller.abort();
      return new Response("[]");
    });
    vi.stubGlobal("fetch", fetchMock);
    await expect(pushToSupabase([record({ company_domain: "not_enriched" })], "2026-09-30", "funding_discoveries", controller.signal)).rejects.toThrow("funding write failed");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][1].signal?.aborted).toBe(true);
  });
});
