import { config } from "dotenv";
import { resolve } from "path";

config({ path: resolve("../../.env") });

const url = [process.env.SUPABASE_PROJECT_URL, process.env.SUPABASE_URL]
  .find((u) => u?.startsWith("http"))
  ?.replace(/\/+$/, "") ?? "";
const key = process.env.SUPABASE_ANON_KEY ?? process.env.SUPABASE_KEY ?? "";
const headers = { apikey: key, Authorization: `Bearer ${key}`, Prefer: "count=exact" };

const daysAgo = (d) => new Date(Date.now() - d * 86_400_000).toISOString().split("T")[0];

// Mirror fetchRetryRows filters exactly (enrichment-retry.ts)
const tables = [
  { table: "funding_discoveries", domainCol: "company_domain", minAge: 7, maxAge: 60 },
  { table: "product_launches", domainCol: "maker_website", minAge: 25, maxAge: 90 },
];

for (const t of tables) {
  const params =
    `select=company_name,${t.domainCol},discovered_date` +
    `&${t.domainCol}=not.is.null` +
    `&enriched_at=is.null` +
    `&discovered_date=lte.${daysAgo(t.minAge)}` +
    `&discovered_date=gte.${daysAgo(t.maxAge)}` +
    `&order=discovered_date.asc&limit=50`;
  const resp = await fetch(`${url}/rest/v1/${t.table}?${params}`, { headers });
  const total = resp.headers.get("content-range")?.split("/")[1] ?? "?";
  const rows = await resp.json();
  console.log(`\n${t.table}: ${rows.length} in first batch (total matching: ${total})`);
  for (const r of rows.slice(0, 10)) console.log(`  ${r.discovered_date} | ${r.company_name} | ${r[t.domainCol]}`);
  if (rows.length > 10) console.log(`  ... +${rows.length - 10} more`);
}

// DiscoLike headroom
const dlKey = process.env.DISCOLIKE_API_KEY ?? "";
if (dlKey) {
  const u = await (await fetch("https://api.discolike.com/v1/usage", { headers: { "x-discolike-key": dlKey } })).json();
  console.log(`\nDiscoLike usage: $${u.month_to_date_spend ?? "?"} / max $${u.max_spend ?? "?"}`);
} else {
  console.log("\nDISCOLIKE_API_KEY not in workspace env (prod-only) — usage check skipped");
}
