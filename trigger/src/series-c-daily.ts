import { schedules, logger } from "@trigger.dev/sdk";
import { runFundingPipeline } from "./pipeline/pipeline.js";
import { SERIES_C_CONFIG } from "./pipeline/round-configs.js";
import { workflowGate } from "./modules/workflow-gate.js";

const MAX_DURATION_S = 1200;

export const seriesCDaily = schedules.task({
  id: "series-c-daily",
  // Schedule moved to go-to-market-trigger-jobs (Smart Enrich) on 2026-10-06.
  // cron: { pattern: "10 7 * * *", timezone: "America/New_York", },
  // 100 enrichments at 5 concurrent take about 6 minutes; the pipeline stops a minute early.
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

    logger.info("Starting Series C daily pipeline", {
      scheduledDate,
      scheduleId: payload.scheduleId,
      lastRun: payload.lastTimestamp?.toISOString() ?? "none",
    });

    // Workflow gate check
    const gate = await workflowGate("leadgrow", "funding-series-c");
    if (!gate.active) {
      logger.info("Series C daily GATED - skipping", { reason: gate.reason });
      return { skipped: true, reason: gate.reason };
    }

    const result = await runFundingPipeline({
      roundConfig: SERIES_C_CONFIG,
      pipelineId: "series_c_daily",
      tbs: "qdr:d",
      date: scheduledDate,
      skipEnrich: false,
      maxEnrich: 100,
      dryRun: false,
      skipKnownCompanies: true,
      skipKnownDays: 7,
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
