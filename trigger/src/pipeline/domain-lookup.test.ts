import { afterEach, describe, expect, it, vi } from "vitest";
import { lookupDomainMultiSignal } from "./domain-lookup.js";
import { lunaChat } from "./luna.js";
import { searchSerper } from "./serper.js";

vi.mock("./luna.js", () => ({ isLunaConfigured: () => true, lunaChat: vi.fn() }));
vi.mock("./serper.js", () => ({ searchSerper: vi.fn() }));
afterEach(() => vi.resetAllMocks());

describe("funding domain resolution", () => {
  it("bounds unexpected parallel tools to three searches and replays valid tool messages", async () => {
    vi.mocked(searchSerper).mockResolvedValue([{ title: "Acme", link: "https://acme.com", snippet: "Acme software" }]);
    vi.mocked(lunaChat).mockResolvedValueOnce({ content: null, toolCalls: [1, 2, 3, 4].map(id => ({ id: String(id), name: "web_search", args: JSON.stringify({ query: `Acme ${id}` }) })), usage: { inputTokens: 0, cachedInputTokens: 0, outputTokens: 0 }, serviceTier: "flex" });
    vi.mocked(lunaChat).mockResolvedValueOnce({ content: JSON.stringify({ domain: "https://www.acme.com/", confidence: "high", evidence: "Official site" }), toolCalls: [], usage: { inputTokens: 0, cachedInputTokens: 0, outputTokens: 0 }, serviceTier: "flex" });
    const result = await lookupDomainMultiSignal("Acme", {}, "https://publisher.test/story");
    expect(result.domain).toBe("acme.com");
    expect(searchSerper).toHaveBeenCalledTimes(3);
    const second = vi.mocked(lunaChat).mock.calls[1][0];
    expect(second.toolChoice).toBe("none");
    expect((second.messages[2].tool_calls as Array<{ type: string }>).every(call => call.type === "function")).toBe(true);
    expect(second.messages.filter(message => message.role === "tool")).toHaveLength(4);
  });

  it("does not treat an unsupported model guess as a verified domain", async () => {
    vi.mocked(lunaChat).mockResolvedValue({ content: '{"domain":"acme.com","confidence":"high","evidence":"guess"}', toolCalls: [], usage: { inputTokens: 0, cachedInputTokens: 0, outputTokens: 0 }, serviceTier: "flex" });
    expect((await lookupDomainMultiSignal("Acme", {})).domain).toBe("not_found");
    expect(searchSerper).not.toHaveBeenCalled();
  });
});
