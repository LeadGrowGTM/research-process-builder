import { schedules, logger } from "@trigger.dev/sdk";
import { runFundingPipeline } from "./pipeline/pipeline.js";
import { SERIES_B_CONFIG } from "./pipeline/round-configs.js";
import { workflowGate } from "./modules/workflow-gate.js";

export const seriesBDaily = schedules.task({
  id: "series-b-daily",
  cron: {
    pattern: "0 7 * * *",
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
    const scheduledDate = payload.timestamp.toISOString().split("T")[0];

    logger.info("Starting Series B daily pipeline", {
      scheduledDate,
      scheduleId: payload.scheduleId,
      lastRun: payload.lastTimestamp?.toISOString() ?? "none",
    });

    // Workflow gate check
    const gate = await workflowGate("leadgrow", "funding-series-b");
    if (!gate.active) {
      logger.info("Series B daily GATED - skipping", { reason: gate.reason });
      return { skipped: true, reason: gate.reason };
    }

    const result = await runFundingPipeline({
      roundConfig: SERIES_B_CONFIG,
      pipelineId: "series_b_daily",
      tbs: "qdr:d",
      date: scheduledDate,
      skipEnrich: false,
      maxEnrich: 20,
      dryRun: false,
      skipKnownCompanies: true,
      skipKnownDays: 7,
    });

    return {
      date: result.date,
      companyCount: result.companyCount,
      durationMs: result.stats.durationMs,
      stats: result.stats,
    };
  },
});
