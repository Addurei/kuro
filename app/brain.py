"""The cat's brain: Claude + a tool-use loop.

Flow for one message:
  1. send history + new message + tool list to Claude
  2. if Claude asks for tools, run them (asking you first for risky ones)
  3. send the results back, repeat until Claude answers in plain text

`ask()` blocks, so the UI calls it from a worker thread.

The thinking is done by whichever brain is picked in Settings (app/providers.py).
"""
from __future__ import annotations

import datetime as _dt
import json
import logging
import re
from pathlib import Path
from typing import Callable

from .config import Settings
from .i18n import REPLY_RULE
from .providers import GeminiProvider, ProviderError, backups, label, make_provider
from .tools import Tool, run_tool

MAX_TOOL_ROUNDS = 8
log = logging.getLogger("kuro")

Confirm = Callable[[Tool, dict], bool]
Notify = Callable[[str], None]
OnAction = Callable[[str, dict], None]


def persona_prompt(s: Settings) -> str:
    return f"""You are {s.cat_name}, {s.personality}
You live as a little animated cat on {s.user_name}'s Windows desktop and help them with their computer and their life.

How you reply:
- {REPLY_RULE.get(getattr(s, "language", "English"), REPLY_RULE["English"])}
- Your words appear in a small speech bubble and may be read aloud, so keep replies short (1-3 sentences) unless asked for detail. No markdown, no bullet lists, no emoji spam.
- Stay in character through your wording and attitude, but being genuinely useful always comes first.
- Never write actions, sounds or stage directions in asterisks or brackets (no *yawns*, *wags tail*, *purrs*, *mrrp*, (stretches)). Just talk.

How you act:
- Use your tools to actually do things rather than describing how the user could do them.
- Some tools (power actions, PowerShell) pop up an approval dialog. If the user declines, accept it gracefully and don't retry.
- Never use PowerShell to delete, overwrite or move files, change system settings, or download things unless the user explicitly asked for exactly that.
- If a tool returns an error, explain it plainly and suggest what to try.
- If you can't do something, say so honestly. Never pretend an action happened.
- After a tool runs, base what you say on its result. If open_app says no window appeared or the app wasn't found, say exactly that, never "it's open".
- To see or read anything (the screen, a file or image, copied text, the song playing, the PC), use scan, and only when asked. Report scan results exactly: quote numbers, names and text as given, say plainly when something wasn't found, and never fill gaps with guesses. Never describe what is on the screen, in a file, or on a website without having actually scanned it.
- Never try to identify real people from their faces or photos.
- For questions about things that change (news, sports scores, weather, prices), use web_answer and give the answer in chat. Use web_search only when they want to browse results.
- Use the browser the user names or is known to prefer (see what you know about them).
- You may be told which app {s.user_name} is using right now. Use it naturally to be helpful ("working on Kuro again?"), never to lecture.
- For ANY sum, percentage or unit conversion use calculate / convert_units; never work numbers out yourself.
- Show, don't just say: a random pick is the show tool's wheel ("spin the wheel", "pick for me"), chance is its coin or dice, and numbers the user gives or asks about become one of its charts. Quizzes and flashcards from their notes are the study tool.
- Things to keep go in files (write_file) or the to-do list (todo); things for a set time are add_reminder.
- When they describe a routine ("when I say study mode, ...") save it; when they say a saved name, run it and do every step it returns.
- When the user asks you to play something, use play_media so it starts immediately (never just search for it). If they give a time ("at 0:10", "from 1:30"), pass it as start_at. To jump within what's already playing, use seek_media; to pause/resume/skip track, use media.

How you learn:
- You have a long-term memory (shown below as "What you know") and a record of {s.user_name}'s habits. Use them naturally to personalise help: their projects, their preferences, what they usually do at this time. Don't recite them back unprompted, and never make them feel watched.
- When {s.user_name} tells you something worth knowing next time, or says "remember", call remember. If they say to forget something, or a fact is wrong, call forget.
- Memory lives only on this computer, in the Kuro folder of AppData. You keep learning automatically from your chats (what they tell you, how they like things done) and get better over time. If asked what you know, use recall. If asked to forget something, use forget; to wipe everything, use forget_everything (it asks them first)."""


# *yawns*, *wags tail*, (stretches), [mrrp]: roleplay actions. The
# persona forbids them, and this catches any that slip through anyway.
_ACTION = re.compile(
    r"\*{1,2}[A-Za-z][A-Za-z' ,;.~-]{0,40}?\*{1,2}"
    r"|[(\[]\s*(?:mrr+p|mr+o+w|purr+s?|meow|yawns?|stretch(?:es)?|wags?[^)\]]{0,20}|"
    r"tail[^)\]]{0,20}|blinks?[^)\]]{0,20}|ears?[^)\]]{0,20})\s*[)\]]",
    re.I)


