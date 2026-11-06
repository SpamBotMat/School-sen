"""Данные для экранов Mini App (только из памяти — без похода на сайт школы)."""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime, time, timedelta
from typing import Any

from .. import __version__
from .. import grades as G
from ..diffing import a_key, all_assignments
from ..render import done_keys, overdue_items, subjects
from ..schemas import PALETTES, THEME_DEFAULTS, USER_DEFAULTS
from ..school import bells, lessons_for
from ..timeutil import (MONTHS_GEN, WEEKDAYS, WEEKDAYS_SHORT, as_local, human_date, monday_of, now,
                        parse_hhmm, short_date, today)

ABSENT = {"ОТ", "НП", "Б", "УП"}
LATE = {"ОП"}


def iso(dt: datetime | None) -> str | None:
    return as_local(dt).isoformat(timespec="seconds") if dt else None


def parse_week(raw: str | None) -> date:
    try:
        return monday_of(date.fromisoformat(raw)) if raw else monday_of(today())
    except ValueError:
        return monday_of(today())


def day_label(d: date) -> str:
    return f"{WEEKDAYS[d.weekday()].capitalize()}, {d.day} {MONTHS_GEN[d.month - 1]}"


# ───────────────────────────── профиль ─────────────────────────────

def me(ctx, u, tg: dict) -> dict:
    rt = ctx.store.rt
    service = {"paused": bool(rt("sync_paused")) or ctx.breaker.state != "closed",
               "maintenance": bool(rt("maintenance"))}
    base = {
        "settings": u.full_settings() if u else dict(USER_DEFAULTS),
        "theme": u.full_theme() if u else dict(THEME_DEFAULTS),
        "palettes": list(PALETTES),
        "rule": {"sure": rt("calc_sure"), "maybe": rt("calc_maybe")},
        "school": {"name": ctx.cfg.school_name, "city": ctx.cfg.city_name},
        "bot": ctx.bot_username, "version": __version__, "now": iso(now()), "today": today().isoformat(),
        "service": service,
    }
    if u is None:
        return {"user": None, **base}
    paused = u.paused_until if (u.paused_until and u.paused_until > now()) else None
    return {"user": {
        "id": u.id, "name": u.name, "first_name": u.first_name, "display_name": u.display_name or "",
        "connected": u.connected, "status": u.status, "class": u.class_name, "full_name": u.ns_name,
        "paused_until": iso(paused), "last_ok": iso(u.last_ok_at), "last_sync": iso(u.last_sync_at),
        "since": iso(u.created_at), "syncing": u.id in ctx.sync.running,
    }, **base}


# ───────────────────────────── общие кусочки ─────────────────────────────

def hw_item(ctx, a: dict, done: set[str]) -> dict:
    key = a_key(a)
    det = ctx.store.detail_get(key)
    return {
        "key": key, "subject": a["subject"], "type": a.get("type_code") or "", "type_name": a.get("type_name") or "",
        "title": a.get("title") or "", "mark": a.get("mark"), "overdue": bool(a.get("overdue")),
        "date": a["date"], "done": key in done, "hw": a.get("type_code") == "Д",
        "can_details": a.get("aid") is not None,
        "files": len((det or {}).get("attachments") or []), "has_details": det is not None,
    }


def lessons_out(items: list[dict] | None, d: date) -> list[dict] | None:
    if items is None:
        return None
    t = now()
    out = []
    for x in items:
        st, en = parse_hhmm(x.get("start")), parse_hhmm(x.get("end"))
        state = ""
        if d == t.date() and st and en:
            if st <= t.time() <= en:
                state = "now"
            elif t.time() < st:
                state = "next"
            else:
                state = "past"
        out.append({"n": x.get("n"), "s": x.get("s"), "start": x.get("start"), "end": x.get("end"),
                    "room": x.get("room") or "", "room_full": x.get("room_full") or "", "kind": x.get("kind") or "",
                    "state": state})
    # «следующий» — только первый будущий урок
    seen_next = False
    for x in out:
        if x["state"] == "next":
            if seen_next:
                x["state"] = ""
            seen_next = True
    return out


