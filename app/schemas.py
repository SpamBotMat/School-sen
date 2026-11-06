"""Настройки ученика, темы Mini App и «живые» настройки админа: значения по умолчанию и проверка."""
from __future__ import annotations

import re
from typing import Any

from .timeutil import parse_hhmm

USER_DEFAULTS: dict[str, Any] = {
    "morning": True, "morning_time": "07:30",      # утренняя сводка: погода, уроки, ДЗ
    "evening": True, "evening_time": "19:00",      # вечерняя: ДЗ на завтра
    "n_marks": True,                                # новые оценки
    "n_hw": True,                                   # новое ДЗ на завтра (после вечерней сводки)
    "hw_instant": False,                            # о каждом новом ДЗ сразу
    "n_attendance": True,                           # пропуски/опоздания
    "n_debts": True,                                # долги («точки», просрочки)
    "n_news": True,                                 # объявления школы
    "n_totals": True,                               # итоговые за четверть/год
    "reactions": True,                              # подписи-реакции к оценкам
    "quiet": True, "quiet_from": "22:00", "quiet_to": "07:00",
    "cheatsheet": True,                             # закреплённая «Шпаргалка»
    "weekly_archive": True,                         # архив картинками по воскресеньям
    "weather": True,
}

THEME_DEFAULTS: dict[str, Any] = {
    "palette": "telegram",      # telegram | ink | mint | lime | brutal | coffee | night | pastel | glass
    "mode": "auto",             # auto | light | dark
    "accent": "",               # свой акцент #RRGGBB (пусто — из палитры)
    "radius": 16,               # скругление 0..28
    "font": 100,                # размер текста, % (85..130)
    "lite": False,              # «Лёгкий режим»: без размытия, теней и анимаций
    "nav": "dock",              # dock | pill
}
PALETTES = ("telegram", "ink", "mint", "lime", "brutal", "coffee", "night", "pastel", "glass")

RUNTIME_DEFAULTS: dict[str, Any] = {
    "sync_interval": 30,          # минут между опросами одного ученика
    "sync_jitter": 0.15,          # ± разброс, доля
    "sync_concurrency": 2,        # одновременных сессий к NetSchool
    "night_from": "23:00", "night_to": "06:00",   # ночью не опрашиваем
    "slow_factor": 3.0,           # во сколько раз реже в воскресенье и на каникулах
    "report_every": 120,          # «Успеваемость и посещаемость» — раз в N минут
    "details_per_sync": 3,        # сколько «подробностей ДЗ» подтягивать за раз
    "sync_paused": False,         # стоп-кран: опрос NetSchool на паузе
    "maintenance": False,         # техработы: бот отвечает заглушкой
    "maintenance_text": "Идут техработы 🛠 Скоро вернусь!",
    "registration": "open",       # open | invite | closed
    "calc_sure": 0.62,            # «точно» выйдет оценка выше, если дробная часть ≥ этого
    "calc_maybe": 0.5,            # «можно вытянуть» от этого
    "weekly_archive_time": "17:00",
    "announcements": True,        # следить за доской объявлений
    "files_per_day": 10,
    "manual_sync_minutes": 10,
    "feedback_per_day": 10,
    "breaker_streak": 6,          # столько ошибок подряд → автопауза
    "breaker_cooldown": 20,       # минут паузы (дальше удваивается, до 4 часов)
    "bells": {                    # звонки (уроки 1–6 сверены с сайтом, 7–8 — примерно)
        "1": ["09:00", "09:45"], "2": ["09:55", "10:40"], "3": ["10:50", "11:35"],
        "4": ["11:55", "12:40"], "5": ["12:55", "13:40"], "6": ["13:50", "14:35"],
        "7": ["14:45", "15:30"], "8": ["15:40", "16:25"],
    },
    "welcome_note": "",           # доп. строка в приветствии /start
}

_HHMM = re.compile(r"^([01]?\d|2[0-3]):[0-5]\d$")
_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


class ValidationError(ValueError):
    pass


