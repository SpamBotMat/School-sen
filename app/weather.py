"""Погода для утренней сводки: Open-Meteo (бесплатно, без ключа) + советы по правилам.

Open-Meteo бесплатен для некоммерческого использования. Если проект станет платным —
нужен их коммерческий тариф (или другой источник): см. docs/DECISIONS.md.
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any

import httpx

from .timeutil import now

log = logging.getLogger(__name__)

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
AIR_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"

WMO: dict[int, tuple[str, str]] = {
    0: ("ясно", "☀️"), 1: ("почти ясно", "🌤"), 2: ("переменная облачность", "⛅"), 3: ("пасмурно", "☁️"),
    45: ("туман", "🌫"), 48: ("туман с изморозью", "🌫"),
    51: ("лёгкая морось", "🌦"), 53: ("морось", "🌦"), 55: ("сильная морось", "🌧"),
    56: ("ледяная морось", "🌧"), 57: ("ледяная морось", "🌧"),
    61: ("небольшой дождь", "🌦"), 63: ("дождь", "🌧"), 65: ("сильный дождь", "🌧"),
    66: ("ледяной дождь", "🌧"), 67: ("ледяной дождь", "🌧"),
    71: ("небольшой снег", "🌨"), 73: ("снег", "🌨"), 75: ("сильный снег", "❄️"), 77: ("снежная крупа", "🌨"),
    80: ("кратковременный дождь", "🌦"), 81: ("ливень", "🌧"), 82: ("сильный ливень", "⛈"),
    85: ("снегопад", "🌨"), 86: ("сильный снегопад", "❄️"),
    95: ("гроза", "⛈"), 96: ("гроза с градом", "⛈"), 99: ("гроза с градом", "⛈"),
}
RAIN_CODES = {51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82}
SNOW_CODES = {71, 73, 75, 77, 85, 86}
STORM_CODES = {95, 96, 99}
FOG_CODES = {45, 48}

# пороги советов
COLD_C, HOT_C, WIND_KMH, RAIN_PROB, DUST_UG, UV_HIGH = 3, 33, 50, 50, 150, 8


@dataclass
class DayWeather:
    date: str
    tmin: int | None = None
    tmax: int | None = None
    temp_morning: int | None = None     # в 8:00
    code: int | None = None
    desc: str = ""
    emoji: str = "🌡"
    precip_prob: int | None = None      # максимум с 7 до 16
    precip_sum: float | None = None
    gusts: int | None = None
    uv: float | None = None
    dust: int | None = None
    advice: list[str] = field(default_factory=list)   # ключи заготовок: weather.rain ...

    def as_dict(self) -> dict:
        return asdict(self)


def describe(code: int | None) -> tuple[str, str]:
    if code is None:
        return "", "🌡"
    return WMO.get(int(code), ("", "🌡"))


def _r(v: Any) -> int | None:
    try:
        return None if v is None else int(round(float(v)))
    except (TypeError, ValueError):
        return None


def advice_for(w: DayWeather) -> list[str]:
    """Ключи заготовок по приоритету (в сводку идут первые два)."""
    keys: list[str] = []
    code = w.code if w.code is not None else -1
    if code in STORM_CODES:
        keys.append("weather.storm")
    if code in SNOW_CODES:
        keys.append("weather.snow")
    if (code in RAIN_CODES or (w.precip_prob or 0) >= RAIN_PROB) and "weather.storm" not in keys:
        keys.append("weather.rain")
    if (w.dust or 0) >= DUST_UG:
        keys.append("weather.dust")
    if (w.gusts or 0) >= WIND_KMH:
        keys.append("weather.wind")
    cold_ref = min(x for x in (w.tmin, w.temp_morning) if x is not None) if (w.tmin is not None or w.temp_morning is not None) else None
    if cold_ref is not None and cold_ref <= COLD_C:
        keys.append("weather.cold")
    if w.tmax is not None and w.tmax >= HOT_C:
        keys.append("weather.hot")
    elif (w.uv or 0) >= UV_HIGH:
        keys.append("weather.uv")
    if code in FOG_CODES:
        keys.append("weather.fog")
    if not keys and w.tmax is not None and 14 <= w.tmax <= 30 and (w.precip_prob or 0) < 30:
        keys.append("weather.nice")
    return keys


def parse_forecast(data: dict, air: dict | None = None) -> dict[str, DayWeather]:
    """Ответ Open-Meteo → {iso-дата: DayWeather}."""
    out: dict[str, DayWeather] = {}
    daily = data.get("daily") or {}
    days = daily.get("time") or []
    for i, d in enumerate(days):
        def g(name: str) -> Any:
            arr = daily.get(name) or []
            return arr[i] if i < len(arr) else None
        code = _r(g("weather_code"))
        desc, emoji = describe(code)
        out[d] = DayWeather(date=d, tmin=_r(g("temperature_2m_min")), tmax=_r(g("temperature_2m_max")),
                            code=code, desc=desc, emoji=emoji, precip_sum=g("precipitation_sum"),
                            gusts=_r(g("wind_gusts_10m_max")), uv=g("uv_index_max"))
    hourly = data.get("hourly") or {}
    for i, t in enumerate(hourly.get("time") or []):
        d, hh = t[:10], int(t[11:13])
        w = out.get(d)
        if w is None:
            continue
        prob = (hourly.get("precipitation_probability") or [None] * (i + 1))[i]
        if 7 <= hh <= 16 and prob is not None:
            w.precip_prob = max(w.precip_prob or 0, int(prob))
        if hh == 8:
            w.temp_morning = _r((hourly.get("temperature_2m") or [None] * (i + 1))[i])
    if air:
        ah = air.get("hourly") or {}
        for i, t in enumerate(ah.get("time") or []):
            d, hh = t[:10], int(t[11:13])
            w = out.get(d)
            if w is None or not 7 <= hh <= 16:
                continue
            dust = (ah.get("dust") or [None] * (i + 1))[i]
            pm10 = (ah.get("pm10") or [None] * (i + 1))[i]
            val = max(v for v in (dust, pm10, 0) if v is not None)
            w.dust = max(w.dust or 0, int(val))
    for w in out.values():
        w.advice = advice_for(w)
    return out


async def fetch_weather(lat: float, lon: float, *, client: httpx.AsyncClient | None = None) -> dict[str, dict]:
    params = {
        "latitude": lat, "longitude": lon, "timezone": "Asia/Ashgabat", "forecast_days": 3,
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,wind_gusts_10m_max,uv_index_max",
        "hourly": "temperature_2m,precipitation_probability",
        "wind_speed_unit": "kmh",
    }
    own = client is None
    client = client or httpx.AsyncClient(timeout=15)
    try:
        r = await client.get(FORECAST_URL, params=params)
        r.raise_for_status()
        data = r.json()
        air = None
        try:
            ra = await client.get(AIR_URL, params={"latitude": lat, "longitude": lon, "timezone": "Asia/Ashgabat",
                                                   "hourly": "dust,pm10", "forecast_days": 3})
            if ra.status_code == 200:
                air = ra.json()
        except httpx.HTTPError:
            pass
        return {k: v.as_dict() for k, v in parse_forecast(data, air).items()}
    finally:
        if own:
            await client.aclose()


def line(w: dict | None, city: str) -> str:
    """«🌤 Ашхабад: +9…+18°, почти ясно»."""
    if not w:
        return ""
    def t(v: int | None) -> str:
        return "" if v is None else (f"+{v}" if v > 0 else str(v))
    temp = f"{t(w.get('tmin'))}…{t(w.get('tmax'))}°" if w.get("tmin") is not None else ""
    parts = [p for p in (temp, w.get("desc")) if p]
    return f"{w.get('emoji', '🌡')} {city}: " + ", ".join(parts)


class WeatherService:
    """Кэш прогноза в памяти (в БД не пишем — это лишние пробуждения Neon)."""

    def __init__(self, lat: float, lon: float, city: str):
        self.lat, self.lon, self.city = lat, lon, city
        self.days: dict[str, dict] = {}
        self.updated: datetime | None = None
        self.error: str | None = None

    async def refresh(self) -> bool:
        try:
            self.days = await fetch_weather(self.lat, self.lon)
            self.updated = now()
            self.error = None
            return True
        except (httpx.HTTPError, ValueError, KeyError) as e:
            self.error = f"{type(e).__name__}: {e}"[:200]
            log.warning("Погода: %s", self.error)
            return False

    def stale(self, minutes: int = 60) -> bool:
        return self.updated is None or (now() - self.updated).total_seconds() > minutes * 60

    def for_day(self, d: date) -> dict | None:
        return self.days.get(d.isoformat())
