import { task, logger } from "@trigger.dev/sdk";
import { fetchUrl } from "./pipeline/scrape.js";

const SUPABASE_URL = (() => {
  const url = process.env.SUPABASE_PROJECT_URL ?? process.env.SUPABASE_URL ?? "";
  return url.startsWith("http") ? url : "";
})();
const SUPABASE_KEY = process.env.SUPABASE_KEY ?? process.env.SUPABASE_SERVICE_ROLE_KEY ?? process.env.SUPABASE_ANON_KEY ?? "";
const TABLE = "funding_discoveries";

function headers(prefer?: string): Record<string, string> {
  const h: Record<string, string> = {
    apikey: SUPABASE_KEY,
    Authorization: `Bearer ${SUPABASE_KEY}`,
    "Content-Type": "application/json",
  };
  if (prefer) h["Prefer"] = prefer;
  return h;
}

export const backfillArticleText = task({
  id: "backfill-article-text",
  retry: { maxAttempts: 1 },
  run: async (_payload: { limit?: number }) => {
    const limit = _payload.limit ?? 100;

    const resp = await fetch(
      `${SUPABASE_URL}/rest/v1/${TABLE}?article_text=is.null&select=id,company_name,source_url&order=id.asc&limit=${limit}`,
      { headers: headers(), signal: AbortSignal.timeout(15_000) }
    );

    if (!resp.ok) {
      logger.error(`Failed to fetch rows: ${resp.status}`);
      return { success: 0, failed: 0, total: 0 };
    }

    const rows = (await resp.json()) as { id: number; company_name: string; source_url: string }[];
    logger.info(`Found ${rows.length} rows missing article_text`);

    let success = 0;
    let failed = 0;
    const CONCURRENCY = 5;

    for (let batchStart = 0; batchStart < rows.length; batchStart += CONCURRENCY) {
      const batch = rows.slice(batchStart, batchStart + CONCURRENCY);
      logger.info(`Batch ${Math.floor(batchStart / CONCURRENCY) + 1}: rows ${batchStart + 1}-${batchStart + batch.length} of ${rows.length}`);

      const results = await Promise.allSettled(
        batch.map(async (row) => {
          const text = await fetchUrl(row.source_url);
          if (!text) {
            logger.warn(`No content for ${row.company_name}`);
            return false;
          }

          const patchResp = await fetch(
            `${SUPABASE_URL}/rest/v1/${TABLE}?id=eq.${row.id}`,
            {
              method: "PATCH",
              headers: headers(),
              body: JSON.stringify({ article_text: text }),
              signal: AbortSignal.timeout(15_000),
            }
          );

          if (patchResp.ok || patchResp.status === 204) {
            logger.info(`Backfilled ${row.company_name}: ${text.length} chars`);
            return true;
          }
          logger.error(`PATCH failed for ${row.company_name}: ${patchResp.status}`);
          return false;
        })
      );

      for (const r of results) {
        if (r.status === "fulfilled" && r.value) success++;
        else failed++;
      }
    }

    logger.info(`Backfill complete: ${success} success, ${failed} failed`);
    return { success, failed, total: rows.length };
  },
});
