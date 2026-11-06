"""Тексты бота (HTML). Тон — на «ты», дружелюбно, без привязки к полу."""
from __future__ import annotations

import html
from datetime import date, timedelta
from typing import Any, Iterable

from . import grades as G
from .diffing import a_key, all_assignments
from .school import lessons_for
from .timeutil import (MONTHS_GEN, WEEKDAYS, WEEKDAYS_SHORT, human_date, monday_of, now, plural, short_date,
                       today)
from .weather import line as weather_line

MAX_LEN = 3900
MARK_DOT = {5: "🟢", 4: "🔵", 3: "🟠", 2: "🔴", 1: "🔴"}


def esc(v: Any) -> str:
    return html.escape("" if v is None else str(v), quote=False)


def cut(text: str, limit: int = MAX_LEN) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 40].rsplit("\n", 1)[0] + "\n\n<i>…не влезло — остальное в приложении</i>"


def mark_dot(mark: str | None) -> str:
    v = G.values_of([mark]) if mark else []
    return MARK_DOT.get(v[0], "⚪") if v else "⚪"


def day_title(d: date) -> str:
    return f"{WEEKDAYS[d.weekday()].capitalize()}, {d.day} {MONTHS_GEN[d.month - 1]}"


def days_word(n: int) -> str:
    return f"{n} {plural(n, 'день', 'дня', 'дней')}"


# ───────────────────────────── данные ─────────────────────────────

def homework_on(store, uid: int, d: date) -> list[dict]:
    items = [a for a in all_assignments(store.snap(uid, "diary")) if a["date"] == d.isoformat()]
    items.sort(key=lambda a: (a["type_code"] != "Д", a["subject"]))
    return items


def done_keys(store, uid: int) -> set[str]:
    return set((store.snap(uid, "hw_done") or {}).keys())


def overdue_items(store, uid: int) -> list[dict]:
    t = today().isoformat()
    return sorted([a for a in all_assignments(store.snap(uid, "diary")) if a.get("overdue") and a["date"] < t],
                  key=lambda a: a["date"])


def subjects(store, uid: int) -> list[dict]:
    return G.subjects_from_report(store.snap(uid, "report"))


# ───────────────────────────── блоки ─────────────────────────────

def lessons_block(lessons: list[dict] | None, *, empty: str = "Уроков нет 🎉") -> str:
    if lessons is None:
        return "<i>Расписание ещё не подтянулось — загляни чуть позже.</i>"
    if not lessons:
        return empty
    rows = []
    for l in lessons:
        n = l.get("n")
        t = l.get("start") or ""
        room = f" · {esc(l['room'])}" if l.get("room") else ""
        rows.append(f"{n or '•'}. <code>{t or '--:--'}</code> {esc(l['s'])}{room}")
    end = lessons[-1].get("end")
    if end:
        rows.append(f"<i>Конец в {end}</i>")
    return "\n".join(rows)


def hw_lines(items: Iterable[dict], done: set[str], *, details=None, limit: int = 30) -> list[str]:
    rows = []
    for a in list(items)[:limit]:
        tick = "✅" if a_key(a) in done else ("📝" if a["type_code"] != "Д" else "▫️")
        title = a.get("title") or ""
        extra = ""
        if details:
            det = details(a)
            if det and det.get("attachments"):
                extra = " 📎"
        kind = "" if a["type_code"] == "Д" else f"<i>{esc(a.get('type_name') or a['type_code'])}:</i> "
        mark = f" — <b>{esc(a['mark'])}</b>" if a.get("mark") else ""
        rows.append(f"{tick} <b>{esc(a['subject'])}</b> — {kind}{esc(title)}{extra}{mark}")
    return rows


def weather_block(ctx, d: date) -> str:
    w = ctx.weather.for_day(d)
    if not w:
        return ""
    out = [weather_line(w, ctx.cfg.city_name)]
    for key in (w.get("advice") or [])[:2]:
        phrase = ctx.phrases.pick(key, tmin=w.get("tmin"), tmax=w.get("tmax"), precip=w.get("precip_prob"),
                                  wind=w.get("gusts"), temp=w.get("temp_morning"))
        if phrase:
            out.append(esc(phrase))
    return "\n".join(out)


# ───────────────────────────── экраны ─────────────────────────────

