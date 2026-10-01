/**
 * Shared Luna client for the funding-signal pipeline.
 *
 * Model "gpt-6-luna" via OpenAI chat completions with strict JSON schema
 * responses. First attempt uses service_tier "flex"; on 429 or a
 * timeout/network failure it falls back once to standard (API tier "default").
 *
 * Pricing (per token): $0.10/M input, $0.01/M cached input, $0.50/M output.
 */

export const LUNA_MODEL = "gpt-6-luna";

export const LUNA_USD_PER_INPUT_TOKEN = 0.1 / 1_000_000;
export const LUNA_USD_PER_CACHED_INPUT_TOKEN = 0.01 / 1_000_000;
export const LUNA_USD_PER_OUTPUT_TOKEN = 0.5 / 1_000_000;

export const LUNA_FLEX_TIMEOUT_MS = 30_000;

export interface LunaUsage {
  inputTokens: number;
  cachedInputTokens: number;
  outputTokens: number;
}

export interface LunaResult<T> {
  data: T;
  usage: LunaUsage;
  costUsd: number;
  serviceTier: "flex" | "standard";
}

export function priceLunaCall(usage: LunaUsage): number {
  const fresh = Math.max(0, usage.inputTokens - usage.cachedInputTokens);
  return (
    fresh * LUNA_USD_PER_INPUT_TOKEN +
    usage.cachedInputTokens * LUNA_USD_PER_CACHED_INPUT_TOKEN +
    usage.outputTokens * LUNA_USD_PER_OUTPUT_TOKEN
  );
}

function lunaApiKey(): string {
  return process.env.OPENAI_API_KEY ?? "";
}

export function isLunaConfigured(): boolean {
  return Boolean(lunaApiKey());
}

function parseUsage(body: unknown): LunaUsage {
  const usage = (body as { usage?: Record<string, unknown> })?.usage ?? {};
  const details = (usage.prompt_tokens_details ?? {}) as Record<string, unknown>;
  const num = (v: unknown): number =>
    typeof v === "number" && Number.isFinite(v) ? v : 0;
  return {
    inputTokens: num(usage.prompt_tokens),
    cachedInputTokens: num(details.cached_tokens),
    outputTokens: num(usage.completion_tokens),
  };
}

function parseJsonContent<T>(content: string): T | null {
  const text = (content ?? "").trim();
  if (!text) return null;
  try {
    return JSON.parse(text) as T;
  } catch {
    return null;
  }
}

async function postCompletions(
  body: Record<string, unknown>,
  timeoutMs: number
): Promise<Response> {
  return fetch("https://api.openai.com/v1/chat/completions", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${lunaApiKey()}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(timeoutMs),
  });
}

/**
 * POST with flex-first, standard-fallback. Returns the response plus the
 * tier that produced it, or null when no request succeeded.
 */
async function postWithFallback(
  base: Record<string, unknown>,
  timeoutMs: number,
  deadlineAt?: number
): Promise<{ resp: Response; serviceTier: "flex" | "standard" } | null> {
  const remaining = () => deadlineAt === undefined ? timeoutMs : Math.min(timeoutMs, Math.max(0, deadlineAt - Date.now()));
  if (remaining() <= 0) return null;
  try {
    const flex = await postCompletions({ ...base, service_tier: "flex" }, remaining());
    if (flex.status !== 408 && flex.status !== 429 && flex.status < 500) {
      return { resp: flex, serviceTier: "flex" };
    }
  } catch {
    // Flex timed out or failed at transport level: fall through to standard.
  }
  if (remaining() <= 0) return null;
  try {
    const standard = await postCompletions(
      { ...base, service_tier: "default" },
      remaining()
    );
    return { resp: standard, serviceTier: "standard" };
  } catch {
    return null;
  }
}

export interface LunaJsonOptions {
  /** JSON schema name (also used as the strict schema title). */
  name: string;
  schema: Record<string, unknown>;
  systemPrompt: string;
  userPrompt: string;
  maxTokens?: number;
  temperature?: number;
  timeoutMs?: number;
  deadlineAt?: number;
}

