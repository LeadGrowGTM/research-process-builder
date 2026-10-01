"""
Local end-to-end test for game signals pipeline.
No Trigger.dev runtime needed — uses direct HTTP calls throughout.

Tests:
  Stage 1: Serper search (2 queries, last 7 days)
  Stage 2: OpenAI classify
  Stage 3: Clay webhook fire + callback capture via local HTTP server

Run:
  py scripts/test_game_signals_local.py
"""
import json, os, time, uuid, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
import requests

from dotenv import dotenv_values
from pathlib import Path

_env = {
    **dotenv_values(Path(__file__).parent.parent.parent / ".env"),
    **dotenv_values(Path(__file__).parent.parent.parent.parent / ".env"),
    **dotenv_values(Path(__file__).parent.parent / ".env"),
    **os.environ,
}

SERPER_API_KEY = _env.get("SERPER_API_KEY", "")
OPENAI_API_KEY = _env.get("OPEN_AI_API") or _env.get("OPENAI_API_KEY", "")
TRIGGER_SECRET_KEY = _env.get("TRIGGER_SECRET_KEY") or _env.get("TRIGGER_ACCESS_TOKEN", "")
CLAY_WEBHOOK_URL = (
    _env.get("CLAY_GAME_SIGNALS_WEBHOOK")
    or _env.get("CLAY_COMPANY_ENRICH_WEBHOOK_URL", "")
)
WORKER_URL = "https://clay-game-callback.leadgrowai.workers.dev"

if not SERPER_API_KEY:
    raise SystemExit("SERPER_API_KEY not found in .env")
if not OPENAI_API_KEY:
    raise SystemExit("OPEN_AI_API / OPENAI_API_KEY not found in .env")

SYSTEM_PROMPT = """You classify gaming search results as signal or noise, then extract structured data.

SIGNAL TYPES:
- "game_announcement": pre-release game reveal or announcement. Must be a brand-new original game not yet released.
  INCLUDE: action RPG, MMORPG, soulslike, open-world RPG, hack & slash, fighting, third-person story shooter, monster hunting genres
  EXCLUDE: DLC, expansions, patches, reviews of released games, sports games, puzzle/casual mobile, remasters, remakes
- "studio_funding": a game studio raised a funding round
  INCLUDE: any disclosed investment in a game development studio
  EXCLUDE: sports betting companies, game retailers, government grants, crowdfunding under $500K

If neither applies: "noise"

For signals extract: developer, developer_domain, publisher, publisher_domain, game_title, funding_amount, genre, platform, article_date, summary

Return JSON only:
{"classification":"game_announcement"|"studio_funding"|"noise","developer":null,"developer_domain":null,"game_title":null,"funding_amount":null,"genre":null,"platform":null,"article_date":null,"summary":null}"""

TEST_QUERIES = [
    {"id": "A1", "q": '"game reveal" OR "new game announced" OR "reveal trailer" site:ign.com OR site:eurogamer.net OR site:gamespot.com', "num": 5},
    {"id": "B1", "q": '"game studio" raises OR raised OR funding OR investment million 2026', "num": 5},
]

# ── Callback server ────────────────────────────────────────────────────────────

_callbacks = {}

class CallbackHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        token = self.path.strip("/")
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        try:
            _callbacks[token] = json.loads(body)
        except Exception:
            _callbacks[token] = {"raw": body.decode(errors="replace")}
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, *args):
        pass  # silence server logs

def start_callback_server():
    server = HTTPServer(("0.0.0.0", 19876), CallbackHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server

# ── Stage 1: Search ─────────────────────────────────────────────────────────

def serper_search(q, num, tbs="qdr:w"):
    r = requests.post(
        "https://google.serper.dev/search",
        headers={"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"},
        json={"q": q, "num": num, "tbs": tbs},
        timeout=15,
    )
    r.raise_for_status()
    return r.json().get("organic", [])

# ── Stage 2: Classify ────────────────────────────────────────────────────────

def classify(title, url, snippet, stream_ctx):
    r = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
        json={
            "model": "gpt-4o-mini",
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"Title: {title}\nURL: {url}\nSnippet: {snippet[:300]}\nQuery context: {stream_ctx}"},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0,
        },
        timeout=30,
    )
    r.raise_for_status()
    return json.loads(r.json()["choices"][0]["message"]["content"])

# ── Stage 3: Clay fire + wait ────────────────────────────────────────────────