def text_day(ctx, u, d: date, *, header: str) -> str:
    store, cal = ctx.store, ctx.calendar
    parts = [f"<b>{header}</b>"]
    reason = cal.day_off_reason(d)
    if reason:
        nxt = cal.next_school_day(d)
        parts.append(f"Выходной 😎 ({esc(reason)})")
        parts.append(f"Следующий учебный день — {esc(human_date(nxt))}.")
        hw = [a for a in homework_on(store, u.id, nxt) if a["type_code"] == "Д"]
        if hw:
            parts.append(f"\n📚 <b>ДЗ на {esc(short_date(nxt))}</b>\n" + "\n".join(hw_lines(hw, done_keys(store, u.id))))
        return cut("\n".join(parts))
    lessons = lessons_for(store, u.id, d)
    count = len(lessons or [])
    if lessons:
        parts[0] += f" · {count} {plural(count, 'урок', 'урока', 'уроков')}"
    parts.append(lessons_block(lessons))
    hw = homework_on(store, u.id, d)
    if hw:
        parts.append(f"\n📚 <b>ДЗ к этому дню</b>\n" + "\n".join(hw_lines(hw, done_keys(store, u.id))))
    else:
        parts.append("\n📚 ДЗ к этому дню не задано")
    return cut("\n".join(parts))


def text_today(ctx, u) -> str:
    d = today()
    return text_day(ctx, u, d, header=f"📅 Сегодня, {day_title(d).lower()}")


def text_tomorrow(ctx, u) -> str:
    d = today() + timedelta(days=1)
    return text_day(ctx, u, d, header=f"➡️ Завтра, {day_title(d).lower()}")


def text_homework(ctx, u, days: int = 7) -> str:
    store, cal = ctx.store, ctx.calendar
    done = done_keys(store, u.id)
    t = today()
    rows = ["<b>📚 Домашка на ближайшие дни</b>"]
    shown = 0
    for i in range(1, days + 1):
        d = t + timedelta(days=i)
        items = [a for a in homework_on(store, u.id, d)]
        if not items:
            continue
        left = sum(1 for a in items if a_key(a) not in done and a["type_code"] == "Д")
        badge = f" · осталось {left}" if left else " · всё ✅"
        rows.append(f"\n<b>{esc(human_date(d))}</b>{badge}")
        rows += hw_lines(items, done, details=lambda a: store.detail_get(a_key(a)))
        shown += len(items)
    if not shown:
        if store.snap(u.id, "diary") is None:
            rows.append("\nЕщё не успел загрузить дневник — дай мне минутку ⏳")
        else:
            rows.append("\n" + esc(ctx.phrases.pick("hw.none", uid=u.id, name=u.name) or "Пока ничего не задано 😌"))
    od = overdue_items(store, u.id)
    if od:
        rows.append(f"\n⚠️ Долгов: {len(od)} — /debts")
    rows.append("\n<i>Отмечать сделанное и смотреть подробности удобнее в приложении 👇</i>")
    return cut("\n".join(rows))


def text_marks(ctx, u) -> str:
    store = ctx.store
    report = store.snap(u.id, "report")
    if report is None:
        return "Оценки ещё грузятся ⏳ Загляни через пару минут."
    sure, maybe = store.rt("calc_sure"), store.rt("calc_maybe")
    subs = subjects(store, u.id)
    period = report.get("period") or ""
    rows = [f"<b>⭐ Оценки</b>" + (f" · {esc(period)}" if period else "")]
    with_marks = [s for s in subs if s["marks"]]
    for s in with_marks:
        vals = G.values_of(m for _, m in s["marks"])
        p = G.predict(vals, sure, maybe)
        marks = " ".join(esc(m) for _, m in s["marks"][-12:])
        rows.append(f"\n{mark_dot(str(p.grade) if p.grade else None)} <b>{esc(s['name'])}</b> — "
                    f"<b>{G.fmt_avg(p.avg)}</b> {esc(G.prediction_text(p))}\n<code>{marks}</code>")
        need = G.need_text(p)
        if need and p.status != "sure":
            rows.append(f"<i>{esc(need)}</i>")
    empty = [s["name"] for s in subs if not s["marks"]]
    if not with_marks:
        rows.append("\nВ этой четверти оценок пока нет — чистый лист ✍️")
    elif empty:
        rows.append(f"\n<i>Без оценок: {esc(', '.join(empty[:12]))}{'…' if len(empty) > 12 else ''}</i>")
    avg = G.overall(subs)
    if avg is not None:
        rows.append(f"\n📊 Средний по всем: <b>{avg:.2f}</b>")
    rows.append(f"<i>Правило: от x.{int(sure * 100):02d} — точно вверх, от x.{int(maybe * 100):02d} — можно вытянуть.</i>")
    return cut("\n".join(rows))


