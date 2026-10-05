/**
 * Founder waterfall for the funding-signal pipeline.
 *
 * Only runs for icp_fit strong|moderate companies (wired into
 * signal-bank-daily after ICP classification), capped per run by
 * FOUNDER_WATERFALL_CAP (default 50 companies).
 *
 * (a) AI Ark people search by domain for founder titles, max 3 founders.
 * (b) Email waterfall per founder: QuickEnrich (by linkedin_url) -> AI Ark
 *     email find -> Kitt -> pattern guesser, each candidate validated with
 *     MillionVerifier. A provider missing its key is skipped, never fatal.
 * (c) Persisted to founder_contacts, unique (company_domain, linkedin_url).
 *
 * Provider pieces are ported from the read-only reference waterfall
 * (classifyStatus/fetchProvider, quickEnrichFind, aiArkFind, kittFind,
 * mvValidate, email guesser). No new dependencies.
 */

export const FOUNDER_TITLES = ["Founder", "Co-Founder"];
export const MAX_FOUNDERS_PER_COMPANY = 2;

const AIARK_BASE = "https://api.ai-ark.com/api/developer-portal/v1";
const UA =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36";

// ── Cost recorder ─────────────────────────────────────────────────────────────

export interface CostRecorder {
  record(provider: string, costUsd: number): void;
  deadlineAt?: number;
  remainingCalls?: number;
  allowCall?: () => boolean;
}

// Hard upper bound includes retries, discovery, finders, and verification calls.
export const MAX_FOUNDER_PROVIDER_CALLS_PER_COMPANY = 100;
export const MAX_FOUNDER_PROVIDER_CALLS_PER_RUN = 500;

export function logCostRecorder(): CostRecorder {
  return {
    record(provider: string, _costUsd: number): void {
      console.log(`[founders] provider_call=${provider}`);
    },
  };
}

// ── Failure classification + fetch with bounded retry ─────────────────────────

export type ProviderFailure =
  | "rate_limited"
  | "exhausted"
  | "auth"
  | "transient"
  | "not_found";

export function classifyStatus(status: number): ProviderFailure | "ok" {
  if (status >= 200 && status < 300) return "ok";
  if (status === 429) return "rate_limited";
  if (status === 401 || status === 403) return "auth";
  if (status === 404) return "not_found";
  if (status >= 400 && status < 500) return "exhausted";
  return "transient";
}

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

export interface ProviderFetchResult {
  res: Response | null;
  failure: ProviderFailure | null;
}

export async function fetchProvider(url: string, init?: RequestInit, budget?: CostRecorder, beforeAttempt?: () => Promise<void>): Promise<ProviderFetchResult> {
  let transientRetried = false;
  for (let attempt = 0; attempt < 3; attempt++) {
    if (init?.signal?.aborted || (budget?.deadlineAt !== undefined && Date.now() >= budget.deadlineAt) || (budget?.remainingCalls !== undefined && budget.remainingCalls <= 0)) {
      return { res: null, failure: "exhausted" };
    }
    await beforeAttempt?.();
    if (init?.signal?.aborted || (budget?.deadlineAt !== undefined && Date.now() >= budget.deadlineAt)) {
      return { res: null, failure: "exhausted" };
    }
    if (budget?.allowCall && !budget.allowCall()) return { res: null, failure: "exhausted" };
    if (budget?.remainingCalls !== undefined) budget.remainingCalls--;
    let res: Response;
    try {
      const remaining = budget?.deadlineAt === undefined ? 30_000 : Math.min(30_000, Math.max(1, budget.deadlineAt - Date.now()));
      const timeout = AbortSignal.timeout(remaining);
      res = await fetch(url, { ...init, signal: init?.signal ? AbortSignal.any([init.signal, timeout]) : timeout });
    } catch {
      if (!transientRetried) {
        transientRetried = true;
        await sleep(500);
        continue;
      }
      return { res: null, failure: "transient" };
    }
    if (res.ok) return { res, failure: null };
    const kind = classifyStatus(res.status);
    if (kind === "ok") return { res, failure: null };
    if (kind === "transient" && !transientRetried) {
      transientRetried = true;
      await sleep(500);
      continue;
    }
    return { res, failure: kind };
  }
  return { res: null, failure: "transient" };
}

// ── (a) AI Ark people search by domain ────────────────────────────────────────

export interface FounderCandidate {
  first_name: string;
  last_name: string;
  title: string;
  linkedin_url: string | null;
}

