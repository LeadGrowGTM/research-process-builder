import type { EnrichedRecord } from "./types.js";
import { requestSignal } from "./request-signal.js";

export async function pushToWebhook(
  enriched: EnrichedRecord[],
  dateStr: string,
  webhookUrl: string,
  webhookAuthToken: string,
  signal?: AbortSignal
): Promise<number> {
  if (!webhookUrl) return 0;

  let sent = 0;
  for (const record of enriched) {
    if (signal?.aborted) break;
    try {
      const headers: Record<string, string> = {
        "Content-Type": "application/json",
      };
      if (webhookAuthToken) {
        headers["x-clay-webhook-auth"] = webhookAuthToken;
      }

      const resp = await fetch(webhookUrl, {
        method: "POST",
        headers,
        body: JSON.stringify({ date: dateStr, ...record }),
        signal: requestSignal(10_000, signal),
      });
      if (resp.ok) sent++;
    } catch {
      // continue with next record
    }
  }
  return sent;
}