def weather_out(ctx, d: date, u=None) -> dict | None:
    w = ctx.weather.for_day(d)
    if not w:
        return None
    advice = []
    for key in (w.get("advice") or [])[:2]:
        p = ctx.phrases.pick(key, uid=u.id if u else None, tmin=w.get("tmin"), tmax=w.get("tmax"),
                             precip=w.get("precip_prob"), wind=w.get("gusts"), temp=w.get("temp_morning"))
        if p:
            advice.append(p)
    keep = ("emoji", "desc", "tmin", "tmax", "temp_morning", "precip_prob", "precip_sum", "gusts", "uv", "dust")
    return {**{k: w.get(k) for k in keep}, "city": ctx.cfg.city_name, "advice": advice, "date": d.isoformat()}


def recent_marks(subs: list[dict], limit: int = 8) -> list[dict]:
    items = []
    for s in subs:
        for d, m in s["marks"]:
            items.append({"d": d, "m": m, "s": s["name"]})
    items.sort(key=lambda x: x["d"], reverse=True)
    return items[:limit]


def _end_of_day(lessons: list[dict] | None) -> time:
    if lessons:
        t = parse_hhmm(lessons[-1].get("end"))
        if t:
            return t
    return time(15, 0)


# ───────────────────────────── главная ─────────────────────────────

def dashboard(ctx, u) -> dict:
    store, cal = ctx.store, ctx.calendar
    t, n = today(), now()
    lessons_today = lessons_for(store, u.id, t)
    school_today = cal.is_school_day(t)
    if school_today and n.time() < _end_of_day(lessons_today):
        focus = t
    else:
        focus = cal.next_school_day(t)
    focus_lessons = lessons_for(store, u.id, focus) if focus != t else lessons_today
    done = done_keys(store, u.id)
    nxt = cal.next_school_day(t)
    hw_next = [hw_item(ctx, a, done) for a in all_assignments(store.snap(u.id, "diary"))
               if a["date"] == nxt.isoformat()]
    hw_next.sort(key=lambda x: (not x["hw"], x["subject"]))
    debts = [hw_item(ctx, a, done) for a in overdue_items(store, u.id)]
    subs = subjects(store, u.id)
    avg = G.overall(subs)
    recent = recent_marks(subs)
    mood = G.mood_of(avg, G.values_of(x["m"] for x in recent[:5]))
    hour = n.hour
    if 5 <= hour < 12:
        gkey = "greet.monday" if t.weekday() == 0 else ("greet.saturday" if t.weekday() == 5 else "greet.morning")
    elif hour >= 17:
        gkey = "greet.sunday" if t.weekday() == 6 else "greet.evening"
    else:
        gkey = ""
    greet = (ctx.phrases.pick(gkey, uid=u.id, name=u.name) if gkey else None) or f"Привет, {u.name}!"
    cur_vac = cal.vacation_on(t)
    vac = None
    if cur_vac:
        end = date.fromisoformat(cur_vac["end"])
        vac = {"now": True, "title": cur_vac["title"], "end": cur_vac["end"], "days": (end - t).days}
    else:
        nv = cal.next_vacation(t)
        if nv:
            vac = {"now": False, "title": nv["title"], "start": nv["start"], "end": nv["end"], "days": nv["days"]}
    b = store.snap(u.id, "birthdays") or {}
    own = b.get("own")
    bday_self = None
    if own and own[5:] == t.isoformat()[5:]:
        try:
            age = t.year - int(own[:4])
        except ValueError:
            age = None
        bday_self = ctx.phrases.pick("birthday.self", uid=u.id, name=u.name, age=age) or f"С днём рождения, {u.name}! 🎉"
    bdays_today = [x["name"] for x in (b.get("items") or [])
                   if x.get("date") and x["date"][5:] == t.isoformat()[5:] and x["name"] != u.ns_name]
    news = (store.kv_get("news") or {}).get("items") or []
    att = Counter()
    for s in subs:
        for _, code in s["attendance"]:
            att["absent" if code in ABSENT else ("late" if code in LATE else "other")] += 1
    marks_count = Counter(G.values_of(m for s in subs for _, m in s["marks"]))
    off = cal.day_off_reason(t)
    return {
        "greeting": greet,
        "date": {"today": t.isoformat(), "title": day_label(t), "off": off},
        "focus": {"date": focus.isoformat(), "label": human_date(focus), "is_today": focus == t,
                  "lessons": lessons_out(focus_lessons, focus), "off": cal.day_off_reason(focus)},
        "weather": weather_out(ctx, t if hour < 18 else t + timedelta(days=1), u),
        "hw": {"date": nxt.isoformat(), "label": human_date(nxt), "items": hw_next,
               "done": sum(1 for x in hw_next if x["done"] and x["hw"]), "total": sum(1 for x in hw_next if x["hw"])},
        "debts": debts[:20], "debts_total": len(debts),
        "score": {"avg": None if avg is None else round(avg, 2), "mood": mood,
                  "mood_text": ctx.phrases.pick(f"mood.{mood}", uid=u.id, name=u.name) or "",
                  "counts": {str(k): marks_count.get(k, 0) for k in (5, 4, 3, 2)},
                  "subjects": sum(1 for s in subs if s["marks"])},
        "recent": recent,
        "attendance": {"absent": att["absent"], "late": att["late"]},
        "vacation": vac,
        "birthday": {"self": bday_self, "today": bdays_today[:5]},
        "news": {"count": len(news), "latest": ({"title": news[0]["title"], "date": news[0].get("date")}
                                                if news else None)},
        "sync": {"last_ok": iso(u.last_ok_at), "error": u.status == "bad_password",
                 "ready": store.snap_time(u.id, "diary") is not None},
    }


