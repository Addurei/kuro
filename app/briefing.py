"""Daily briefing: one friendly message with today's essentials.

Every part is optional and gathered separately, so a missing piece
(no city set, offline, Google not connected) just gets left out.
"""
from __future__ import annotations

from datetime import datetime


def greeting(now: datetime, user: str) -> str:
    h = now.hour
    part = "Morning" if 4 <= h < 12 else "Afternoon" if h < 18 else "Evening"
    return f"{part}, {user}! It's {now.strftime('%A, %B')} {now.day}."


def compose(user: str, now: datetime, weather: str = "", reminders: list[str] | None = None,
            events: list[str] | None = None, unread: list[str] | None = None,
            focus_yesterday: tuple[int, int] | None = None) -> str:
    lines = [greeting(now, user)]
    if weather:
        lines.append(f"Weather: {weather.strip()}")
    if events is not None:
        if events:
            lines.append("Today: " + "; ".join(events[:6]) + ".")
        else:
            lines.append("Nothing on your calendar today.")
    if reminders:
        lines.append("Reminders: " + "; ".join(reminders[:5]) + ".")
    if unread:
        lines.append(f"{len(unread)} unread emails: " + "; ".join(unread[:3]) + ".")
    if focus_yesterday and focus_yesterday[1]:
        mins, sessions = focus_yesterday
        lines.append(f"Yesterday you focused {mins} min over {sessions} sessions.")
    return "\n".join(lines)


WEATHER_Q = ("Right now in {city}: current temperature in °C, conditions, today's high and "
             "low, and chance of rain. Answer in ONE short sentence, no preamble.")
