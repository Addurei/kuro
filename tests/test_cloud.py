"""Kuro Cloud relay: licenses, daily limits, upstream fallback, and Kuro talking to it."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app import providers as P
from server import kuro_cloud as C


class FakeAI(BaseHTTPRequestHandler):
    tired = False

    def _json(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._json(200, {"data": [{"id": "llama-3.3-70b-versatile"}]})

    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        if self.server.tired:
            return self._json(429, {"error": {"message": "quota"}})
        assert self.headers["Authorization"] == f"Bearer {self.server.key}"
        self._json(200, {"choices": [{"message": {"content": f"hi from {self.server.key}"}}]})

    def log_message(self, *a):
        pass


def serve(handler, **attrs):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    for k, v in attrs.items():
        setattr(srv, k, v)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


@pytest.fixture
def cloud(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "LICENSES", tmp_path / "licenses.json")
    monkeypatch.setattr(C, "USAGE", tmp_path / "usage.json")
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    tired, tired_url = serve(FakeAI, tired=True, key="up1")
    good, good_url = serve(FakeAI, tired=False, key="up2")
    monkeypatch.setenv("KURO_UPSTREAMS", json.dumps(
        [{"base": tired_url, "key": "up1"}, {"base": good_url, "key": "up2"}]))
    relay, relay_url = serve(C.Handler)
    C._save(C.LICENSES, {"KURO-good": {"name": "Juan", "daily": 3, "expires": "2099-01-01"},
                         "KURO-old": {"name": "Ana", "daily": 3, "expires": "2020-01-01"}})
    yield relay_url + "/v1"
    for s in (tired, good, relay):
        s.shutdown()


def test_kuro_talks_to_the_cloud_and_upstream_falls_back(cloud):
    brain = P.make("cloud", "KURO-good", base=cloud)
    assert brain.complete("hello") == "hi from up2"          # first upstream tired → second


def test_licenses_are_enforced(cloud):
    with pytest.raises(P.ProviderError) as e:
        P.make("cloud", "KURO-nope", base=cloud).complete("hi")
    assert e.value.kind == "auth"
    with pytest.raises(P.ProviderError) as e:
        P.make("cloud", "KURO-old", "m", base=cloud).complete("hi")
    assert "expired" in e.value.detail
    b = P.make("cloud", "KURO-good", "m", base=cloud)
    b.complete("1"), b.complete("2"), b.complete("3")
    with pytest.raises(P.ProviderError) as e:
        b.complete("4")
    assert e.value.kind == "rate" and "daily limit" in e.value.detail
