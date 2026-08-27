import { schedules, logger } from "@trigger.dev/sdk";
import { runJobsPipeline } from "./pipeline/jobs-pipeline.js";
import { workflowGate } from "./modules/workflow-gate.js";

export const jobsWeekly = schedules.task({
  id: "jobs-weekly",
  // Monday + Thursday 7am ET — job listings rotate frequently
  cron: {
    pattern: "0 7 * * 1,4",
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
    const date = payload?.timestamp
      ? payload.timestamp.toISOString().split("T")[0]
      : new Date().toISOString().split("T")[0];

    logger.info("Starting 80.lv jobs pipeline", {
      date,
      scheduleId: payload?.scheduleId ?? "manual",
      lastRun: payload?.lastTimestamp?.toISOString() ?? "none",
    });

    // Workflow gate check
    const gate = await workflowGate("motorica", "game-jobs");
    if (!gate.active) {
      logger.info("Jobs weekly GATED - skipping", { reason: gate.reason });
      return { skipped: true, reason: gate.reason };
    }

    const result = await runJobsPipeline({ dryRun: false, date });

    // TODO (Charles): Add Slack webhook notification here after pipeline completes.
    // Send a Monday morning signal snapshot to the Motorica alerts channel so
    // Jamie + Nathan can review before sequences fire (human-in-loop agreed on 2026-05-08 call).
    // Payload should include: new high-signal jobs, company names, HubSpot links, and
    // a suggested action (reach out / hold). Mirror this pattern in the series-a-weekly
    // and game-signals tasks too — same channel, same format.
    // Slack webhook env var: SLACK_MOTORICA_WEBHOOK_URL

    return result;
  },
});
