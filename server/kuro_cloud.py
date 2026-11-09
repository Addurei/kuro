"""Kuro Cloud: a tiny paid-brain relay. You run this; buyers never see your key.

    Kuro (buyer's PC) --license key--> kuro_cloud.py --your key--> Groq / Gemini / ...

It speaks the OpenAI chat format (what Kuro's "Kuro Cloud" brain uses), checks
each request's license (exists, not expired, under its daily limit), then
forwards it to the upstream AI with YOUR key. If an upstream is out of quota it
tries the next one.

Run it:
    pip install httpx
    set KURO_UPSTREAMS=[{"base":"https://api.groq.com/openai/v1","key":"gsk_..."}]
    python kuro_cloud.py serve --port 8787

Licenses (sell them via Gumroad / Ko-fi / PayMongo, then create one per buyer):
    python kuro_cloud.py new-license --name "Juan" --days 30 --daily 400
    python kuro_cloud.py list

Buyer side: in Kuro's config.json set "cloud_url": "https://your-server/v1",
then pick "Kuro Cloud" in Settings → AI & Voice and paste their license key.

Host it on anything that runs Python (Render, Railway, Fly.io, a VPS) behind
HTTPS. Standard library + httpx only.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import threading
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx

HERE = Path(os.environ.get("KURO_CLOUD_DATA", Path(__file__).parent))
LICENSES = HERE / "licenses.json"
USAGE = HERE / "usage.json"
_lock = threading.Lock()


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(path: Path, data: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    tmp.replace(path)


def upstreams() -> list[dict]:
    raw = os.environ.get("KURO_UPSTREAMS", "")
    try:
        ups = json.loads(raw) if raw else []
    except ValueError:
        ups = []
    if not ups and os.environ.get("GROQ_API_KEY"):
        ups = [{"base": "https://api.groq.com/openai/v1", "key": os.environ["GROQ_API_KEY"]}]
    return ups


def check_license(key: str, today: date | None = None) -> tuple[bool, str]:
    """(ok, reason). Counts the request toward today's limit when ok."""
    today = today or date.today()
    with _lock:
        lic = _load(LICENSES).get(key)
        if not lic:
            return False, "unknown license key"
        if lic.get("expires") and date.fromisoformat(lic["expires"]) < today:
            return False, f"license expired on {lic['expires']}"
        usage = _load(USAGE)
        day = usage.setdefault(today.isoformat(), {})
        used = day.get(key, 0)
        if used >= int(lic.get("daily", 300)):
            return False, "daily limit reached; it resets at midnight"
        day[key] = used + 1
        for d in [d for d in usage if d < (today - timedelta(days=7)).isoformat()]:
            del usage[d]                                  # keep a week of history
        _save(USAGE, usage)
    return True, ""


def forward(path: str, body: dict | None) -> tuple[int, bytes]:
    last = (503, json.dumps({"error": {"message": "no upstream AI configured"}}).encode())
    for up in upstreams():
        try:
            r = httpx.request("POST" if body is not None else "GET", up["base"].rstrip("/") + path,
                              json=body, timeout=90,
                              headers={"Authorization": f"Bearer {up['key']}"})
        except httpx.HTTPError as e:
            last = (502, json.dumps({"error": {"message": f"upstream unreachable: {e}"}}).encode())
            continue
        if r.status_code in (429, 500, 502, 503, 504):
            last = (r.status_code, r.content)
            continue                                      # tired: try the next upstream
        return r.status_code, r.content
    return last


class Handler(BaseHTTPRequestHandler):
    server_version = "KuroCloud/1"

    def _reply(self, code: int, data: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _auth(self) -> bool:
        key = self.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        ok, why = check_license(key) if self.command == "POST" else \
            ((key in _load(LICENSES)), "unknown license key")
        if not ok:
            self._reply(401 if "unknown" in why else 429,
                        json.dumps({"error": {"message": why}}).encode())
        return ok

    def do_GET(self) -> None:
        if self.path.rstrip("/") == "/health":
            return self._reply(200, b'{"ok":true}')
        if not self.path.endswith("/models"):
            return self._reply(404, b'{"error":{"message":"not found"}}')
        if self._auth():
            self._reply(*forward("/models", None))

    def do_POST(self) -> None:
        if not self.path.endswith("/chat/completions"):
            return self._reply(404, b'{"error":{"message":"not found"}}')
        n = int(self.headers.get("Content-Length", 0) or 0)
        try:
            body = json.loads(self.rfile.read(min(n, 2_000_000)) or b"{}")
        except ValueError:
            return self._reply(400, b'{"error":{"message":"bad JSON"}}')
        if self._auth():
            self._reply(*forward("/chat/completions", body))

    def log_message(self, fmt, *args) -> None:            # no request bodies in logs
        print(f"{self.address_string()} {self.command} {self.path} {args[1] if len(args) > 1 else ''}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Kuro Cloud relay")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve")
    s.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8787)))
    n = sub.add_parser("new-license")
    n.add_argument("--name", required=True)
    n.add_argument("--days", type=int, default=30)
    n.add_argument("--daily", type=int, default=400)
    sub.add_parser("list")
    a = ap.parse_args()
    if a.cmd == "serve":
        if not upstreams():
            print("Set KURO_UPSTREAMS (or GROQ_API_KEY) first.")
            return
        print(f"Kuro Cloud on :{a.port}")
        ThreadingHTTPServer(("0.0.0.0", a.port), Handler).serve_forever()
    elif a.cmd == "new-license":
        with _lock:
            lic = _load(LICENSES)
            key = "KURO-" + secrets.token_urlsafe(18)
            lic[key] = {"name": a.name, "daily": a.daily,
                        "expires": (date.today() + timedelta(days=a.days)).isoformat()}
            _save(LICENSES, lic)
        print(key)
    else:
        today = _load(USAGE).get(date.today().isoformat(), {})
        for k, v in _load(LICENSES).items():
            print(f"{k}  {v['name']:<16} expires {v.get('expires')}  "
                  f"today {today.get(k, 0)}/{v.get('daily')}")


if __name__ == "__main__":
    main()
