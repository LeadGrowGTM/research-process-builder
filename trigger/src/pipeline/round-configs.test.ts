import { afterEach, describe, expect, it } from "vitest";
import { SERIES_A_CONFIG, SERIES_B_CONFIG, SERIES_C_CONFIG } from "./round-configs.js";

const CASES = [
  ["A", SERIES_A_CONFIG],
  ["B", SERIES_B_CONFIG],
  ["C", SERIES_C_CONFIG],
] as const;

const KEYS = CASES.flatMap(([l]) => [`CLAY_SERIES_${l}_WEBHOOK_URL`, `CLAY_SERIES_${l}_WEBHOOK_TOKEN`]);
const saved = Object.fromEntries(KEYS.map((k) => [k, process.env[k]]));

afterEach(() => {
  for (const k of KEYS) {
    if (saved[k] === undefined) delete process.env[k];
    else process.env[k] = saved[k];
  }
});

describe("round config Clay webhook credentials", () => {
  for (const [letter, config] of CASES) {
    it(`Series ${letter} reads its webhook URL and token from the environment`, () => {
      process.env[`CLAY_SERIES_${letter}_WEBHOOK_URL`] = `https://example.test/hook-${letter}`;
      process.env[`CLAY_SERIES_${letter}_WEBHOOK_TOKEN`] = `token-${letter}`;
      expect(config.webhookUrl).toBe(`https://example.test/hook-${letter}`);
      expect(config.webhookAuthToken).toBe(`token-${letter}`);
    });

    it(`Series ${letter} fails closed with the variable name when unset or blank`, () => {
      delete process.env[`CLAY_SERIES_${letter}_WEBHOOK_URL`];
      process.env[`CLAY_SERIES_${letter}_WEBHOOK_TOKEN`] = "   ";
      expect(() => config.webhookUrl).toThrow(`CLAY_SERIES_${letter}_WEBHOOK_URL`);
      expect(() => config.webhookAuthToken).toThrow(`CLAY_SERIES_${letter}_WEBHOOK_TOKEN`);
    });
  }

  it("keeps no literal Clay webhook URL in the configs", () => {
    for (const k of KEYS) delete process.env[k];
    for (const [, config] of CASES) {
      const literal = Object.entries(Object.getOwnPropertyDescriptors(config))
        .filter(([, d]) => "value" in d)
        .map(([, d]) => String(d.value));
      expect(literal.some((v) => v.includes("api.clay.com/v3/sources/webhook"))).toBe(false);
    }
  });
});
