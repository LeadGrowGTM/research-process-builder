// Usage: lg run --env prod node scripts/trigger-task.mjs <taskId> [payloadJson]
const [taskId, payload = "{}"] = process.argv.slice(2);
const key = process.env.TRIGGER_SECRET_KEY;
if (!taskId || !key) { console.error("need taskId and TRIGGER_SECRET_KEY"); process.exit(1); }
const res = await fetch(`https://api.trigger.dev/api/v1/tasks/${taskId}/trigger`, {
  method: "POST",
  headers: { Authorization: `Bearer ${key}`, "Content-Type": "application/json" },
  body: JSON.stringify({ payload: JSON.parse(payload) }),
});
console.log(res.status, JSON.stringify(await res.json()));