def strip_actions(text: str) -> str:
    out = _ACTION.sub("", text or "")
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r"^\s*[,.…!]+\s*", "", out)           # "*yawn* …I'm up" -> "I'm up"
    out = re.sub(r"\s+([,.!?…])", r"\1", out)
    return out.strip()


def live_text(raw: str) -> str:
    """What to show while a reply is still streaming in: actions removed, and
    a half-written '*action' or '(stretch' held back until it closes."""
    t = raw
    if t.count("*") % 2:                       # an *action* that hasn't closed yet
        t = t[:t.rfind("*")]
    m = re.search(r"[(\[][^)\]]{0,24}$", t)   # "(stret" at the very end
    if m:
        t = t[:m.start()]
    return strip_actions(t)


def context_prompt(s: Settings, memory_text: str = "", habits_text: str = "",
                   extra: str = "") -> str:
    now = _dt.datetime.now().astimezone()
    parts = [f"Current local time: {now:%A %d %B %Y, %H:%M} ({now.tzname()})."]
    if memory_text:
        parts.append(f"What you know about {s.user_name}:\n{memory_text}")
    if habits_text:
        parts.append(f"{s.user_name}'s recent habits (from things you've done for them):\n{habits_text}")
    if extra:
        parts.append(extra)
    return "\n\n".join(parts)


def _slim(msg: dict, limit: int = 600) -> dict:
    content = msg.get("content")
    if not isinstance(content, list):
        return msg
    blocks = []
    for b in content:
        if not isinstance(b, dict):
            blocks.append(b)
            continue
        b = {k: v for k, v in b.items() if k != "extra"}
        if b.get("type") == "tool_result" and isinstance(b.get("content"), str) \
                and len(b["content"]) > limit:
            b["content"] = b["content"][:limit] + " …(trimmed)"
        blocks.append(b)
    return {**msg, "content": blocks}