def text_debts(ctx, u) -> str:
    store = ctx.store
    od = overdue_items(store, u.id)
    dots = []
    for s in subjects(store, u.id):
        for d in s["debts"]:
            dots.append((d, s["name"]))
    rows = ["<b>⚠️ Долги</b>"]
    if not od and not dots:
        rows.append("\nДолгов нет — красота ✨")
        return "\n".join(rows)
    if od:
        rows.append("\n<b>Просроченные задания</b>")
        for a in od[:25]:
            rows.append(f"• <b>{esc(a['subject'])}</b> — {esc(a['title'])} <i>(до {esc(short_date(date.fromisoformat(a['date'])))})</i>")
    if dots:
        rows.append("\n<b>«Точки» в журнале</b>")
        for d, name in sorted(dots)[:25]:
            rows.append(f"• {esc(name)} — {esc(short_date(date.fromisoformat(d)))}")
    phrase = ctx.phrases.pick("debt.reminder", uid=u.id, count=len(od) + len(dots))
    if phrase:
        rows.append(f"\n{esc(phrase)}")
    return cut("\n".join(rows))


def text_totals(ctx, u) -> str:
    t = ctx.store.snap(u.id, "totals")
    if t is None:
        return "Итоговые ещё не загрузились ⏳"
    periods = t.get("periods") or []
    rows = ["<b>🏁 Итоговые отметки</b>"]
    any_mark = False
    for s in t.get("subjects", []):
        marks = [f"{p.split()[0]}ч: {s['periods'][p]}" for p in periods if (s.get("periods") or {}).get(p)]
        for field, title in (("year", "год"), ("exam", "экз"), ("final", "итог")):
            if s.get(field):
                marks.append(f"{title}: {s[field]}")
        if marks:
            any_mark = True
            rows.append(f"• <b>{esc(s['name'])}</b> — {esc(' · '.join(marks))}")
    if not any_mark:
        rows.append("\nИтоговых пока нет.")
    return cut("\n".join(rows))


def text_holidays(ctx, u) -> str:
    cal = ctx.calendar
    t = today()
    rows = ["<b>🏖 Каникулы и праздники</b>"]
    cur = cal.vacation_on(t)
    if cur:
        end = date.fromisoformat(cur["end"])
        left = (end - t).days
        rows.append(f"\nСейчас: <b>{esc(cur['title'])}</b> 🎉 до {esc(short_date(end))}"
                    + (f" (ещё {days_word(left)})" if left > 0 else " (последний день!)"))
    nv = cal.next_vacation(t)
    if nv:
        start = date.fromisoformat(nv["start"])
        end = date.fromisoformat(nv["end"])
        rows.append(f"\nСледующие: <b>{esc(nv['title'])}</b>\n{esc(short_date(start))} — {esc(short_date(end))}"
                    f" · через <b>{days_word(nv['days'])}</b>")
    off = cal.upcoming_off(t, 45)
    if off:
        rows.append("\n<b>Ближайшие выходные-праздники</b>")
        for x in off[:6]:
            d = date.fromisoformat(x["date"])
            rows.append(f"• {esc(human_date(d))} — {esc(x['title'])}")
    if len(rows) == 1:
        rows.append("\nКалендарь ещё не загружен ⏳")
    return "\n".join(rows)


def text_birthdays(ctx, u) -> str:
    b = ctx.store.snap(u.id, "birthdays") or {}
    items = b.get("items") or []
    rows = ["<b>🎂 Именинники месяца</b>"]
    if not items:
        rows.append("\nВ этом месяце никого не вижу (сайт школы показывает не всех).")
        return "\n".join(rows)
    for x in items[:40]:
        d = x.get("date")
        when = short_date(date.fromisoformat(d)) if d else "?"
        cls = f" · {esc(x['class'])}" if x.get("class") else ""
        rows.append(f"• {esc(when)} — {esc(x['name'])}{cls}")
    return cut("\n".join(rows))


def text_news(ctx, limit: int = 5) -> str:
    news = ctx.store.kv_get("news") or {}
    items = news.get("items") or []
    rows = ["<b>📢 Объявления школы</b>"]
    if not items:
        rows.append("\nПока пусто.")
        return "\n".join(rows)
    for a in items[:limit]:
        d = f"{short_date(date.fromisoformat(a['date']))} · " if a.get("date") else ""
        text = a.get("text") or ""
        if len(text) > 500:
            text = text[:500].rsplit(" ", 1)[0] + "…"
        rows.append(f"\n<b>{esc(a['title'])}</b>\n<i>{esc(d)}{esc(a.get('author') or '')}</i>\n{esc(text)}")
    return cut("\n".join(rows))


