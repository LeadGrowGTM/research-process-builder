import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import {
  lunaChat,
  lunaJson,
  priceLunaCall,
  LUNA_MODEL,
  LUNA_USD_PER_INPUT_TOKEN,
  LUNA_USD_PER_OUTPUT_TOKEN,
} from "./luna.js";

function chatBody(content: string, usage = {}) {
  return {
    choices: [{ message: { content } }],
    usage: {
      prompt_tokens: 1000,
      completion_tokens: 500,
      prompt_tokens_details: { cached_tokens: 100 },
      ...usage,
    },
  };
}

describe("lunaJson", () => {
  const OLD_KEY = process.env.OPENAI_API_KEY;

  beforeEach(() => {
    process.env.OPENAI_API_KEY = "test-key";
    vi.stubGlobal("fetch", vi.fn());
  });

  afterEach(() => {
    process.env.OPENAI_API_KEY = OLD_KEY;
    vi.unstubAllGlobals();
  });

  it("uses flex first and returns parsed data with usage and cost", async () => {
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify(chatBody('{"industry":"Fintech"}')), { status: 200 })
    );

    const result = await lunaJson<{ industry: string }>({
      name: "t",
      schema: { type: "object" },
      systemPrompt: "sys",
      userPrompt: "user",
    });

    expect(result).not.toBeNull();
    expect(result?.data).toEqual({ industry: "Fintech" });
    expect(result?.serviceTier).toBe("flex");
    expect(result?.usage).toEqual({ inputTokens: 1000, cachedInputTokens: 100, outputTokens: 500 });
    expect(result?.costUsd).toBeCloseTo(priceLunaCall(result!.usage), 12);
    const sent = JSON.parse(String(fetchMock.mock.calls[0][1].body));
    expect(sent.model).toBe(LUNA_MODEL);
    expect(sent.service_tier).toBe("flex");
    expect(sent.response_format.json_schema.strict).toBe(true);
  });

  it("falls back to standard on 429", async () => {
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock.mockResolvedValueOnce(new Response("limited", { status: 429 }));
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify(chatBody('{"ok":true}')), { status: 200 })
    );

    const result = await lunaJson<{ ok: boolean }>({
      name: "t",
      schema: { type: "object" },
      systemPrompt: "sys",
      userPrompt: "user",
    });

    expect(result?.data).toEqual({ ok: true });
    expect(result?.serviceTier).toBe("standard");
    const second = JSON.parse(String(fetchMock.mock.calls[1][1].body));
    expect(second.service_tier).toBe("default");
  });

  it("falls back to standard on retryable flex server errors", async () => {
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock.mockResolvedValueOnce(new Response("unavailable", { status: 503 }));
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify(chatBody('{"ok":true}')), { status: 200 })
    );

    const result = await lunaJson<{ ok: boolean }>({
      name: "t",
      schema: { type: "object" },
      systemPrompt: "sys",
      userPrompt: "user",
    });

    expect(result?.data.ok).toBe(true);
    expect(result?.serviceTier).toBe("standard");
  });

  it("falls back to standard when flex throws (timeout)", async () => {
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock.mockRejectedValueOnce(new Error("timeout"));
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify(chatBody('{"ok":1}')), { status: 200 })
    );

    const result = await lunaJson<{ ok: number }>({
      name: "t",
      schema: { type: "object" },
      systemPrompt: "sys",
      userPrompt: "user",
    });

    expect(result?.data).toEqual({ ok: 1 });
    expect(result?.serviceTier).toBe("standard");
  });

  it("returns null when the key is missing or content is not JSON", async () => {
    delete process.env.OPENAI_API_KEY;
    const noKey = await lunaJson({ name: "t", schema: {}, systemPrompt: "s", userPrompt: "u" });
    expect(noKey).toBeNull();

    process.env.OPENAI_API_KEY = "test-key";
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify(chatBody("no json here")), { status: 200 }));
    const bad = await lunaJson({ name: "t", schema: {}, systemPrompt: "s", userPrompt: "u" });
    expect(bad).toBeNull();
  });

  it("does not retry permanent request failures or an expired run budget", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValue(new Response("bad request", { status: 400 }));
    expect(await lunaJson({ name: "t", schema: {}, systemPrompt: "s", userPrompt: "u" })).toBeNull();
    expect(fetchMock).toHaveBeenCalledOnce();
    expect(await lunaJson({ name: "t", schema: {}, systemPrompt: "s", userPrompt: "u", deadlineAt: Date.now() - 1 })).toBeNull();
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it("rejects refusals and truncated structured output", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ choices: [{ finish_reason: "length", message: { content: '{"ok":true}' } }] })));
    expect(await lunaJson({ name: "t", schema: {}, systemPrompt: "s", userPrompt: "u" })).toBeNull();
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ choices: [{ message: { content: '{}', refusal: "Refused" } }] })));
    expect(await lunaJson({ name: "t", schema: {}, systemPrompt: "s", userPrompt: "u" })).toBeNull();
  });
});

describe("lunaChat", () => {
  const OLD_KEY = process.env.OPENAI_API_KEY;

  beforeEach(() => {
    process.env.OPENAI_API_KEY = "test-key";
    vi.stubGlobal("fetch", vi.fn());
  });

  afterEach(() => {
    process.env.OPENAI_API_KEY = OLD_KEY;
    vi.unstubAllGlobals();
  });

  it("sends a strict JSON schema alongside tools", async () => {
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify(chatBody('{"domain":"acme.com"}')), { status: 200 })
    );

    await lunaChat({
      name: "domain",
      schema: { type: "object", properties: { domain: { type: "string" } } },
      messages: [{ role: "user", content: "find acme" }],
      tools: [{ name: "search", description: "search", parameters: { type: "object" } }],
    });

    const sent = JSON.parse(String(fetchMock.mock.calls[0][1].body));
    expect(sent.response_format.json_schema).toMatchObject({ name: "domain", strict: true });
    expect(sent.tools[0].function.name).toBe("search");
    expect(sent.tools[0].function.strict).toBe(true);
    expect(sent.parallel_tool_calls).toBe(false);
    expect(sent.max_completion_tokens).toBe(300);
    expect(sent.max_tokens).toBeUndefined();
  });
});

describe("priceLunaCall", () => {
  it("prices fresh, cached, and output tokens separately", () => {
    const cost = priceLunaCall({ inputTokens: 1_000_000, cachedInputTokens: 0, outputTokens: 0 });
    expect(cost).toBeCloseTo(LUNA_USD_PER_INPUT_TOKEN * 1_000_000, 8);
    const out = priceLunaCall({ inputTokens: 0, cachedInputTokens: 0, outputTokens: 1_000_000 });
    expect(out).toBeCloseTo(LUNA_USD_PER_OUTPUT_TOKEN * 1_000_000, 8);
    const cached = priceLunaCall({ inputTokens: 1_000_000, cachedInputTokens: 1_000_000, outputTokens: 0 });
    expect(cached).toBeCloseTo(0.01, 8);
  });
});
