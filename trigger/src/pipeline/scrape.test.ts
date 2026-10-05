import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchUrl, htmlToText, isUsableContent, looksBlocked, scrapePage } from "./scrape.js";

const URL = "https://company.test/article";
const SPIDER_URL = "https://api.spider.cloud/scrape";
const UNBLOCKER_URL = "https://api.spider.cloud/unblocker";
const CONTENT = "Acme builds payment software for retailers. ".repeat(12).trim();
const OPTIONS = { maxChars: 300 };
const words = (count: number) => Array.from({ length: count }, (_, i) => `word${i}`).join(" ");

function htmlResponse(html: string) {
  return new Response(html, { headers: { "Content-Type": "text/html; charset=utf-8" } });
}

function jsonResponse(data: unknown) {
  return new Response(JSON.stringify(data), { headers: { "Content-Type": "application/json" } });
}

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllEnvs(); vi.unstubAllGlobals(); });

describe("htmlToText", () => {
  it.each(["script", "style", "noscript", "svg", "template"])("drops %s blocks", (tag) => {
    expect(htmlToText(`<title>Acme</title><${tag} class="junk"><p>Hidden junk</p></${tag}><main>Useful evidence</main>`)).toBe("Acme\nUseful evidence");
  });

  it.each(["nav", "header", "footer", "aside"])("keeps %s text as full page content", (tag) => {
    expect(htmlToText(`<${tag}>Company links</${tag}><main>Useful evidence</main>`)).toBe("Company links\nUseful evidence");
  });

  it("keeps the title, separates blocks and collapses whitespace without breaking inline words", () => {
    expect(htmlToText("<TITLE> Acme &amp; Co </TITLE><div><h1>Payments</h1><p>Build <strong>better</strong> tools.\t Today.</p><p>Pay<strong>ments</strong><br>For retailers.</p></div>"))
      .toBe("Acme & Co\nPayments\nBuild better tools. Today.\nPayments\nFor retailers.");
  });

  it("decodes common and numeric entities after stripping tags", () => {
    expect(htmlToText("<p>&amp; &lt;literal&gt; &quot;quote&quot; &#39; &apos; &nbsp; &#65; &#x42; &#X1F600;</p>"))
      .toBe("& <literal> \"quote\" ' ' A B \u{1F600}");
  });

  it("leaves unknown and invalid entities intact and hides HTML comments", () => {
    expect(htmlToText("<!-- junk <p>hidden</p> --><p>&unknown; &#x110000; &#xD800; &#0;</p>"))
      .toBe("&unknown; &#x110000; &#xD800; &#0;");
  });

  it("retains link destinations used for Product Hunt and company domain extraction", () => {
    expect(htmlToText('<p><a href="https://acme.com?ref=producthunt&amp;utm_source=ph"><strong>Website</strong></a> <a href=\'/posts/acme\'>Acme</a> <a href="javascript:void(0)">Menu</a></p>'))
      .toBe("Website (https://acme.com?ref=producthunt&utm_source=ph) Acme (/posts/acme) Menu");
  });
});

describe("usable content", () => {
  it.each([49, 50])("requires at least 50 whitespace-separated words (words: %s)", (count) => {
    expect(isUsableContent(` \n${words(count).replace(/ /g, "\t\n")} \n`)).toBe(count === 50);
  });

  it("accepts a 1000-word page mentioning reCAPTCHA and Cloudflare", () => {
    const text = `${words(995)} Protected by reCAPTCHA and Cloudflare`;
    expect(looksBlocked(text)).toBe(false);
    expect(isUsableContent(text)).toBe(true);
  });

  it("rejects a 40-word Just a moment page", () => {
    expect(isUsableContent(`Just a moment ${words(37)}`)).toBe(false);
  });

  it.each(["Just a moment...", "ATTENTION REQUIRED", "Checking your browser", "Verify you are human", "Verify you are a human", "Are you a robot", "Press and hold", "Access denied", "Please enable JavaScript", "JavaScript is required", "Please\n enable cookies", "cf-browser-verification", "Protected by reCAPTCHA", "DataDome", "PerimeterX"])("rejects short bot walls containing %s even above 50 words", (wall) => {
    expect(isUsableContent(`${wall} ${CONTENT}`)).toBe(false);
  });

  it.each([299, 300])("only treats matching text under 300 words as blocked (words: %s)", (count) => {
    expect(isUsableContent(`Just a moment ${words(count - 3)}`)).toBe(count === 300);
  });

  it("accepts short legitimate content mentioning Cloudflare alone", () => {
    expect(isUsableContent(`Cloudflare ${CONTENT}`)).toBe(true);
  });
});

