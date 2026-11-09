"""Google Calendar + Gmail, signed in with your own Google account.

Sign-in is the standard "installed app" OAuth flow: Kuro opens Google's
sign-in page in your browser, Google sends the answer back to a tiny
server on 127.0.0.1, and Kuro keeps a refresh token. The token is stored
in AppData encrypted with Windows' own per-user encryption (DPAPI), so it
can't be read from another account or another PC.

Access asked for:
  * calendar.events - see your events and add new ones (adding always asks first)
  * gmail.readonly  - read emails. Kuro can never send, delete or change mail.

You need an OAuth client ID ("Desktop app") from Google Cloud Console;
see README. Nothing is sent anywhere except Google.
"""
from __future__ import annotations

import base64
import hashlib
import http.server
import json
import os
import platform
import secrets
import threading
import time
import urllib.parse
import webbrowser
from datetime import datetime, timedelta
from pathlib import Path

import httpx

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
CAL = "https://www.googleapis.com/calendar/v3"
GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"
SCOPES = ["https://www.googleapis.com/auth/calendar.events",
          "https://www.googleapis.com/auth/gmail.readonly"]


class GoogleError(Exception):
    pass


# ------------------------------------------------------------- token storage
def _protect(data: bytes) -> bytes:
    """Encrypt for this Windows user (DPAPI). Elsewhere: base64 only."""
    if platform.system() != "Windows":
        return base64.b64encode(data)
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]
    buf = ctypes.create_string_buffer(data, len(data))
    inp, out = BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), BLOB()
    if not ctypes.windll.crypt32.CryptProtectData(ctypes.byref(inp), None, None, None, None,
                                                  0, ctypes.byref(out)):
        raise GoogleError("couldn't encrypt the sign-in token")
    try:
        return b"DPAPI" + ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def _unprotect(blob: bytes) -> bytes:
    if not blob.startswith(b"DPAPI"):
        return base64.b64decode(blob)
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]
    raw = blob[5:]
    buf = ctypes.create_string_buffer(raw, len(raw))
    inp, out = BLOB(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), BLOB()
    if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(inp), None, None, None, None,
                                                    0, ctypes.byref(out)):
        raise GoogleError("couldn't decrypt the sign-in token")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


