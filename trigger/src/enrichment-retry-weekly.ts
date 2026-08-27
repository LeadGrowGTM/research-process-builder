import { schedules, logger } from "@trigger.dev/sdk";
import { runEnrichmentRetryPass } from "./pipeline/enrichment-retry.js";
import { workflowGate } from "./modules/workflow-gate.js";

export const enrichmentRetryWeekly = schedules.task({
  id: "enrichment-retry-weekly",
  cron: {
    // Sunday 6 AM ET — quiet slot, before the daily pipelines
    pattern: "0 6 * * 0",
    timezone: "America/New_York",
  },
  retry: {
    maxAttempts: 2,
    factor: 2,
    minTimeoutInMs: 10_000,
    maxTimeoutInMs: 120_000,
    randomize: true,
  },
  run: async (payload) => {
    logger.info("Starting weekly enrichment retry pass", {
      scheduleId: payload.scheduleId,
      lastRun: payload.lastTimestamp?.toISOString() ?? "none",
    });

    // Workflow gate check
    const gate = await workflowGate("leadgrow", "rpb-enrichment-retry");
    if (!gate.active) {
      logger.info("Enrichment retry weekly GATED - skipping", { reason: gate.reason });
      return { skipped: true, reason: gate.reason };
    }

    const result = await runEnrichmentRetryPass();

    return result;
  },
});
