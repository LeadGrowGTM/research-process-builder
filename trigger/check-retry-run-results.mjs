import { config } from "dotenv";
import { resolve } from "path";

config({ path: resolve("../../.env") });

const url = [process.env.SUPABASE_PROJECT_URL, process.env.SUPABASE_URL]
  .find((u) => u?.startsWith("http"))
  ?.replace(/\/+$/, "") ?? "";
const key = process.env.SUPABASE_ANON_KEY ?? process.env.SUPABASE_KEY ?? "";
const headers = { apikey: key, Authorization: `Bearer ${key}` };

const since = new Date(Date.now() - 2 * 3600_000).toISOString(); // last 2h

for (const table of ["funding_discoveries", "product_launches"]) {
  const resp = await fetch(
    `${url}/rest/v1/${table}?select=enriched_by,company_name,linkedin_url,employee_count&enriched_at=gte.${since}&order=enriched_at.asc`,
    { headers }
  );
  const rows = await resp.json();
  const byProvider = {};
  let withLinkedin = 0, withEmployees = 0;
  for (const r of rows) {
    byProvider[r.enriched_by] = (byProvider[r.enriched_by] ?? 0) + 1;
    if (r.linkedin_url) withLinkedin++;
    if (r.employee_count != null) withEmployees++;
  }
  console.log(`\n${table} — ${rows.length} rows stamped in last 2h`);
  console.log(`  by provider:`, byProvider);
  console.log(`  with linkedin_url: ${withLinkedin}, with employee_count: ${withEmployees}`);
}

const dlKey = process.env.DISCOLIKE_API_KEY ?? "";
if (dlKey) {
  const u = await (await fetch("https://api.discolike.com/v1/usage", { headers: { "x-discolike-key": dlKey } })).json();
  console.log(`\nDiscoLike usage now: $${u.month_to_date_spend ?? "?"} / max $${u.max_spend ?? "?"} (was $17.25 pre-run)`);
}
