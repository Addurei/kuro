"""Kuro's brain. Pick one in Settings → AI & Voice:

  gemini      Google Gemini (free key)          native REST API
  groq        Groq (free key, very fast)        OpenAI-compatible
  openrouter  OpenRouter (free models)          OpenAI-compatible
  ollama      Ollama (free, offline, on PC)     OpenAI-compatible, no key
  openai      OpenAI (paid key)                 OpenAI-compatible
  claude      Anthropic Claude (paid key)       native Messages API

Kuro keeps one conversation format internally (Anthropic-style content
blocks, see brain.py). Each provider translates it to its own API and
back, and they all offer the same methods: chat, complete, vision,
web_answer, test.

Models change all the time, so unless you type one in Settings, Kuro asks
the provider which models this key can use and picks the best one.
"""
from __future__ import annotations

import base64
import html as _html
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
    label = "Gemini"

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




# ===================================================================== presets
PRESETS: dict[str, dict] = {
    "gemini": {"label": "Gemini", "tag": "free", "needs_key": True,
               "key_url": "https://aistudio.google.com/apikey",
               "key_hint": "Free key from Google AI Studio (small daily limit)"},
    "groq": {"label": "Groq", "tag": "free", "badge": "★ best free", "needs_key": True,
             "base": "https://api.groq.com/openai/v1",
             "key_url": "https://console.groq.com/keys",
             "key_hint": "Free key, very fast, generous daily limits",
             "prefer": ["openai/gpt-oss-120b", "llama-3.3-70b", "kimi-k2", "qwen3-32b",
                        "llama-4", "gpt-oss-20b"],
             "light": ["llama-3.1-8b-instant", "gpt-oss-20b", "llama-3.3-70b"],
             "skip": ["whisper", "tts", "guard", "playai", "orpheus", "distil", "compound"]},
    "openrouter": {"label": "OpenRouter", "tag": "free", "needs_key": True,
                   "base": "https://openrouter.ai/api/v1",
                   "key_url": "https://openrouter.ai/keys",
                   "key_hint": "Free key; Kuro only uses the free models",
                   "prefer": ["deepseek", "qwen3", "gpt-oss", "llama-3.3-70b", "gemini",
                              "mistral", "glm"],
                   "free_only": True},
    "ollama": {"label": "Ollama", "tag": "offline", "needs_key": False,
               "base": "http://localhost:11434/v1",
               "key_url": "https://ollama.com/download",
               "key_hint": "Runs on this PC, no internet. Run setup_offline_brain.bat once",
               "prefer": ["qwen3", "qwen2.5", "llama3.1", "llama3.2", "mistral", "gemma"]},
    "openai": {"label": "OpenAI", "tag": "paid", "needs_key": True,
               "base": "https://api.openai.com/v1",
               "key_url": "https://platform.openai.com/api-keys",
               "key_hint": "Paid key from OpenAI",
               "prefer": ["gpt-5-mini", "gpt-5", "gpt-4.1-mini", "gpt-4o-mini"],
               "light": ["gpt-5-nano", "gpt-4.1-nano", "gpt-5-mini", "gpt-4o-mini"],
               "skip": ["audio", "realtime", "tts", "transcribe", "image", "embedding",
                        "search", "moderation", "dall", "whisper", "codex", "instruct"],
               "openai_params": True},
    "cloud": {"label": "Kuro Cloud", "tag": "subscription", "needs_key": True,
              "base": "", "key_url": "", "key_hint": "Your Kuro license key",
              "prefer": []},
    "claude": {"label": "Claude", "tag": "paid", "needs_key": True,
               "key_url": "https://console.anthropic.com/settings/keys",
               "key_hint": "Paid key from Anthropic"},
}
ORDER = ["groq", "gemini", "openrouter", "ollama", "openai", "claude"]
RECOMMENDED = "groq"          # free, fast, and far more generous limits than Gemini's free tier
# when the chosen brain is out of quota / unreachable, try these (if they have keys)
FALLBACK_ORDER = ["groq", "gemini", "openrouter", "openai", "claude"]


def label(name: str) -> str:
    return PRESETS.get(name, PRESETS["gemini"])["label"]


