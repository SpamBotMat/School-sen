"""Снимки данных ученика и поиск изменений между опросами → события для уведомлений.

Источники:
  • дневник (текущая неделя, по пт–вс и следующая) — оценки с типом работы, новое ДЗ, долги;
  • «Успеваемость и посещаемость» (раз в ~2 часа) — оценки за прошлые недели, пропуски/опоздания;
  • итоговые отметки (раз в день); расписание недели (раз в день).
Первый снимок — база, по нему ничего не шлём. Недели сравниваем только с той же неделей.
"""
from __future__ import annotations

from collections import Counter
from datetime import date, timedelta
from typing import Any

from netschool.changes import assignment_from_dict, assignment_to_dict, diff_assignments
from netschool.models import Assignment, StudentTotalReport, TotalMarksReport

from .timeutil import monday_of

ABSENT_CODES = {"ОТ", "НП", "Б", "УП"}
LATE_CODES = {"ОП"}


# ───────────────────────────── снимки ─────────────────────────────

def diary_snapshot(old: dict | None, fresh: dict[str, list[Assignment]], current_monday: date) -> dict:
    """Сливает свежие недели со старыми; хранит прошлую, текущую и следующую."""
    weeks = dict((old or {}).get("weeks") or {})
    for monday, items in fresh.items():
        weeks[monday] = [assignment_to_dict(a) for a in items]
    keep = {(current_monday + timedelta(days=7 * i)).isoformat() for i in (-1, 0, 1)}
    return {"weeks": {k: v for k, v in weeks.items() if k in keep}}


def report_snapshot(r: StudentTotalReport) -> dict:
    return {
        "start": r.period_start.isoformat() if r.period_start else None,
        "end": r.period_end.isoformat() if r.period_end else None,
        "period": r.info.get("Период", ""),
        "subjects": [
            {"name": s.subject, "avg": s.average,
             "cells": [{"d": c.date.isoformat(), "m": list(c.marks), "a": list(c.attendance), "debt": bool(c.debt)}
                       for c in s.cells if c.marks or c.attendance or c.debt]}
            for s in r.subjects
        ],
    }


def totals_snapshot(t: TotalMarksReport) -> dict:
    return {
        "periods": list(t.period_names),
        "subjects": [{"name": s.subject, "periods": dict(s.periods), "year": s.year, "exam": s.exam,
                      "final": s.final} for s in t.subjects],
    }


def all_assignments(diary: dict | None) -> list[dict]:
    """Все задания из снимка без дублей (просроченные повторяются в разных неделях)."""
    seen, out = set(), []
    for monday in sorted((diary or {}).get("weeks", {})):
        for a in diary["weeks"][monday]:
            key = a_key(a)
            if key in seen:
                continue
            seen.add(key)
            out.append(a)
    return out


def a_key(a: dict) -> str:
    if a.get("aid") is not None:
        return f"{a['aid']}:{a.get('cid')}:{a.get('tp')}"
    return f"{a['date']}:{a['subject']}:{a['type_code']}:{a['title']}"


# ───────────────────────────── события ─────────────────────────────

def _ev(kind: str, a: Assignment, **extra: Any) -> dict:
    return {"type": kind, "subject": a.subject, "date": a.date.isoformat(), "work": a.type_name or a.type_code,
            "title": a.title, "mark": a.mark, "key": a.key, **extra}


