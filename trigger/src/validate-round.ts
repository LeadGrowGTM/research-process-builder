import { task, logger } from "@trigger.dev/sdk";
import { runFundingPipeline } from "./pipeline/pipeline.js";
import { SERIES_B_CONFIG, SERIES_C_CONFIG } from "./pipeline/round-configs.js";
import type { RoundConfig, EnrichedRecord } from "./pipeline/types.js";

const CONFIGS: Record<string, RoundConfig> = {
  B: SERIES_B_CONFIG,
  C: SERIES_C_CONFIG,
};

const MAX_DURATION_S = 1200;
// The pipeline keeps a 5-minute delivery reserve, so a round needs at least this much left to do any work.
const MIN_ROUND_S = 360;

function summarize(companies: EnrichedRecord[]) {
  return companies.map((c) => ({
    company_name: c.company_name,
    company_domain: c.company_domain,
    amount_raised: c.amount_raised,
    round_type: c.round_type,
    source_url: c.source_url,
    lead_investors: c.lead_investors,
  }));
}

export const validateRound = task({
  id: "validate-round",
  retry: { maxAttempts: 1 },
  maxDuration: MAX_DURATION_S,
  run: async (payload: { round?: "B" | "C"; tbs?: string; maxEnrich?: number }) => {
    const rounds = payload.round ? [payload.round] : (["B", "C"] as const);
    const today = new Date().toISOString().split("T")[0];
    const tbs = payload.tbs ?? "qdr:m";
    const maxEnrich = payload.maxEnrich ?? 25;
    const allResults: Record<string, unknown> = {};
    const deadlineAt = Date.now() + (MAX_DURATION_S - 60) * 1000;

    for (const round of rounds) {
      if (deadlineAt - Date.now() < MIN_ROUND_S * 1000) {
        logger.warn(`Skipping Series ${round}: run budget exhausted`);
        allResults[`series_${round}`] = { round, skipped: "budget" };
        continue;
      }
      const config = CONFIGS[round];
      logger.info(`Validation run: Series ${round}`, { tbs, maxEnrich });

      const result = await runFundingPipeline({
        roundConfig: config,
        pipelineId: `validate_series_${round.toLowerCase()}`,
        tbs,
        date: today,
        skipEnrich: false,
        maxEnrich,
        dryRun: true,
        deadlineAt,
      });

      const summary = summarize(result.companies);

      logger.info(`Series ${round} complete: ${summary.length} companies`);
      for (const c of summary) {
        logger.info(`  [${round}] ${c.company_name} | ${c.amount_raised} | ${c.company_domain}`);
      }

      allResults[`series_${round}`] = {
        round,
        companyCount: result.companyCount,
        stats: result.stats,
        companies: summary,
      };
    }

    return { date: today, results: allResults };
  },
});
