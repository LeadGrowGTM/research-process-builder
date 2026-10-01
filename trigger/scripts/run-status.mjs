// Usage: lg run --env prod node scripts/run-status.mjs <runId>
const id = process.argv[2];
const res = await fetch(`https://api.trigger.dev/api/v3/runs/${id}`, { headers: { Authorization: `Bearer ${process.env.TRIGGER_SECRET_KEY}` } });
const r = await res.json();
console.log(JSON.stringify({ status: r.status, error: r.error, output: r.output ?? r.outputPresignedUrl ?? null }).slice(0, 1500));