# ===================================================================== shared
def _http(method: str, url: str, headers: dict, body: dict | None = None,
          timeout: float = 60.0, who: str = "the AI") -> dict:
    try:
        r = httpx.request(method, url, json=body, headers=headers, timeout=timeout)
    except (httpx.ConnectError, httpx.ConnectTimeout) as e:
        raise ProviderError("connection", f"can't connect to {who}", str(e)) from e
    except httpx.TimeoutException as e:
        raise ProviderError("connection", "timed out", str(e)) from e
    except httpx.HTTPError as e:
        raise ProviderError("connection", type(e).__name__, str(e)) from e
    if r.status_code != 200:
        raise classify(r.status_code, r.text)
    try:
        return r.json()
    except ValueError as e:
        raise ProviderError("other", f"bad JSON from {who}", r.text[:300]) from e


def pick_models(available: list[str], prefer: list[str], skip: list[str] = ()) -> list[str]:
    """Order the models a key can use: preferred names first (in order),
    then the rest. Drops speech/image/embedding models."""
    ok = [m for m in available if not any(k in m.lower() for k in skip)]
    out: list[str] = []
    for want in prefer:
        out += [m for m in ok if want in m.lower() and m not in out]
    return out + [m for m in ok if m not in out]


def web_snippets(query: str, limit: int = 6) -> list[tuple[str, str, str]]:
    """(title, url, snippet) from DuckDuckGo's plain HTML results, so
    every brain can answer 'what's the news / score / price' questions."""
    try:
        r = httpx.get("https://html.duckduckgo.com/html/", params={"q": query},
                      headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
                      timeout=15, follow_redirects=True)
    except httpx.HTTPError as e:
        raise ProviderError("connection", "can't reach the web", str(e)) from e
    out = []
    blocks = re.findall(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>.*?'
                        r'class="result__snippet"[^>]*>(.*?)</a>', r.text, re.S)
    for href, title, snip in blocks[:limit]:
        m = re.search(r"uddg=([^&]+)", href)
        url = httpx.URL("https://x/?u=" + m.group(1)).params.get("u") if m else href
        strip = lambda t: _html.unescape(re.sub(r"<[^>]+>", "", t)).strip()
        out.append((strip(title), url, strip(snip)))
    return out


class _Base:
    """What every non-Gemini brain shares: web answers from search snippets."""
    label = "the AI"

    def complete(self, prompt: str, max_tokens: int = 1024, light: bool = False) -> str:
        raise NotImplementedError

    def web_answer(self, question: str) -> tuple[str, list[tuple[str, str]]]:
        hits = web_snippets(question)
        if not hits:
            return "I couldn't find anything on the web for that.", []
        ctx = "\n".join(f"[{i + 1}] {t}: {s}" for i, (t, _u, s) in enumerate(hits))
        text = self.complete(
            f"Today's web search results for: {question}\n\n{ctx}\n\n"
            "Answer the question in 1-3 sentences using only these results. "
            "If they don't contain the answer, say so.", 600)
        return text, [(t, u) for t, u, _s in hits[:4]]

    def test(self) -> str:
        self.complete("Reply with just the word OK.", 16)
        return self.model


# ============================================================ OpenAI-compatible
def to_openai(messages: list[dict]) -> list[dict]:
    out: list[dict] = []
    for m in messages:
        c = m["content"]
        if isinstance(c, str):
            out.append({"role": m["role"], "content": c})
            continue
        if m["role"] == "user":
            texts = []
            for b in c:
                if b.get("type") == "tool_result":
                    content = str(b.get("content", ""))
                    if b.get("is_error"):
                        content = "ERROR: " + content
                    out.append({"role": "tool", "tool_call_id": b["tool_use_id"],
                                "content": content})
                elif b.get("type") == "text":
                    texts.append(b["text"])
            if texts:
                out.append({"role": "user", "content": "\n".join(texts)})
            continue
        text = " ".join(b.get("text", "") for b in c if b.get("type") == "text").strip()
        calls = [{"id": b["id"], "type": "function",
                  "function": {"name": b["name"],
                               "arguments": json.dumps(b.get("input") or {})}}
                 for b in c if b.get("type") == "tool_use"]
        msg: dict = {"role": "assistant", "content": text or None}
        if calls:
            msg["tool_calls"] = calls
        out.append(msg)
    return out


