// Live check of the scrape ladder. Prints provider, words, cost and time per URL; never prints keys.
//   lg run --env prod npx -y tsx <abs>\scrape-probe.ts <url> [<url> ...]   (from pipelines\gtm-orchestrator)
import { scrapePage } from "../src/pipeline/scrape.js";

for (const url of process.argv.slice(2)) {
  const t = Date.now();
  const r = await scrapePage(url);
  const words = r ? r.content.split(/\s+/).filter(Boolean).length : 0;
  console.log(`${r?.provider ?? "DEAD"}\t${words}w\t$${(r?.costUsd ?? 0).toFixed(5)}\t${((Date.now() - t) / 1000).toFixed(1)}s\t${url}`);
  if (r) console.log(`   ${r.content.slice(0, 160).replace(/\s+/g, " ")}`);
}
