import { schedules, logger } from "@trigger.dev/sdk";
import { runFundingPipeline } from "./pipeline/pipeline.js";
import { SERIES_A_CONFIG } from "./pipeline/round-configs.js";
import { workflowGate } from "./modules/workflow-gate.js";

const MAX_DURATION_S = 1200;

export const seriesAWeekly = schedules.task({
  id: "series-a-weekly",
  cron: {
    pattern: "0 8 * * 1",
    timezone: "America/New_York",
  },
  // Same budget as the daily task; the pipeline stops a minute early and keeps its delivery reserve.
  maxDuration: MAX_DURATION_S,
  retry: {
    maxAttempts: 3,
    factor: 2,
    minTimeoutInMs: 10_000,
    maxTimeoutInMs: 120_000,
    randomize: true,
  },
  run: async (payload) => {
    const scheduledDate = payload.timestamp.toISOString().split("T")[0];

    logger.info("Starting Series A weekly catch-up", {
      scheduledDate,
      scheduleId: payload.scheduleId,
      lastRun: payload.lastTimestamp?.toISOString() ?? "none",
    });

    // Workflow gate check
    const gate = await workflowGate("leadgrow", "funding-series-a");
    if (!gate.active) {
      logger.info("Series A weekly GATED - skipping", { reason: gate.reason });
      return { skipped: true, reason: gate.reason };
    }

    const result = await runFundingPipeline({
      roundConfig: SERIES_A_CONFIG,
      pipelineId: "series_a_weekly",
      tbs: "qdr:w",
      date: scheduledDate,
      skipEnrich: false,
      maxEnrich: 20,
      dryRun: false,
      deadlineAt: Date.now() + (MAX_DURATION_S - 60) * 1000,
    });

    return {
      date: result.date,
      companyCount: result.companyCount,
      durationMs: result.stats.durationMs,
      stats: result.stats,
    };
  },
});