def _time(value: Any, lo: str = "00:00", hi: str = "23:59") -> str:
    if not isinstance(value, str) or not _HHMM.match(value.strip()):
        raise ValidationError(f"время в формате ЧЧ:ММ, а не {value!r}")
    t = parse_hhmm(value)
    if not (parse_hhmm(lo) <= t <= parse_hhmm(hi)):
        raise ValidationError(f"время должно быть между {lo} и {hi}")
    return t.strftime("%H:%M")


def clean_user_settings(patch: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in (patch or {}).items():
        if key not in USER_DEFAULTS:
            continue
        default = USER_DEFAULTS[key]
        if isinstance(default, bool):
            out[key] = bool(value)
        elif key == "morning_time":
            out[key] = _time(value, "05:00", "11:00")
        elif key == "evening_time":
            out[key] = _time(value, "14:00", "23:30")
        elif key in ("quiet_from", "quiet_to"):
            out[key] = _time(value)
    return out


def clean_theme(patch: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in (patch or {}).items():
        if key == "palette" and value in PALETTES:
            out[key] = value
        elif key == "mode" and value in ("auto", "light", "dark"):
            out[key] = value
        elif key == "accent" and (value in ("", None) or (isinstance(value, str) and _COLOR.match(value))):
            out[key] = value or ""
        elif key == "radius" and isinstance(value, (int, float)):
            out[key] = int(min(28, max(0, value)))
        elif key == "font" and isinstance(value, (int, float)):
            out[key] = int(min(130, max(85, value)))
        elif key == "lite":
            out[key] = bool(value)
        elif key == "nav" and value in ("dock", "pill"):
            out[key] = value
    return out


def clean_runtime(patch: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    rng = {
        "sync_interval": (10, 240), "sync_concurrency": (1, 4), "report_every": (30, 720),
        "details_per_sync": (0, 10), "files_per_day": (0, 100), "manual_sync_minutes": (1, 120),
        "feedback_per_day": (1, 100), "breaker_streak": (2, 50), "breaker_cooldown": (5, 240),
    }
    for key, value in (patch or {}).items():
        if key not in RUNTIME_DEFAULTS:
            continue
        default = RUNTIME_DEFAULTS[key]
        try:
            if key in rng:
                lo, hi = rng[key]
                v = int(value)
                if not lo <= v <= hi:
                    raise ValidationError(f"{key}: от {lo} до {hi}")
                out[key] = v
            elif key == "sync_jitter":
                out[key] = min(0.5, max(0.0, float(value)))
            elif key == "slow_factor":
                out[key] = min(10.0, max(1.0, float(value)))
            elif key in ("calc_sure", "calc_maybe"):
                v = float(value)
                if not 0.0 < v < 1.0:
                    raise ValidationError(f"{key}: дробная часть от 0 до 1 (например 0.62)")
                out[key] = round(v, 3)
            elif key in ("night_from", "night_to", "weekly_archive_time"):
                out[key] = _time(value)
            elif key == "registration":
                if value not in ("open", "invite", "closed"):
                    raise ValidationError("registration: open | invite | closed")
                out[key] = value
            elif key == "bells":
                if not isinstance(value, dict):
                    raise ValidationError("bells: объект {номер: [начало, конец]}")
                bells = {}
                for num, pair in value.items():
                    if not str(num).isdigit() or not isinstance(pair, (list, tuple)) or len(pair) != 2:
                        raise ValidationError("bells: {\"1\": [\"09:00\", \"09:45\"], ...}")
                    bells[str(int(num))] = [_time(pair[0]), _time(pair[1])]
                out[key] = bells
            elif isinstance(default, bool):
                out[key] = bool(value)
            elif isinstance(default, str):
                out[key] = str(value)[:500]
        except (TypeError, ValueError) as e:
            if isinstance(e, ValidationError):
                raise
            raise ValidationError(f"{key}: неверное значение") from e
    if "calc_sure" in out or "calc_maybe" in out:
        sure = out.get("calc_sure", RUNTIME_DEFAULTS["calc_sure"])
        maybe = out.get("calc_maybe", RUNTIME_DEFAULTS["calc_maybe"])
        if maybe > sure:
            raise ValidationError("«можно вытянуть» должно быть не больше «точно»")
    return out