export interface PeopleSearchOutcome {
  people: FounderCandidate[];
  /** Set only after a request was sent. A missing key is not a call and not proof that nobody exists. */
  failure: ProviderFailure | null;
  called: boolean;
}

function withoutContacts(value: string): string {
  return value
    .replace(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/gi, " ")
    .replace(/(?:\+|00)?\d[\d\s().-]{8,}\d/g, " ")
    .replace(/\s{2,}/g, " ")
    .trim();
}

/** Public name or title. Emails and phone numbers never leave this module on those fields. */
function publicLabel(raw: unknown): string {
  if (typeof raw !== "string") return "";
  const cleaned = withoutContacts(raw).trim();
  if (!cleaned || /^(n\/a|none|null|unknown|undefined|not_stated|not_found|unclear)$/i.test(cleaned)) return "";
  if (cleaned.startsWith("🔒")) return "";
  return cleaned;
}

export async function aiArkSearchPeopleOutcome(
  domain: string,
  titles: string[] = FOUNDER_TITLES,
  apiKey: string = process.env.AI_ARK_API_KEY ?? "",
  cost?: CostRecorder
): Promise<PeopleSearchOutcome> {
  if (!apiKey || !domain) return { people: [], failure: null, called: false };
  const body = {
    page: 0,
    size: 25,
    account: { domain: { all: { include: [domain] } } },
    contact: {
      experience: {
        current: {
          title: {
            any: { include: { mode: "SMART", content: titles } },
          },
        },
      },
    },
  };
  const { res, failure } = await fetchProvider(`${AIARK_BASE}/people`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-TOKEN": apiKey,
      "User-Agent": UA,
    },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(30_000),
  }, cost);
  if (!res || !res.ok) return { people: [], failure: failure ?? "transient", called: true };
  const data = (await res.json().catch(() => null)) as {
    content?: Array<Record<string, unknown>>;
  } | null;
  const rows = data?.content ?? [];
  const out: FounderCandidate[] = [];
  const seen = new Set<string>();
  for (const row of rows) {
    const prof = (row.profile ?? {}) as Record<string, unknown>;
    const link = (row.link ?? {}) as Record<string, unknown>;
    const first = publicLabel(prof.first_name);
    const last = publicLabel(prof.last_name);
    const linkedinUrl = normalizeFounderLinkedin(link.linkedin);
    const title = publicLabel(prof.title);
    if (!first || !last || !linkedinUrl || seen.has(linkedinUrl) || !/\bfounder\b/i.test(title)) continue;
    seen.add(linkedinUrl);
    out.push({
      first_name: first,
      last_name: last,
      title,
      linkedin_url: linkedinUrl,
    });
    if (out.length >= MAX_FOUNDERS_PER_COMPANY) break;
  }
  return { people: out, failure: null, called: true };
}

export async function aiArkSearchPeople(
  domain: string,
  titles: string[] = FOUNDER_TITLES,
  apiKey: string = process.env.AI_ARK_API_KEY ?? "",
  cost?: CostRecorder
): Promise<FounderCandidate[]> {
  return (await aiArkSearchPeopleOutcome(domain, titles, apiKey, cost)).people;
}

function normalizeFounderLinkedin(raw: unknown): string | null {
  if (typeof raw !== "string") return null;
  try {
    const url = new URL(raw);
    if (!/^https?:$/.test(url.protocol) || !/^(www\.)?linkedin\.com$/i.test(url.hostname) || !/^\/in\/[^/]+\/?$/.test(url.pathname)) return null;
    return `https://linkedin.com${url.pathname.replace(/\/$/, "").toLowerCase()}`;
  } catch {
    return null;
  }
}

// ── (b) Email waterfall ───────────────────────────────────────────────────────

export interface FounderKeys {
  quickEnrich?: string;
  aiArk?: string;
  kitt?: string;
  millionVerifier?: string;
}

export function founderKeysFromEnv(): FounderKeys {
  return {
    quickEnrich: process.env.QUICKENRICH_API_KEY ?? "",
    aiArk: process.env.AI_ARK_API_KEY ?? "",
    kitt: process.env.TRYKITT_API_KEY ?? "",
    millionVerifier: process.env.MILLION_VERIFIER_API_KEY ?? "",
  };
}

export function isFoundersConfigured(): boolean {
  return Boolean(process.env.AI_ARK_API_KEY);
}