def openai_tools(tools: list[dict]) -> list[dict]:
    out = []
    for t in tools:
        params = dict(t.get("input_schema") or {})
        params.setdefault("type", "object")
        params.setdefault("properties", {})
        out.append({"type": "function", "function": {
            "name": t["name"], "description": t["description"], "parameters": params}})
    return out


def from_openai(data: dict) -> Reply:
    choices = data.get("choices") or []
    if not choices:
        raise ProviderError("blocked", "no answer", json.dumps(data)[:300])
    msg = choices[0].get("message") or {}
    blocks: list[dict] = []
    text = msg.get("content") or ""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()   # reasoning models
    if text:
        blocks.append({"type": "text", "text": text})
    calls = msg.get("tool_calls") or []
    for c in calls:
        fn = c.get("function") or {}
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except ValueError:
            args = {}
        blocks.append({"type": "tool_use", "id": c.get("id") or f"call_{uuid.uuid4().hex[:10]}",
                       "name": fn.get("name", ""), "input": args if isinstance(args, dict) else {}})
    if not blocks:
        blocks.append({"type": "text",
                       "text": f"(My answer got cut off: {choices[0].get('finish_reason')}.)"})
    return Reply("tool_use" if calls else "end_turn", blocks)


class OpenAIProvider(_Base):
    def __init__(self, name: str, api_key: str = "", model: str = "", timeout: float = 90.0,
                 base: str = ""):
        self.name = name
        self.p = PRESETS[name]
        self.label = self.p["label"]
        self.base = (base or self.p["base"]).rstrip("/")
        self.api_key = (api_key or "").strip()
        self.timeout = timeout
        self.fixed = model.strip()
        self._models: list[str] | None = [self.fixed] if self.fixed else None
        self._good = 0

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        if self.name == "openrouter":
            h["X-Title"] = "Kuro"
        return h

    @property
    def models(self) -> list[str]:
        if self._models is None:
            data = _http("GET", self.base + "/models", self._headers(), timeout=20,
                         who=self.label)
            items = data.get("data") or data.get("models") or []
            ids = []
            for it in items:
                mid = it.get("id") or it.get("name") if isinstance(it, dict) else str(it)
                if not mid:
                    continue
                if self.p.get("free_only"):
                    params = it.get("supported_parameters") or []
                    if not mid.endswith(":free") or (params and "tools" not in params):
                        continue
                ids.append(mid)
            ordered = pick_models(ids, self.p.get("prefer", []), self.p.get("skip", []))
            if not ordered:
                hint = {"ollama": "No models installed yet. Run setup_offline_brain.bat.",
                        "openrouter": "No free tool-using models are available right now."
                        }.get(self.name, "This key can't use any chat models.")
                raise ProviderError("model_missing", "no models", hint)
            self._models = ordered[:6]
        return self._models

    @property
    def model(self) -> str:
        return self.models[min(self._good, len(self.models) - 1)]

    def _light_order(self) -> list[int]:
        ms = self.models
        for want in self.p.get("light", []):
            for i, m in enumerate(ms):
                if want in m.lower():
                    return [i] + [j for j in range(len(ms)) if j != i]
        return [self._good] + [j for j in range(len(ms)) if j != self._good]

    def _call(self, body: dict, light: bool = False) -> dict:
        ms = self.models
        order = self._light_order() if light else \
            [self._good] + [i for i in range(len(ms)) if i != self._good]
        last: ProviderError | None = None
        for i in order[:4]:
            try:
                data = _http("POST", self.base + "/chat/completions", self._headers(),
                             {**body, "model": ms[i]}, self.timeout, self.label)
                if not light:
                    self._good = i
                return data
            except ProviderError as e:
                if self.name == "ollama" and e.kind == "connection":
                    e.detail = "Ollama isn't running. Start the Ollama app, then try again."
                log.warning("%s %s failed: %s %s", self.name, ms[i], e.kind, e.detail)
                if e.kind not in ("rate", "model_missing", "bad_request") or self.fixed:
                    raise
                last = e
        raise last  # type: ignore[misc]

    def _limits(self, max_tokens: int, temperature: float) -> dict:
        if self.p.get("openai_params"):            # newer OpenAI models: no temperature
            return {"max_completion_tokens": max(max_tokens, 4096)}
        return {"max_tokens": max_tokens, "temperature": temperature}

    def chat(self, persona: str, context: str, tools: list[dict],
             messages: list[dict], max_tokens: int) -> Reply:
        body = {"messages": [{"role": "system", "content": persona + "\n\n" + context}]
                + to_openai(messages), **self._limits(max(max_tokens, 1024), 0.7)}
        if tools:
            body["tools"] = openai_tools(tools)
        return from_openai(self._call(body))

    def complete(self, prompt: str, max_tokens: int = 1024, light: bool = False) -> str:
        data = self._call({"messages": [{"role": "user", "content": prompt}],
                           **self._limits(max(max_tokens, 256), 0.2)}, light=light)
        return clean_text(" ".join(b["text"] for b in from_openai(data).content
                                   if b["type"] == "text"))

    def vision(self, prompt: str, jpeg: bytes) -> str:
        url = "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")
        try:
            data = self._call({"messages": [{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": url}}]}],
                **self._limits(1024, 0.3)})
        except ProviderError as e:
            if e.kind in ("bad_request", "model_missing"):
                raise ProviderError("other", "no vision",
                                    f"This {self.label} model can't see images. "
                                    "Gemini or Claude can.") from e
            raise
        return clean_text(" ".join(b["text"] for b in from_openai(data).content
                                   if b["type"] == "text"))


# ===================================================================== Claude
CLAUDE_API = "https://api.anthropic.com/v1"
CLAUDE_MODELS = ["claude-haiku-4-5", "claude-sonnet-4-5"]


def _claude_messages(messages: list[dict]) -> list[dict]:
    out = []
    for m in messages:
        c = m["content"]
        if isinstance(c, list):
            c = [{k: v for k, v in b.items() if k != "extra"} for b in c]
        out.append({"role": m["role"], "content": c})
    return out


class ClaudeProvider(_Base):
    name = "claude"
    label = "Claude"

    def __init__(self, api_key: str, model: str = "", timeout: float = 90.0):
        self.api_key = api_key.strip()
        self.timeout = timeout
        self.fixed = model.strip()
        self._models: list[str] | None = [self.fixed] if self.fixed else None
        self._good = 0

    def _headers(self) -> dict:
        return {"x-api-key": self.api_key, "anthropic-version": "2023-06-01",
                "Content-Type": "application/json"}

    @property
    def models(self) -> list[str]:
        if self._models is None:
            try:
                data = _http("GET", CLAUDE_API + "/models?limit=100", self._headers(),
                             timeout=20, who="Claude")
                ids = [d["id"] for d in data.get("data", []) if d.get("id")]
            except ProviderError as e:
                if e.kind == "auth":
                    raise
                ids = []
            ordered = pick_models(ids, ["haiku", "sonnet"], ["opus"]) or list(CLAUDE_MODELS)
            self._models = ordered[:4]
        return self._models

    @property
    def model(self) -> str:
        return self.models[self._good]

    def _call(self, body: dict) -> dict:
        ms = self.models
        order = [self._good] + [i for i in range(len(ms)) if i != self._good]
        last: ProviderError | None = None
        for i in order:
            try:
                data = _http("POST", CLAUDE_API + "/messages", self._headers(),
                             {**body, "model": ms[i]}, self.timeout, "Claude")
                self._good = i
                return data
            except ProviderError as e:
                log.warning("claude %s failed: %s %s", ms[i], e.kind, e.detail)
                if e.kind not in ("rate", "model_missing") or self.fixed:
                    raise
                last = e
        raise last  # type: ignore[misc]

    @staticmethod
    def _reply(data: dict) -> Reply:
        blocks = [b for b in data.get("content", []) if b.get("type") in ("text", "tool_use")]
        return Reply("tool_use" if data.get("stop_reason") == "tool_use" else "end_turn", blocks)

    def _text(self, data: dict) -> str:
        return clean_text(" ".join(b.get("text", "") for b in self._reply(data).content
                                   if b["type"] == "text"))

    def chat(self, persona: str, context: str, tools: list[dict],
             messages: list[dict], max_tokens: int) -> Reply:
        body = {"system": persona + "\n\n" + context, "max_tokens": max(max_tokens, 1024),
                "messages": _claude_messages(messages)}
        if tools:
            body["tools"] = tools
        return self._reply(self._call(body))

    def complete(self, prompt: str, max_tokens: int = 1024, light: bool = False) -> str:
        return self._text(self._call({"max_tokens": max(max_tokens, 256),
                                      "messages": [{"role": "user", "content": prompt}]}))

    def vision(self, prompt: str, jpeg: bytes) -> str:
        return self._text(self._call({"max_tokens": 1024, "messages": [{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                         "data": base64.b64encode(jpeg).decode("ascii")}},
            {"type": "text", "text": prompt}]}]}))


