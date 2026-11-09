"""Where you are (roughly) and today's weather, with nothing to type.

The city comes from your internet connection (city-level only, like any
website sees), refreshed once a week and kept in config.json. Weather
comes from Open-Meteo: free, no key, and it works with every brain.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

import httpx

log = logging.getLogger("kuro")

IP_SERVICES = [
    ("https://ipwho.is/", lambda d: (d.get("city"), d.get("latitude"), d.get("longitude"))
     if d.get("success", True) else None),
    ("https://ipapi.co/json/", lambda d: (d.get("city"), d.get("latitude"), d.get("longitude"))),
    ("https://get.geojs.io/v1/ip/geo.json",
     lambda d: (d.get("city"), d.get("latitude"), d.get("longitude"))),
]

WMO = {0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "cloudy", 45: "foggy",
       48: "foggy", 51: "light drizzle", 53: "drizzle", 55: "heavy drizzle",
       61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain",
       67: "freezing rain", 71: "light snow", 73: "snow", 75: "heavy snow",
       80: "rain showers", 81: "rain showers", 82: "heavy showers",
       95: "thunderstorms", 96: "thunderstorms with hail", 99: "thunderstorms with hail"}


def _get(url: str, **params) -> dict:
    r = httpx.get(url, params=params or None, timeout=8,
                  headers={"User-Agent": "Kuro desktop assistant"})
    r.raise_for_status()
    return r.json()


def from_ip() -> tuple[str, float, float] | None:
    for url, parse in IP_SERVICES:
        try:
            hit = parse(_get(url))
        except (httpx.HTTPError, ValueError, AttributeError) as e:
            log.info("location via %s failed: %s", url, e)
            continue
        if hit and hit[0] and hit[1] is not None and hit[2] is not None:
            return str(hit[0]), float(hit[1]), float(hit[2])
    return None


def geocode(city: str) -> tuple[str, float, float] | None:
    try:
        d = _get("https://geocoding-api.open-meteo.com/v1/search", name=city, count=1)
    except (httpx.HTTPError, ValueError) as e:
        log.info("geocode failed: %s", e)
        return None
    res = (d.get("results") or [None])[0]
    if not res:
        return None
    return res.get("name", city), float(res["latitude"]), float(res["longitude"])


def locate(s, today: date | None = None) -> tuple[str, float, float] | None:
    """Your city and coordinates. A city typed into config.json wins;
    otherwise the cached auto-location (a week old at most)."""
    today = today or date.today()
    if s.city.strip():
        return geocode(s.city.strip())
    fresh = s.auto_located and \
        date.fromisoformat(s.auto_located) > today - timedelta(days=7)
    if fresh and s.auto_city:
        return s.auto_city, s.auto_lat, s.auto_lon
    hit = from_ip()
    if hit:
        s.auto_city, s.auto_lat, s.auto_lon = hit
        s.auto_located = today.isoformat()
        try:
            s.save()
        except OSError:
            pass
        return hit
    if s.auto_city:                       # offline: last known place is fine
        return s.auto_city, s.auto_lat, s.auto_lon
    return None


def describe(data: dict) -> str:
    cur, day = data.get("current") or {}, data.get("daily") or {}
    temp = cur.get("temperature_2m")
    sky = WMO.get(int(cur.get("weather_code", -1)), "")
    hi = (day.get("temperature_2m_max") or [None])[0]
    lo = (day.get("temperature_2m_min") or [None])[0]
    rain = (day.get("precipitation_probability_max") or [None])[0]
    parts = []
    if temp is not None:
        parts.append(f"{round(temp)}°C" + (f" and {sky}" if sky else ""))
    if hi is not None and lo is not None:
        parts.append(f"high {round(hi)}°, low {round(lo)}°")
    if rain is not None:
        parts.append(f"{rain}% chance of rain")
    return ", ".join(parts)


def weather(s) -> str:
    """'Manila: 31°C and partly cloudy, high 33°, low 26°, 40% chance of rain'."""
    where = locate(s)
    if not where:
        return ""
    city, lat, lon = where
    try:
        data = _get("https://api.open-meteo.com/v1/forecast", latitude=lat, longitude=lon,
                    current="temperature_2m,weather_code",
                    daily="temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                    timezone="auto", forecast_days=1)
    except (httpx.HTTPError, ValueError) as e:
        log.info("weather failed: %s", e)
        return ""
    text = describe(data)
    return f"{city}: {text}" if text else ""