export interface FinderOutcome {
  email: string | null;
  failure?: ProviderFailure;
}

const MV_RESULT_MAP: Record<number, string> = {
  1: "valid",
  2: "catch_all",
  3: "unknown",
  4: "error",
  5: "disposable",
  6: "invalid",
};

export async function mvValidate(
  email: string,
  apiKey: string,
  cost?: CostRecorder
): Promise<string> {
  if (!apiKey) return "unverified";
  const { res } = await fetchProvider(
    `https://api.millionverifier.com/api/v3/?api=${encodeURIComponent(apiKey)}&email=${encodeURIComponent(email)}&timeout=20`,
    { headers: { "User-Agent": UA }, signal: AbortSignal.timeout(30_000) }, cost
  );
  cost?.record("millionverifier", 0);
  if (!res || !res.ok) return "unverified";
  const data = (await res.json().catch(() => null)) as { resultcode?: number } | null;
  return MV_RESULT_MAP[data?.resultcode ?? 0] ?? "error";
}

export async function quickEnrichFind(
  linkedinUrl: string | null | undefined,
  apiKey: string,
  cost?: CostRecorder
): Promise<FinderOutcome> {
  if (!linkedinUrl || !apiKey) return { email: null };
  const params = new URLSearchParams({ linkedin_url: linkedinUrl });
  const { res, failure } = await fetchProvider(
    `https://app.quickenrich.io/api/employees/search?${params}`,
    {
      headers: { Authorization: `Bearer ${apiKey}`, Accept: "application/json" },
      signal: AbortSignal.timeout(30_000),
    }, cost
  );
  cost?.record("quickenrich", 0);
  if (!res || !res.ok) return { email: null, failure: failure ?? "transient" };
  const data = (await res.json().catch(() => null)) as {
    success?: boolean;
    data?: { email?: string };
  } | null;
  const email = data?.data?.email;
  if (!email || ["n/a", "none", ""].includes(email.toLowerCase())) {
    return { email: null, failure: "not_found" };
  }
  return { email };
}

export async function aiArkFind(
  firstName: string,
  lastName: string,
  domain: string,
  apiKey: string,
  cost?: CostRecorder
): Promise<FinderOutcome> {
  if (!firstName || !lastName || !domain || !apiKey) return { email: null };
  const body = {
    page: 0,
    size: 5,
    account: { domain: { all: { include: [domain] } } },
    contact: { name: { any: { include: [`${firstName} ${lastName}`] } } },
  };
  const { res, failure } = await fetchProvider(`${AIARK_BASE}/people`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-TOKEN": apiKey,
      "User-Agent": UA,
    },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(30_000),
  }, cost);
  cost?.record("aiark", 0);
  if (!res || !res.ok) return { email: null, failure: failure ?? "transient" };
  const data = (await res.json().catch(() => null)) as {
    content?: Array<Record<string, unknown>>;
  } | null;
  for (const person of data?.content ?? []) {
    const prof = (person.profile ?? {}) as Record<string, unknown>;
    const full = normalizeGuessName(String(prof.full_name ?? `${prof.first_name ?? ""} ${prof.last_name ?? ""}`));
    const expected = normalizeGuessName(`${firstName} ${lastName}`);
    const email = typeof prof.email === "string" ? prof.email.trim() : "";
    if (full === expected && /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) return { email };
  }
  return { email: null, failure: "not_found" };
}

export async function kittFind(
  firstName: string,
  lastName: string,
  domain: string,
  linkedinUrl: string | null | undefined,
  apiKey: string,
  cost?: CostRecorder
): Promise<FinderOutcome> {
  if (!firstName || !lastName || !domain || !apiKey) return { email: null };
  const payload: Record<string, unknown> = {
    fullName: `${firstName} ${lastName}`.trim(),
    domain,
    realtime: true,
    strictNameMatches: true,
  };
  if (linkedinUrl) payload.linkedinStandardProfileURL = linkedinUrl;
  const { res, failure } = await fetchProvider("https://api.trykitt.ai/job/find_email", {
    method: "POST",
    headers: { "Content-Type": "application/json", "x-api-key": apiKey },
    body: JSON.stringify(payload),
    signal: AbortSignal.timeout(30_000),
  }, cost);
  cost?.record("kitt", 0);
  if (!res || !res.ok) return { email: null, failure: failure ?? "transient" };
  const data = (await res.json().catch(() => null)) as {
    email?: string;
  } | null;
  const email = data?.email;
  if (!email || ["no-results-found", "none", ""].includes(email.toLowerCase())) {
    return { email: null, failure: "not_found" };
  }
  return { email };
}