# ───────────────────────────── сводки ─────────────────────────────

def greeting(ctx, u, key: str) -> str:
    return esc(ctx.phrases.pick(key, uid=u.id, name=u.name) or f"Привет, {u.name}!")


def morning_digest(ctx, u, *, pending: list[str] | None = None) -> str | None:
    store, cal = ctx.store, ctx.calendar
    d = today()
    if not cal.is_school_day(d):
        return None
    key = "greet.monday" if d.weekday() == 0 else ("greet.saturday" if d.weekday() == 5 else "greet.morning")
    parts = [f"<b>{greeting(ctx, u, key)}</b>"]
    bday = birthday_phrase(ctx, u, d)
    if bday:
        parts.append(bday)
    if u.opt("weather"):
        w = weather_block(ctx, d)
        if w:
            parts.append(w)
    lessons = lessons_for(store, u.id, d)
    n = len(lessons or [])
    parts.append(f"\n📅 <b>Сегодня {n} {plural(n, 'урок', 'урока', 'уроков')}</b>" if n else "\n📅 <b>Сегодня</b>")
    parts.append(lessons_block(lessons))
    hw = [a for a in homework_on(store, u.id, d) if a["type_code"] == "Д"]
    done = done_keys(store, u.id)
    if hw:
        left = [a for a in hw if a_key(a) not in done]
        parts.append(f"\n📚 <b>ДЗ на сегодня</b>" + (" — всё отмечено ✅" if not left else ""))
        parts += hw_lines(hw, done)
    tasks = [a for a in homework_on(store, u.id, d) if a["type_code"] != "Д" and not a.get("mark")]
    if tasks:
        parts.append("\n📝 <b>Сегодня по плану</b>")
        parts += hw_lines(tasks, done)
    od = overdue_items(store, u.id)
    if od:
        parts.append(f"\n⚠️ Долгов: {len(od)} — /debts")
    parts += vacation_lines(ctx, u, d)
    if pending:
        parts.append("\n🌙 <b>Пока была ночь</b>")
        parts += pending
    return cut("\n".join(parts))


def evening_digest(ctx, u) -> str | None:
    store, cal = ctx.store, ctx.calendar
    d = today()
    tomorrow = d + timedelta(days=1)
    if not cal.is_school_day(tomorrow):
        # завтра не учимся: скажем только про праздник посреди недели (обычный выходной — молчим)
        reason = cal.day_off_reason(tomorrow)
        if reason and reason != "выходной" and cal.is_school_day(d) and not cal.vacation_on(tomorrow):
            phrase = ctx.phrases.pick("holiday.tomorrow", uid=u.id, title=reason)
            return esc(phrase) if phrase else None
        if cal.vacation_on(tomorrow) and cal.is_school_day(d):
            phrase = ctx.phrases.pick("vacation.start", uid=u.id, title=cal.vacation_on(tomorrow)["title"])
            return esc(phrase) if phrase else None
        return None
    key = "greet.sunday" if d.weekday() == 6 else "greet.evening"
    if cal.vacation_on(d):
        parts = [f"<b>{esc(ctx.phrases.pick('vacation.end', uid=u.id, name=u.name) or 'Завтра снова в школу 🎒')}</b>"]
    else:
        parts = [f"<b>{greeting(ctx, u, key)}</b>"]
    hw = [a for a in homework_on(store, u.id, tomorrow)]
    done = done_keys(store, u.id)
    parts.append(f"\n📚 <b>На завтра, {esc(day_title(tomorrow).lower())}</b>")
    real = [a for a in hw if a["type_code"] == "Д"]
    if hw:
        parts += hw_lines(hw, done, details=lambda a: store.detail_get(a_key(a)))
        if len(real) >= 6:
            phrase = ctx.phrases.pick("hw.many", uid=u.id, count=len(real))
            if phrase:
                parts.append(f"<i>{esc(phrase)}</i>")
    else:
        parts.append(esc(ctx.phrases.pick("hw.none", uid=u.id, name=u.name) or "ДЗ на завтра нет 😌"))
    lessons = lessons_for(store, u.id, tomorrow)
    if lessons:
        first, last = lessons[0], lessons[-1]
        n = len(lessons)
        parts.append(f"\n🗓 Завтра {n} {plural(n, 'урок', 'урока', 'уроков')}: "
                     f"с {first.get('start') or '?'} до {last.get('end') or '?'}, первым — {esc(first['s'])}")
    od = overdue_items(store, u.id)
    if od:
        parts.append(f"⚠️ Висит долгов: {len(od)} — /debts")
    if d.toordinal() % 3 == 0:
        tip = ctx.phrases.pick("tip.general", uid=u.id)
        if tip:
            parts.append(f"\n{esc(tip)}")
    return cut("\n".join(parts))