describe("scrapePage waterfall", () => {
  const fetchMock = vi.fn<typeof fetch>();

  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
    vi.stubEnv("SPIDER_API_KEY", "spider-test-key");
  });

  it("uses full direct text, drops scripts and makes no paid calls", async () => {
    const timeout = vi.spyOn(AbortSignal, "timeout");
    const text = `Navigation\n${CONTENT}\nCompany footer`;
    fetchMock.mockResolvedValueOnce(htmlResponse(`<nav>Navigation</nav><script>${"junk".repeat(100)}</script><main>${CONTENT}</main><footer>Company footer</footer>`));
    expect(await scrapePage(URL)).toEqual({ content: text, provider: "direct", costUsd: 0 });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith(URL, expect.objectContaining({
      method: "GET", redirect: "follow", headers: { "User-Agent": expect.stringContaining("Chrome/"), Accept: "text/html" },
    }));
    expect(timeout).toHaveBeenCalledWith(10_000);
  });

  it.each([200, 204, undefined])("uses Spider smart for a blocked direct page with page status %s", async (status) => {
    fetchMock.mockResolvedValueOnce(htmlResponse(`<title>Just a moment</title><p>${CONTENT}</p>`));
    fetchMock.mockResolvedValueOnce(jsonResponse([{ content: CONTENT, status, costs: { total_cost: 0.001 } }]));
    expect(await scrapePage(URL, OPTIONS)).toEqual({ content: CONTENT.slice(0, 300), provider: "spider-smart", costUsd: 0.001 });
    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([URL, SPIDER_URL]);
  });

  it("runs direct -> smart -> chrome -> unblocker for thin or blocked pages and sums costs", async () => {
    const timeout = vi.spyOn(AbortSignal, "timeout");
    fetchMock.mockResolvedValueOnce(htmlResponse(`<p>${words(49)}</p>`));
    fetchMock.mockResolvedValueOnce(jsonResponse([{ content: `Access denied ${CONTENT}`, status: 200, costs: { total_cost: 0.001 } }]));
    fetchMock.mockResolvedValueOnce(jsonResponse([{ content: words(49), status: 200, costs: { total_cost: 0.002 } }]));
    fetchMock.mockResolvedValueOnce(jsonResponse([{ content: CONTENT, status: 200, costs: { total_cost: 0.003 } }]));
    const result = await scrapePage(URL, OPTIONS);
    expect(result).toMatchObject({ content: CONTENT.slice(0, 300), provider: "spider-unblocker" });
    expect(result?.costUsd).toBeCloseTo(0.006);
    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([URL, SPIDER_URL, SPIDER_URL, UNBLOCKER_URL]);
    for (const [index, request] of ["smart", "chrome", "smart"].entries()) {
      const init = fetchMock.mock.calls[index + 1][1];
      expect(init?.method).toBe("POST");
      expect(init?.headers).toEqual({ Authorization: "Bearer spider-test-key", "Content-Type": "application/json" });
      expect(JSON.parse(String(init?.body))).toEqual({ url: URL, return_format: "markdown", request, filter_output_main_only: false });
    }
    expect(timeout.mock.calls.map(([ms]) => ms)).toEqual([10_000, 45_000, 60_000, 60_000]);
  });

  it("returns chrome content and stops before unblocker when chrome succeeds", async () => {
    fetchMock.mockResolvedValueOnce(htmlResponse("<div id='root'></div>"));
    fetchMock.mockResolvedValueOnce(jsonResponse([{ content: "short", status: 200, costs: { total_cost: 0.001 } }]));
    fetchMock.mockResolvedValueOnce(jsonResponse([{ content: CONTENT, status: 200, costs: { total_cost: 0.002 } }]));
    expect(await scrapePage(URL)).toEqual({ content: CONTENT, provider: "spider-chrome", costUsd: 0.003 });
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  const deadCases = ["smart", "chrome", "unblocker"].flatMap((provider, index) =>
    ["page 403", "page 404", "page 500", "HTTP 500", "timeout", "throws", "page error", "invalid JSON"].map(failure => ({ provider, step: index + 1, failure })));
  it.each(deadCases)("stops without escalation when Spider $provider returns $failure", async ({ step, failure }) => {
    fetchMock.mockResolvedValueOnce(htmlResponse("<p>short</p>"));
    for (let i = 1; i < step; i++) fetchMock.mockResolvedValueOnce(jsonResponse([{ content: "short", status: 200 }]));
    if (failure === "timeout") fetchMock.mockRejectedValueOnce(new DOMException("Timed out", "TimeoutError"));
    else if (failure === "throws") fetchMock.mockRejectedValueOnce(new Error("Network failure"));
    else if (failure === "HTTP 500") fetchMock.mockResolvedValueOnce(new Response("failed", { status: 500 }));
    else if (failure === "invalid JSON") fetchMock.mockResolvedValueOnce(new Response("invalid JSON"));
    else fetchMock.mockResolvedValueOnce(jsonResponse([{ content: CONTENT, status: failure === "page error" ? 200 : Number(failure.slice(5)), error: failure === "page error" ? "failed" : undefined }]));
    expect(await scrapePage(URL, OPTIONS)).toBeNull();
    expect(fetchMock).toHaveBeenCalledTimes(step + 1);
  });

  it.each([null, [], [null], [42], { content: CONTENT }].map(data => ({ data })))("returns null for malformed Spider response $data", async ({ data }) => {
    fetchMock.mockResolvedValueOnce(htmlResponse("<p>short</p>"));
    fetchMock.mockResolvedValueOnce(jsonResponse(data));
    expect(await scrapePage(URL)).toBeNull();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it.each([undefined, null, "bad", { total_cost: "0.001" }, { total_cost: null }])("defaults missing or invalid costs to zero (%j)", async (costs) => {
    fetchMock.mockResolvedValueOnce(htmlResponse("<p>short</p>"));
    fetchMock.mockResolvedValueOnce(jsonResponse([{ content: CONTENT, status: 200, costs }]));
    expect(await scrapePage(URL)).toEqual({ content: CONTENT, provider: "spider-smart", costUsd: 0 });
  });

  it("returns null after all three Spider steps yield thin content", async () => {
    fetchMock.mockResolvedValueOnce(htmlResponse("<p>short</p>"));
    fetchMock.mockImplementation(async () => jsonResponse([{ content: "short", status: 200 }]));
    expect(await scrapePage(URL)).toBeNull();
    expect(fetchMock).toHaveBeenCalledTimes(4);
  });

  it.each(["application/json", "text/plain"])("escalates non-HTML direct %s responses", async (contentType) => {
    fetchMock.mockResolvedValueOnce(new Response(CONTENT, { headers: { "Content-Type": contentType } }));
    fetchMock.mockResolvedValueOnce(jsonResponse([{ content: CONTENT, status: 200 }]));
    expect((await scrapePage(URL))?.provider).toBe("spider-smart");
  });

  it("escalates direct HTTP 403 even when the body contains usable text", async () => {
    fetchMock.mockResolvedValueOnce(new Response(`<p>${CONTENT}</p>`, { status: 403, headers: { "Content-Type": "text/html" } }));
    fetchMock.mockResolvedValueOnce(jsonResponse([{ content: CONTENT, status: 200 }]));
    expect((await scrapePage(URL))?.provider).toBe("spider-smart");
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it.each([404, 410])("treats a direct HTTP %s as a dead page without paying Spider", async (status) => {
    fetchMock.mockResolvedValueOnce(new Response("<p>Page not found</p>", { status, headers: { "Content-Type": "text/html" } }));
    expect(await scrapePage(URL)).toBeNull();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("starts at Spider chrome when startAt says so, through fetchUrl too", async () => {
    fetchMock.mockImplementation(async () => jsonResponse([{ content: CONTENT, status: 200, costs: { total_cost: 0.005 } }]));
    expect(await scrapePage(URL, { startAt: "spider-chrome" })).toEqual({ content: CONTENT, provider: "spider-chrome", costUsd: 0.005 });
    expect(await fetchUrl(URL, { startAt: "spider-chrome" })).toBe(CONTENT);
    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([SPIDER_URL, SPIDER_URL]);
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body)).request).toBe("chrome");
  });

  it("escalates a thrown direct request", async () => {
    fetchMock.mockRejectedValueOnce(new Error("Direct timeout"));
    fetchMock.mockResolvedValueOnce(jsonResponse([{ content: CONTENT, status: 200 }]));
    expect((await scrapePage(URL))?.provider).toBe("spider-smart");
  });

  it.each([true, false])("uses direct only without a Spider key (usable: %s)", async (usable) => {
    vi.stubEnv("SPIDER_API_KEY", "");
    fetchMock.mockResolvedValueOnce(htmlResponse(`<p>${usable ? CONTENT : "Enable JavaScript"}</p>`));
    expect(await scrapePage(URL, OPTIONS)).toEqual(usable ? { content: CONTENT.slice(0, 300), provider: "direct", costUsd: 0 } : null);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("does not fetch after an expired deadline", async () => {
    expect(await scrapePage(URL, { ...OPTIONS, deadlineAt: Date.now() - 1 })).toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("stops remaining steps when the deadline expires during smart", async () => {
    let now = 1_000;
    vi.spyOn(Date, "now").mockImplementation(() => now);
    fetchMock.mockResolvedValueOnce(htmlResponse("<p>short</p>"));
    fetchMock.mockImplementationOnce(async () => { now = 1_501; return jsonResponse([{ content: "short", status: 200 }]); });
    expect(await scrapePage(URL, { ...OPTIONS, deadlineAt: 1_500 })).toBeNull();
    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([URL, SPIDER_URL]);
  });

  it("caps every step timeout by the remaining deadline", async () => {
    let now = 1_000;
    vi.spyOn(Date, "now").mockImplementation(() => now);
    const timeout = vi.spyOn(AbortSignal, "timeout");
    fetchMock.mockImplementation(async () => {
      now += 100;
      if (fetchMock.mock.calls.length === 1) return htmlResponse("<p>short</p>");
      return jsonResponse([{ content: fetchMock.mock.calls.length === 4 ? CONTENT : "short", status: 200 }]);
    });
    expect((await scrapePage(URL, { ...OPTIONS, deadlineAt: 1_500 }))?.provider).toBe("spider-unblocker");
    expect(timeout.mock.calls.map(([ms]) => ms)).toEqual([500, 400, 300, 200]);
  });

  it.each([49, 50])("fetchUrl uses the shared word minimum (words: %s)", async (count) => {
    vi.stubEnv("SPIDER_API_KEY", "");
    fetchMock.mockResolvedValueOnce(htmlResponse(`<p>${words(count)}</p>`));
    expect(await fetchUrl(URL)).toBe(count === 50 ? words(50) : null);
  });

  it("keeps uncapped scrape content and fetchUrl's default and custom maxChars", async () => {
    const text = words(3000);
    fetchMock.mockImplementation(async () => htmlResponse(`<p>${text}</p>`));
    expect(await scrapePage(URL)).toEqual({ content: text, provider: "direct", costUsd: 0 });
    expect(await scrapePage(URL, { maxChars: 100 })).toEqual({ content: text.slice(0, 100), provider: "direct", costUsd: 0 });
    expect(await fetchUrl(URL)).toBe(text.slice(0, 15_000));
    expect(await fetchUrl(URL, { renderJs: true, waitForSecs: 3, maxChars: 30_000 })).toBe(text);
  });
});