// ── Pattern guesser (last rung) ───────────────────────────────────────────────

export function normalizeGuessName(raw: string | null | undefined): string {
  return String(raw ?? "")
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]/g, "");
}

export function guessLocalParts(first: string, last: string): string[] {
  const fi = first.slice(0, 1);
  const li = last.slice(0, 1);
  const parts: string[] = [];
  if (first && last) {
    parts.push(`${first}.${last}`, `${fi}${last}`);
  }
  if (first) parts.push(first);
  if (first && last) {
    parts.push(
      `${first}${last}`,
      `${first}_${last}`,
      `${fi}.${last}`,
      `${first}${li}`,
      `${last}${fi}`
    );
  }
  if (last) parts.push(last);
  if (first && last) parts.push(`${last}.${first}`);
  return parts.filter(Boolean);
}

export async function guessEmail(
  firstName: string,
  lastName: string,
  domain: string,
  validate: (email: string) => Promise<string>,
  maxChecks = 10,
  tried?: Set<string>
): Promise<{ email: string | null; status: string | null; pattern: string | null }> {
  const first = normalizeGuessName(firstName);
  const last = normalizeGuessName(lastName);
  const cleanDomain = String(domain ?? "")
    .trim()
    .toLowerCase()
    .replace(/^[a-z]+:\/\//, "")
    .replace(/^www\./, "")
    .split(/[/?#:]/)[0];
  if (!cleanDomain || (!first && !last)) return { email: null, status: null, pattern: null };
  const seen = new Set<string>();
  let checked = 0;
  for (const local of guessLocalParts(first, last)) {
    const email = `${local}@${cleanDomain}`;
    if (seen.has(email)) continue;
    seen.add(email);
    if (tried?.has(email)) continue;
    if (checked >= maxChecks) break;
    checked++;
    const verdict = await validate(email);
    if (verdict === "valid") {
      return { email, status: verdict, pattern: local };
    }
  }
  return { email: null, status: null, pattern: null };
}

// ── Waterfall engine ──────────────────────────────────────────────────────────

export interface FounderEmailResult {
  email: string | null;
  email_status: string | null;
  email_provider: string | null;
  waterfall_path: string[];
}

const SENDABLE = new Set(["valid"]);

export async function runFounderWaterfall(
  founder: FounderCandidate,
  domain: string,
  keys: FounderKeys = founderKeysFromEnv(),
  cost: CostRecorder = logCostRecorder()
): Promise<FounderEmailResult> {
  const path: string[] = [];
  const millionVerifierKey = keys.millionVerifier;
  if (!millionVerifierKey) {
    return {
      email: null,
      email_status: null,
      email_provider: null,
      waterfall_path: ["millionverifier:skipped"],
    };
  }
  const tried = new Set<string>();
  const validate = async (email: string): Promise<string> => {
    tried.add(email.toLowerCase());
    return mvValidate(email, millionVerifierKey, cost);
  };

  const consider = async (
    email: string | null,
    source: string,
    failure?: ProviderFailure
  ): Promise<FounderEmailResult | null> => {
    if (!email) {
      path.push(`${source}:${failure ?? "not_found"}`);
      return null;
    }
    email = email.trim().toLowerCase();
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
      path.push(`${source}:invalid_format`);
      return null;
    }
    if (tried.has(email.toLowerCase())) {
      path.push(`${source}:already_tried`);
      return null;
    }
    const status = await validate(email);
    path.push(`${source}:${status}`);
    if (SENDABLE.has(status)) {
      return { email, email_status: status, email_provider: source, waterfall_path: [...path] };
    }
    return null;
  };

  // Rung 1: QuickEnrich by linkedin_url
  if (keys.quickEnrich && founder.linkedin_url) {
    const found = await quickEnrichFind(founder.linkedin_url, keys.quickEnrich, cost);
    const done = await consider(found.email, "quickenrich", found.failure);
    if (done) return done;
  } else {
    path.push("quickenrich:skipped");
  }

  // Rung 2: AI Ark email find
  if (keys.aiArk) {
    const found = await aiArkFind(
      founder.first_name,
      founder.last_name,
      domain,
      keys.aiArk,
      cost
    );
    const done = await consider(found.email, "aiark", found.failure);
    if (done) return done;
  } else {
    path.push("aiark:skipped");
  }

  // Rung 3: Kitt
  if (keys.kitt) {
    const found = await kittFind(
      founder.first_name,
      founder.last_name,
      domain,
      founder.linkedin_url,
      keys.kitt,
      cost
    );
    const done = await consider(found.email, "kitt", found.failure);
    if (done) return done;
  } else {
    path.push("kitt:skipped");
  }

  // Rung 4: pattern guesser (each guess validated via MV, skips tried)
  const guess = await guessEmail(
    founder.first_name,
    founder.last_name,
    domain,
    (email) => validate(email),
    10,
    tried
  );
  if (guess.email && guess.status) {
    path.push(`guesser:${guess.status}`);
    return {
      email: guess.email,
      email_status: guess.status,
      email_provider: "guesser",
      waterfall_path: [...path],
    };
  }
  path.push("guesser:not_found");
  return { email: null, email_status: null, email_provider: null, waterfall_path: [...path] };
}

// ── (c) Persist + per-company runner ──────────────────────────────────────────

function supabaseRest(): { url: string; key: string } | null {
  const raw = process.env.SUPABASE_PROJECT_URL ?? process.env.SUPABASE_URL ?? "";
  const url = raw.startsWith("http") ? raw : "";
  const key =
    process.env.SUPABASE_SERVICE_ROLE_KEY ??
  process.env.SUPABASE_KEY ??
    process.env.SUPABASE_ANON_KEY ??
    "";
  if (!url || !key) return null;
  return { url, key };
}

export interface FounderContactRow {
  company_domain: string;
  full_name: string;
  title: string | null;
  linkedin_url: string;
  email: string | null;
  email_status: string | null;
  email_provider: string | null;
  waterfall_path: string[];
  found_at: string;
}

export async function persistFounderContacts(
  rows: FounderContactRow[],
  schema = "leadgrow_knowledge"
): Promise<number> {
  const rest = supabaseRest();
  if (rows.length === 0) return 0;
  if (!rest) throw new Error("Supabase is not configured for founder_contacts");
  const resp = await fetch(
    `${rest.url}/rest/v1/founder_contacts?on_conflict=company_domain,linkedin_url`,
    {
      method: "POST",
      headers: {
        apikey: rest.key,
        Authorization: `Bearer ${rest.key}`,
        "Content-Type": "application/json",
        "Accept-Profile": schema,
        "Content-Profile": schema,
        Prefer: "resolution=merge-duplicates",
      },
      body: JSON.stringify(rows),
      signal: AbortSignal.timeout(15_000),
    }
  );
  if (!resp.ok) {
    throw new Error(`founder_contacts upsert failed with HTTP ${resp.status}`);
  }
  return rows.length;
}

export async function runFoundersForCompany(
  domain: string,
  keys: FounderKeys = founderKeysFromEnv(),
  cost: CostRecorder = logCostRecorder()
): Promise<number> {
  if (!domain || !keys.aiArk) return 0;
  const budget: CostRecorder = {
    record: (provider, costUsd) => cost.record(provider, costUsd),
    deadlineAt: Math.min(cost.deadlineAt ?? Infinity, Date.now() + 60_000),
    remainingCalls: MAX_FOUNDER_PROVIDER_CALLS_PER_COMPANY,
    allowCall: () => {
      if (cost.remainingCalls !== undefined) {
        if (cost.remainingCalls <= 0) return false;
        cost.remainingCalls--;
      }
      return cost.allowCall?.() ?? true;
    },
  };
  const founders = await aiArkSearchPeople(domain, FOUNDER_TITLES, keys.aiArk, budget);
  if (founders.length === 0) return 0;
  const rows: FounderContactRow[] = [];
  for (const founder of founders) {
    if (!founder.linkedin_url) continue;
    const result = await runFounderWaterfall(founder, domain, keys, budget);
    rows.push({
      company_domain: domain,
      full_name: `${founder.first_name} ${founder.last_name}`.trim(),
      title: founder.title || null,
      linkedin_url: founder.linkedin_url,
      email: result.email,
      email_status: result.email_status,
      email_provider: result.email_provider,
      waterfall_path: result.waterfall_path,
      found_at: new Date().toISOString(),
    });
  }
  const persisted = await persistFounderContacts(rows);
  return persisted;
}
