"""Заготовки фраз: админ пишет их в админке, бот выбирает случайно и без повторов подряд.

В тексте можно использовать подстановки: {name}, {subject}, {mark}, {tmin}, {tmax}, {temp},
{precip}, {wind}, {days}, {title}, {date}, {count}, {age}, {period}. Неизвестная — пустая строка.
Если у заготовки указан «предмет» — она берётся только для этого предмета (часть названия).
"""
from __future__ import annotations

import random
import string
from collections import deque
from typing import Any

CATALOG: dict[str, tuple[str, str]] = {
    "greet.morning": ("Утро: приветствие", "{name}"),
    "greet.monday": ("Утро понедельника", "{name}"),
    "greet.saturday": ("Утро субботы", "{name}"),
    "greet.evening": ("Вечер: приветствие", "{name}"),
    "greet.sunday": ("Вечер воскресенья (завтра понедельник)", "{name}"),
    "weather.rain": ("Погода: дождь", "{precip} {tmin} {tmax}"),
    "weather.snow": ("Погода: снег", "{tmin} {tmax}"),
    "weather.storm": ("Погода: гроза", "{precip}"),
    "weather.fog": ("Погода: туман", ""),
    "weather.cold": ("Погода: холодно (≤ +3°)", "{tmin} {temp}"),
    "weather.hot": ("Погода: жара (≥ +33°)", "{tmax}"),
    "weather.wind": ("Погода: сильный ветер", "{wind}"),
    "weather.dust": ("Погода: пыль", ""),
    "weather.uv": ("Погода: сильное солнце (УФ)", ""),
    "weather.nice": ("Погода: просто хорошая", "{tmin} {tmax}"),
    "mark.5": ("Реакция на 5", "{subject} {mark}"),
    "mark.4": ("Реакция на 4", "{subject} {mark}"),
    "mark.3": ("Реакция на 3", "{subject} {mark}"),
    "mark.2": ("Реакция на 2", "{subject} {mark}"),
    "hw.none": ("ДЗ на завтра нет", "{name}"),
    "hw.many": ("ДЗ на завтра много", "{count}"),
    "hw.all_done": ("Всё ДЗ отмечено сделанным", "{name}"),
    "debt.new": ("Новый долг", "{subject}"),
    "debt.reminder": ("Напоминание о долгах", "{count}"),
    "att.late": ("Опоздание", "{subject} {date}"),
    "att.absent": ("Пропуск", "{subject} {date}"),
    "birthday.self": ("День рождения ученика", "{name} {age}"),
    "holiday.tomorrow": ("Завтра праздник/выходной", "{title}"),
    "vacation.soon": ("Скоро каникулы", "{days} {title}"),
    "vacation.start": ("Каникулы начались", "{title}"),
    "vacation.end": ("Завтра снова в школу", "{name}"),
    "term.total": ("Выставлена итоговая", "{subject} {mark} {period}"),
    "mood.great": ("Главная: всё отлично", "{name}"),
    "mood.good": ("Главная: хорошо", "{name}"),
    "mood.ok": ("Главная: средне", "{name}"),
    "mood.bad": ("Главная: тяжело", "{name}"),
    "mood.none": ("Главная: оценок ещё нет", "{name}"),
    "tip.general": ("Совет по учёбе (иногда вечером)", ""),
    "welcome.connected": ("Ученик подключился", "{name}"),
}


class _Safe(dict):
    def __missing__(self, key: str) -> str:
        return ""


_FMT = string.Formatter()


def render(text: str, ctx: dict[str, Any]) -> str:
    """Безопасная подстановка: {неизвестное} → '', кривые скобки — текст как есть."""
    try:
        return _FMT.vformat(text, (), _Safe({k: ("" if v is None else v) for k, v in ctx.items()}))
    except (ValueError, IndexError, KeyError, AttributeError):
        return text


def validate(text: str) -> str | None:
    """Ошибка в заготовке (для админки) или None."""
    if not text or not text.strip():
        return "пустой текст"
    if len(text) > 600:
        return "слишком длинно (до 600 символов)"
    try:
        for _, field_name, _, _ in _FMT.parse(text):
            if field_name is not None and not field_name.isidentifier():
                return f"странная подстановка {{{field_name}}}"
    except ValueError:
        return "непарные фигурные скобки { }"
    return None


class Phrases:
    def __init__(self, store, rng: random.Random | None = None):
        self.store = store
        self.rng = rng or random.Random()
        self._recent: dict[tuple[int | None, str], deque] = {}

    def candidates(self, key: str, subject: str | None = None) -> list:
        items = [t for t in self.store.templates.values() if t.enabled and t.key == key]
        if subject:
            low = subject.lower()
            special = [t for t in items if t.subject and t.subject.lower() in low]
            if special and self.rng.random() < 0.6:     # предметные — чаще, но не всегда
                return special
        return [t for t in items if not t.subject]

    def pick(self, key: str, *, uid: int | None = None, subject: str | None = None, **ctx: Any) -> str | None:
        items = self.candidates(key, subject)
        if not items:
            return None
        memory = self._recent.setdefault((uid, key), deque(maxlen=6))
        fresh = [t for t in items if t.id not in memory] or items
        tpl = self.rng.choice(fresh)
        if len(items) > 1:
            memory.append(tpl.id)
            if len(memory) >= len(items):        # все уже были — начинаем круг заново
                memory.clear()
                memory.append(tpl.id)
        if subject is not None:
            ctx.setdefault("subject", subject)
        return render(tpl.text, ctx).strip() or None