def vacation_lines(ctx, u, d: date) -> list[str]:
    nv = ctx.calendar.next_vacation(d)
    if nv and 0 < nv["days"] <= 14:
        phrase = ctx.phrases.pick("vacation.soon", uid=u.id, days=days_word(nv["days"]), title=nv["title"])
        return [f"\n{esc(phrase)}"] if phrase else [f"\n🏖 До каникул {days_word(nv['days'])}"]
    return []


def birthday_phrase(ctx, u, d: date) -> str | None:
    b = ctx.store.snap(u.id, "birthdays") or {}
    own = b.get("own")
    if not own or own[5:] != d.isoformat()[5:]:
        return None
    try:
        age = d.year - int(own[:4])
    except ValueError:
        age = None
    phrase = ctx.phrases.pick("birthday.self", uid=u.id, name=u.name, age=age)
    return esc(phrase) if phrase else f"🎉 С днём рождения, {esc(u.name)}!"


def cheatsheet(ctx, u) -> str:
    """Закреплённая «Шпаргалка»: завтрашние уроки + ДЗ на 3 учебных дня + долги."""
    store, cal = ctx.store, ctx.calendar
    t = today()
    nxt = cal.next_school_day(t, include=now().hour < 15)
    parts = [f"📌 <b>Шпаргалка</b> · {now():%d.%m %H:%M}"]
    lessons = lessons_for(store, u.id, nxt)
    label = "Сегодня" if nxt == t else ("Завтра" if nxt == t + timedelta(days=1) else WEEKDAYS[nxt.weekday()].capitalize())
    parts.append(f"\n<b>{label}, {esc(short_date(nxt))}</b>")
    parts.append(lessons_block(lessons))
    done = done_keys(store, u.id)
    shown = 0
    d = nxt
    parts.append("\n📚 <b>ДЗ</b>")
    for _ in range(3):
        items = [a for a in homework_on(store, u.id, d) if a["type_code"] == "Д"]
        if items:
            parts.append(f"<u>{esc(WEEKDAYS_SHORT[d.weekday()])}, {esc(short_date(d))}</u>")
            parts += hw_lines(items, done, limit=12)
            shown += len(items)
        d = cal.next_school_day(d)
    if not shown:
        parts.append("Пока ничего 😌")
    od = overdue_items(store, u.id)
    if od:
        parts.append(f"\n⚠️ Долгов: {len(od)}")
    parts.append("\n<i>Обновляется само. Картинки на всякий случай — «📦 Архив».</i>")
    return cut("\n".join(parts), 4000)


# ───────────────────────────── уведомления ─────────────────────────────