# ===================================================================== fallback
class FallbackProvider:
    """The chosen brain first; when it's out of quota, overloaded or can't be
    reached, the next brain you have a key for answers instead, so Kuro never
    just stops. A brain that ran out rests for a while before it's tried again."""

    REST = {"rate": 15 * 60, "model_missing": 30 * 60, "connection": 60}

    def __init__(self, chain: list, on_switch=None):
        self.chain = chain                          # providers, chosen one first
        self.on_switch = on_switch or (lambda _frm, _to: None)
        self._rest_until: dict[str, float] = {}
        self.active = chain[0]

    @property
    def name(self) -> str:
        return self.active.name

    @property
    def label(self) -> str:
        return self.active.label

    @property
    def model(self) -> str:
        return self.active.model

    def _order(self) -> list:
        import time as _t
        now = _t.monotonic()
        awake = [p for p in self.chain if self._rest_until.get(p.name, 0) <= now]
        return awake or list(self.chain)             # all resting: try anyway

    def _run(self, method: str, *args, **kwargs):
        import time as _t
        last: ProviderError | None = None
        for p in self._order():
            try:
                out = getattr(p, method)(*args, **kwargs)
            except ProviderError as e:
                if e.kind not in self.REST:
                    raise                            # a bad key etc.: say so, don't hide it
                self._rest_until[p.name] = _t.monotonic() + self.REST[e.kind]
                log.info("brain %s unavailable (%s); trying the next one", p.name, e.kind)
                last = e
                continue
            if p is not self.active:
                prev, self.active = self.active, p
                try:
                    self.on_switch(prev.label, p.label)
                except Exception:
                    pass
            return out
        raise last  # type: ignore[misc]

    def chat(self, *a, **k):
        return self._run("chat", *a, **k)

    def complete(self, *a, **k):
        return self._run("complete", *a, **k)

    def vision(self, *a, **k):
        return self._run("vision", *a, **k)

    def web_answer(self, *a, **k):
        return self._run("web_answer", *a, **k)

    def test(self) -> str:
        return self.chain[0].test()


# ===================================================================== factory
def make(name: str, key: str = "", model: str = "", base: str = ""):
    name = name if name in PRESETS else "gemini"
    if name == "gemini":
        return GeminiProvider(key, model)
    if name == "claude":
        return ClaudeProvider(key, model)
    return OpenAIProvider(name, key, model, base=base)


def backups(settings) -> list[str]:
    """Other brains Kuro can fall back to: ones you've added a key for."""
    if not getattr(settings, "auto_fallback", True):
        return []
    return [n for n in FALLBACK_ORDER
            if n != settings.provider and settings.key_for(n).strip()]


def make_provider(settings, on_switch=None):
    base = getattr(settings, "cloud_url", "") if settings.provider == "cloud" else ""
    first = make(settings.provider, settings.key_for(), settings.model, base=base)
    rest = [make(n, settings.key_for(n)) for n in backups(settings)]
    if not rest:
        return first
    return FallbackProvider([first] + rest, on_switch)