class Brain:
    def __init__(self, settings: Settings, tools: dict[str, Tool],
                 confirm: Confirm, notify: Notify | None = None,
                 provider: GeminiProvider | None = None,
                 context: Callable[[], str] | None = None,
                 on_action: OnAction | None = None,
                 on_switch: Callable[[str, str], None] | None = None):
        self.s = settings
        self.tools = tools
        self.confirm = confirm
        self.notify = notify or (lambda _msg: None)
        self.context = context or (lambda: context_prompt(settings))
        self.on_action = on_action or (lambda _n, _a: None)
        self.on_switch = on_switch
        self.history: list[dict] = []
        self._provider = provider  # injectable for tests

    @property
    def provider(self) -> GeminiProvider:
        if self._provider is None:
            if not self.s.ready:
                raise RuntimeError("no API key")
            self._provider = make_provider(self.s, self.on_switch)
        return self._provider

    def refresh_provider(self) -> None:
        """Call after the settings change, e.g. a new key or brain."""
        self._provider = None

    def reset(self) -> None:
        self.history.clear()

    # ------------------------------------------------------------------
    def ask(self, text: str, on_text: Callable[[str], None] | None = None) -> str:
        """One message in, one reply out. With on_text, the reply streams:
        on_text gets the whole cleaned reply-so-far every time it grows."""
        start = len(self.history)
        self.history.append({"role": "user", "content": text})
        try:
            reply = self._loop(on_text)
        except Exception as e:
            del self.history[start:]  # never leave a half-finished tool exchange
            return self._friendly_error(e)
        self._trim()
        return reply

    def _loop(self, on_text: Callable[[str], None] | None = None) -> str:
        api_tools = [t.as_api() for t in self.tools.values()]
        persona = persona_prompt(self.s)
        said: list[str] = []                   # text from earlier rounds ("Opening it…")
        stream = on_text is not None and hasattr(self.provider, "chat_stream")
        for _ in range(MAX_TOOL_ROUNDS):
            if stream:
                parts: list[str] = []
                shown = [""]

                def grow(delta: str, parts=parts, shown=shown) -> None:
                    parts.append(delta)
                    now = live_text(" ".join(said + ["".join(parts)]))
                    if now != shown[0]:
                        shown[0] = now
                        on_text(now)
                resp = self.provider.chat_stream(persona, self.context(), api_tools,
                                                 self.history, self.s.max_tokens, grow)
            else:
                resp = self.provider.chat(persona, self.context(), api_tools,
                                          self.history, self.s.max_tokens)
            content = resp.content
            self.history.append({"role": "assistant", "content": content})
            text = " ".join(b.get("text", "") for b in content
                            if b.get("type") == "text").strip()
            if resp.stop_reason != "tool_use":
                final = strip_actions(" ".join(said + [text]))
                return final or "Hmm, I lost my words there. Try again?"
            if text:
                said.append(text)

            results = []
            for block in content:
                if block.get("type") != "tool_use":
                    continue
                results.append(self._run_block(block))
            self.history.append({"role": "user", "content": results})
        return "I got a bit tangled up doing that. Could you try asking in smaller steps?"

    def _run_block(self, block: dict) -> dict:
        name, args = block["name"], block.get("input") or {}
        tool = self.tools.get(name)
        if tool is None:
            out, err = f"Error: unknown tool {name}", True
        elif tool.risky and not self.confirm(tool, args):
            out, err = "The user declined this action.", True
        else:
            self.notify(name)
            out, err = run_tool(tool, args)
            if not err:
                try:
                    self.on_action(name, args)
                except Exception:
                    pass  # habit logging must never break a reply
        return {"type": "tool_result", "tool_use_id": block["id"],
                "content": out, "is_error": err}

    def _trim(self) -> None:
        """Keep the last N user turns. Only cut at a plain-text user message,
        so a tool_use is never separated from its tool_result."""
        starts = [i for i, m in enumerate(self.history)
                  if m["role"] == "user" and isinstance(m["content"], str)]
        if len(starts) > self.s.history_turns:
            cut = starts[-self.s.history_turns]
            del self.history[:cut]

    # ---- persistence -------------------------------------------------
    def save(self, path: Path) -> None:
        """Save the conversation compactly: no model signatures, and long
        tool outputs (file contents, scans) trimmed, since they're only
        needed in the moment."""
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps([_slim(m) for m in self.history], ensure_ascii=False,
                                  separators=(",", ":")), encoding="utf-8")
        tmp.replace(path)

    def load(self, path: Path) -> None:
        """Restore a saved conversation, cut back to the last clean point
        (a plain assistant reply) so the API never sees a broken exchange."""
        try:
            hist = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(hist, list):
            return
        end = 0
        for i, m in enumerate(hist):
            if (isinstance(m, dict) and m.get("role") == "assistant"
                    and isinstance(m.get("content"), list)
                    and not any(b.get("type") == "tool_use" for b in m["content"])):
                end = i + 1
        hist = hist[:end]
        while hist and not (hist[0]["role"] == "user" and isinstance(hist[0]["content"], str)):
            hist.pop(0)
        self.history = hist
        self._trim()

    def visible_turns(self) -> list[tuple[str, str]]:
        """(who, text) pairs for redrawing the chat after a restart."""
        out = []
        for m in self.history:
            if m["role"] == "user" and isinstance(m["content"], str):
                out.append(("you", m["content"]))
            elif m["role"] == "assistant":
                text = " ".join(b.get("text", "") for b in m["content"]
                                if b.get("type") == "text").strip()
                if text and not any(b.get("type") == "tool_use" for b in m["content"]):
                    out.append(("cat", text))
        return out

    def _friendly_error(self, e: Exception) -> str:
        who = label(self.s.provider)
        if isinstance(e, RuntimeError) and "API key" in str(e):
            return (f"I need my {who} key first! Right-click me, open Settings → AI & Voice, "
                    "and paste it in.")
        if not isinstance(e, ProviderError):
            log.exception("brain error")
            return f"Something went wrong ({type(e).__name__}). Try again?"
        log.warning("brain error %s: %s", e.kind, e.detail)
        if e.kind == "auth":
            return f"{who} rejected my key. Could you check it in Settings? The Test button there tells you why."
        if e.kind == "rate":
            if not backups(self.s):
                other = "Gemini" if self.s.provider == "groq" else "Groq"
                return (f"I've hit {who}'s free limit for now. Add a free {other} key in "
                        "Settings → AI & Voice and I'll switch to it by myself next time.")
            return "All my brains are tired right now. Give me a minute and ask again."
        if e.kind == "connection":
            if self.s.provider == "ollama":
                return "I can't reach Ollama. Is the Ollama app running?"
            return f"I can't reach {who}. Is the internet on? (Settings → Test shows the details.)"
        if e.kind == "model_missing":
            return f"No {who} model is available right now. {e.detail or 'Try Settings → Test.'}"
        if e.kind == "blocked":
            return f"{who} wouldn't answer that one."
        return f"My brain hiccuped: {e.detail[:140] or e}"