# ───────────────────────────── дневник ─────────────────────────────

def diary(ctx, u, monday: date) -> dict:
    store, cal = ctx.store, ctx.calendar
    snap = store.snap(u.id, "diary") or {}
    weeks = snap.get("weeks") or {}
    done = done_keys(store, u.id)
    items = weeks.get(monday.isoformat())
    days = []
    carried = []
    by_day: dict[str, list] = {}
    for a in items or []:
        if a["date"] < monday.isoformat():
            carried.append(hw_item(ctx, a, done))
        else:
            by_day.setdefault(a["date"], []).append(hw_item(ctx, a, done))
    count = 7 if any(k >= (monday + timedelta(days=6)).isoformat() for k in by_day) else 6
    for i in range(count):
        d = monday + timedelta(days=i)
        lst = by_day.get(d.isoformat(), [])
        lst.sort(key=lambda x: (not x["hw"], x["subject"]))
        days.append({"date": d.isoformat(), "title": day_label(d), "short": WEEKDAYS_SHORT[d.weekday()],
                     "day": d.day, "off": cal.day_off_reason(d) if d.weekday() < 6 else None,
                     "today": d == today(), "items": lst,
                     "lessons": lessons_out(lessons_for(store, u.id, d), d)})
    keys = sorted(weeks)
    return {
        "week": monday.isoformat(), "title": f"{short_date(monday)} — {short_date(monday + timedelta(days=5))}",
        "loaded": items is not None, "days": days, "carried": carried,
        "prev": (monday - timedelta(days=7)).isoformat() if (monday - timedelta(days=7)).isoformat() in weeks else None,
        "next": (monday + timedelta(days=7)).isoformat() if (monday + timedelta(days=7)).isoformat() in weeks else None,
        "weeks": keys, "current": monday_of(today()).isoformat(),
    }


# ───────────────────────────── оценки ─────────────────────────────

def marks(ctx, u) -> dict:
    store = ctx.store
    report = store.snap(u.id, "report")
    sure, maybe = float(store.rt("calc_sure")), float(store.rt("calc_maybe"))
    if report is None:
        return {"ready": False, "rule": {"sure": sure, "maybe": maybe}}
    subs = subjects(store, u.id)
    out = []
    for s in subs:
        vals = G.values_of(m for _, m in s["marks"])
        p = G.predict(vals, sure, maybe)
        att = Counter("absent" if c in ABSENT else ("late" if c in LATE else "other") for _, c in s["attendance"])
        out.append({"name": s["name"], "marks": [{"d": d, "m": m} for d, m in s["marks"]], "values": vals,
                    "pred": p.as_dict(), "text": G.prediction_text(p), "need": G.need_text(p),
                    "server_avg": s.get("server_avg"), "absent": att["absent"], "late": att["late"],
                    "debts": len(s["debts"])})
    avg = G.overall(subs)
    totals = store.snap(u.id, "totals")
    dist = Counter(v for s in out for v in s["values"])
    return {
        "ready": True, "period": report.get("period") or "", "start": report.get("start"), "end": report.get("end"),
        "rule": {"sure": sure, "maybe": maybe}, "subjects": out,
        "overall": None if avg is None else round(avg, 2),
        "dist": {str(k): dist.get(k, 0) for k in (5, 4, 3, 2)},
        "totals": totals, "updated": iso(store.snap_time(u.id, "report")),
    }


