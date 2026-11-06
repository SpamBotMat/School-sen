"""Поиск изменений между двумя снимками дневника — основа уведомлений бота.

Храни в Neon снимок недели (список assignment_to_dict) и сравнивай с новым.
Сравнивай снимки ОДНОЙ И ТОЙ ЖЕ недели: при смене недели старые задания
«исчезают» — это не удаление. Первый снимок пользователя — просто база,
по нему уведомления не шлём.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from typing import Iterable

from .models import Assignment


@dataclass(slots=True)
class Change:
    kind: str                    # "new" | "mark" | "mark_changed" | "mark_removed" | "overdue" | "edited"
    item: Assignment
    old: Assignment | None = None


def diff_assignments(old: Iterable[Assignment], new: Iterable[Assignment]) -> list[Change]:
    before = {a.key: a for a in old}
    changes: list[Change] = []
    for a in new:
        b = before.get(a.key)
        if b is None:
            # «Ответ на уроке» и т.п. появляются сразу с оценкой — это новая оценка, а не новое ДЗ
            changes.append(Change("mark" if a.mark else "new", a))
            continue
        if a.mark != b.mark:
            kind = "mark" if not b.mark else ("mark_removed" if not a.mark else "mark_changed")
            changes.append(Change(kind, a, b))
        if a.overdue and not b.overdue:
            changes.append(Change("overdue", a, b))
        if (a.title, a.date) != (b.title, b.date):
            changes.append(Change("edited", a, b))
    return changes


def assignment_to_dict(a: Assignment) -> dict:
    d = asdict(a)
    d["date"] = a.date.isoformat()
    return d


def assignment_from_dict(d: dict) -> Assignment:
    data = dict(d)
    data["date"] = date.fromisoformat(data["date"])
    return Assignment(**data)
