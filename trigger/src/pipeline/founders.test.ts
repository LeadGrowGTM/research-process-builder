import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import {
  aiArkSearchPeople,
  persistFounderContacts,
  runFounderWaterfall,
  classifyStatus,
  guessLocalParts,
  normalizeGuessName,
  guessEmail,
  runFoundersForCompany,
  type FounderCandidate,
} from "./founders.js";

const founder: FounderCandidate = {
  first_name: "Jane",
  last_name: "Doe",
  title: "CEO",
  linkedin_url: "https://linkedin.com/in/janedoe",
};

const silentCost = { record: () => {} };

function mvBody(resultcode: number) {
  return new Response(JSON.stringify({ resultcode }), { status: 200 });
}

describe("classifyStatus", () => {
  it("classifies http statuses", () => {
    expect(classifyStatus(200)).toBe("ok");
    expect(classifyStatus(429)).toBe("rate_limited");
    expect(classifyStatus(401)).toBe("auth");
    expect(classifyStatus(404)).toBe("not_found");
    expect(classifyStatus(402)).toBe("exhausted");
    expect(classifyStatus(500)).toBe("transient");
  });
});

describe("runFounderWaterfall order", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("tries quickenrich first, then aiark, and stops on first valid", async () => {
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    const urls: string[] = [];
    fetchMock.mockImplementation(async (url: string) => {
      urls.push(url);
      if (url.includes("quickenrich")) {
        return new Response(JSON.stringify({ data: { email: "qe@acme.com" } }), { status: 200 });
      }
      if (url.includes("millionverifier")) {
        // First MV call (quickenrich email) is invalid, second (aiark) is valid
        const invalid = urls.filter((u) => u.includes("millionverifier")).length === 1;
        return mvBody(invalid ? 6 : 1);
      }
      if (url.includes("ai-ark")) {
        return new Response(
          JSON.stringify({
            content: [
              { profile: { first_name: "Jane", last_name: "Doe", full_name: "Jane Doe", email: "jane@acme.com" } },
            ],
          }),
          { status: 200 }
        );
      }
      throw new Error(`unexpected fetch: ${url}`);
    });

    const result = await runFounderWaterfall(
      founder,
      "acme.com",
      { quickEnrich: "qe", aiArk: "ark", kitt: "kitt", millionVerifier: "mv" },
      silentCost
    );

    expect(result.email).toBe("jane@acme.com");
    expect(result.email_provider).toBe("aiark");
    expect(result.email_status).toBe("valid");
    expect(result.waterfall_path).toEqual(["quickenrich:invalid", "aiark:valid"]);
    // Kitt and guesser never ran
    expect(urls.some((u) => u.includes("trykitt"))).toBe(false);
    // Order: quickenrich before ai-ark before MV-for-aiark
    expect(urls[0]).toContain("quickenrich");
    expect(urls.findIndex((u) => u.includes("ai-ark"))).toBeGreaterThan(0);
  });

  it("skips all email work when MillionVerifier is not configured", async () => {
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;

    const result = await runFounderWaterfall(founder, "acme.com", {}, silentCost);

    expect(result.email).toBeNull();
    expect(result.email_provider).toBeNull();
    expect(result.waterfall_path).toEqual(["millionverifier:skipped"]);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("skips missing finders and validates pattern guesses when MV is configured", async () => {
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock.mockResolvedValue(mvBody(1));

    const result = await runFounderWaterfall(
      founder,
      "acme.com",
      { millionVerifier: "mv" },
      silentCost
    );

    expect(result.email).toBe("jane.doe@acme.com");
    expect(result.email_provider).toBe("guesser");
    expect(result.waterfall_path.slice(0, 3)).toEqual([
      "quickenrich:skipped",
      "aiark:skipped",
      "kitt:skipped",
    ]);
    expect(fetchMock).toHaveBeenCalledOnce();
  });
});

describe("guesser", () => {
  it("rejects unverified and catch-all pattern guesses", async () => {
    for (const status of ["unverified", "catch_all", "invalid", "error"]) {
      const validate = vi.fn(async () => status);
      expect((await guessEmail("Jane", "Doe", "acme.com", validate, 2)).email).toBeNull();
      expect(validate).toHaveBeenCalledTimes(2);
    }
  });
  it("orders first.last first and normalizes names", () => {
    expect(normalizeGuessName("Jose-Luis")).toBe("joseluis");
    const parts = guessLocalParts("jane", "doe");
    expect(parts[0]).toBe("jane.doe");
    expect(parts[1]).toBe("jdoe");
    expect(parts).toContain("jane");
    expect(parts).toContain("doe");
  });
});

describe("founder discovery and persistence", () => {
  const oldUrl = process.env.SUPABASE_PROJECT_URL;
  const oldKey = process.env.SUPABASE_KEY;

  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });

  afterEach(() => {
    if (oldUrl === undefined) delete process.env.SUPABASE_PROJECT_URL;
    else process.env.SUPABASE_PROJECT_URL = oldUrl;
    if (oldKey === undefined) delete process.env.SUPABASE_KEY;
    else process.env.SUPABASE_KEY = oldKey;
    vi.unstubAllGlobals();
  });

  it("keeps at most three founders and excludes candidates without LinkedIn", async () => {
    const content = [
      ["A", "One", "https://linkedin.com/in/a-one"],
      ["B", "Two", "https://linkedin.com/in/b-two"],
      ["Missing", "Link", null],
      ["C", "Three", "https://linkedin.com/in/c-three"],
      ["D", "Four", "https://linkedin.com/in/d-four"],
    ].map(([first_name, last_name, linkedin]) => ({
      profile: { first_name, last_name, title: "Founder" },
      link: { linkedin },
    }));
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ content }), { status: 200 })
    );

    const founders = await aiArkSearchPeople("acme.com", undefined, "ark");
    expect(founders).toHaveLength(3);
    expect(founders.every((item) => item.linkedin_url)).toBe(true);
    expect(founders.map((item) => item.first_name)).toEqual(["A", "B", "C"]);
  });

  it("targets the leadgrow_knowledge schema and surfaces failed upserts", async () => {
    process.env.SUPABASE_PROJECT_URL = "https://project.supabase.co";
    process.env.SUPABASE_KEY = "test-key";
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock.mockResolvedValueOnce(new Response("failed", { status: 500 }));

    await expect(
      persistFounderContacts([
        {
          company_domain: "acme.com",
          full_name: "Jane Doe",
          title: "CEO",
          linkedin_url: "https://linkedin.com/in/janedoe",
          email: null,
          email_status: null,
          email_provider: null,
          waterfall_path: [],
          found_at: "2026-09-30T00:00:00.000Z",
        },
      ])
    ).rejects.toThrow("HTTP 500");

    const request = fetchMock.mock.calls[0];
    expect(request[0]).toContain("founder_contacts?on_conflict=company_domain,linkedin_url");
    expect(request[1].headers).toMatchObject({
      "Accept-Profile": "leadgrow_knowledge",
      "Content-Profile": "leadgrow_knowledge",
    });
  });

  it("deduplicates public founder identities and rejects unsupported profile URLs and titles", async () => {
    vi.mocked(fetch).mockResolvedValue(new Response(JSON.stringify({ content: [
      { profile: { first_name: "Ada", last_name: "Lovelace", title: "Founder" }, link: { linkedin: "https://www.linkedin.com/in/ada/?trk=fixture" } },
      { profile: { first_name: "Ada", last_name: "Lovelace", title: "Founder" }, link: { linkedin: "https://linkedin.com/in/ada" } },
      { profile: { first_name: "Other", last_name: "Person", title: "Account Manager" }, link: { linkedin: "https://linkedin.com/in/other" } },
      { profile: { first_name: "Bad", last_name: "Link", title: "Founder" }, link: { linkedin: "https://untrusted.test/in/bad" } },
    ] })));
    expect(await aiArkSearchPeople("acme.com", undefined, "ark")).toHaveLength(1);
  });

  it("stops provider calls at the shared run budget and logs no contact details", async () => {
    const log = vi.spyOn(console, "log").mockImplementation(() => {});
    const budget = { record: () => {}, remainingCalls: 0 };
    expect(await runFoundersForCompany("acme.com", { aiArk: "fixture" }, budget)).toBe(0);
    expect(fetch).not.toHaveBeenCalled();
    expect(log).not.toHaveBeenCalled();
    log.mockRestore();
  });
});
