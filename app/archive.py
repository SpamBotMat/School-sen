"""Офлайн-архив: картинки (оценки, посещаемость, ДЗ на неделю, расписание) в чат.

Одинаковые данные → те же картинки: повторно шлём по file_id, без перерисовки.
Рисуем в отдельном потоке, чтобы бот не подвисал (у Render Free всего 0.1 CPU).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta

from . import images
from .diffing import a_key
from .render import done_keys, homework_on
from .school import lessons_for
from .store import digest, dumps
from .timeutil import monday_of, now, today

log = logging.getLogger(__name__)

KINDS = {"marks": "⭐ Оценки", "attendance": "🚪 Посещаемость", "hw": "📚 ДЗ на неделю", "schedule": "🗓 Расписание"}


def _hw_days(ctx, u) -> list[tuple[date, list[dict]]]:
    out = []
    d = today()
    for _ in range(6):
        d = ctx.calendar.next_school_day(d)
        out.append((d, homework_on(ctx.store, u.id, d)))
    return out


def _week_days(ctx, u) -> list[tuple[date, list[dict] | None]]:
    t = today()
    monday = monday_of(t) + (timedelta(days=7) if t.weekday() >= 5 else timedelta())
    days = []
    for i in range(7):
        d = monday + timedelta(days=i)
        if d.weekday() in ctx.calendar.school_weekdays():
            days.append((d, lessons_for(ctx.store, u.id, d)))
    return days


def inputs(ctx, u, kind: str):
    store = ctx.store
    if kind == "marks":
        return (store.snap(u.id, "report"), store.rt("calc_sure"), store.rt("calc_maybe"), u.name, u.class_name)
    if kind == "attendance":
        return (store.snap(u.id, "report"), u.name, u.class_name)
    if kind == "hw":
        return (_hw_days(ctx, u), sorted(done_keys(store, u.id)), u.name)
    if kind == "schedule":
        return (_week_days(ctx, u), u.name)
    raise ValueError(kind)


def render_kind(ctx, u, kind: str, data) -> list[bytes]:
    if kind == "marks":
        return images.marks_images(u, data[0], data[1], data[2])
    if kind == "attendance":
        return images.attendance_images(u, data[0])
    if kind == "hw":
        return images.homework_images(u, data[0], set(data[1]), a_key)
    if kind == "schedule":
        return images.schedule_images(u, data[0])
    raise ValueError(kind)


def _hash(data) -> str:
    return digest(dumps(data))


async def build(ctx, u, kind: str) -> tuple[str, list[bytes] | None, list[str] | None]:
    """→ (хэш данных, png-страницы или None, file_id из кэша или None)."""
    data = inputs(ctx, u, kind)
    h = _hash(data)
    cached = ctx.store.file_get(f"img:{kind}:{h}")
    if cached:
        return h, None, cached["file_id"].split(",")
    pngs = await asyncio.to_thread(render_kind, ctx, u, kind, data)
    ctx.store.stat("images_rendered", len(pngs))
    return h, pngs, None


async def send_kind(ctx, u, kind: str) -> bool:
    h, pngs, fids = await build(ctx, u, kind)
    title = KINDS[kind]
    caption = f"{title} · {now():%d.%m %H:%M}"
    if fids:
        sent = await ctx.notifier.send_album(u, [(f"{kind}.png", f) for f in fids], caption=caption)
        if sent:
            return True
        ctx.store.file_set(f"img:{kind}:{h}", "", None, None)        # file_id протух — перерисуем
        h, pngs, _ = await build(ctx, u, kind)
    if not pngs:
        return False
    ids = await ctx.notifier.send_album(u, [(f"{kind}-{i + 1}.png", p) for i, p in enumerate(pngs)], caption=caption)
    if ids and len(ids) == len(pngs):
        ctx.store.file_set(f"img:{kind}:{h}", ",".join(ids), kind, sum(len(p) for p in pngs))
    return bool(ids)


async def send_weekly(ctx, u) -> bool:
    """Воскресный архив: все 4 картинки одним альбомом, только если что-то поменялось."""
    store = ctx.store
    parts = {k: inputs(ctx, u, k) for k in KINDS}
    h = _hash(parts)
    day = today().isoformat()
    u.archive_sent = day
    if u.archive_hash == h:
        store.save_user(u)
        return False
    pages: list[tuple[str, bytes | str]] = []
    for kind in ("hw", "schedule", "marks", "attendance"):
        kh, pngs, fids = await build(ctx, u, kind)
        if fids:
            pages += [(f"{kind}.png", f) for f in fids]
        elif pngs:
            pages += [(f"{kind}-{i + 1}.png", p) for i, p in enumerate(pngs)]
    if not pages:
        store.save_user(u)
        return False
    sent = await ctx.notifier.send_album(u, pages[:10], caption="📦 Архив недели — откроется и без интернета")
    u.archive_hash = h
    store.save_user(u)
    store.stat("weekly_archive")
    return bool(sent)
