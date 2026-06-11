// One-off: drain the historical enrichment backlog by firing the deployed
// enrichment-retry-weekly task in a loop until both tables scan 0 rows.
// Reuses the prod task (correct env keys, validated code, spend gate intact).
import { config } from "dotenv";
import { resolve } from "path";

config({ path: resolve("../../.env") });

const key = process.env.TRIGGER_SECRET_KEY;
if (!key) { console.error("TRIGGER_SECRET_KEY not set"); process.exit(1); }

const MAX_RUNS = 40; // safety cap (~2000 rows/table max)
const auth = { Authorization: `Bearer ${key}`, "Content-Type": "application/json" };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

let totals = { funding: { scanned: 0, enriched: 0 }, ph: { scanned: 0, enriched: 0 } };

for (let i = 1; i <= MAX_RUNS; i++) {
  const trig = await fetch("https://api.trigger.dev/api/v1/tasks/enrichment-retry-weekly/trigger", {
    method: "POST",
    headers: auth,
    body: JSON.stringify({ payload: { type: "MANUAL", timestamp: new Date().toISOString(), upcoming: [] } }),
  });
  const { id: runId } = await trig.json();
  if (!trig.ok || !runId) { console.error(`[${i}] trigger failed ${trig.status}`); process.exit(1); }
  console.log(`[${i}] fired ${runId}`);

  let run;
  for (let p = 0; p < 60; p++) {
    await sleep(20_000);
    run = await (await fetch(`https://api.trigger.dev/api/v3/runs/${runId}`, { headers: auth })).json();
    if (run.isCompleted || run.isFailed || run.isCancelled) break;
  }

  if (!run?.isSuccess) {
    console.error(`[${i}] run ended ${run?.status ?? "UNKNOWN"} — stopping. Inspect: node inspect-run.mjs ${runId}`);
    break;
  }

  const o = run.output ?? {};
  console.log(`[${i}] funding ${o.funding?.scanned ?? 0}/${o.funding?.enriched ?? 0} | ph ${o.ph?.scanned ?? 0}/${o.ph?.enriched ?? 0} (scanned/enriched)`);
  for (const k of ["funding", "ph"]) {
    totals[k].scanned += o[k]?.scanned ?? 0;
    totals[k].enriched += o[k]?.enriched ?? 0;
  }

  if ((o.funding?.scanned ?? 0) === 0 && (o.ph?.scanned ?? 0) === 0) {
    console.log("Backlog dry (or spend gate tripped — check task logs).");
    break;
  }
}

console.log("\nTOTALS:", JSON.stringify(totals));
