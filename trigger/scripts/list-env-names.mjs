// Prints Trigger.dev prod env var NAMES only (never values).
const res = await fetch("https://api.trigger.dev/api/v1/projects/proj_vvsvdbeeoiaausrkdiqp/envvars/prod", { headers: { Authorization: `Bearer ${process.env.TRIGGER_SECRET_KEY}` } });
const body = await res.json();
console.log(res.status, Array.isArray(body) ? body.map((v) => v.name).sort().join(" ") : Object.keys(body).join(" "));
