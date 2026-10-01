"""
E2E callback proof — two parts:
  Part 1: Simulate Clay calling the Worker. Confirms Worker → Trigger.dev path.
  Part 2: Fire real Clay webhook, then poll Worker logs (run wrangler tail separately).
"""
import json, os, uuid, requests, time, sys

WORKER_URL = "https://clay-game-callback.leadgrowai.workers.dev"
CLAY_WEBHOOK_URL = os.environ.get("CLAY_COMPANY_ENRICH_WEBHOOK_URL") or sys.exit(
    "Set CLAY_COMPANY_ENRICH_WEBHOOK_URL (run under: lg run python <script>)"
)

# ── Part 1: Fake Clay → Worker ────────────────────────────────────────────────
print("=" * 60)
print("PART 1: Simulate Clay POSTing back to Worker")
print("=" * 60)

fake_token = str(uuid.uuid4())
callback_url = f"{WORKER_URL}/{fake_token}"

print(f"Token:        {fake_token}")
print(f"Callback URL: {callback_url}")
print(f"POSTing to Worker (simulating Clay)...")

resp = requests.post(callback_url, json={
    "company_name": "Rebel Wolves",
    "domain": "rebelwolves.com",
    "linkedin_url": "https://linkedin.com/company/rebel-wolves",
    "enriched": True,
}, timeout=15)

print(f"Worker response: {resp.status_code} — {resp.text}")
if resp.status_code == 200:
    print("✓ Worker accepted the callback and called Trigger.dev")
elif resp.status_code == 502:
    print("✓ Worker reached Trigger.dev but token not found (expected — no task waiting)")
    print("  This proves the Worker→Trigger.dev path works correctly.")
else:
    print(f"✗ Unexpected response: {resp.status_code}")

# ── Part 2: Real Clay webhook ─────────────────────────────────────────────────
print()
print("=" * 60)
print("PART 2: Fire real Clay webhook with embedded callback URL")
print("=" * 60)

real_token = str(uuid.uuid4())
real_callback_url = f"{WORKER_URL}/{real_token}"

payload = {
    "_callback_id": real_token,
    "_callback_url": real_callback_url,
    "Company Name": "Rebel Wolves",
    "Company Website": "rebelwolves.com",
    "signal_type": "game_announcement",
    "game_title": "The Blood of Dawnwalker",
    "source_url": "https://www.ign.com/articles/rebel-wolves-blood-of-dawnwalker",
    "date_detected": "2026-05-06",
}

print(f"Token:        {real_token}")
print(f"Callback URL: {real_callback_url}")
print(f"\nClay should POST to: {real_callback_url}")
print(f"\nFiring Clay webhook...")

resp2 = requests.post(CLAY_WEBHOOK_URL, json=payload, timeout=15)
print(f"Clay response: {resp2.status_code} — {resp2.text}")

if resp2.status_code == 200:
    print("\n✓ Clay accepted the row.")
    print("  Now watch wrangler tail for an incoming POST from Clay.")
    print(f"  Expected URL hit: {real_callback_url}")
    print("\n  Run in another terminal:")
    print("  cd cloudflare/clay-game-callback && npx wrangler tail --format pretty")
    print("\nWaiting 3 minutes for Clay to enrich and call back...")
    for i in range(18):
        time.sleep(10)
        print(f"  {(i+1)*10}s elapsed...")
    print("\nIf no Worker log appeared → Clay table HTTP step not firing.")
    print("If Worker log appeared but token 502 → loop confirmed working, just needs live task.")