def diff_diary(old: dict | None, fresh: dict[str, list[Assignment]], today_: date) -> tuple[list[dict], set[str]]:
    """События по дневнику + множество недель, которые сравнивались (их оценки не дублируем из отчёта)."""
    events: list[dict] = []
    covered: set[str] = set()
    old_weeks = (old or {}).get("weeks") or {}
    for monday, items in fresh.items():
        before = old_weeks.get(monday)
        if before is None:
            continue
        covered.add(monday)
        for ch in diff_assignments([assignment_from_dict(x) for x in before], items):
            a = ch.item
            if ch.kind == "mark":
                events.append(_ev("mark", a))
            elif ch.kind == "mark_changed":
                events.append(_ev("mark_changed", a, old_mark=ch.old.mark if ch.old else None))
            elif ch.kind == "mark_removed":
                events.append(_ev("mark_removed", a, old_mark=ch.old.mark if ch.old else None))
            elif ch.kind == "new":
                if a.overdue and a.date < today_:
                    events.append(_ev("overdue", a))
                elif a.is_homework:
                    if a.date >= today_:
                        events.append(_ev("hw_new", a))
                elif a.date >= today_:
                    events.append(_ev("task_new", a))       # контрольная/тест в планах
            elif ch.kind == "overdue":
                events.append(_ev("overdue", a))
            elif ch.kind == "edited" and a.is_homework and a.date >= today_:
                events.append(_ev("hw_changed", a, old_title=ch.old.title if ch.old else None))
    # одно и то же задание может прийти из двух недель (просрочки) — убираем дубли
    seen, unique = set(), []
    for e in events:
        k = (e["type"], e["key"], e.get("mark"))
        if k not in seen:
            seen.add(k)
            unique.append(e)
    return unique, covered


def diff_report(old: dict | None, new: dict, covered_weeks: set[str]) -> list[dict]:
    if not old or old.get("start") != new.get("start"):
        return []           # первый снимок или новая четверть — это база
    def index(snap: dict) -> dict:
        out = {}
        for s in snap.get("subjects", []):
            for c in s.get("cells", []):
                out[(s["name"], c["d"])] = c
        return out
    before = index(old)
    events = []
    for s in new.get("subjects", []):
        for c in s.get("cells", []):
            prev = before.get((s["name"], c["d"]), {})
            d = date.fromisoformat(c["d"])
            if monday_of(d).isoformat() not in covered_weeks:
                added = Counter(c.get("m", [])) - Counter(prev.get("m", []))
                for mark, n in added.items():
                    for _ in range(n):
                        events.append({"type": "mark", "subject": s["name"], "date": c["d"], "mark": mark,
                                       "work": None, "title": None, "key": f"r:{s['name']}:{c['d']}:{mark}",
                                       "late": True})
            for code in sorted(set(c.get("a", [])) - set(prev.get("a", []))):
                if code in ABSENT_CODES or code in LATE_CODES:
                    events.append({"type": "late" if code in LATE_CODES else "absent", "subject": s["name"],
                                   "date": c["d"], "code": code, "key": f"a:{s['name']}:{c['d']}:{code}"})
    return events


def diff_totals(old: dict | None, new: dict) -> list[dict]:
    if not old:
        return []
    before = {s["name"]: s for s in old.get("subjects", [])}
    events = []
    for s in new.get("subjects", []):
        prev = before.get(s["name"], {})
        for period, mark in (s.get("periods") or {}).items():
            if mark and (prev.get("periods") or {}).get(period) != mark:
                events.append({"type": "total", "subject": s["name"], "period": period, "mark": mark,
                               "key": f"t:{s['name']}:{period}"})
        for field, title in (("year", "год"), ("exam", "экзамен"), ("final", "итоговая")):
            if s.get(field) and prev.get(field) != s.get(field):
                events.append({"type": "total", "subject": s["name"], "period": title, "mark": s[field],
                               "key": f"t:{s['name']}:{field}"})
    return events


def diff_schedule(old: dict | None, new: dict, days: list[date]) -> list[dict]:
    """Поменялись уроки на указанные дни (обычно сегодня/завтра)."""
    if not old:
        return []
    events = []
    for d in days:
        m = monday_of(d).isoformat()
        ow = (old.get("weeks") or {}).get(m)
        nw = (new.get("weeks") or {}).get(m)
        if ow is None or nw is None:
            continue
        def sig(items: list[dict]) -> list[tuple]:
            return sorted((x.get("n") or 0, x.get("s"), x.get("room")) for x in items if x.get("d") == d.isoformat())
        if sig(ow) != sig(nw) and sig(nw):
            events.append({"type": "schedule", "date": d.isoformat(), "key": f"s:{d.isoformat()}"})
    return events
