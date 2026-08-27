import { schedules, logger } from "@trigger.dev/sdk";
import { runNewsLaunchPipeline } from "./pipeline/product-launches-news.js";
import { workflowGate } from "./modules/workflow-gate.js";

export const productLaunchesNewsDaily = schedules.task({
  id: "product-launches-news-daily",
  cron: {
    pattern: "0 10 * * *",
    timezone: "America/New_York", // 10 AM ET, after PH task
  },
  retry: {
    maxAttempts: 3,
    factor: 2,
    minTimeoutInMs: 10_000,
    maxTimeoutInMs: 120_000,
    randomize: true,
  },
  run: async (payload) => {
    const scheduledDate = payload?.timestamp
      ? payload.timestamp.toISOString().split("T")[0]
      : new Date().toISOString().split("T")[0];

    logger.info("Starting product launches news daily pipeline", {
      scheduledDate,
      scheduleId: payload?.scheduleId ?? "manual",
      lastRun: payload?.lastTimestamp?.toISOString() ?? "none",
    });

    // Workflow gate check
    const gate = await workflowGate("leadgrow", "product-launches");
    if (!gate.active) {
      logger.info("Product launches news daily GATED - skipping", { reason: gate.reason });
      return { skipped: true, reason: gate.reason };
    }

    const result = await runNewsLaunchPipeline({
      date: scheduledDate,
      tbs: "qdr:d",
      skipSerper: false,
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