def notification(ctx, u, events: list[dict]) -> str | None:
    """Пачка событий одного опроса → одно сообщение (или None, если показывать нечего)."""
    allow = {
        "mark": u.opt("n_marks"), "mark_changed": u.opt("n_marks"), "mark_removed": u.opt("n_marks"),
        "overdue": u.opt("n_debts"), "late": u.opt("n_attendance"), "absent": u.opt("n_attendance"),
        "total": u.opt("n_totals"), "hw_new": True, "hw_changed": True, "task_new": True, "schedule": True,
    }
    evs = [e for e in events if allow.get(e["type"], False)]
    if not evs:
        return None
    blocks: list[str] = []
    marks = [e for e in evs if e["type"] == "mark"]
    if marks:
        rows = []
        for e in marks[:15]:
            d = date.fromisoformat(e["date"])
            work = f" · {esc(e['work'])}" if e.get("work") else ""
            when = f" · за {esc(short_date(d))}" if e.get("late") or d != today() else ""
            rows.append(f"{mark_dot(e['mark'])} <b>{esc(e['subject'])}</b> — <b>{esc(e['mark'])}</b>{work}{when}")
            if u.opt("reactions") and len(marks) <= 3:
                v = G.values_of([e["mark"]])
                if v:
                    phrase = ctx.phrases.pick(f"mark.{max(2, v[0])}", uid=u.id, subject=e["subject"], mark=e["mark"])
                    if phrase:
                        rows.append(f"<i>{esc(phrase)}</i>")
        title = "⭐ <b>Новая оценка</b>" if len(marks) == 1 else f"⭐ <b>Новые оценки ({len(marks)})</b>"
        blocks.append(title + "\n" + "\n".join(rows))
    changed = [e for e in evs if e["type"] in ("mark_changed", "mark_removed")]
    if changed:
        rows = []
        for e in changed[:10]:
            if e["type"] == "mark_changed":
                rows.append(f"✏️ <b>{esc(e['subject'])}</b>: {esc(e.get('old_mark'))} → <b>{esc(e['mark'])}</b>"
                            f" · {esc(short_date(date.fromisoformat(e['date'])))}")
            else:
                rows.append(f"🗑 <b>{esc(e['subject'])}</b>: оценку {esc(e.get('old_mark'))} убрали"
                            f" · {esc(short_date(date.fromisoformat(e['date'])))}")
        blocks.append("\n".join(rows))
    hw = [e for e in evs if e["type"] in ("hw_new", "hw_changed", "task_new")]
    if hw:
        rows = []
        for e in hw[:15]:
            d = date.fromisoformat(e["date"])
            prefix = {"hw_new": "📚", "hw_changed": "✏️", "task_new": "📝"}[e["type"]]
            kind = "" if e["type"] != "task_new" else f"{esc(e.get('work') or '')}: "
            rows.append(f"{prefix} <b>{esc(e['subject'])}</b> ({esc(human_date(d, weekday=False))}) — {kind}{esc(e['title'])}")
        title = "📚 <b>ДЗ и планы</b>" if len(hw) > 1 else ""
        blocks.append((title + "\n" if title else "") + "\n".join(rows))
    debts = [e for e in evs if e["type"] == "overdue"]
    if debts:
        rows = [f"⚠️ <b>{esc(e['subject'])}</b> — {esc(e['title'])} <i>(до {esc(short_date(date.fromisoformat(e['date'])))})</i>"
                for e in debts[:10]]
        phrase = ctx.phrases.pick("debt.new", uid=u.id, subject=debts[0]["subject"])
        if phrase:
            rows.append(f"<i>{esc(phrase)}</i>")
        blocks.append(("⚠️ <b>Новый долг</b>\n" if len(debts) == 1 else f"⚠️ <b>Новые долги ({len(debts)})</b>\n")
                      + "\n".join(rows))
    att = [e for e in evs if e["type"] in ("late", "absent")]
    if att:
        rows = []
        for e in att[:10]:
            icon = "⏰" if e["type"] == "late" else "🚪"
            what = "опоздание" if e["type"] == "late" else "пропуск"
            rows.append(f"{icon} {what}: <b>{esc(e['subject'])}</b> · {esc(short_date(date.fromisoformat(e['date'])))}")
        key = "att.late" if all(e["type"] == "late" for e in att) else "att.absent"
        phrase = ctx.phrases.pick(key, uid=u.id, subject=att[0]["subject"])
        if phrase:
            rows.append(f"<i>{esc(phrase)}</i>")
        blocks.append("\n".join(rows))
    totals = [e for e in evs if e["type"] == "total"]
    if totals:
        rows = [f"🏁 <b>{esc(e['subject'])}</b> — {esc(e['period'])}: <b>{esc(e['mark'])}</b>" for e in totals[:20]]
        phrase = ctx.phrases.pick("term.total", uid=u.id)
        if phrase:
            rows.append(f"<i>{esc(phrase)}</i>")
        blocks.append("🏁 <b>Итоговые</b>\n" + "\n".join(rows))
    sched = [e for e in evs if e["type"] == "schedule"]
    if sched:
        days = ", ".join(esc(human_date(date.fromisoformat(e["date"]), weekday=False)) for e in sched)
        blocks.append(f"🗓 Поменялось расписание: {days}. Глянь /tomorrow")
    if not blocks:
        return None
    return cut("\n\n".join(blocks))


def news_message(item: dict) -> str:
    d = f"{short_date(date.fromisoformat(item['date']))} · " if item.get("date") else ""
    text = item.get("text") or ""
    if len(text) > 1500:
        text = text[:1500].rsplit(" ", 1)[0] + "…"
    return cut(f"📢 <b>{esc(item['title'])}</b>\n<i>{esc(d)}{esc(item.get('author') or '')}</i>\n\n{esc(text)}")