# ───────────────────────────── расписание ─────────────────────────────

def schedule(ctx, u, monday: date) -> dict:
    store, cal = ctx.store, ctx.calendar
    days = []
    has_any = False
    for i in range(7):
        d = monday + timedelta(days=i)
        ls = lessons_for(store, u.id, d)
        if ls:
            has_any = True
        if i == 6 and not ls:
            continue
        days.append({"date": d.isoformat(), "title": day_label(d), "short": WEEKDAYS_SHORT[d.weekday()], "day": d.day,
                     "today": d == today(), "off": cal.day_off_reason(d) if not ls else None,
                     "lessons": lessons_out(ls, d)})
    sched = store.snap(u.id, "schedule") or {}
    weeks = sorted((sched.get("weeks") or {}).keys())
    return {"week": monday.isoformat(), "title": f"{short_date(monday)} — {short_date(monday + timedelta(days=5))}",
            "days": days, "loaded": has_any or monday.isoformat() in weeks, "weeks": weeks,
            "current": monday_of(today()).isoformat(),
            "bells": {str(k): list(v) for k, v in sorted(bells(store).items())}}


# ───────────────────────────── календарь, объявления, именинники ─────────────────────────────

def calendar(ctx) -> dict:
    cal = ctx.calendar
    t = today()
    data = cal.data
    cur = cal.vacation_on(t)
    return {
        "loaded": bool(data),
        "current": cur, "next": cal.next_vacation(t),
        "vacations": [{"start": s, "end": e, "title": ti, "past": e < t.isoformat()}
                      for s, e, ti in sorted(data.get("vacations", []))],
        "holidays": cal.upcoming_off(t, 120),
        "year": {"start": data.get("start"), "end": data.get("end")},
        "today": t.isoformat(),
    }


def news(ctx) -> dict:
    data = ctx.store.kv_get("news") or {}
    return {"items": [{"key": x.get("key"), "title": x.get("title"), "author": x.get("author"),
                       "date": x.get("date"), "text": x.get("text"), "files": x.get("files") or []}
                      for x in (data.get("items") or [])[:30]],
            "updated": data.get("at")}


def birthdays(ctx, u) -> dict:
    b = ctx.store.snap(u.id, "birthdays") or {}
    t = today()
    items = []
    for x in b.get("items") or []:
        d = x.get("date")
        items.append({"name": x.get("name"), "date": d, "role": x.get("role"), "class": x.get("class"),
                      "day": int(d[8:10]) if d else None, "today": bool(d and d[5:] == t.isoformat()[5:]),
                      "past": bool(d and d[5:] < t.isoformat()[5:])})
    items.sort(key=lambda x: (x["day"] or 99, x["name"] or ""))
    return {"month": b.get("month"), "items": items, "own": b.get("own")}


def details_out(det: dict | None) -> dict | None:
    if det is None:
        return None
    return {"kind": det.get("kind"), "subject": det.get("subject"), "teacher": det.get("teacher"),
            "topic": det.get("topic"), "date": det.get("date"), "notes": det.get("notes"),
            "files": [{"id": a.get("id"), "name": a.get("name")} for a in det.get("attachments") or []
                      if a.get("id") is not None]}


def set_done(store, uid: int, key: str, done: bool) -> dict:
    cur: dict[str, Any] = dict(store.snap(uid, "hw_done") or {})
    if done:
        cur[key] = today().isoformat()
    else:
        cur.pop(key, None)
    # старые отметки (старше 60 дней) — чистим
    border = (today() - timedelta(days=60)).isoformat()
    cur = {k: v for k, v in cur.items() if v >= border}
    store.set_snap(uid, "hw_done", cur)
    return cur
