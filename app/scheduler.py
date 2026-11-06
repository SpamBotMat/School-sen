"""Планировщик внутри процесса: опрос NetSchool, сводки, погода, архив, уборка.

Один цикл раз в ~15 секунд. Опрос: каждый ученик раз в sync_interval минут (±разброс),
не больше sync_concurrency одновременно, ночью пауза, в воскресенье и на каникулах — реже.
Если процесс уснул (Render Free) и проснулся — пропущенные сводки досылаются в течение 90 минут.
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from collections import deque
from datetime import datetime, timedelta

from . import render
from .timeutil import in_window, now, parse_hhmm, today

log = logging.getLogger(__name__)
TICK_SECONDS = 15
CATCH_UP = timedelta(minutes=90)


class Scheduler:
    def __init__(self, ctx, rng: random.Random | None = None):
        self.ctx = ctx
        self.rng = rng or random.Random()
        self.next_at: dict[int, datetime] = {}
        self.manual: deque[int] = deque()
        self.tasks: set[asyncio.Task] = set()
        self._task: asyncio.Task | None = None
        self.last_tick: float = 0.0
        self.last_weather = 0.0
        self.last_cleanup_day: str | None = None
        self.last_hourly: str | None = None
        self.archive_queue: deque[int] = deque()
        self.archive_day: str | None = None
        self._digest_running = False
        self.last_replan = 0.0

    @property
    def store(self):
        return self.ctx.store

    # ───────────────────────── жизненный цикл ─────────────────────────

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(self._loop())

    @property
    def alive(self) -> bool:
        return self._task is not None and not self._task.done() and time.monotonic() - self.last_tick < 120

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        for t in list(self.tasks):
            t.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)

    async def _loop(self) -> None:
        self.plan_all()
        while True:
            self.last_tick = time.monotonic()
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("Планировщик: ошибка в цикле")
            await asyncio.sleep(TICK_SECONDS)

    # ───────────────────────── план опросов ─────────────────────────

    def interval(self, d=None) -> float:
        rt = self.store.rt
        minutes = float(rt("sync_interval"))
        if self.ctx.calendar.slow_day(d or today()):
            minutes *= float(rt("slow_factor"))
        return minutes * 60

    def plan_all(self) -> None:
        """Разложить всех равномерно по интервалу (после старта)."""
        t = now()
        span = self.interval()
        for u in self.store.users.values():
            if not u.syncable:
                continue
            if u.meta.get("baseline") is None:
                self.next_at[u.id] = t + timedelta(seconds=self.rng.uniform(5, 120))
            else:
                self.next_at[u.id] = self._skip_night(t + timedelta(seconds=self.rng.uniform(30, span)))

    def plan_next(self, uid: int, base: datetime | None = None) -> datetime:
        t = base or now()
        span = self.interval()
        j = float(self.store.rt("sync_jitter"))
        nxt = self._skip_night(t + timedelta(seconds=span * (1 + self.rng.uniform(-j, j))))
        self.next_at[uid] = nxt
        return nxt

    def _skip_night(self, t: datetime) -> datetime:
        rt = self.store.rt
        start, end = parse_hhmm(rt("night_from")), parse_hhmm(rt("night_to"))
        if not (start and end) or not in_window(t.time(), start, end):
            return t
        wake = t.replace(hour=end.hour, minute=end.minute, second=0, microsecond=0)
        if wake <= t:
            wake += timedelta(days=1)
        return wake + timedelta(seconds=self.rng.uniform(0, 40 * 60))

    def request(self, uid: int) -> None:
        """Ручное «обновить сейчас» — в начало очереди."""
        if uid not in self.manual:
            self.manual.appendleft(uid)

    def forget(self, uid: int) -> None:
        self.next_at.pop(uid, None)
        try:
            self.manual.remove(uid)
        except ValueError:
            pass

    # ───────────────────────── тик ─────────────────────────

    async def tick(self) -> None:
        t = now()
        self._alerts()
        await self._periodic(t)
        if not self._digest_running:
            self._spawn(self._digests(t))
        self._dispatch(t)

    def _spawn(self, coro) -> asyncio.Task:
        task = asyncio.get_running_loop().create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    def _alerts(self) -> None:
        br = self.ctx.breaker
        if br.pending_alerts:
            msgs, br.pending_alerts = br.pending_alerts, []
            from .bot.keyboards import breaker_kb
            for m in msgs:
                self.store.event("breaker", m.splitlines()[0][:200], level="warn")
                markup = breaker_kb() if m.startswith("⛔") else None
                self._spawn(self.ctx.notifier.admins(m, markup=markup))

    def _dispatch(self, t: datetime) -> None:
        rt = self.store.rt
        br = self.ctx.breaker
        br.configure(int(rt("breaker_streak")), int(rt("breaker_cooldown")))
        if rt("sync_paused") or rt("maintenance"):
            return
        capacity = int(rt("sync_concurrency")) - len(self.ctx.sync.running)
        if capacity <= 0:
            return
        start, end = parse_hhmm(rt("night_from")), parse_hhmm(rt("night_to"))
        night = bool(start and end and in_window(t.time(), start, end))
        picked: list[int] = []
        while self.manual and len(picked) < capacity:
            uid = self.manual.popleft()
            u = self.store.get_user(uid)
            if u and u.syncable and uid not in self.ctx.sync.running:
                picked.append(uid)
        if not night and len(picked) < capacity:
            due = sorted((at, uid) for uid, at in self.next_at.items() if at <= t and uid not in picked)
            for _, uid in due:
                if len(picked) >= capacity:
                    break
                u = self.store.get_user(uid)
                if not u or not u.syncable:
                    self.next_at.pop(uid, None)
                    continue
                if uid in self.ctx.sync.running:
                    continue
                picked.append(uid)
        for uid in picked:
            if not br.allow():
                break
            u = self.store.get_user(uid)
            self.plan_next(uid, t)
            baseline = u.meta.get("baseline") is None
            self._spawn(self.ctx.sync.run(u, baseline=baseline))

    # ───────────────────────── периодика ─────────────────────────

    def replan_missing(self, t: datetime) -> int:
        """Вернуть в план тех, кто снова может опрашиваться (кончилась пауза, разбан и т.п.)."""
        n = 0
        for u in self.store.users.values():
            if u.id not in self.next_at and u.syncable:
                self.next_at[u.id] = t + timedelta(seconds=self.rng.uniform(20, 240))
                n += 1
        return n

    async def _periodic(self, t: datetime) -> None:
        mono = time.monotonic()
        if mono - self.last_replan > 300:
            self.last_replan = mono
            self.replan_missing(t)
        if (mono - self.last_weather > 3600 or self.ctx.weather.stale(90)) and 5 <= t.hour < 23:
            self.last_weather = mono
            self._spawn(self.ctx.weather.refresh())
        hour_key = t.strftime("%Y-%m-%d %H")
        if self.last_hourly != hour_key:
            self.last_hourly = hour_key
            self.ctx.limits.gc()
            if t.hour in (0, 15) or self.last_hourly is None:
                self._spawn(self._refresh_cheatsheets())
        day = t.date().isoformat()
        if t.hour >= 4 and self.last_cleanup_day != day:
            self.last_cleanup_day = day
            self._spawn(self._cleanup())
        # еженедельный архив — по воскресеньям, по одному ученику раз в несколько секунд
        at = parse_hhmm(self.store.rt("weekly_archive_time"))
        if t.weekday() == 6 and at and t.time() >= at and self.archive_day != day:
            self.archive_day = day
            for u in self.store.users.values():
                if u.status == "active" and u.connected and u.opt("weekly_archive") and u.archive_sent != day:
                    self.archive_queue.append(u.id)
            if self.archive_queue:
                self._spawn(self._archive_worker())

    async def _cleanup(self) -> None:
        try:
            res = await self.store.cleanup()
            border = now() - timedelta(days=30)
            gone = [u.id for u in self.store.users.values()
                    if u.status == "bot_blocked" and u.updated_at and u.updated_at < border]
            for uid in gone:
                await self.store.delete_user(uid)
                self.forget(uid)
            if gone:
                res["blocked_users_deleted"] = len(gone)
            self.store.event("cleanup", f"уборка: {res}")
        except Exception:  # noqa: BLE001
            log.exception("Уборка не удалась")

    async def _refresh_cheatsheets(self) -> None:
        for u in list(self.store.users.values()):
            if u.status == "active" and u.connected and u.opt("cheatsheet") and u.pinned_message_id:
                try:
                    await self.ctx.notifier.update_cheatsheet(u, render.cheatsheet(self.ctx, u))
                except Exception:  # noqa: BLE001
                    log.exception("Шпаргалка %s", u.id)

    async def _archive_worker(self) -> None:
        from .archive import send_weekly
        while self.archive_queue:
            uid = self.archive_queue.popleft()
            u = self.store.get_user(uid)
            if not u:
                continue
            t0 = time.monotonic()
            try:
                await send_weekly(self.ctx, u)
            except Exception:  # noqa: BLE001
                log.exception("Архив %s", uid)
            # на Render Free всего 0.1 CPU: пауза не короче самой отрисовки → архив занимает ≤ ~половины CPU
            await asyncio.sleep(max(4.0, min(30.0, time.monotonic() - t0)))

    # ───────────────────────── сводки ─────────────────────────

    async def _digests(self, t: datetime) -> None:
        self._digest_running = True
        try:
            for u in list(self.store.users.values()):
                if u.status != "active" or not u.connected:
                    continue
                try:
                    await self._digest_for(u, t)
                except Exception:  # noqa: BLE001
                    log.exception("Сводка %s", u.id)
        finally:
            self._digest_running = False

    def _due(self, u, t: datetime, time_key: str, sent: str | None) -> bool:
        at = parse_hhmm(u.opt(time_key))
        if at is None or sent == t.date().isoformat():
            return False
        target = t.replace(hour=at.hour, minute=at.minute, second=0, microsecond=0)
        return target <= t <= target + CATCH_UP

    async def _digest_for(self, u, t: datetime) -> None:
        ctx = self.ctx
        day = t.date().isoformat()
        # перед утренней сводкой — свежий опрос (если давно не было)
        mt = parse_hhmm(u.opt("morning_time"))
        if u.opt("morning") and mt and u.morning_sent != day:
            target = t.replace(hour=mt.hour, minute=mt.minute, second=0, microsecond=0)
            if target - timedelta(minutes=25) <= t < target - timedelta(minutes=8):
                if not u.last_ok_at or u.last_ok_at < target - timedelta(minutes=50):
                    if u.id not in self.manual and u.id not in ctx.sync.running:
                        self.request(u.id)
        if u.opt("morning") and self._due(u, t, "morning_time", u.morning_sent):
            u.morning_sent = day
            self.store.save_user(u)
            pending = ctx.notifier.take_pending(u) if not ctx.notifier.quiet_now(u) else []
            text = render.morning_digest(ctx, u, pending=pending)
            if text:
                from .bot.keyboards import open_app_kb
                await ctx.notifier.send(u, text, markup=open_app_kb(ctx, "home"))
                self.store.stat("digest_morning")
            elif pending:
                await ctx.notifier.send(u, "🌙 <b>Пока были тихие часы</b>\n\n" + "\n\n".join(pending))
            return
        if u.opt("evening") and self._due(u, t, "evening_time", u.evening_sent):
            u.evening_sent = day
            self.store.save_user(u)
            text = render.evening_digest(ctx, u)
            if text:
                from .bot.keyboards import open_app_kb
                await ctx.notifier.send(u, text, markup=open_app_kb(ctx, "diary"))
                self.store.stat("digest_evening")
            return
        if u.pending and not ctx.notifier.quiet_now(u):
            # подождём утреннюю сводку, если она скоро
            if u.opt("morning") and mt and u.morning_sent != day:
                target = t.replace(hour=mt.hour, minute=mt.minute, second=0, microsecond=0)
                if t < target and target - t <= timedelta(minutes=75) and ctx.calendar.is_school_day(t.date()):
                    return
            items = ctx.notifier.take_pending(u)
            if items:
                await ctx.notifier.send(u, "🌙 <b>Пока были тихие часы</b>\n\n" + "\n\n".join(items))
