/**
 * Unit tests for workflow-gate module.
 * Tests the fail-open pattern and paused state handling.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { workflowGate } from "./workflow-gate.js";
import { logger } from "@trigger.dev/sdk";

// Mock logger to prevent output during tests
vi.mock("@trigger.dev/sdk", () => ({
  logger: {
    info: vi.fn(),
    warn: vi.fn(),
  },
}));

describe("workflowGate", () => {
  const clientSlug = "test-client";
  const workflow = "test-workflow";
  const supabaseUrl = "https://example.supabase.co";
  const supabaseKey = "test-key";

  beforeEach(() => {
    process.env.SUPABASE_PROJECT_URL = supabaseUrl;
    process.env.SUPABASE_KEY = supabaseKey;
    vi.clearAllMocks();
  });

  afterEach(() => {
    delete process.env.SUPABASE_PROJECT_URL;
    delete process.env.SUPABASE_KEY;
  });

  it("should return active=true when status is active", async () => {
    global.fetch = vi.fn(() =>
      Promise.resolve({
        ok: true,
        json: async () => [
          {
            status: "active",
            pause_reason: null,
            paused_at: null,
          },
        ],
      })
    ) as any;

    const result = await workflowGate(clientSlug, workflow);

    expect(result.active).toBe(true);
    expect(result.reason).toBe("active");
  });

  it("should return active=false when status is paused", async () => {
    global.fetch = vi.fn(() =>
      Promise.resolve({
        ok: true,
        json: async () => [
          {
            status: "paused",
            pause_reason: "manual pause for testing",
            paused_at: "2026-08-27T10:00:00Z",
          },
        ],
      })
    ) as any;

    const result = await workflowGate(clientSlug, workflow);

    expect(result.active).toBe(false);
    expect(result.reason).toContain("paused");
    expect(result.reason).toContain("manual pause for testing");
  });

  it("should fail-open when row is missing (no registry entry)", async () => {
    global.fetch = vi.fn(() =>
      Promise.resolve({
        ok: true,
        json: async () => [],
      })
    ) as any;

    const result = await workflowGate(clientSlug, workflow);

    expect(result.active).toBe(true);
    expect(result.reason).toContain("fail-open");
    expect(result.reason).toContain("no registry row");
  });

  it("should fail-open when table/query error occurs", async () => {
    global.fetch = vi.fn(() =>
      Promise.resolve({
        ok: false,
        status: 404,
        json: async () => ({ error: "Not Found" }),
      })
    ) as any;

    const result = await workflowGate(clientSlug, workflow);

    expect(result.active).toBe(true);
    expect(result.reason).toContain("fail-open");
    expect(result.reason).toContain("404");
  });

  it("should fail-open when fetch throws an error", async () => {
    global.fetch = vi.fn(() =>
      Promise.reject(new Error("Network error"))
    ) as any;

    const result = await workflowGate(clientSlug, workflow);

    expect(result.active).toBe(true);
    expect(result.reason).toContain("fail-open");
    expect(result.reason).toContain("Network error");
  });

  it("should fail-open when Supabase is not configured", async () => {
    delete process.env.SUPABASE_PROJECT_URL;
    delete process.env.SUPABASE_KEY;

    const result = await workflowGate(clientSlug, workflow);

    expect(result.active).toBe(true);
    expect(result.reason).toContain("fail-open");
    expect(result.reason).toContain("not configured");
  });

  it("should log info when paused with reason", async () => {
    global.fetch = vi.fn(() =>
      Promise.resolve({
        ok: true,
        json: async () => [
          {
            status: "paused",
            pause_reason: "client churn - retargeting",
            paused_at: "2026-08-26T08:00:00Z",
          },
        ],
      })
    ) as any;

    await workflowGate(clientSlug, workflow);

    expect(logger.info).toHaveBeenCalledWith(
      "workflow-gate: PAUSED - skipping run",
      expect.objectContaining({
        clientSlug,
        workflow,
      })
    );
  });

  it("should log warn when query fails", async () => {
    global.fetch = vi.fn(() =>
      Promise.resolve({
        ok: false,
        status: 500,
      })
    ) as any;

    await workflowGate(clientSlug, workflow);

    expect(logger.warn).toHaveBeenCalledWith(
      "workflow-gate: registry query failed - failing OPEN",
      expect.objectContaining({
        clientSlug,
        workflow,
      })
    );
  });

  it("should correctly encode client_slug and workflow in query string", async () => {
    const encodedSlug = "test/client";
    const encodedWorkflow = "test&workflow";

    global.fetch = vi.fn(() =>
      Promise.resolve({
        ok: true,
        json: async () => [{ status: "active", pause_reason: null, paused_at: null }],
      })
    ) as any;

    await workflowGate(encodedSlug, encodedWorkflow);

    expect(global.fetch).toHaveBeenCalledWith(
      expect.stringContaining("client_slug=eq.test%2Fclient"),
      expect.any(Object)
    );
    expect(global.fetch).toHaveBeenCalledWith(
      expect.stringContaining("workflow=eq.test%26workflow"),
      expect.any(Object)
    );
  });
});
