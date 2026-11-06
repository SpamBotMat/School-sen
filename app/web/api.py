"""API для Mini App. Вход — подпись Telegram (initData) в заголовке X-Telegram-Init-Data."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from netschool.errors import NetSchoolError

from .. import archive
from .. import feedback as FB
from ..crypto import CredentialsError
from ..schemas import ValidationError, clean_theme, clean_user_settings
from ..security import validate_init_data
from ..tasks import background
from ..timeutil import now
from . import views as V

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api")


@dataclass
class Caller:
    ctx: object
    tg: dict
    user: object | None


def _ctx(request: Request):
    ctx = getattr(request.app.state, "ctx", None)
    if ctx is None:
        raise HTTPException(503, "Сервис запускается, попробуй через пару секунд")
    return ctx


async def caller(request: Request) -> Caller:
    ctx = _ctx(request)
    raw = request.headers.get("X-Telegram-Init-Data") or ""
    tg = validate_init_data(raw, ctx.cfg.bot_token) if raw else None
    if tg is None and ctx.cfg.dev_mode:
        dev = request.query_params.get("dev_user") or request.headers.get("X-Dev-User") or ""
        if dev.isdigit():
            tg = {"id": int(dev), "first_name": "Dev"}
    if tg is None:
        raise HTTPException(401, "Открой приложение из Telegram 🙂")
    uid = tg["id"]
    if not ctx.limits.allow(f"api:{uid}", 90, 60):
        raise HTTPException(429, "Слишком часто — подожди минутку")
    user = ctx.store.get_user(uid)
    is_admin = ctx.is_admin(uid)
    if user is not None and user.status == "banned" and not is_admin:
        raise HTTPException(403, "Доступ закрыт администратором")
    if ctx.store.rt("maintenance") and not is_admin:
        raise HTTPException(503, ctx.store.rt("maintenance_text") or "Техработы")
    if user is not None:
        t = now()
        if not user.last_seen_at or (t - user.last_seen_at).total_seconds() > 1800:
            user.last_seen_at = t
            ctx.store.save_user(user)
        ctx.store.stat("app_requests")
    return Caller(ctx=ctx, tg=tg, user=user)


def need_user(c: Caller = Depends(caller)) -> Caller:
    if c.user is None:
        raise HTTPException(404, "Сначала открой бота и нажми «Старт»")
    if not c.user.connected:
        raise HTTPException(409, "Сначала подключи NetSchool в чате с ботом: /login")
    return c


async def body(request: Request) -> dict:
    try:
        data = await request.json()
    except ValueError:
        raise HTTPException(400, "Нужен JSON")
    if not isinstance(data, dict):
        raise HTTPException(400, "Нужен объект JSON")
    return data


# ───────────────────────── чтение ─────────────────────────

@router.get("/me")
async def get_me(c: Caller = Depends(caller)):
    return V.me(c.ctx, c.user, c.tg)


@router.get("/dashboard")
async def get_dashboard(c: Caller = Depends(need_user)):
    return V.dashboard(c.ctx, c.user)


@router.get("/diary")
async def get_diary(week: str | None = None, c: Caller = Depends(need_user)):
    return V.diary(c.ctx, c.user, V.parse_week(week))


@router.get("/marks")
async def get_marks(c: Caller = Depends(need_user)):
    return V.marks(c.ctx, c.user)


@router.get("/schedule")
async def get_schedule(week: str | None = None, c: Caller = Depends(need_user)):
    return V.schedule(c.ctx, c.user, V.parse_week(week))


@router.get("/calendar")
async def get_calendar(c: Caller = Depends(caller)):
    return V.calendar(c.ctx)


@router.get("/news")
async def get_news(c: Caller = Depends(caller)):
    return V.news(c.ctx)


@router.get("/birthdays")
async def get_birthdays(c: Caller = Depends(need_user)):
    return V.birthdays(c.ctx, c.user)


# ───────────────────────── ДЗ ─────────────────────────

@router.post("/hw/done")
async def post_done(data: dict = Depends(body), c: Caller = Depends(need_user)):
    key = str(data.get("key") or "")[:80]
    if not key or c.ctx.sync.find_assignment(c.user, key) is None:
        raise HTTPException(404, "Задание не найдено")
    V.set_done(c.ctx.store, c.user.id, key, bool(data.get("done")))
    return {"ok": True, "key": key, "done": bool(data.get("done"))}


@router.get("/hw/details")
async def get_details(key: str, c: Caller = Depends(need_user)):
    ctx, u = c.ctx, c.user
    a = ctx.sync.find_assignment(u, key)
    if a is None:
        raise HTTPException(404, "Задание уже не в дневнике")
    cached = ctx.store.detail_get(key)
    if cached is not None:
        return {"ok": True, "details": V.details_out(cached)}
    if a.get("aid") is None:
        return {"ok": True, "details": None, "note": "У этого задания нет подробностей"}
    if u.id in ctx.sync.running:
        return JSONResponse({"ok": False, "busy": True, "detail": "Как раз обновляю дневник — секунду…"}, 202)
    if not ctx.limits.allow(f"details:{u.id}", 20, 3600):
        raise HTTPException(429, "Много запросов подробностей — попробуй позже")
    try:
        det = await asyncio.wait_for(ctx.sync.details(u, key), timeout=45)
    except (NetSchoolError, httpx.HTTPError, CredentialsError, OSError, asyncio.TimeoutError) as e:
        log.info("details %s: %s", u.id, e)
        raise HTTPException(502, "Сайт школы не ответил — попробуй позже")
    return {"ok": True, "details": V.details_out(det)}


@router.post("/hw/file")
async def post_file(data: dict = Depends(body), c: Caller = Depends(need_user)):
    from ..bot.handlers_main import send_file
    try:
        key, att_id = str(data["key"]), int(data["id"])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(400, "Нужны key и id")
    if c.ctx.bot is None:
        raise HTTPException(503, "Бот не подключён")
    res = await send_file(c.ctx, c.user, key, att_id)
    return {"ok": res == "ok", "status": res}


# ───────────────────────── действия ─────────────────────────

@router.post("/settings")
async def post_settings(data: dict = Depends(body), c: Caller = Depends(caller)):
    ctx, u = c.ctx, c.user
    if u is None:
        raise HTTPException(404, "Сначала открой бота и нажми «Старт»")
    try:
        s = clean_user_settings(data.get("settings") or {})
        th = clean_theme(data.get("theme") or {})
    except ValidationError as e:
        raise HTTPException(400, str(e))
    if "display_name" in data:
        name = " ".join(str(data.get("display_name") or "").split())[:24]
        u.display_name = name or None
    before = u.opt("cheatsheet")
    u.settings.update(s)
    u.theme.update(th)
    ctx.store.save_user(u)
    if before != u.opt("cheatsheet") and ctx.bot is not None:
        from .. import render
        if u.opt("cheatsheet") and u.connected:
            background(ctx.notifier.update_cheatsheet(u, render.cheatsheet(ctx, u), force=True))
        elif not u.opt("cheatsheet"):
            background(ctx.notifier.unpin_cheatsheet(u))
    return V.me(ctx, u, c.tg)


@router.post("/sync")
async def post_sync(c: Caller = Depends(need_user)):
    ctx, u = c.ctx, c.user
    minutes = int(ctx.store.rt("manual_sync_minutes"))
    if ctx.store.rt("sync_paused") or ctx.breaker.state != "closed":
        return {"ok": False, "status": "paused"}
    if u.id in ctx.sync.running:
        return {"ok": True, "status": "running"}
    if not ctx.limits.allow(f"sync:{u.id}", 1, minutes * 60):
        return {"ok": False, "status": "limit", "retry": ctx.limits.retry_after(f"sync:{u.id}", 1, minutes * 60)}
    ctx.scheduler.request(u.id)
    return {"ok": True, "status": "queued"}


@router.post("/feedback")
async def post_feedback(data: dict = Depends(body), c: Caller = Depends(caller)):
    if c.user is None:
        raise HTTPException(404, "Сначала открой бота и нажми «Старт»")
    text = str(data.get("text") or "").strip()
    if len(text) < 2:
        raise HTTPException(400, "Напиши хоть пару слов 🙂")
    try:
        fid = await FB.submit(c.ctx, c.user, text, source="app")
    except FB.FeedbackLimit:
        raise HTTPException(429, "На сегодня сообщений уже много — напиши завтра")
    return {"ok": True, "id": fid}


@router.post("/archive")
async def post_archive(data: dict = Depends(body), c: Caller = Depends(need_user)):
    kind = data.get("kind")
    if kind not in archive.KINDS:
        raise HTTPException(400, "Неизвестный вид архива")
    if c.ctx.bot is None:
        raise HTTPException(503, "Бот не подключён")
    if not c.ctx.limits.allow(f"img:{c.user.id}", 12, 3600):
        raise HTTPException(429, "Многовато картинок за час — попробуй позже")
    background(archive.send_kind(c.ctx, c.user, kind))
    return {"ok": True}


@router.post("/logout")
async def post_logout(data: dict = Depends(body), c: Caller = Depends(need_user)):
    from ..bot.handlers_start import disconnect
    if data.get("confirm") is not True:
        raise HTTPException(400, "Нужно подтверждение")
    await disconnect(c.ctx, c.user)
    await c.ctx.notifier.send(c.user, "Отключил NetSchool из приложения и стёр данные дневника. "
                                      "Подключиться снова — /login")
    return {"ok": True}


@router.post("/delete")
async def post_delete(data: dict = Depends(body), c: Caller = Depends(caller)):
    from ..bot.handlers_start import delete_everything
    if c.user is None:
        return {"ok": True}
    if data.get("confirm") is not True:
        raise HTTPException(400, "Нужно подтверждение")
    await c.ctx.notifier.send(c.user, "Удалил все твои данные 🧹 Вернуться — /start")
    await delete_everything(c.ctx, c.user)
    return {"ok": True}
