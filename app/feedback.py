"""Обращения учеников к администратору: из чата и из Mini App, ответы — анонимно, «от Администратора»."""
from __future__ import annotations

import logging

from sqlalchemy import func, select, update

from . import db as D
from .render import esc
from .timeutil import as_local, now

log = logging.getLogger(__name__)
MAX_LEN = 2000


class FeedbackLimit(Exception):
    pass


def _iso(dt) -> str | None:
    return as_local(dt).isoformat(timespec="seconds") if dt else None


def _who(u) -> str:
    bits = [esc(u.first_name or "без имени")]
    if u.username:
        bits.append("@" + esc(u.username))
    bits.append(f"id <code>{u.id}</code>")
    if u.class_name:
        bits.append(f"класс {esc(u.class_name)}")
    return " · ".join(bits)


async def submit(ctx, u, text: str, *, source: str = "bot") -> int:
    """Сохранить обращение и показать админам. FeedbackLimit — если превышен дневной лимит."""
    text = (text or "").strip()[:MAX_LEN]
    if not text:
        raise ValueError("пустое сообщение")
    if not ctx.limits.allow(f"fb:{u.id}", int(ctx.store.rt("feedback_per_day")), 86400):
        raise FeedbackLimit()
    res = await ctx.store.execute(D.feedback.insert().values(
        user_id=u.id, text=text, created_at=now(), status="new"))
    fid = int(res.inserted_primary_key[0])
    ctx.store.stat("feedback")
    ctx.store.event("feedback", f"обращение #{fid} ({source})", user_id=u.id)
    from .bot.keyboards import feedback_admin_kb
    body = (f"✉️ <b>Обращение #{fid}</b>{' (из приложения)' if source == 'app' else ''}\n"
            f"От: {_who(u)}\n\n{esc(text)}")
    await ctx.notifier.admins(body, html_ok=True, markup=feedback_admin_kb(fid))
    return fid


async def get(ctx, fid: int) -> dict | None:
    rows = await ctx.store.query(select(D.feedback).where(D.feedback.c.id == fid))
    return rows[0] if rows else None


async def reply(ctx, fid: int, text: str) -> str:
    """Ответить ученику от имени «Администратор». → 'ok' | 'missing' | 'gone' | 'failed'."""
    item = await get(ctx, fid)
    if item is None:
        return "missing"
    u = ctx.store.get_user(item["user_id"])
    if u is None:
        return "gone"
    text = (text or "").strip()[:MAX_LEN]
    msg = await ctx.notifier.send(u, f"✉️ <b>Ответ администратора</b>\n\n{esc(text)}\n\n"
                                     "<i>Хочешь ответить — /feedback</i>")
    if msg is None:
        return "failed"
    await ctx.store.execute(update(D.feedback).where(D.feedback.c.id == fid).values(
        reply=text, replied_at=now(), status="answered"))
    ctx.store.event("feedback_reply", f"ответ на обращение #{fid}", user_id=u.id)
    return "ok"


async def close(ctx, fid: int) -> bool:
    res = await ctx.store.execute(update(D.feedback).where(D.feedback.c.id == fid).values(status="closed"))
    return bool(res.rowcount)


async def listing(ctx, status: str | None = None, limit: int = 100, offset: int = 0) -> dict:
    q = select(D.feedback).order_by(D.feedback.c.id.desc()).limit(limit).offset(offset)
    cq = select(func.count()).select_from(D.feedback)
    if status:
        q = q.where(D.feedback.c.status == status)
        cq = cq.where(D.feedback.c.status == status)
    rows = await ctx.store.query(q)
    async with ctx.store.engine.connect() as conn:
        total = (await conn.execute(cq)).scalar() or 0
        new = (await conn.execute(select(func.count()).select_from(D.feedback)
                                  .where(D.feedback.c.status == "new"))).scalar() or 0
    items = []
    for r in rows:
        u = ctx.store.get_user(r["user_id"])
        items.append({
            "id": r["id"], "user_id": r["user_id"], "text": r["text"], "status": r["status"],
            "reply": r["reply"], "created_at": _iso(r["created_at"]), "replied_at": _iso(r["replied_at"]),
            "user": {"tg": u.tg_title, "first_name": u.first_name, "class": u.class_name} if u else None,
        })
    return {"items": items, "total": total, "new": new}
