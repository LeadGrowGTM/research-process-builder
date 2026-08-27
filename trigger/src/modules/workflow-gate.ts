/**
 * Workflow gate - per-client on/off switch for RPB signal workflows.
 *
 * Every scheduled signal task calls this at the top of its run body. The
 * switch lives in lg_market_data.signal_workflows (one row per
 * client_slug x workflow) so pausing a churned client is a single row
 * UPDATE - no code change, no deploy.
 *
 * FAIL-OPEN by design: a missing row, a missing table, or a query error
 * must never kill a live pipeline. Only an explicit status='paused' row
 * skips the run - and the skip is logged loudly so it is visible in the
 * Trigger run output.
 *
 * REST API version for RPB (no Supabase SDK dependency).
 */

import { logger } from "@trigger.dev/sdk";

export interface GateVerdict {
  active: boolean;
  reason: string;
}

/**
 * Query the workflow gate from lg_market_data.signal_workflows.
 * Returns a GateVerdict that indicates whether the workflow should proceed.
 *
 * Respects the fail-open pattern:
 * - Table missing → proceed (fail-open)
 * - Row missing → proceed (fail-open)
 * - Query error → proceed (fail-open)
 * - status='paused' → skip the run
 */
export async function workflowGate(
  clientSlug: string,
  workflow: string
): Promise<GateVerdict> {
  // Get Supabase credentials from environment
  const SUPABASE_URL = (() => {
    const u = process.env.SUPABASE_PROJECT_URL ?? process.env.SUPABASE_URL ?? "";
    return u.startsWith("http") ? u : "";
  })();

  const SUPABASE_KEY =
    process.env.SUPABASE_KEY ??
    process.env.SUPABASE_SERVICE_ROLE_KEY ??
    process.env.SUPABASE_ANON_KEY ??
    "";

  if (!SUPABASE_URL || !SUPABASE_KEY) {
    logger.warn("workflow-gate: Supabase not configured - failing OPEN", {
      clientSlug,
      workflow,
    });
    return { active: true, reason: "supabase not configured (fail-open)" };
  }

  try {
    const url =
      `${SUPABASE_URL}/rest/v1/signal_workflows` +
      `?client_slug=eq.${encodeURIComponent(clientSlug)}` +
      `&workflow=eq.${encodeURIComponent(workflow)}` +
      `&select=status,pause_reason,paused_at`;

    const resp = await fetch(url, {
      headers: {
        apikey: SUPABASE_KEY,
        Authorization: `Bearer ${SUPABASE_KEY}`,
        "Content-Type": "application/json",
        "Accept-Profile": "lg_market_data",
      },
      signal: AbortSignal.timeout(10_000),
    });

    if (!resp.ok) {
      logger.warn("workflow-gate: registry query failed - failing OPEN", {
        clientSlug,
        workflow,
        status: resp.status,
      });
      return {
        active: true,
        reason: `registry query error ${resp.status} (fail-open)`,
      };
    }

    const data: Array<{
      status: string;
      pause_reason: string | null;
      paused_at: string | null;
    }> = await resp.json();

    if (!Array.isArray(data) || data.length === 0) {
      logger.warn("workflow-gate: no registry row - failing OPEN", {
        clientSlug,
        workflow,
      });
      return { active: true, reason: "no registry row (fail-open)" };
    }

    const row = data[0];

    if (row.status === "paused") {
      logger.info("workflow-gate: PAUSED - skipping run", {
        clientSlug,
        workflow,
        paused_at: row.paused_at,
        pause_reason: row.pause_reason,
      });
      return {
        active: false,
        reason: `paused${row.pause_reason ? `: ${row.pause_reason}` : ""}`,
      };
    }

    return { active: true, reason: "active" };
  } catch (e) {
    logger.warn("workflow-gate: unexpected error - failing OPEN", {
      clientSlug,
      workflow,
      error: (e as Error).message,
    });
    return {
      active: true,
      reason: `gate threw (fail-open): ${(e as Error).message}`,
    };
  }
}