def create_trigger_token(timeout="2m"):
    if not TRIGGER_SECRET_KEY:
        return None
    r = requests.post(
        "https://api.trigger.dev/api/v1/waitpoints/tokens",
        headers={"Authorization": f"Bearer {TRIGGER_SECRET_KEY}", "Content-Type": "application/json"},
        json={"timeout": timeout},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()  # {id, url, ...}

def poll_trigger_token(token_id, timeout_s=120):
    if not TRIGGER_SECRET_KEY:
        return None
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        r = requests.get(
            f"https://api.trigger.dev/api/v1/waitpoints/tokens/{token_id}",
            headers={"Authorization": f"Bearer {TRIGGER_SECRET_KEY}"},
            timeout=10,
        )
        if r.ok:
            data = r.json()
            if data.get("status") == "COMPLETED":
                return data.get("output")
        time.sleep(5)
        elapsed = int(timeout_s - (deadline - time.time()))
        print(f"    {elapsed}s — polling token status...")
    return None

def fire_clay(signal, callback_url, token_id):
    payload = {
        "_callback_id": token_id,
        "_callback_url": callback_url,
        "Company Name": signal["developer"],
        "Company Website": signal.get("developer_domain") or "",
        "signal_type": signal["classification"],
        "game_title": signal.get("game_title") or "",
        "source_url": signal["source_url"],
    }
    r = requests.post(CLAY_WEBHOOK_URL, json=payload, timeout=15)
    return r.status_code, r.text

# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("STAGE 1: Search")
    print("=" * 60)

    raw = []
    for q in TEST_QUERIES:
        print(f"  Query {q['id']}: {q['q'][:60]}...")
        results = serper_search(q["q"], q["num"])
        print(f"  -> {len(results)} results")
        for r in results:
            raw.append({"queryId": q["id"], "title": r.get("title",""), "url": r.get("link",""), "snippet": r.get("snippet","")})

    # URL dedup
    seen = set()
    unique = []
    for r in raw:
        if r["url"] not in seen:
            seen.add(r["url"])
            unique.append(r)
    print(f"\n{len(unique)} unique results\n")

    print("=" * 60)
    print("STAGE 2: Classify")
    print("=" * 60)

    signals = []
    for item in unique:
        ctx = "Stream A (game announcements)" if item["queryId"].startswith("A") else "Stream B (studio funding)"
        try:
            result = classify(item["title"], item["url"], item["snippet"], ctx)
            result["source_url"] = item["url"]
            cls = result.get("classification", "noise")
            if cls != "noise":
                signals.append(result)
                print(f"  OK {cls}: {result.get('developer') or result.get('game_title')} — {item['url'][:60]}")
            else:
                print(f"  — noise: {item['title'][:60]}")
        except Exception as e:
            print(f"  FAIL classify error: {e}")

    dev_signals = [s for s in signals if s.get("developer")]
    print(f"\n{len(signals)} signals ({len(dev_signals)} with developer for Clay)\n")

    if not dev_signals:
        print("No signals with developer — skipping Clay test. Try broader tbs or check OpenAI key.")
        return

    print("=" * 60)
    print("STAGE 3: Clay fire + callback")
    print("=" * 60)

    # NOTE: Local callback server only works if Clay can reach your machine.
    # If you're behind NAT/firewall, Clay can't hit localhost:19876.
    # In that case, use the Cloudflare Worker fallback shown below.
    print("\nStarting local callback server on :19876")
    start_callback_server()

    target = dev_signals[0]
    token = str(uuid.uuid4())
    local_url = f"http://localhost:19876/{token}"
    worker_url = f"https://clay-game-callback.leadgrowai.workers.dev/{token}"

    print(f"  Developer: {target['developer']}")
    print(f"  Token: {token}")
    print(f"  Local callback URL: {local_url}")
    print(f"  Worker fallback URL: {worker_url}")
    print()

    # Create real Trigger.dev waitpoint token
    if TRIGGER_SECRET_KEY:
        print("  Creating real Trigger.dev waitpoint token...")
        tok = create_trigger_token("2m")
        if tok:
            token_id = tok["id"]
            callback_url = f"{WORKER_URL}/{token_id}"
            print(f"  Token ID: {token_id}")
            print(f"  Callback URL: {callback_url}")
        else:
            print("  FAIL could not create token")
            return
    else:
        print("  TRIGGER_SECRET_KEY not found — using fake token (Worker will 502)")
        token_id = str(uuid.uuid4())
        callback_url = f"{WORKER_URL}/{token_id}"

    status, body = fire_clay(target, callback_url, token_id)
    print(f"  Clay accepted: {status} — {body[:100]}")
    if status != 200:
        print(f"  FAIL Clay rejected: {status} {body}")
        return

    print(f"\n  Polling Trigger.dev for token completion (2min timeout)...")
    output = poll_trigger_token(token_id, timeout_s=120)
    if output:
        print("\n  OK FULL LOOP CONFIRMED — Clay enrichment received:")
        print(json.dumps(output, indent=2))
    else:
        print("\n  FAIL token not completed within 120s")
        print("  Clay HTTP step may not be configured in the table.")

if __name__ == "__main__":
    main()