class Google:
    def __init__(self, token_path: Path, client_id: str = "", client_secret: str = ""):
        self.token_path = token_path
        self.client_id, self.client_secret = client_id.strip(), client_secret.strip()
        self._tok: dict = {}
        self._lock = threading.Lock()
        try:
            self._tok = json.loads(_unprotect(token_path.read_bytes()))
        except Exception:
            self._tok = {}

    # ------------------------------------------------------------- state
    @property
    def configured(self) -> bool:
        return bool(self.client_id)

    @property
    def connected(self) -> bool:
        return bool(self._tok.get("refresh_token"))

    @property
    def email(self) -> str:
        return self._tok.get("email", "")

    # ------------------------------------------------------------- sign-in
    def connect(self, timeout: float = 180, open_browser=webbrowser.open) -> str:
        """Blocking: opens the browser and waits for you to sign in."""
        if not self.configured:
            raise GoogleError("add your Google OAuth client ID in Settings → Google first")
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        state = secrets.token_urlsafe(16)
        result: dict = {}
        done = threading.Event()

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                if q.get("state", [""])[0] == state:
                    result.update({k: v[0] for k, v in q.items()})
                    done.set()
                ok = "code" in result
                body = ("<html><body style='font-family:sans-serif;background:#0d0d10;color:#e9e9ec;"
                        "text-align:center;padding-top:80px'><h2>"
                        + ("Connected! You can close this tab and go back to Kuro."
                           if ok else "Sign-in was cancelled.") + "</h2></body></html>")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(body.encode())

            def log_message(self, *_a):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        redirect = f"http://127.0.0.1:{server.server_port}"
        threading.Thread(target=server.serve_forever, daemon=True).start()
        params = {"client_id": self.client_id, "redirect_uri": redirect,
                  "response_type": "code", "scope": " ".join(SCOPES + ["email"]),
                  "code_challenge": challenge, "code_challenge_method": "S256",
                  "access_type": "offline", "prompt": "consent", "state": state}
        open_browser(AUTH_URL + "?" + urllib.parse.urlencode(params))
        try:
            if not done.wait(timeout):
                raise GoogleError("sign-in timed out")
        finally:
            server.shutdown()
        if "code" not in result:
            raise GoogleError(f"sign-in failed ({result.get('error', 'cancelled')})")
        r = httpx.post(TOKEN_URL, data={
            "client_id": self.client_id, "client_secret": self.client_secret,
            "code": result["code"], "code_verifier": verifier,
            "grant_type": "authorization_code", "redirect_uri": redirect}, timeout=30)
        if r.status_code != 200:
            raise GoogleError(f"Google refused the sign-in ({r.text[:200]})")
        tok = r.json()
        tok["expires_at"] = time.time() + tok.get("expires_in", 3600) - 60
        tok["email"] = _email_from_id_token(tok.get("id_token", ""))
        self._tok = tok
        self._save()
        return tok["email"] or "your Google account"

    def disconnect(self) -> None:
        tok = self._tok.get("refresh_token")
        self._tok = {}
        try:
            self.token_path.unlink()
        except OSError:
            pass
        if tok:
            try:
                httpx.post(REVOKE_URL, data={"token": tok}, timeout=10)
            except httpx.HTTPError:
                pass

    # ------------------------------------------------------------- http
    def _access(self) -> str:
        with self._lock:
            if not self.connected:
                raise GoogleError("Google isn't connected (Settings → Google → Connect)")
            if self._tok.get("access_token") and time.time() < self._tok.get("expires_at", 0):
                return self._tok["access_token"]
            r = httpx.post(TOKEN_URL, data={
                "client_id": self.client_id, "client_secret": self.client_secret,
                "refresh_token": self._tok["refresh_token"], "grant_type": "refresh_token"},
                timeout=30)
            if r.status_code != 200:
                raise GoogleError("Google sign-in expired; connect again in Settings → Google")
            new = r.json()
            self._tok["access_token"] = new["access_token"]
            self._tok["expires_at"] = time.time() + new.get("expires_in", 3600) - 60
            self._save()
            return self._tok["access_token"]

    def _req(self, method: str, url: str, **kw) -> dict:
        try:
            r = httpx.request(method, url, headers={"Authorization": f"Bearer {self._access()}"},
                              timeout=20, **kw)
        except httpx.HTTPError as e:
            raise GoogleError(f"can't reach Google ({type(e).__name__})")
        if r.status_code >= 400:
            try:
                msg = r.json().get("error", {}).get("message", r.text[:200])
            except ValueError:
                msg = r.text[:200]
            raise GoogleError(f"Google said: {msg}")
        return r.json() if r.content else {}

    def _save(self) -> None:
        try:
            self.token_path.write_bytes(_protect(json.dumps(self._tok).encode()))
        except OSError:
            pass

    # ------------------------------------------------------------- calendar
    def events(self, days: int = 1, start: datetime | None = None) -> list[dict]:
        start = start or datetime.now().astimezone().replace(hour=0, minute=0, second=0,
                                                             microsecond=0)
        end = start + timedelta(days=max(1, days))
        data = self._req("GET", f"{CAL}/calendars/primary/events", params={
            "timeMin": start.isoformat(), "timeMax": end.isoformat(),
            "singleEvents": "true", "orderBy": "startTime", "maxResults": 50})
        out = []
        for e in data.get("items", []):
            s = e.get("start", {})
            all_day = "date" in s and "dateTime" not in s
            when = s.get("dateTime") or s.get("date", "")
            out.append({"title": e.get("summary", "(no title)"), "start": when,
                        "all_day": all_day, "location": e.get("location", "")})
        return out

    def add_event(self, title: str, start: datetime, minutes: int = 60,
                  description: str = "") -> dict:
        if start.tzinfo is None:
            start = start.astimezone()
        end = start + timedelta(minutes=max(5, minutes))
        body = {"summary": title, "description": description,
                "start": {"dateTime": start.isoformat()}, "end": {"dateTime": end.isoformat()},
                "reminders": {"useDefault": True}}
        return self._req("POST", f"{CAL}/calendars/primary/events", json=body)

    # ------------------------------------------------------------- gmail (read-only)
    def emails(self, query: str = "is:unread in:inbox", count: int = 5) -> list[dict]:
        data = self._req("GET", f"{GMAIL}/messages",
                         params={"q": query, "maxResults": max(1, min(15, count))})
        out = []
        for m in data.get("messages", []):
            full = self._req("GET", f"{GMAIL}/messages/{m['id']}", params={
                "format": "metadata", "metadataHeaders": ["From", "Subject", "Date"]})
            h = {x["name"].lower(): x["value"] for x in full.get("payload", {}).get("headers", [])}
            out.append({"from": h.get("from", ""), "subject": h.get("subject", "(no subject)"),
                        "date": h.get("date", ""), "snippet": full.get("snippet", "")})
        return out


def _email_from_id_token(id_token: str) -> str:
    try:
        payload = id_token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload)).get("email", "")
    except Exception:
        return ""


def fmt_event(e: dict) -> str:
    if e["all_day"]:
        return f"{e['title']} (all day)"
    try:
        t = datetime.fromisoformat(e["start"].replace("Z", "+00:00")).astimezone()
        when = t.strftime("%I:%M %p").lstrip("0")
    except ValueError:
        when = e["start"]
    return f"{when} {e['title']}" + (f" @ {e['location']}" if e.get("location") else "")


def fmt_email(m: dict) -> str:
    sender = m["from"].split("<")[0].strip().strip('"') or m["from"]
    return f"{sender}: {m['subject']}"


def env_client() -> tuple[str, str]:
    return os.environ.get("KURO_GOOGLE_CLIENT_ID", ""), os.environ.get("KURO_GOOGLE_CLIENT_SECRET", "")
