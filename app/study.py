"""Study mode: turn notes / a PDF into a quiz or flashcards.

The AI writes the questions, but everything it returns is validated here
(right shape, 4 distinct options, a valid answer, nothing empty) and
questions that fail the checks are dropped, so the Study panel never
shows a broken card.
"""
from __future__ import annotations

import json
import random
from typing import Callable

MAX_SOURCE = 24000


class StudyError(Exception):
    pass


QUIZ_PROMPT = """You are making a study quiz for a student from THEIR material below.

Rules:
- Exactly {n} multiple-choice questions, each answerable ONLY from the material.
- Each has exactly 4 options; exactly one is correct; the wrong ones are plausible.
- "answer" is the 0-based index of the correct option. Vary its position.
- "why" is one short sentence explaining the answer, based on the material.
- Cover different parts of the material. No trick questions. No "all of the above".
{focus}
Reply with JSON only, no markdown:
[{{"q": "...", "options": ["...", "...", "...", "..."], "answer": 0, "why": "..."}}]

Material:
\"\"\"
{text}
\"\"\""""

CARDS_PROMPT = """You are making flashcards for a student from THEIR material below.

Rules:
- Exactly {n} cards covering the most important terms, facts and ideas.
- "front": a short term or question. "back": a clear, short answer (max 2 sentences).
- Only use information from the material.
{focus}
Reply with JSON only, no markdown:
[{{"front": "...", "back": "..."}}]

Material:
\"\"\"
{text}
\"\"\""""


def _json_list(raw: str) -> list:
    start, end = raw.find("["), raw.rfind("]")
    if start < 0 or end <= start:
        raise StudyError("the AI didn't return any questions")
    try:
        data = json.loads(raw[start:end + 1])
    except json.JSONDecodeError as e:
        raise StudyError(f"couldn't read the AI's questions ({e})")
    if not isinstance(data, list):
        raise StudyError("the AI returned something that isn't a list")
    return data


def valid_quiz(items: list) -> list[dict]:
    out, seen = [], set()
    for it in items:
        if not isinstance(it, dict):
            continue
        q = str(it.get("q", "")).strip()
        opts = [str(o).strip() for o in it.get("options", []) if str(o).strip()]
        ans = it.get("answer")
        if not q or len(opts) != 4 or len({o.lower() for o in opts}) != 4:
            continue
        if isinstance(ans, str) and ans.strip().isdigit():
            ans = int(ans.strip())
        if not isinstance(ans, int) or not 0 <= ans < 4:
            continue
        if q.lower() in seen:
            continue
        seen.add(q.lower())
        out.append({"q": q, "options": opts, "answer": ans,
                    "why": str(it.get("why", "")).strip()})
    return out


def valid_cards(items: list) -> list[dict]:
    out, seen = [], set()
    for it in items:
        if not isinstance(it, dict):
            continue
        f, b = str(it.get("front", "")).strip(), str(it.get("back", "")).strip()
        if f and b and f.lower() not in seen:
            seen.add(f.lower())
            out.append({"front": f, "back": b})
    return out


def make(complete: Callable[[str], str], text: str, mode: str = "quiz", count: int = 8,
         focus: str = "") -> list[dict]:
    """Ask the AI (once, then once more if too few items pass validation)."""
    text = (text or "").strip()
    if len(text.split()) < 25:
        raise StudyError("there isn't enough text to study from (need at least a paragraph)")
    count = max(3, min(20, int(count or 8)))
    text = text[:MAX_SOURCE]
    focus_line = f"- Focus on: {focus}" if focus else ""
    prompt = (QUIZ_PROMPT if mode == "quiz" else CARDS_PROMPT).format(
        n=count, text=text, focus=focus_line)
    check = valid_quiz if mode == "quiz" else valid_cards
    items: list[dict] = []
    for _attempt in range(2):
        try:
            items = check(_json_list(complete(prompt)))
        except StudyError:
            items = []
        if len(items) >= max(3, count // 2):
            break
    if len(items) < 3:
        raise StudyError("the AI couldn't make good questions from this; try a longer section")
    if mode == "quiz":
        for it in items:                  # make sure answers aren't always "A"
            pairs = list(enumerate(it["options"]))
            random.shuffle(pairs)
            it["options"] = [o for _, o in pairs]
            it["answer"] = [i for i, _ in pairs].index(it["answer"])
    return items[:count]