/** Strict json_schema call. Returns null when the key is missing or the call/parses fails. */
export async function lunaJson<T>(opts: LunaJsonOptions): Promise<LunaResult<T> | null> {
  if (!lunaApiKey()) return null;
  const base = {
    model: LUNA_MODEL,
    reasoning_effort: "low",
    max_completion_tokens: opts.maxTokens ?? 500,
    messages: [
      { role: "system", content: opts.systemPrompt },
      { role: "user", content: opts.userPrompt },
    ],
    response_format: {
      type: "json_schema",
      json_schema: { name: opts.name, strict: true, schema: opts.schema },
    },
  };
  const got = await postWithFallback(base, opts.timeoutMs ?? LUNA_FLEX_TIMEOUT_MS, opts.deadlineAt);
  if (!got || !got.resp.ok) return null;
  let body: unknown;
  try {
    body = await got.resp.json();
  } catch {
    return null;
  }
  const choice = (body as { choices?: Array<{ finish_reason?: string; message?: { content?: string; refusal?: string } }> })
    ?.choices?.[0];
  if (choice?.message?.refusal || choice?.finish_reason === "length") return null;
  const content = choice?.message?.content ?? "";
  const data = parseJsonContent<T>(content);
  if (data === null) return null;
  const usage = parseUsage(body);
  return { data, usage, costUsd: priceLunaCall(usage), serviceTier: got.serviceTier };
}

export interface LunaToolDef {
  name: string;
  description: string;
  parameters: Record<string, unknown>;
}

export interface LunaChatToolCall {
  id: string;
  name: string;
  args: string;
}

export interface LunaChatResult {
  content: string | null;
  toolCalls: LunaChatToolCall[];
  usage: LunaUsage;
  serviceTier: "flex" | "standard";
}

export interface LunaChatOptions {
  name: string;
  schema: Record<string, unknown>;
  messages: Array<Record<string, unknown>>;
  tools?: LunaToolDef[];
  toolChoice?: string;
  maxTokens?: number;
  temperature?: number;
  timeoutMs?: number;
  deadlineAt?: number;
}

/** Plain chat call with optional function tools (used by domain lookup). Same flex/standard fallback. */
export async function lunaChat(opts: LunaChatOptions): Promise<LunaChatResult | null> {
  if (!lunaApiKey()) return null;
  const base: Record<string, unknown> = {
    model: LUNA_MODEL,
    reasoning_effort: "low",
    max_completion_tokens: opts.maxTokens ?? 300,
    messages: opts.messages,
    response_format: {
      type: "json_schema",
      json_schema: { name: opts.name, strict: true, schema: opts.schema },
    },
  };
  if (opts.tools && opts.tools.length > 0) {
    base.tools = opts.tools.map((t) => ({
      type: "function",
      function: { name: t.name, description: t.description, parameters: t.parameters, strict: true },
    }));
    base.tool_choice = opts.toolChoice ?? "auto";
    base.parallel_tool_calls = false;
  }
  const got = await postWithFallback(base, opts.timeoutMs ?? LUNA_FLEX_TIMEOUT_MS, opts.deadlineAt);
  if (!got || !got.resp.ok) return null;
  let body: unknown;
  try {
    body = await got.resp.json();
  } catch {
    return null;
  }
  const choice = (body as { choices?: Array<{ finish_reason?: string; message?: Record<string, unknown> }> })
    ?.choices?.[0];
  const message = choice?.message ?? {};
  if (message.refusal || choice?.finish_reason === "length") return null;
  const rawCalls = (message.tool_calls ?? []) as Array<{
    id: string;
    function?: { name?: string; arguments?: string };
  }>;
  return {
    content: (message.content as string | null) ?? null,
    toolCalls: rawCalls.map((tc) => ({
      id: tc.id,
      name: tc.function?.name ?? "",
      args: tc.function?.arguments ?? "{}",
    })),
    usage: parseUsage(body),
    serviceTier: got.serviceTier,
  };
}
