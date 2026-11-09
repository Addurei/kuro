"""Kuro's brain: Google Gemini, through its native REST API.

Kuro keeps one conversation format internally (Anthropic-style content
blocks, see brain.py). This module translates it to Gemini's `contents`
/ `parts` format and back.

Free keys only work on some models, and limits change, so Kuro tries a
short list of models in order and sticks with the first one that works.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass

import httpx

log = logging.getLogger("kuro")

API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
KEY_URL = "https://aistudio.google.com/apikey"
# Free-tier models first; later ones are fallbacks when a model is busy or
# unavailable for this key.
MODELS = ["gemini-3.5-flash", "gemini-3.1-flash-lite", "gemini-3.6-flash"]


class ProviderError(Exception):
    """kind: auth | rate | connection | model_missing | bad_request | blocked | other"""

    def __init__(self, kind: str, message: str, detail: str = ""):
        super().__init__(message)
        self.kind = kind
        self.detail = detail      # Google's own error text, for the Test button / log


@dataclass
class Reply:
    stop_reason: str            # "tool_use" or "end_turn"
    content: list[dict]         # Anthropic-style blocks


# ------------------------------------------------------------- schema
_ALLOWED = {"type", "description", "enum", "properties", "required", "items",
            "minimum", "maximum", "nullable", "format"}


def gemini_schema(schema: dict) -> dict | None:
    """JSON Schema -> Gemini's OpenAPI subset. Upper-case types, drop
    unsupported keys, and return None for 'no parameters' (Gemini rejects
    an OBJECT with empty properties)."""
    def walk(node: dict) -> dict:
        out = {}
        for k, v in node.items():
            if k not in _ALLOWED:
                continue
            if k == "type":
                out[k] = str(v).upper()
            elif k == "properties":
                out[k] = {name: walk(sub) for name, sub in v.items()}
            elif k == "items":
                out[k] = walk(v)
            elif k == "required":
                if v:
                    out[k] = list(v)
            else:
                out[k] = v
        return out
    if not schema.get("properties"):
        return None
    return walk(schema)


def gemini_tools(tools: list[dict]) -> list[dict]:
    decls = []
    for t in tools:
        d = {"name": t["name"], "description": t["description"]}
        params = gemini_schema(t["input_schema"])
        if params:
            d["parameters"] = params
        decls.append(d)
    return [{"functionDeclarations": decls}] if decls else []


# ------------------------------------------------------------- history
def to_gemini(messages: list[dict]) -> list[dict]:
    """Anthropic-style history -> Gemini contents."""
    contents: list[dict] = []
    names: dict[str, str] = {}
    for m in messages:
        c = m["content"]
        if m["role"] == "user":
            if isinstance(c, str):
                contents.append({"role": "user", "parts": [{"text": c}]})
                continue
            parts = []
            for b in c:
                if b.get("type") == "tool_result":
                    parts.append({"functionResponse": {
                        "name": names.get(b["tool_use_id"], "tool"),
                        "response": {"error" if b.get("is_error") else "result":
                                     str(b.get("content", ""))}}})
                elif b.get("type") == "text":
                    parts.append({"text": b["text"]})
            if parts:
                contents.append({"role": "user", "parts": parts})
            continue
        # assistant -> model
        if isinstance(c, str):
            contents.append({"role": "model", "parts": [{"text": c}]})
            continue
        parts = []
        for b in c:
            raw = (b.get("extra") or {}).get("raw")
            if b.get("type") == "tool_use":
                names[b["id"]] = b["name"]
                parts.append(raw or {"functionCall": {"name": b["name"],
                                                      "args": b.get("input", {})}})
            elif b.get("type") == "text" and b.get("text"):
                parts.append(raw or {"text": b["text"]})
        if parts:
            contents.append({"role": "model", "parts": parts})
    return contents


def from_gemini(data: dict) -> Reply:
    cands = data.get("candidates") or []
    if not cands:
        reason = (data.get("promptFeedback") or {}).get("blockReason", "no answer")
        raise ProviderError("blocked", f"Gemini declined to answer ({reason}).")
    cand = cands[0]
    parts = (cand.get("content") or {}).get("parts") or []
    blocks: list[dict] = []
    calls = 0
    for part in parts:
        if part.get("thought"):
            continue                       # internal reasoning, never shown
        if "functionCall" in part:
            fc = part["functionCall"]
            calls += 1
            blocks.append({"type": "tool_use", "id": f"call_{uuid.uuid4().hex[:10]}",
                           "name": fc.get("name", ""), "input": fc.get("args") or {},
                           "extra": {"raw": part}})   # keeps thoughtSignature
        elif part.get("text"):
            blocks.append({"type": "text", "text": part["text"], "extra": {"raw": part}})
    if not blocks and cand.get("finishReason") not in (None, "STOP"):
        blocks.append({"type": "text",
                       "text": f"(My answer got cut off: {cand.get('finishReason')}.)"})
    return Reply("tool_use" if calls else "end_turn", blocks)


def clean_text(text: str) -> str:
    return re.sub(r"\s+\n", "\n", text or "").strip()


# ------------------------------------------------------------- errors
def classify(status: int, body: str) -> ProviderError:
    try:
        err = json.loads(body).get("error", {})
        msg = err.get("message", body)
        state = err.get("status", "")
    except (ValueError, AttributeError):
        msg, state = body, ""
    low = msg.lower()
    if "api key" in low or status in (401, 403) or state in ("UNAUTHENTICATED", "PERMISSION_DENIED"):
        kind = "auth"
    elif status == 429 or state == "RESOURCE_EXHAUSTED":
        kind = "rate"
    elif status == 404 or state == "NOT_FOUND":
        kind = "model_missing"
    elif status in (500, 502, 503, 504) or state == "UNAVAILABLE":
        kind = "rate"                      # overloaded: try the next model
    elif "location" in low and "not supported" in low:
        kind = "blocked"
    elif status == 400:
        kind = "bad_request"
    else:
        kind = "other"
    return ProviderError(kind, f"HTTP {status}", f"{state} {msg}".strip()[:500])


# ------------------------------------------------------------- provider
class GeminiProvider:
    name = "gemini"

    def __init__(self, api_key: str, model: str = "", timeout: float = 60.0):
        self.api_key = api_key.strip()
        self.models = [model] if model else list(MODELS)
        self.timeout = timeout
        self._good = 0                     # index of the model that last worked

    @property
    def model(self) -> str:
        return self.models[self._good]

    def _post(self, model: str, body: dict) -> dict:
        try:
            r = httpx.post(API.format(model=model), json=body, timeout=self.timeout,
                           headers={"x-goog-api-key": self.api_key,
                                    "Content-Type": "application/json"})
        except (httpx.ConnectError, httpx.ConnectTimeout) as e:
            raise ProviderError("connection", "can't connect", str(e)) from e
        except httpx.TimeoutException as e:
            raise ProviderError("connection", "timed out", str(e)) from e
        except httpx.HTTPError as e:
            raise ProviderError("connection", type(e).__name__, str(e)) from e
        if r.status_code != 200:
            raise classify(r.status_code, r.text)
        try:
            return r.json()
        except ValueError as e:
            raise ProviderError("other", "bad JSON from Gemini", r.text[:300]) from e

    def _call(self, body: dict, light: bool = False) -> dict:
        """Try the working model first, then the others on rate/missing errors.
        `light` starts from the lightest model (background jobs), which has a
        separate, more generous free quota."""
        first = self._good
        if light:
            lite = [i for i, m in enumerate(self.models) if "lite" in m]
            first = lite[0] if lite else self._good
        order = [first] + [i for i in range(len(self.models)) if i != first]
        last: ProviderError | None = None
        for i in order:
            try:
                data = self._post(self.models[i], body)
                if not light:
                    self._good = i
                return data
            except ProviderError as e:
                log.warning("gemini %s failed: %s %s", self.models[i], e.kind, e.detail)
                if e.kind not in ("rate", "model_missing"):
                    raise
                last = e
        raise last  # type: ignore[misc]

    def chat(self, persona: str, context: str, tools: list[dict],
             messages: list[dict], max_tokens: int) -> Reply:
        body = {
            "systemInstruction": {"parts": [{"text": persona + "\n\n" + context}]},
            "contents": to_gemini(messages),
            # roomy limit: newer models think before answering, and thinking
            # tokens count toward it
            "generationConfig": {"maxOutputTokens": max(max_tokens, 4096),
                                 "temperature": 0.7},
        }
        if tools:
            body["tools"] = gemini_tools(tools)
        return from_gemini(self._call(body))

    def complete(self, prompt: str, max_tokens: int = 1024, light: bool = False) -> str:
        data = self._call({"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                           "generationConfig": {"maxOutputTokens": max(max_tokens, 2048),
                                                "temperature": 0.2}}, light=light)
        return clean_text(" ".join(b["text"] for b in from_gemini(data).content
                                   if b["type"] == "text"))

    def vision(self, prompt: str, jpeg: bytes) -> str:
        """Ask Gemini about an image (used for 'look at my screen')."""
        import base64
        data = self._call({"contents": [{"role": "user", "parts": [
            {"inlineData": {"mimeType": "image/jpeg",
                            "data": base64.b64encode(jpeg).decode("ascii")}},
            {"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": 2048, "temperature": 0.3}})
        return clean_text(" ".join(b["text"] for b in from_gemini(data).content
                                   if b["type"] == "text"))

    def web_answer(self, question: str) -> tuple[str, list[tuple[str, str]]]:
        """Answer from live Google Search results. Returns (answer, [(title, url)])."""
        data = self._call({"contents": [{"role": "user", "parts": [{"text": question}]}],
                           "tools": [{"googleSearch": {}}],
                           "generationConfig": {"maxOutputTokens": 2048, "temperature": 0.3}})
        text = clean_text(" ".join(b["text"] for b in from_gemini(data).content
                                   if b["type"] == "text"))
        meta = ((data.get("candidates") or [{}])[0].get("groundingMetadata") or {})
        sources = []
        for ch in meta.get("groundingChunks") or []:
            web = ch.get("web") or {}
            if web.get("uri"):
                sources.append((web.get("title", ""), web["uri"]))
        return text, sources[:4]

    def test(self) -> str:
        """One tiny request. Returns the model that answered, or raises."""
        self.complete("Reply with just the word OK.", 16)
        return self.model


def make_provider(settings) -> GeminiProvider:
    return GeminiProvider(settings.gemini_key, settings.model)
