// Read-only health check: raisingfi rows per day in funding_discoveries (last 14 days).
// Usage: lg run node scripts/check-raisingfi-health.mjs
const url = process.env.SUPABASE_PROJECT_URL || process.env.SUPABASE_URL;
const key = process.env.SUPABASE_SERVICE_ROLE_KEY || process.env.SUPABASE_KEY;
if (!url || !key) {
  console.error("Supabase URL/key not set in env");
  process.exit(1);
}

const since = new Date(Date.now() - 14 * 86400_000).toISOString().slice(0, 10);
const res = await fetch(
  `${url}/rest/v1/funding_discoveries?select=discovered_date,discovered_by_pipeline,company_domain,industry,lead_investors&discovered_date=gte.${since}&order=discovered_date.desc&limit=2000`,
  { headers: { apikey: key, Authorization: `Bearer ${key}` } },
);
if (!res.ok) {
  console.error(`HTTP ${res.status}`);
  process.exit(1);
}
const rows = await res.json();

const byPipeline = {};
const raisingfiByDay = {};
let raisingfiNoDomain = 0;
for (const r of rows) {
  const p = r.discovered_by_pipeline || "(none)";
  byPipeline[p] = (byPipeline[p] || 0) + 1;
  if (p === "raisingfi") {
    const d = String(r.discovered_date).slice(0, 10);
    raisingfiByDay[d] = (raisingfiByDay[d] || 0) + 1;
    if (!r.company_domain) raisingfiNoDomain++;
  }
}
console.log(JSON.stringify({ since, total: rows.length, byPipeline, raisingfiByDay, raisingfiNoDomain }, null, 2));
