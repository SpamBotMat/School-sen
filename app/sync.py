"""Опрос NetSchool для одного ученика: вход → дневник/отчёты → выход → изменения → уведомления.

Бережём сайт школы: одна сессия = один вход и выход; неизменённые страницы не разбираем
заново (отпечаток содержимого); тяжёлые отчёты — реже (раз в ~2 часа / раз в день).
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import httpx

from netschool import parsers as P
from netschool.errors import LoginError, NetSchoolError
from netschool.models import Assignment

from . import render
from .crypto import CredentialsError
from .diffing import (a_key, all_assignments, diary_snapshot, diff_diary, diff_report, diff_schedule,
                      diff_totals, report_snapshot, totals_snapshot)
from .school import calendar_from_year, lessons_to_snapshot
from .timeutil import monday_of, now, today

log = logging.getLogger(__name__)
SYNC_TIMEOUT = 240


@dataclass
class Fetched:
    weeks_fresh: dict[str, list[Assignment]] = field(default_factory=dict)
    weeks_same: set[str] = field(default_factory=set)
    report: Any = None
    schedule: dict[str, list] | None = None
    totals: Any = None
    birthdays: list | None = None
    calendar: Any = None
    month_events: list | None = None
    news: list | None = None
    details: dict[str, Any] = field(default_factory=dict)
    fp: dict[str, str] = field(default_factory=dict)
    did: list[str] = field(default_factory=list)


@dataclass
class Outcome:
    status: str                  # ok | skipped | login_failed | creds | error
    events: list[dict] = field(default_factory=list)
    error: str | None = None
    seconds: float = 0.0

    @property
    def ok(self) -> bool:
        return self.status == "ok"


def detail_to_dict(d) -> dict:
    return {
        "kind": d.kind, "subject": d.subject, "teacher": d.teacher, "topic": d.topic,
        "date": d.date.isoformat() if d.date else None, "notes": d.notes,
        "attachments": [{"name": a.name, "url": a.url, "id": a.attachment_id} for a in d.attachments],
    }


def news_item(a) -> dict:
    return {"key": a.key, "title": a.title, "author": a.author,
            "date": a.date.isoformat() if a.date else None,
            "text": (a.text or "").replace("??", "").strip()[:4000],
            "files": [x.name for x in a.attachments]}


class SyncService:
    def __init__(self, ctx):
        self.ctx = ctx
        self.running: set[int] = set()
        self.durations: deque[float] = deque(maxlen=200)
        self.results: deque[tuple[float, str]] = deque(maxlen=500)   # (time, status)
        self.ondemand = asyncio.Semaphore(2)    # подробности/файлы по запросу — не больше 2 сессий разом
        self.connect_gate = asyncio.Semaphore(2)   # первые входы новых учеников — тоже не больше 2 разом

    @property
    def store(self):
        return self.ctx.store

    # ───────────────────────── вход ─────────────────────────

    async def open_session(self, login: str, md5: str, plen: int):
        ns = self.ctx.client_factory()
        try:
            await ns.login(login, password_md5=md5, password_len=plen)
        except BaseException:
            await ns.aclose()
            raise
        return ns

    async def _session_for(self, u):
        md5, plen = self.ctx.vault.open(u.ns_cred or "")
        return await self.open_session(u.ns_login or "", md5, plen)

    # ───────────────────────── основной цикл опроса ─────────────────────────

    async def run(self, u, *, baseline: bool = False, reason: str = "auto", client=None) -> Outcome:
        if u.id in self.running:
            return Outcome("skipped")
        self.running.add(u.id)
        t0 = time.monotonic()
        store = self.store
        try:
            ns = client
            try:
                if ns is None:
                    ns = await self._session_for(u)
                fetched = await asyncio.wait_for(self._fetch(u, ns, baseline), timeout=SYNC_TIMEOUT)
            finally:
                if ns is not None:
                    try:
                        await ns.logout()
                    except Exception:  # noqa: BLE001
                        pass
                    await ns.aclose()
            events = self._apply(u, fetched, baseline)
            u.last_sync_at = u.last_ok_at = now()
            u.fail_count = 0
            u.last_error = None
            if u.status == "bad_password":
                u.status = "active"
            store.save_user(u, urgent=baseline)
            store.stat("sync_ok")
            self.ctx.breaker.success()
            dt = time.monotonic() - t0
            self.durations.append(dt)
            self.results.append((time.time(), "ok"))
            if not baseline:
                await self._deliver(u, events)
            if u.opt("cheatsheet"):
                await self.ctx.notifier.update_cheatsheet(u, render.cheatsheet(self.ctx, u))
            return Outcome("ok", events=events, seconds=dt)
        except CredentialsError as e:
            u.status = "bad_password"
            u.last_error = "ключ шифрования сменился — нужно войти заново"
            store.save_user(u, urgent=True)
            store.event("creds", str(e), user_id=u.id, level="warn")
            await self.ctx.notifier.send(u, "🔑 Нужно заново подключить NetSchool (обновились настройки безопасности). "
                                            "Нажми /login — это минута.")
            return Outcome("creds", error=str(e))
        except LoginError as e:
            return await self._login_failed(u, e)
        except (NetSchoolError, httpx.HTTPError, asyncio.TimeoutError, OSError) as e:
            text = f"{type(e).__name__}: {e}"[:300]
            u.last_sync_at = now()
            u.fail_count = (u.fail_count or 0) + 1
            u.last_error = text
            store.save_user(u)
            store.stat("sync_fail")
            self.ctx.breaker.failure(text)
            self.results.append((time.time(), "error"))
            if u.fail_count in (3, 10):
                store.event("sync_error", text, user_id=u.id, level="warn")
            return Outcome("error", error=text, seconds=time.monotonic() - t0)
        except Exception as e:  # noqa: BLE001 — баг в разборе не должен ронять планировщик
            log.exception("Опрос %s упал", u.id)
            text = f"{type(e).__name__}: {e}"[:300]
            u.last_sync_at = now()
            u.fail_count = (u.fail_count or 0) + 1
            u.last_error = "внутренняя ошибка: " + text
            store.save_user(u)
            store.stat("sync_bug")
            store.event("sync_bug", text, user_id=u.id, level="error")
            self.results.append((time.time(), "bug"))
            return Outcome("error", error=text, seconds=time.monotonic() - t0)
        finally:
            self.running.discard(u.id)

    async def _login_failed(self, u, e: LoginError) -> Outcome:
        store = self.store
        text = str(e)[:300]
        u.last_sync_at = now()
        store.stat("login_fail")
        if not e.bad_credentials:
            # сайт ответил странно (нет AT, 5xx, не JSON) — это не пароль, а сбой сервера
            u.fail_count = (u.fail_count or 0) + 1
            u.last_error = "вход: " + text
            store.save_user(u)
            self.ctx.breaker.failure("вход: " + text)
            self.results.append((time.time(), "error"))
            return Outcome("error", error=text)
        self.ctx.breaker.login_failure(u.id)
        self.results.append((time.time(), "login"))
        u.status = "bad_password"
        u.last_error = "неверный логин/пароль: " + text
        store.save_user(u, urgent=True)
        store.event("bad_password", text, user_id=u.id, level="warn")
        locked = "блок" in text.lower()
        msg = ("🔒 NetSchool пишет, что учётная запись заблокирована. Разблокировать её может только школа. "
               "Потом нажми /login." if locked else
               "🔑 Не получается войти в NetSchool — похоже, пароль поменялся.\n"
               "Обнови его: /login (сообщение с паролем я сразу удалю).")
        await self.ctx.notifier.send(u, msg)
        return Outcome("login_failed", error=text)

    # ───────────────────────── загрузка ─────────────────────────

    async def _fetch(self, u, ns, baseline: bool) -> Fetched:
        store, rt = self.store, self.store.rt
        f = Fetched()
        t = today()
        monday = monday_of(t)
        meta_fp = u.meta.get("fp") or {}
        old_diary = store.snap(u.id, "diary") or {}
        mondays = [monday]
        if t.weekday() >= 4 or baseline:
            mondays.append(monday + timedelta(days=7))

        for m in mondays:
            key = m.isoformat()
            if hasattr(ns, "diary_html"):
                html = await ns.diary_html(m)
                fp = P.content_fingerprint(html)
                f.fp["d:" + key] = fp
                if not baseline and fp == meta_fp.get("d:" + key) and key in (old_diary.get("weeks") or {}):
                    f.weeks_same.add(key)
                else:
                    f.weeks_fresh[key] = P.parse_diary(html)
            else:
                f.weeks_fresh[key] = await ns.diary(m)
            f.did.append("diary")

        report_at = u.meta.get("report_at")
        due = baseline or not report_at or (now().timestamp() - report_at) >= rt("report_every") * 60
        if due:
            if hasattr(ns, "student_total_html"):
                html = await ns.student_total_html()
                fp = P.content_fingerprint(html)
                f.fp["r"] = fp
                if baseline or fp != meta_fp.get("r") or store.snap(u.id, "report") is None:
                    f.report = P.parse_student_total(html)
            else:
                f.report = await ns.student_total()
            f.did.append("report")

        sched = store.snap(u.id, "schedule") or {}
        have = set((sched.get("weeks") or {}).keys())
        if baseline or u.meta.get("sched_day") != t.isoformat() or any(m.isoformat() not in have for m in mondays):
            f.schedule = {}
            for m in mondays:
                f.schedule[m.isoformat()] = lessons_to_snapshot(await ns.week_schedule(m))
            f.did.append("schedule")

        if baseline or u.meta.get("totals_day") != t.isoformat():
            try:
                f.totals = await ns.total_marks()
                f.did.append("totals")
            except NetSchoolError as e:
                if "не проставлен" not in str(e).lower():
                    raise

        month = t.strftime("%Y-%m")
        if baseline or u.meta.get("bday_month") != month:
            try:
                f.birthdays = await ns.birthdays()
                f.did.append("birthdays")
            except NetSchoolError as e:
                log.info("Именинники: %s", e)
                f.birthdays = []

        # общее для всей школы — подтягиваем «попутно» в чьей-то сессии
        cal_at = store.kv_get("calendar_at") or 0
        if now().timestamp() - cal_at > 3 * 86400:
            store.kv_set("calendar_at", now().timestamp(), persist=False)
            try:
                f.calendar = await ns.year_calendar()
                f.month_events = await ns.month_events()
                f.did.append("calendar")
            except NetSchoolError as e:
                log.info("Календарь: %s", e)
        news_at = store.kv_get("news_at") or 0
        if rt("announcements") and 6 <= now().hour < 22 and now().timestamp() - news_at > 3600:
            store.kv_set("news_at", now().timestamp(), persist=False)
            try:
                f.news = await ns.announcements()
                f.did.append("news")
            except NetSchoolError as e:
                log.info("Объявления: %s", e)

        limit = rt("details_per_sync")
        if limit and hasattr(ns, "assignment_details"):
            fresh = [a for items in f.weeks_fresh.values() for a in items]
            want = [a for a in fresh if a.is_homework and a.aid is not None and a.date >= t
                    and store.detail_get(a.key) is None]
            want.sort(key=lambda a: a.date)
            for a in want[:limit]:
                try:
                    f.details[a.key] = await ns.assignment_details(a)
                except (NetSchoolError, ValueError) as e:
                    log.info("Подробности %s: %s", a.key, e)
        return f

    # ───────────────────────── применение ─────────────────────────

    def _apply(self, u, f: Fetched, baseline: bool) -> list[dict]:
        store = self.store
        t = today()
        monday = monday_of(t)
        events: list[dict] = []
        old_diary = store.snap(u.id, "diary")
        covered = set(f.weeks_same)
        if f.weeks_fresh:
            ev, cov = diff_diary(old_diary, f.weeks_fresh, t)
            covered |= cov
            events += ev
            store.set_snap(u.id, "diary", diary_snapshot(old_diary, f.weeks_fresh, monday))
        elif old_diary:
            store.set_snap(u.id, "diary", diary_snapshot(old_diary, {}, monday))

        if f.report is not None:
            new = report_snapshot(f.report)
            events += diff_report(store.snap(u.id, "report"), new, covered)
            store.set_snap(u.id, "report", new)
            info = f.report.info or {}
            if info.get("Ученик"):
                u.ns_name = info["Ученик"][:200]
            if info.get("Класс"):
                u.class_name = info["Класс"][:32]
        if "report" in f.did:
            u.meta["report_at"] = now().timestamp()

        if f.schedule is not None:
            old = store.snap(u.id, "schedule") or {}
            weeks = dict(old.get("weeks") or {})
            weeks.update(f.schedule)
            keep = {(monday + timedelta(days=7 * i)).isoformat() for i in (-1, 0, 1)}
            new = {"weeks": {k: v for k, v in weeks.items() if k in keep}}
            days = [d for d in (t, t + timedelta(days=1)) if d > t or now().hour < 9]
            events += diff_schedule(old if old else None, new, days)
            store.set_snap(u.id, "schedule", new)
            u.meta["sched_day"] = t.isoformat()

        if f.totals is not None:
            new = totals_snapshot(f.totals)
            events += diff_totals(store.snap(u.id, "totals"), new)
            store.set_snap(u.id, "totals", new)
        if "totals" in f.did:
            u.meta["totals_day"] = t.isoformat()

        if f.birthdays is not None:
            items = []
            for b in f.birthdays:
                items.append({"name": b.name, "date": b.birth_date.isoformat() if b.birth_date else None,
                              "role": b.role, "class": b.class_name})
                if b.birth_date and self._is_me(u, b.name):
                    u.meta["bday"] = b.birth_date.isoformat()
            store.set_snap(u.id, "birthdays", {"month": t.strftime("%Y-%m"), "items": items,
                                               "own": u.meta.get("bday")})
            u.meta["bday_month"] = t.strftime("%Y-%m")

        if f.calendar is not None:
            store.kv_set("calendar", calendar_from_year(f.calendar, f.month_events))
            store.kv_set("calendar_at", now().timestamp())
        if f.news is not None:
            self._apply_news(f.news)
        for key, det in f.details.items():
            store.detail_set(key, detail_to_dict(det))

        fp = dict(u.meta.get("fp") or {})
        fp.update(f.fp)
        keep_keys = {"r"} | {"d:" + (monday + timedelta(days=7 * i)).isoformat() for i in (0, 1)}
        u.meta["fp"] = {k: v for k, v in fp.items() if k in keep_keys}
        u.meta["baseline"] = u.meta.get("baseline") or now().isoformat()
        return [] if baseline else events

    @staticmethod
    def _is_me(u, name: str) -> bool:
        if not u.ns_name or not name:
            return False
        mine = {p.lower().strip(".") for p in u.ns_name.split() if len(p) > 1}
        theirs = {p.lower().strip(".") for p in name.split() if len(p) > 1}
        return len(mine & theirs) >= 2 or (len(theirs) == 1 and theirs <= mine)

    def _apply_news(self, items) -> None:
        store = self.store
        old = store.kv_get("news") or {}
        known = set(old.get("keys") or [])
        fresh = [news_item(a) for a in items]
        store.kv_set("news", {"items": fresh[:30], "keys": sorted(known | {x["key"] for x in fresh})[-300:],
                              "at": now().isoformat()})
        if not old:
            return              # первый раз — база, не рассылаем
        new_items = [x for x in fresh if x["key"] not in known]
        if new_items:
            store.event("news", f"новых объявлений: {len(new_items)}")
            asyncio.get_running_loop().create_task(self._broadcast_news(new_items[:3]))

    async def _broadcast_news(self, items: list[dict]) -> None:
        from .bot.keyboards import open_app_kb
        for item in items:
            text = render.news_message(item)
            for u in list(self.store.users.values()):
                if u.status == "active" and u.connected and u.opt("n_news"):
                    await self.ctx.notifier.notify(u, text, markup=open_app_kb(self.ctx, "news"))

    # ───────────────────────── уведомления ─────────────────────────

    def filter_events(self, u, events: list[dict]) -> list[dict]:
        t = today()
        nxt = self.ctx.calendar.next_school_day(t)
        evening_done = (u.evening_sent == t.isoformat()) or not u.opt("evening")
        out = []
        for e in events:
            if e["type"] in ("hw_new", "hw_changed"):
                d = date.fromisoformat(e["date"])
                if u.opt("hw_instant"):
                    out.append(e)
                elif u.opt("n_hw") and d == nxt and evening_done:
                    out.append(e)            # вечерняя сводка уже ушла, а на завтра добавили ДЗ
            elif e["type"] == "task_new":
                d = date.fromisoformat(e["date"])
                if (d - t).days <= 7:
                    out.append(e)
            else:
                out.append(e)
        return out

    async def _deliver(self, u, events: list[dict]) -> None:
        if not events:
            return
        store = self.store
        store.stat("events", len(events))
        evs = self.filter_events(u, events)
        text = render.notification(self.ctx, u, evs)
        if not text:
            return
        from .bot.keyboards import open_app_kb
        await self.ctx.notifier.notify(u, text, markup=open_app_kb(self.ctx, "marks" if any(
            e["type"].startswith("mark") for e in evs) else "diary"))

    # ───────────────────────── по запросу из приложения ─────────────────────────

    def find_assignment(self, u, key: str) -> dict | None:
        for a in all_assignments(self.store.snap(u.id, "diary")):
            if a_key(a) == key:
                return a
        return None

    async def details(self, u, key: str, *, fresh: bool = False) -> dict | None:
        """Подробности задания из кэша; если нет — сходить на сайт (отдельная короткая сессия)."""
        a = self.find_assignment(u, key)
        if a is None:
            return None
        cached = self.store.detail_get(key)
        if cached is not None and not fresh:
            return cached
        if a.get("aid") is None or not u.connected:
            return cached
        if self.ctx.breaker.state != "closed" or self.store.rt("sync_paused") or u.id in self.running:
            return cached
        async with self.ondemand:
            ns = await self._session_for(u)
            try:
                det = await ns.assignment_details(Assignment(**{**a, "date": date.fromisoformat(a["date"])}))
            finally:
                await ns.logout()
                await ns.aclose()
        data = detail_to_dict(det)
        self.store.detail_set(key, data)
        return data

    async def download(self, u, key: str, attachment_id: int):
        """Скачивает вложение задания (ЭКСПЕРИМЕНТ — на живом файле ещё не проверено)."""
        from netschool.models import Attachment
        det = self.store.detail_get(key) or await self.details(u, key)
        if not det:
            raise ValueError("нет подробностей задания")
        att = next((x for x in det.get("attachments", []) if x.get("id") == attachment_id), None)
        if att is None:
            raise ValueError("нет такого файла")
        if self.ctx.breaker.state != "closed" or self.store.rt("sync_paused"):
            raise ValueError("опрос сайта школы на паузе")
        async with self.ondemand:
            ns = await self._session_for(u)
            try:
                return await ns.download_attachment(Attachment(name=att["name"], url=att["url"],
                                                               attachment_id=att["id"]))
            finally:
                await ns.logout()
                await ns.aclose()

    def stats(self) -> dict:
        cutoff = time.time() - 3600
        last = [s for t_, s in self.results if t_ >= cutoff]
        return {
            "running": len(self.running),
            "last_hour": len(last),
            "ok_last_hour": sum(1 for s in last if s == "ok"),
            "errors_last_hour": sum(1 for s in last if s != "ok"),
            "avg_seconds": round(sum(self.durations) / len(self.durations), 1) if self.durations else None,
        }
