"""Оценки: средний балл, прогноз итоговой и калькулятор «что если».

Правило школы (настраивается в админке): средний 4.62 и выше → 5 «точно»,
от 4.50 до 4.61 — «можно вытянуть» (решает учитель), ниже — 4. Так для любой оценки.
Средний — простое среднее без весов (так считает сам NetSchool).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable

from netschool.models import mark_value

EPS = 1e-9


def values_of(marks: Iterable[str | int | None]) -> list[int]:
    out = []
    for m in marks:
        v = m if isinstance(m, int) else mark_value(m)
        if v is not None and 1 <= v <= 5:
            out.append(v)
    return out


def average(values: Iterable[int]) -> float | None:
    vals = list(values)
    if not vals:
        return None
    return sum(vals) / len(vals)


def fmt_avg(avg: float | None) -> str:
    return "—" if avg is None else f"{avg:.2f}"


@dataclass
class Prediction:
    avg: float | None
    grade: int | None          # что выйдет по правилу «точно»
    status: str                # "none" | "sure" | "maybe" | "floor" | "top"
    up: int | None = None      # до какой оценки «можно вытянуть»
    to_next: float | None = None   # сколько среднего не хватает до «точно» следующей
    need5: int | None = None   # сколько пятёрок подряд нужно до «точно» следующей
    need5_maybe: int | None = None
    impact: dict[int, float] = field(default_factory=dict)   # средний, если следующей будет 5/4/3/2

    def as_dict(self) -> dict:
        return {"avg": None if self.avg is None else round(self.avg, 2), "grade": self.grade,
                "status": self.status, "up": self.up,
                "to_next": None if self.to_next is None else round(self.to_next, 2),
                "need5": self.need5, "need5_maybe": self.need5_maybe,
                "impact": {str(k): round(v, 2) for k, v in self.impact.items()}}


def needed(values: list[int], target_avg: float, mark: int = 5, limit: int = 60) -> int | None:
    """Минимум оценок `mark`, чтобы средний стал ≥ target_avg. None — не получится."""
    s, c = sum(values), len(values)
    if c and s / c + EPS >= target_avg:
        return 0
    if mark - target_avg <= EPS:          # такими оценками до цели не дотянуть
        return None
    n = math.ceil((target_avg * c - s) / (mark - target_avg) - EPS)
    n = max(n, 0 if c else 1)
    return n if n <= limit else None


def predict(values: list[int], sure: float = 0.62, maybe: float = 0.5) -> Prediction:
    avg = average(values)
    if avg is None:
        return Prediction(avg=None, grade=None, status="none")
    impact = {m: (sum(values) + m) / (len(values) + 1) for m in (5, 4, 3, 2)}
    a = round(avg, 2)          # как на сайте: сравниваем по двум знакам
    base = int(math.floor(a + EPS))
    frac = a - base
    if base >= 5:
        return Prediction(avg=avg, grade=5, status="top", impact=impact)
    if frac + EPS >= sure:
        grade, status, up = base + 1, "sure", None
    elif frac + EPS >= maybe:
        grade, status, up = base, "maybe", base + 1
    else:
        grade, status, up = max(base, 1), "floor", None
    nxt = grade + 1
    if grade >= 5:
        return Prediction(avg=avg, grade=5, status=status, impact=impact)
    sure_target = (nxt - 1) + sure
    maybe_target = (nxt - 1) + maybe
    return Prediction(
        avg=avg, grade=grade, status=status, up=up,
        to_next=max(0.0, sure_target - a),
        need5=needed(values, sure_target),
        need5_maybe=needed(values, maybe_target) if status != "maybe" else 0,
        impact=impact,
    )


def what_if(values: list[int], extra: list[int], sure: float = 0.62, maybe: float = 0.5) -> Prediction:
    return predict(values + [v for v in extra if 1 <= v <= 5], sure, maybe)


def prediction_text(p: Prediction) -> str:
    """Короткая подпись для бота: «→ 5 точно», «→ 4, можно вытянуть на 5»."""
    if p.status == "none":
        return "оценок пока нет"
    if p.status == "top":
        return "→ 5 💯"
    if p.status == "sure":
        return f"→ {p.grade} точно"
    if p.status == "maybe":
        return f"→ {p.grade}, можно вытянуть на {p.up}"
    return f"→ {p.grade}"


def need_text(p: Prediction) -> str:
    if p.status in ("none", "top") or p.grade is None or p.grade >= 5:
        return ""
    nxt = p.grade + 1
    if p.need5 is None:
        return f"до {nxt} пятёрками уже не дотянуть 😔"
    if p.need5 == 0:
        return ""
    word = "пятёрка" if p.need5 == 1 else ("пятёрки" if 2 <= p.need5 <= 4 else "пятёрок")
    return f"до {nxt}: {p.need5} {word}"


def subjects_from_report(report: dict | None) -> list[dict]:
    """Из снимка «Успеваемость и посещаемость» → [{name, marks: [(date, '5')], attendance, debts}]."""
    out = []
    for s in (report or {}).get("subjects", []):
        marks, att, debts = [], [], []
        for c in s.get("cells", []):
            for m in c.get("m", []):
                marks.append((c["d"], m))
            for a in c.get("a", []):
                att.append((c["d"], a))
            if c.get("debt"):
                debts.append(c["d"])
        out.append({"name": s["name"], "marks": marks, "attendance": att, "debts": debts,
                    "server_avg": s.get("avg")})
    return out


def overall(subjects: list[dict]) -> float | None:
    avgs = [average(values_of(m for _, m in s["marks"])) for s in subjects]
    avgs = [a for a in avgs if a is not None]
    return sum(avgs) / len(avgs) if avgs else None


def mood_of(avg: float | None, recent: list[int] | None = None) -> str:
    """great | good | ok | bad | none — для рожицы на главной."""
    if avg is None:
        return "none"
    score = avg
    if recent:
        score = avg * 0.7 + (sum(recent) / len(recent)) * 0.3
    if score >= 4.5:
        return "great"
    if score >= 3.8:
        return "good"
    if score >= 3.0:
        return "ok"
    return "bad"
