"""
Test Clay webhook + callback roundtrip.
POSTs a real gaming studio to Clay, then polls Worker logs via wrangler tail (run separately).
"""
import json
import os
import sys
import requests
import uuid
import time

CLAY_WEBHOOK_URL = os.environ.get("CLAY_COMPANY_ENRICH_WEBHOOK_URL") or sys.exit(
    "Set CLAY_COMPANY_ENRICH_WEBHOOK_URL (run under: lg run python <script>)"
)
CALLBACK_URL = "https://clay-game-callback.leadgrowai.workers.dev"

TEST_COMPANY = {
    "Company Name": "Rebel Wolves",
    "Company Website": "rebelwolves.com",
    "signal_type": "game_announcement",
    "game_title": "The Blood of Dawnwalker",
    "source_url": "https://www.ign.com/articles/rebel-wolves-blood-of-dawnwalker",
    "date_detected": "2026-05-06",
}

token = str(uuid.uuid4())
payload = {
    "_callback_id": token,
    "_callback_url": CALLBACK_URL,
    **TEST_COMPANY,
}

print(f"Token: {token}")
print(f"Payload:\n{json.dumps(payload, indent=2)}")
print(f"\nPOSTing to Clay...")

resp = requests.post(CLAY_WEBHOOK_URL, json=payload, timeout=15)
print(f"Clay response: {resp.status_code}")
print(resp.text[:500] if resp.text else "(empty)")
print(f"\nWaiting for Clay to enrich + callback...")
print(f"Run in another terminal to watch Worker logs:")
print(f"  cd trigger && npx wrangler tail clay-game-callback --format pretty")
