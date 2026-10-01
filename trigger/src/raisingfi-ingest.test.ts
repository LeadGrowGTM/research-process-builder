import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import {
  parseTweet,
  requireRaisingFiStorage,
  resolveTcoUrl,
  type Tweet,
} from "./raisingfi-ingest.js";

const EMOJI_POST: Tweet = {
  id: "111",
  text: "🏛️ Company: Acme AI\n🔗 Website: https://t.co/acme123\n📊 Amount: $20M\n🔄 Round: Series A\n⚙️ Industry: Artificial Intelligence\n🌍 Location: San Francisco\n💰 Investors: a16z, Sequoia",
  created_at: "2026-09-01T10:00:00Z",
  entities: {
    urls: [{ url: "https://t.co/acme123", expanded_url: "https://acme.ai/product" }],
  },
};

const TCO_ONLY_POST: Tweet = {
  id: "222",
  text: "🏛️ Company: BetaPay\n🔗 Website: https://t.co/beta999\n📊 Amount: $5M\n🔄 Round: Seed",
  created_at: "2026-09-02T10:00:00Z",
};

const SPARSE_POST: Tweet = {
  id: "333",
  text: "🏛️ Company: GhostCo\n📊 Amount: Undisclosed",
  created_at: "2026-09-03T10:00:00Z",
};

const NON_FUNDING: Tweet = {
  id: "444",
  text: "Just some market commentary, no funding details here.",
  created_at: "2026-09-04T10:00:00Z",
};

describe("raisingfi parseTweet", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("parses the emoji format with entity expansion, investors, and taxonomy", async () => {
    const row = await parseTweet(EMOJI_POST);
    expect(row).not.toBeNull();
    expect(row?.company_name).toBe("Acme AI");
    expect(row?.company_domain).toBe("acme.ai");
    expect(row?.amount_raised).toBe("$20M");
    expect(row?.round_type).toBe("Series A");
    expect(row?.industry).toBe("AI/ML");
    expect(row?.location).toBe("San Francisco");
    expect(row?.lead_investors).toBe("a16z, Sequoia");
    expect(row?.raw_text).toBe(EMOJI_POST.text);
    expect(row?.website_url).toBe("https://acme.ai/product");
    expect(row?.logo_url).toBe("https://www.google.com/s2/favicons?domain=acme.ai&sz=128");
    expect(row?.source_url).toBe("https://x.com/raisingfi/status/111");
    expect(row?.source_name).toBe("@raisingfi on X");
  });

  it("resolves a t.co website via redirect when entities are absent", async () => {
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock.mockResolvedValueOnce({
      ok: true,
      url: "https://betapay.io/home",
      headers: { get: () => null },
    });

    const row = await parseTweet(TCO_ONLY_POST);
    expect(fetchMock).toHaveBeenCalledOnce();
    expect(row?.company_domain).toBe("betapay.io");
    expect(row?.website_url).toBe("https://betapay.io/home");
    expect(row?.round_type).toBe("Seed");
    expect(row?.lead_investors).toBeNull();
    expect(row?.industry).toBeNull();
  });

  it("nulls missing fields instead of storing sentinels", async () => {
    const row = await parseTweet(SPARSE_POST);
    expect(row).not.toBeNull();
    expect(row?.company_name).toBe("GhostCo");
    expect(row?.company_domain).toBeNull();
    expect(row?.round_type).toBeNull();
    expect(row?.lead_investors).toBeNull();
    expect(row?.round_reasoning).toBeNull();
    expect(row?.industry).toBeNull();
    expect(row?.location).toBeNull();
    expect(row?.logo_url).toBeNull();
  });

  it("rejects non-funding tweets", async () => {
    expect(await parseTweet(NON_FUNDING)).toBeNull();
  });
});

describe("resolveTcoUrl", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("returns null when the HEAD request fails", async () => {
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock.mockRejectedValueOnce(new Error("timeout"));
    expect(await resolveTcoUrl("https://t.co/dead")).toBeNull();
  });

  it("uses a bounded HEAD request", async () => {
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock.mockResolvedValueOnce({
      url: "https://acme.com",
      headers: { get: () => null },
    });
    await resolveTcoUrl("https://t.co/acme");
    expect(fetchMock).toHaveBeenCalledWith(
      "https://t.co/acme",
      expect.objectContaining({ method: "HEAD", redirect: "follow", signal: expect.any(AbortSignal) })
    );
  });
});

describe("requireRaisingFiStorage", () => {
  it("fails before ingestion when Supabase is unconfigured", async () => {
    await expect(
      requireRaisingFiStorage(() => false, async () => true)
    ).rejects.toThrow("Supabase is not configured");
  });

  it("fails when the public funding table cannot be reached", async () => {
    await expect(
      requireRaisingFiStorage(() => true, async () => false)
    ).rejects.toThrow("Table funding_discoveries not found");
  });
});
