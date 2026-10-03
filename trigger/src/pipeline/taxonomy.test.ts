import { describe, it, expect } from "vitest";
import {
  normalizeRoundType,
  normalizeIndustry,
  normalizeIcpFit,
  signalTypeForRound,
  logoUrlForDomain,
  sourceNameForUrl,
  isPublicHttpsUrl,
  normalizeOptionalText,
  INDUSTRIES,
  ROUND_TYPES,
} from "./taxonomy.js";

describe("normalizeOptionalText", () => {
  it("nulls sentinels and paywall placeholders", () => {
    expect(normalizeOptionalText(" n/a ")).toBeNull();
    expect(normalizeOptionalText("🔒 Get Pro")).toBeNull();
    expect(normalizeOptionalText(" San Francisco ")).toBe("San Francisco");
  });
});

describe("normalizeRoundType", () => {
  it("maps series variants to canonical labels", () => {
    expect(normalizeRoundType("Series A")).toBe("Series A");
    expect(normalizeRoundType("series-a")).toBe("Series A");
    expect(normalizeRoundType("Serie B raise")).toBe("Series B");
    expect(normalizeRoundType("Series C")).toBe("Series C");
    expect(normalizeRoundType("Series D")).toBe("Series D+");
    expect(normalizeRoundType("seed")).toBe("Seed");
    expect(normalizeRoundType("Pre-Seed")).toBe("Pre-Seed");
    expect(normalizeRoundType("preseed round")).toBe("Pre-Seed");
    expect(normalizeRoundType("growth equity")).toBe("Growth");
    expect(normalizeRoundType("bridge loan")).toBe("Debt");
    expect(normalizeRoundType("grant")).toBe("Grant");
  });

  it("returns null for sentinels and junk, never stores them", () => {
    expect(normalizeRoundType("not_stated")).toBeNull();
    expect(normalizeRoundType("")).toBeNull();
    expect(normalizeRoundType("   ")).toBeNull();
    expect(normalizeRoundType(null)).toBeNull();
    expect(normalizeRoundType(undefined)).toBeNull();
    expect(normalizeRoundType("mystery round")).toBeNull();
  });

  it("maps bare unknown to Unknown", () => {
    expect(normalizeRoundType("unknown")).toBe("Unknown");
  });
});

describe("normalizeIndustry", () => {
  it("accepts exact canonical labels case-insensitively", () => {
    expect(normalizeIndustry("fintech")).toBe("Fintech");
    expect(normalizeIndustry("AI/ML")).toBe("AI/ML");
    expect(normalizeIndustry("DevTools")).toBe("DevTools");
  });

  it("maps free text to the closest enum value", () => {
    expect(normalizeIndustry("generative ai startup")).toBe("AI/ML");
    expect(normalizeIndustry("payments platform")).toBe("Fintech");
    expect(normalizeIndustry("digital health")).toBe("Healthcare");
  });

  it("returns Other for recognized-but-unlisted text", () => {
    expect(normalizeIndustry("generic SaaS")).toBe("Other");
  });

  it("returns null for sentinels", () => {
    expect(normalizeIndustry("not_stated")).toBeNull();
    expect(normalizeIndustry("")).toBeNull();
    expect(normalizeIndustry(null)).toBeNull();
  });

  it("covers the fixed enum size", () => {
    expect(INDUSTRIES).toHaveLength(23);
    expect(ROUND_TYPES).toContain("Series A");
  });
});

describe("normalizeIcpFit", () => {
  it("passes valid fits through and defaults junk to weak", () => {
    expect(normalizeIcpFit("strong")).toBe("strong");
    expect(normalizeIcpFit("moderate")).toBe("moderate");
    expect(normalizeIcpFit("bogus")).toBe("weak");
    expect(normalizeIcpFit(null)).toBe("weak");
  });
});

describe("signalTypeForRound", () => {
  it("derives signal_type from the normalized round", () => {
    expect(signalTypeForRound("Series A")).toBe("series_a");
    expect(signalTypeForRound("Series B")).toBe("series_b");
    expect(signalTypeForRound("Series C")).toBe("series_c");
    expect(signalTypeForRound("Seed")).toBe("seed");
    expect(signalTypeForRound("Pre-Seed")).toBe("seed");
    expect(signalTypeForRound("Growth")).toBe("funded");
    expect(signalTypeForRound(null)).toBe("funded");
  });
});

describe("logoUrlForDomain", () => {
  it("builds the google s2 favicon url only for real domains", () => {
    expect(logoUrlForDomain("acme.com")).toBe(
      "https://www.google.com/s2/favicons?domain=acme.com&sz=128"
    );
    expect(logoUrlForDomain("https://www.acme.com/x")).toBe(
      "https://www.google.com/s2/favicons?domain=acme.com&sz=128"
    );
  });

  it("returns null for junk and never a logo.dev url", () => {
    expect(logoUrlForDomain(null)).toBeNull();
    expect(logoUrlForDomain("")).toBeNull();
    expect(logoUrlForDomain("not_found")).toBeNull();
    expect(logoUrlForDomain("not_stated")).toBeNull();
    expect(logoUrlForDomain("localhost")).toBeNull();
  });
});

describe("isPublicHttpsUrl", () => {
  it("allows ordinary https links and rejects credentials, insecure protocols, and secret query keys", () => {
    expect(isPublicHttpsUrl("https://www.techcrunch.com/acme")).toBe(true);
    expect(isPublicHttpsUrl("https://techcrunch.com/acme?utm_source=x")).toBe(true);
    expect(isPublicHttpsUrl("http://www.techcrunch.com/acme")).toBe(false);
    expect(isPublicHttpsUrl("javascript:alert(1)")).toBe(false);
    expect(isPublicHttpsUrl("https://user:pass@techcrunch.com/acme")).toBe(false);
    expect(isPublicHttpsUrl("https://techcrunch.com/acme?token=secret")).toBe(false);
    expect(isPublicHttpsUrl("https://techcrunch.com/acme?api_key=secret")).toBe(false);
    expect(isPublicHttpsUrl("https://news.example/round?author=editor")).toBe(true);
    expect(isPublicHttpsUrl("https://news.example/round?author=editor&utm_medium=social")).toBe(true);
    expect(isPublicHttpsUrl("https://news.example/round?auth=1")).toBe(false);
    expect(isPublicHttpsUrl("https://news.example/round?access_token=abc")).toBe(false);
    expect(isPublicHttpsUrl("https://news.example/round?author=editor&token=abc")).toBe(false);
    expect(isPublicHttpsUrl("https://news.example/round?apiKey=abc")).toBe(false);
    expect(isPublicHttpsUrl("https://news.example/round?password=secret")).toBe(false);
    expect(isPublicHttpsUrl("not a url")).toBe(false);
  });
});

describe("sourceNameForUrl", () => {
  it("uses publisher labels for known funding sources", () => {
    expect(sourceNameForUrl("https://www.techcrunch.com/2026/raise")).toBe("TechCrunch");
    expect(sourceNameForUrl("https://finsmes.com/a")).toBe("FinSMEs");
  });

  it("uses the raisingfi label and bare-host fallback", () => {
    expect(sourceNameForUrl("https://x.com/raisingfi/status/123")).toBe("@raisingfi on X");
    expect(sourceNameForUrl("https://www.example.news/story")).toBe("example.news");
    expect(sourceNameForUrl("not a url")).toBeNull();
  });
});
