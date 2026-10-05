import { describe, expect, it, vi } from "vitest";
import { readFileSync } from "node:fs";
import { isOperatingCompanyFormD, parseDailyIndex, parseFormDXml, primaryDocumentUrl, runSecFormDDaily } from "./sec-form-d-daily.js";
const operating = readFileSync(new URL("./__fixtures__/sec-form-d/operating-company.xml", import.meta.url), "utf8");
const pooled = readFileSync(new URL("./__fixtures__/sec-form-d/pooled-fund.xml", import.meta.url), "utf8");

const row = { cik: "1", companyName: "Acme Labs", formType: "D", filingDate: "2026-09-30", filename: "edgar/data/1/0000001-26-000001.txt" };
describe("SEC Form D", () => {
  it("parses a daily index and conservative operating-company XML", () => {
    expect(parseDailyIndex("1|Acme Labs|D|2026-09-30|edgar/data/1/0000001-26-000001.txt", "2026-09-30")).toEqual([row]);
    const parsed = parseFormDXml(operating, row, primaryDocumentUrl(row));
    expect(parsed).toMatchObject({ company_name: "Acme Labs", amount_raised_usd: 1500000, round_type: "Unknown", location: "Toronto, ON", company_domain: "" });
    expect(isOperatingCompanyFormD(pooled)).toBe(false);
    expect(primaryDocumentUrl(row)).toBe("https://www.sec.gov/Archives/edgar/data/1/000000126000001/primary_doc.xml");
    expect(parsed?.funding_date).toBe("2026-09-28");
    expect(parsed?.raw_text).not.toContain("Fixture Person");
    expect(parsed?.article_text).not.toContain("Different City");
  });

  it("excludes amendments, funds, small or indefinite offerings and unsold raises", () => {
    expect(parseDailyIndex("1|Acme|D/A|2026-09-30|file.txt", "2026-09-30")).toEqual([]);
    for (const xml of [operating.replace("Other Technology", "Pooled Investment Fund"), operating.replace("Acme Labs", "Acme Real Estate Fund"), operating.replace("2000000", "999999"), operating.replace("2000000", "Indefinite"), operating.replace("1500000", "0"), operating.replace("<isAmendment>false", "<isAmendment>true")]) {
      expect(parseFormDXml(xml, row, primaryDocumentUrl(row))).toBeNull();
    }
  });

  it("never writes an unresolved issuer and passes raw industry/location to lookup", async () => {
    const lookupDomain = vi.fn(async (_name: string, _clues: unknown, _url: string, _deadline?: number) => ({ domain: "acme.test", confidence: "low" as const, source: "search_only" as const, evidence: "Uncertain" }));
    const fetchImpl = vi.fn().mockResolvedValueOnce(new Response("1|Acme Labs|D|2026-09-30|edgar/data/1/0000001-26-000001.txt")).mockResolvedValueOnce(new Response(operating));
    const result = await runSecFormDDaily({ date: "2026-09-30", fetchImpl, sleep: async () => {}, supabaseUrl: "https://db.test", supabaseKey: "fixture", lookupDomain });
    expect(result.written).toBe(0);
    expect(fetchImpl).toHaveBeenCalledTimes(2);
    expect(lookupDomain.mock.calls[0][1]).toEqual({ industry: "Other Technology", location: "Toronto, ON" });
  });

  it("uses a completed business-day index and backs off missing holiday indexes", async () => {
    const fetchImpl = vi.fn().mockResolvedValueOnce(new Response("missing", { status: 404 })).mockResolvedValueOnce(new Response(""));
    const result = await runSecFormDDaily({ asOfDate: "2026-09-28", fetchImpl, sleep: async () => {}, supabaseUrl: "https://db.test", supabaseKey: "fixture" });
    expect(fetchImpl.mock.calls[0][0]).toContain("master.20260925.idx");
    expect(fetchImpl.mock.calls[1][0]).toContain("master.20260924.idx");
    expect(result.date).toBe("2026-09-24");
  });

  it("deduplicates same-domain filings before a public-schema write", async () => {
    const fetchImpl = vi.fn().mockResolvedValueOnce(new Response("1|Acme Labs|D|2026-09-30|edgar/data/1/0000001-26-000001.txt\n2|Acme Labs|D|2026-09-30|edgar/data/2/0000002-26-000001.txt")).mockResolvedValueOnce(new Response(operating)).mockResolvedValueOnce(new Response("[]")).mockResolvedValueOnce(new Response(operating)).mockResolvedValueOnce(new Response(null, { status: 201 }));
    const result = await runSecFormDDaily({ date: "2026-09-30", fetchImpl, sleep: async () => {}, supabaseUrl: "https://db.test", supabaseKey: "fixture", lookupDomain: async () => ({ domain: "acme.test", confidence: "high", source: "search_validated", evidence: "fixture" }) });
    expect(result.written).toBe(1);
    const write = fetchImpl.mock.calls.at(-1);
    expect(write?.[1].headers).toMatchObject({ "Accept-Profile": "public", "Content-Profile": "public" });
    expect(JSON.parse(String(write?.[1].body))).toHaveLength(1);
  });

  it("fails on broken storage reads and writes and rejects invalid dates", async () => {
    for (const failWrite of [false, true]) {
      const fetchImpl = vi.fn().mockResolvedValueOnce(new Response("1|Acme Labs|D|2026-09-30|edgar/data/1/0000001-26-000001.txt")).mockResolvedValueOnce(new Response(operating)).mockResolvedValueOnce(new Response(failWrite ? "[]" : "failure", { status: failWrite ? 200 : 500 }));
      if (failWrite) fetchImpl.mockResolvedValueOnce(new Response("failure", { status: 500 }));
      await expect(runSecFormDDaily({ date: "2026-09-30", fetchImpl, sleep: async () => {}, supabaseUrl: "https://db.test", supabaseKey: "fixture", lookupDomain: async () => ({ domain: "acme.test", confidence: "high", source: "search_validated", evidence: "fixture" }) })).rejects.toThrow(failWrite ? "write failed" : "dedup read failed");
    }
    await expect(runSecFormDDaily({ date: "2026-02-30", fetchImpl: vi.fn() })).rejects.toThrow("Invalid SEC run date");
  });
  it("caps filings and domain work, paces SEC calls, and fails core reads", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response("1|Acme Labs|D|2026-09-30|edgar/data/1/0000001-26-000001.txt\n2|Two|D|2026-09-30|edgar/data/2/0000002-26-000001.txt"))
      .mockResolvedValueOnce(new Response(operating))
      .mockResolvedValueOnce(new Response(JSON.stringify([])))
      .mockResolvedValueOnce(new Response(JSON.stringify([]), { status: 201 }));
    const sleep = vi.fn(async () => {});
    const result = await runSecFormDDaily({ date: "2026-09-30", maxFilings: 1, maxDomainLookups: 1, fetchImpl: fetchMock, sleep, minRequestGapMs: 1, supabaseUrl: "https://db.test", supabaseKey: "k", lookupDomain: async () => ({ domain: "acme.test", confidence: "high", source: "search_validated", evidence: "fixture" }) });
    expect(result.written).toBe(1); expect(result.domainLookups).toBe(1); expect(fetchMock).toHaveBeenCalledTimes(4);
    await expect(runSecFormDDaily({ date: "2026-09-30", fetchImpl: vi.fn(async () => new Response("no", { status: 500 })), sleep, supabaseUrl: "https://db.test", supabaseKey: "k" })).rejects.toThrow("SEC core read failed");
  });
});
