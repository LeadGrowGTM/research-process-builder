import { schedules, logger } from "@trigger.dev/sdk";
import { runPhLaunchPipeline } from "./pipeline/product-launches-ph.js";
import { workflowGate } from "./modules/workflow-gate.js";

export const productLaunchesPhDaily = schedules.task({
  id: "product-launches-ph-daily",
  cron: {
    // 9 AM ET -- after PH leaderboard settles (results finalize ~6-8 AM ET)
    pattern: "0 9 * * *",
    timezone: "America/New_York",
  },
  retry: {
    maxAttempts: 3,
    factor: 2,
    minTimeoutInMs: 10_000,
    maxTimeoutInMs: 120_000,
    randomize: true,
  },
  run: async (payload) => {
    // Support manual date override for testing: trigger with { "date": "2026-05-04" }
    const payloadDate = (payload as unknown as { date?: string })?.date;
    const scheduledDate = payloadDate
      ?? (payload?.timestamp ? payload.timestamp.toISOString().split("T")[0] : new Date().toISOString().split("T")[0]);

    logger.info("Starting Product Hunt daily launch pipeline", {
      scheduledDate,
      scheduleId: payload?.scheduleId ?? "manual",
      lastRun: payload?.lastTimestamp?.toISOString() ?? "none",
    });

    // Workflow gate check
    const gate = await workflowGate("leadgrow", "product-launches");
    if (!gate.active) {
      logger.info("Product launches PH daily GATED - skipping", { reason: gate.reason });
      return { skipped: true, reason: gate.reason };
    }

    const result = await runPhLaunchPipeline({
      date: scheduledDate,
      dryRun: false,
    });

    return {
      date: result.date,
      launchCount: result.launchCount,
      durationMs: result.stats.durationMs,
      stats: result.stats,
    };
  },
});
