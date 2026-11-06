"""Календарь школы (каникулы, праздники, учебные дни) и расписание с временем звонков."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from netschool.models import Lesson, MonthEvent, YearCalendar

from .timeutil import monday_of, today

KNOWN_HOLIDAYS = {
    (1, 1): "Новый год", (2, 23): "День защитника Отечества", (3, 8): "Международный женский день",
    (3, 21): "Новруз", (3, 22): "Новруз", (5, 9): "День Победы", (5, 18): "День Конституции",
    (9, 27): "День независимости", (10, 6): "День памяти", (11, 4): "День народного единства",
    (12, 12): "День нейтралитета",
}


def vacation_title(start: date) -> str:
    m = start.month
    if m in (10, 11):
        return "Осенние каникулы"
    if m in (12, 1):
        return "Зимние каникулы"
    if m == 2:
        return "Февральские каникулы"
    if m in (3, 4):
        return "Весенние каникулы"
    return "Летние каникулы"


def calendar_from_year(yc: YearCalendar, events: list[MonthEvent] | None = None) -> dict[str, Any]:
    """YearCalendar → компактный словарь для kv 'calendar'."""
    vacations, one_day = [], []
    for start, end in yc.vacation_periods():
        if (end - start).days >= 2:
            vacations.append([start.isoformat(), end.isoformat(), vacation_title(start)])
        else:
            d = start
            while d <= end:
                one_day.append(d.isoformat())
                d += timedelta(days=1)
    holidays = sorted(set(one_day) | {d.isoformat() for d in yc.holidays()})
    days = sorted(d.date for d in yc.days)
    titles: dict[str, str] = {}
    for e in events or []:
        titles.setdefault(e.date.isoformat(), e.title)
    return {
        "vacations": vacations,
        "holidays": holidays,
        "school_weekdays": sorted(yc.usual_weekdays()) or [0, 1, 2, 3, 4, 5],
        "start": days[0].isoformat() if days else None,
        "end": days[-1].isoformat() if days else None,
        "titles": titles,
    }


class SchoolCalendar:
    def __init__(self, store):
        self.store = store

    @property
    def data(self) -> dict:
        return self.store.kv_get("calendar") or {}

    def school_weekdays(self) -> set[int]:
        return set(self.data.get("school_weekdays") or [0, 1, 2, 3, 4, 5])

    def vacation_on(self, d: date) -> dict | None:
        iso = d.isoformat()
        for start, end, title in self.data.get("vacations", []):
            if start <= iso <= end:
                return {"start": start, "end": end, "title": title}
        return None

    def holiday_on(self, d: date) -> str | None:
        iso = d.isoformat()
        if iso in set(self.data.get("holidays", [])):
            return (self.data.get("titles") or {}).get(iso) or KNOWN_HOLIDAYS.get((d.month, d.day)) or "праздник"
        return None

    def is_school_day(self, d: date) -> bool:
        if d.weekday() not in self.school_weekdays():
            return False
        return self.vacation_on(d) is None and self.holiday_on(d) is None

    def day_off_reason(self, d: date) -> str | None:
        v = self.vacation_on(d)
        if v:
            return v["title"]
        h = self.holiday_on(d)
        if h:
            return h
        if d.weekday() not in self.school_weekdays():
            return "выходной"
        return None

    def next_school_day(self, d: date, *, include: bool = False, limit: int = 120) -> date:
        cur = d if include else d + timedelta(days=1)
        for _ in range(limit):
            if self.is_school_day(cur):
                return cur
            cur += timedelta(days=1)
        return d + timedelta(days=1)

    def next_vacation(self, d: date | None = None) -> dict | None:
        d = d or today()
        iso = d.isoformat()
        for start, end, title in sorted(self.data.get("vacations", [])):
            if start > iso:
                days_left = (date.fromisoformat(start) - d).days
                return {"start": start, "end": end, "title": title, "days": days_left}
        return None

    def slow_day(self, d: date) -> bool:
        """Опрашивать реже: каникулы или нерабочий день недели."""
        return d.weekday() not in self.school_weekdays() or self.vacation_on(d) is not None

    def upcoming_off(self, d: date, days: int = 60) -> list[dict]:
        out = []
        for i in range(1, days + 1):
            x = d + timedelta(days=i)
            if x.weekday() in self.school_weekdays():
                h = self.holiday_on(x)
                if h and not self.vacation_on(x):
                    out.append({"date": x.isoformat(), "title": h})
        return out


# ───────────────────────────── расписание ─────────────────────────────

def short_room(room: str | None) -> str:
    if not room:
        return ""
    first = room.split()[0]
    if any(ch.isdigit() for ch in first):
        return first
    return room[:22]


def lessons_to_snapshot(lessons: list[Lesson]) -> list[dict]:
    out = []
    for l in lessons:
        if l.subject is None or l.date is None:
            continue
        out.append({
            "d": l.date.isoformat(), "n": l.number, "s": l.subject.name, "room": short_room(l.subject.room),
            "room_full": l.subject.room or "", "group": l.subject.group or "", "kind": l.kind or "",
            "start": l.start.strftime("%H:%M") if l.start else None,
            "end": l.end.strftime("%H:%M") if l.end else None,
        })
    return out


def bells(store) -> dict[int, tuple[str, str]]:
    raw = store.rt("bells") or {}
    out = {}
    for k, v in raw.items():
        try:
            out[int(k)] = (v[0], v[1])
        except (ValueError, TypeError, IndexError):
            continue
    return out


def lessons_for(store, uid: int, d: date) -> list[dict] | None:
    """Уроки дня со временем звонков. None — расписания этой недели ещё нет."""
    sched = store.snap(uid, "schedule") or {}
    week = (sched.get("weeks") or {}).get(monday_of(d).isoformat())
    if week is None:
        return None
    b = bells(store)
    items = [dict(x) for x in week if x.get("d") == d.isoformat()]
    items.sort(key=lambda x: (x.get("n") or 99, x.get("start") or ""))
    for x in items:
        n = x.get("n")
        if not x.get("start") and n in b:
            x["start"], x["end"] = b[n]
    return items
