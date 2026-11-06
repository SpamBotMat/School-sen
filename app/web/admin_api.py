"""API админки. Вход: одноразовый код из бота → cookie (HttpOnly, SameSite=Strict) + CSRF-заголовок.

Админ видит служебное (Telegram-имя, логин NetSchool, ФИО, класс, статус, ошибки),
но НЕ видит пароли, оценки и ДЗ — их здесь просто нет.
"""
from __future__ import annotations

import asyncio
import html
import logging
import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import desc, select, update

from .. import db as D
from .. import feedback as FB
from ..phrases import CATALOG, render as render_phrase, validate as validate_phrase
from ..schemas import RUNTIME_DEFAULTS, ValidationError, clean_runtime
from ..stats import overview
from ..tasks import background
from ..timeutil import now, today
from .views import iso

log = logging.getLogger(__name__)
router = APIRouter(prefix="/admin/api")
COOKIE = "ss_admin"

RUNTIME_HELP = {
    "sync_interval": "Минут между опросами одного ученика",
    "sync_jitter": "Разброс интервала (доля, 0–0.5)",
    "sync_concurrency": "Одновременных сессий к сайту школы (1–4)",
    "night_from": "Ночная пауза: с", "night_to": "Ночная пауза: до",
    "slow_factor": "Во сколько раз реже опрос в воскресенье и на каникулах",
    "report_every": "«Успеваемость и посещаемость» — раз в N минут",
    "details_per_sync": "Сколько «подробностей ДЗ» подтягивать за опрос",
    "sync_paused": "Стоп-кран: опрос NetSchool на паузе",
    "maintenance": "Техработы: бот отвечает заглушкой",
    "maintenance_text": "Текст заглушки техработ",
    "registration": "Регистрация: open — всем, invite — по приглашениям, closed — закрыта",
    "calc_sure": "Калькулятор: дробная часть для «точно» (0.62 → 4.62 = 5)",
    "calc_maybe": "Калькулятор: дробная часть для «можно вытянуть»",
    "weekly_archive_time": "Воскресный архив картинками — во сколько",
    "announcements": "Следить за объявлениями школы",
    "files_per_day": "Файлов из ДЗ на ученика в сутки",
    "manual_sync_minutes": "«Обновить сейчас» — не чаще раза в N минут",
    "feedback_per_day": "Обращений к админу на ученика в сутки",
    "breaker_streak": "Ошибок подряд до автопаузы",
    "breaker_cooldown": "Минут автопаузы (дальше удваивается)",
    "bells": "Звонки: {номер урока: [начало, конец]}",
    "welcome_note": "Дополнительная строка в приветствии /start",
}
SAMPLE = {"name": "Алекс", "subject": "Алгебра", "mark": "5", "tmin": 9, "tmax": 18, "temp": 7, "precip": 70,
          "wind": 45, "days": "5 дней", "title": "осенние каникулы", "date": "7 ноября", "count": 3, "age": 15,
          "period": "2 четверть"}


# ───────────────────────── доступ ─────────────────────────

def _ctx(request: Request):
    ctx = getattr(request.app.state, "ctx", None)
    if ctx is None:
        raise HTTPException(503, "Сервис запускается")
    return ctx


def _ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    return (fwd.split(",")[0].strip() if fwd else "") or (request.client.host if request.client else "")


class Admin:
    def __init__(self, ctx, session):
        self.ctx, self.session = ctx, session

    @property
    def id(self) -> int:
        return self.session.admin_id


def admin(request: Request) -> Admin:
    ctx = _ctx(request)
    s = ctx.admin_auth.session(request.cookies.get(COOKIE))
    if s is None or not ctx.is_admin(s.admin_id):
        raise HTTPException(401, "Нужен вход")
    if request.method not in ("GET", "HEAD") and request.headers.get("x-csrf-token") != s.csrf:
        raise HTTPException(403, "CSRF")
    return Admin(ctx, s)


async def body(request: Request) -> dict:
    try:
        data = await request.json()
    except ValueError:
        raise HTTPException(400, "Нужен JSON")
    if not isinstance(data, dict):
        raise HTTPException(400, "Нужен объект JSON")
    return data


@router.post("/login/request")
async def login_request(request: Request, data: dict = Depends(body)):
    ctx = _ctx(request)
    ip = _ip(request)
    if not ctx.limits.allow(f"adm-req:{ip}", 6, 600) or ctx.admin_auth.ip_blocked(ip):
        raise HTTPException(429, "Слишком много попыток — подожди")
    try:
        aid = int(str(data.get("admin_id", "")).strip())
    except ValueError:
        raise HTTPException(400, "Нужен числовой Telegram ID")
    # одинаковый ответ для всех ID — чтобы нельзя было подобрать, кто админ
    if ctx.is_admin(aid) and ctx.admin_auth.can_send(aid) == 0 and ctx.bot is not None:
        from ..bot.handlers_admin import send_admin_code
        background(send_admin_code(ctx, aid))
    return {"ok": True}


@router.post("/login/verify")
async def login_verify(request: Request, response: Response, data: dict = Depends(body)):
    ctx = _ctx(request)
    ip = _ip(request)
    if ctx.admin_auth.ip_blocked(ip):
        raise HTTPException(429, "Слишком много попыток — подожди 15 минут")
    try:
        aid = int(str(data.get("admin_id", "")).strip())
    except ValueError:
        raise HTTPException(400, "Нужен числовой Telegram ID")
    res = ctx.admin_auth.verify(aid, str(data.get("code", "")), ip) if ctx.is_admin(aid) else None
    if res is None:
        if not ctx.is_admin(aid):
            ctx.admin_auth.fail(ip)
        await asyncio.sleep(0.6)
        raise HTTPException(401, "Код не подошёл или устарел")
    token, s = res
    response.set_cookie(COOKIE, token, max_age=ctx.admin_auth.SESSION_TTL, httponly=True, samesite="strict",
                        secure=ctx.cfg.secure_cookies, path="/admin")
    ctx.store.event("admin_login", f"вход в админку ({ip})", user_id=aid)
    return {"ok": True, "csrf": s.csrf, "admin_id": aid, "now": iso(now())}


@router.get("/session")
async def session_info(a: Admin = Depends(admin)):
    return {"ok": True, "csrf": a.session.csrf, "admin_id": a.id, "now": iso(now())}


@router.post("/logout")
async def logout(request: Request, response: Response, a: Admin = Depends(admin)):
    a.ctx.admin_auth.revoke(request.cookies.get(COOKIE))
    response.delete_cookie(COOKIE, path="/admin")
    return {"ok": True}


# ───────────────────────── обзор ─────────────────────────

@router.get("/overview")
async def get_overview(a: Admin = Depends(admin)):
    ctx = a.ctx
    o = overview(ctx)
    since = (today() - timedelta(days=13)).isoformat()
    try:
        rows = await ctx.store.query(select(D.stats_daily).where(D.stats_daily.c.day >= since))
    except Exception:  # noqa: BLE001
        rows = []
    days = {r["day"]: dict(r["data"] or {}) for r in rows}
    days[today().isoformat()] = ctx.store.stats_today()
    o["daily"] = [{"day": d, **days[d]} for d in sorted(days)]
    o["warnings"] = [_ev(e) for e in list(ctx.store.recent_events)[::-1] if e["level"] in ("warn", "error")][:12]
    try:
        fb = await FB.listing(ctx, "new", limit=1)
        o["feedback_new"] = fb["new"]
    except Exception:  # noqa: BLE001
        o["feedback_new"] = None
    o["problems"] = ctx.cfg.problems()
    o["bot"] = ctx.bot_username
    o["now"] = iso(now())
    return o


# ───────────────────────── ученики ─────────────────────────

def _user_row(ctx, u) -> dict:
    return {
        "id": u.id, "tg": u.tg_title, "username": u.username, "first_name": u.first_name,
        "ns_login": u.ns_login, "ns_name": u.ns_name, "class": u.class_name, "status": u.status,
        "connected": u.connected, "created": iso(u.created_at), "seen": iso(u.last_seen_at),
        "last_sync": iso(u.last_sync_at), "last_ok": iso(u.last_ok_at), "error": u.last_error,
        "fails": u.fail_count, "paused_until": iso(u.paused_until) if u.paused_until and u.paused_until > now() else None,
        "invite": u.invite_code, "syncing": u.id in ctx.sync.running,
    }


@router.get("/users")
async def list_users(q: str = "", status: str = "", cls: str = "", sort: str = "seen", a: Admin = Depends(admin)):
    ctx = a.ctx
    users = list(ctx.store.users.values())
    if status:
        users = [u for u in users if u.status == status]
    if cls:
        users = [u for u in users if (u.class_name or "") == cls]
    if q:
        low = q.lower().lstrip("@")
        users = [u for u in users if low in " ".join(str(x or "") for x in (
            u.id, u.username, u.first_name, u.ns_login, u.ns_name, u.class_name)).lower()]
    keyf = {
        "seen": lambda u: u.last_seen_at.timestamp() if u.last_seen_at else 0,
        "created": lambda u: u.created_at.timestamp() if u.created_at else 0,
        "errors": lambda u: u.fail_count or 0,
        "name": lambda u: (u.ns_name or u.first_name or "").lower(),
        "class": lambda u: (u.class_name or "~"),
    }.get(sort)
    if keyf:
        users.sort(key=keyf, reverse=sort in ("seen", "created", "errors"))
    classes = sorted({u.class_name for u in ctx.store.users.values() if u.class_name})
    return {"items": [_user_row(ctx, u) for u in users[:1000]], "total": len(users), "classes": classes}


@router.get("/classes")
async def classes(a: Admin = Depends(admin)):
    return {"classes": sorted({u.class_name for u in a.ctx.store.users.values() if u.class_name})}


def _get_user(ctx, uid: int):
    u = ctx.store.get_user(uid)
    if u is None:
        raise HTTPException(404, "Нет такого ученика")
    return u


def _ev(e: dict) -> dict:
    ts = e.get("ts")
    return {"ts": iso(ts) if hasattr(ts, "isoformat") else ts, "user_id": e.get("user_id"), "kind": e.get("kind"),
            "level": e.get("level"), "message": e.get("message")}


@router.get("/users/{uid}")
async def user_detail(uid: int, a: Admin = Depends(admin)):
    ctx = a.ctx
    u = _get_user(ctx, uid)
    evs = [_ev(e) for e in list(ctx.store.recent_events)[::-1] if e.get("user_id") == uid][:40]
    if len(evs) < 10:
        try:
            rows = await ctx.store.query(select(D.events).where(D.events.c.user_id == uid)
                                         .order_by(desc(D.events.c.id)).limit(40))
            seen = {(x["ts"], x["kind"]) for x in evs}
            evs += [_ev(r) for r in rows if (iso(r["ts"]), r["kind"]) not in seen]
        except Exception:  # noqa: BLE001
            pass
    fb = await ctx.store.query(select(D.feedback).where(D.feedback.c.user_id == uid).order_by(desc(D.feedback.c.id))
                               .limit(20))
    nxt = ctx.scheduler.next_at.get(uid) if ctx.scheduler else None
    snaps = {k: iso(ctx.store.snap_time(uid, k)) for k in ctx.store.snap_kinds(uid)}
    return {
        **_user_row(ctx, u), "next_sync": iso(nxt), "snapshots": snaps,
        "meta": {k: u.meta.get(k) for k in ("baseline", "report_at", "sched_day", "totals_day", "bday_month")},
        "settings": u.full_settings(), "theme": u.full_theme().get("palette"),
        "consent": iso(u.consent_at), "pinned": bool(u.pinned_message_id), "pending": len(u.pending or []),
        "events": evs[:60],
        "feedback": [{"id": r["id"], "text": r["text"], "status": r["status"], "reply": r["reply"],
                      "created": iso(r["created_at"])} for r in fb],
    }


@router.post("/users/{uid}/action")
async def user_action(uid: int, data: dict = Depends(body), a: Admin = Depends(admin)):
    from ..bot.handlers_start import delete_everything
    ctx = a.ctx
    u = _get_user(ctx, uid)
    act = data.get("action")
    st = ctx.store
    if act == "block":
        if u.status != "banned":
            u.meta["status_before_ban"] = u.status
            u.status = "banned"
            ctx.scheduler.forget(uid)
    elif act == "unblock":
        if u.status == "banned":
            u.status = u.meta.pop("status_before_ban", None) or ("active" if u.connected else "new")
    elif act == "pause":
        if u.status == "active":
            u.status = "paused"
            ctx.scheduler.forget(uid)
    elif act == "resume":
        if u.status in ("paused", "bad_password") and u.connected:
            u.status = "active"
            u.fail_count = 0
            u.paused_until = None
            ctx.scheduler.next_at[uid] = now() + timedelta(seconds=20)
    elif act == "sync":
        if not u.connected:
            raise HTTPException(409, "NetSchool не подключён")
        ctx.scheduler.request(uid)
    elif act == "reset_creds":
        u.ns_cred = None
        u.status = "new"
        ctx.scheduler.forget(uid)
        await ctx.notifier.send(u, "🔑 Администратор попросил заново подключить NetSchool — /login")
    elif act == "delete":
        await delete_everything(ctx, u)
        st.event("admin_delete", "ученик удалён админом", user_id=None)
        return {"ok": True, "deleted": True}
    elif act == "message":
        text = str(data.get("text") or "").strip()[:3500]
        if not text:
            raise HTTPException(400, "Пустое сообщение")
        msg = await ctx.notifier.send(u, f"✉️ <b>Сообщение от администратора</b>\n\n{html.escape(text, quote=False)}")
        if msg is None:
            raise HTTPException(502, "Не доставлено (бот заблокирован?)")
    else:
        raise HTTPException(400, "Неизвестное действие")
    st.save_user(u, urgent=act in ("block", "unblock", "reset_creds"))
    st.event("admin_action", f"админ: {act}", user_id=uid)
    return {"ok": True, "user": _user_row(ctx, u)}


@router.post("/users/bulk")
async def users_bulk(data: dict = Depends(body), a: Admin = Depends(admin)):
    ctx = a.ctx
    act = data.get("action")
    if act != "reactivate_bad_password":
        raise HTTPException(400, "Неизвестное действие")
    n = 0
    for u in ctx.store.users.values():
        if u.status == "bad_password" and u.connected:
            u.status = "active"
            u.fail_count = 0
            u.last_error = None
            ctx.scheduler.next_at[u.id] = now() + timedelta(seconds=30 + n * 20)   # не всех разом
            ctx.store.save_user(u)
            n += 1
    ctx.store.event("admin_bulk", f"вернул в опрос «пароль не подходит»: {n}")
    return {"ok": True, "count": n}


# ───────────────────────── заготовки фраз ─────────────────────────

@router.get("/templates")
async def templates(a: Admin = Depends(admin)):
    items = sorted((t.as_dict() for t in a.ctx.store.templates.values()), key=lambda x: (x["key"], x["id"]))
    return {"catalog": [{"key": k, "title": v[0], "placeholders": v[1]} for k, v in CATALOG.items()],
            "items": items}


def _check_tpl(data: dict) -> tuple[str, str, str, bool]:
    key = str(data.get("key") or "")
    if key not in CATALOG:
        raise HTTPException(400, "Неизвестный раздел")
    text = str(data.get("text") or "").strip()
    err = validate_phrase(text)
    if err:
        raise HTTPException(400, f"Заготовка: {err}")
    return key, text, str(data.get("subject") or "").strip()[:64], bool(data.get("enabled", True))


@router.post("/templates")
async def tpl_add(data: dict = Depends(body), a: Admin = Depends(admin)):
    key, text, subject, enabled = _check_tpl(data)
    t = await a.ctx.store.add_template(key, text, subject or None, enabled)
    a.ctx.store.event("template", f"заготовка #{t.id} добавлена ({key})")
    return t.as_dict()


@router.put("/templates/{tid}")
async def tpl_update(tid: int, data: dict = Depends(body), a: Admin = Depends(admin)):
    key, text, subject, enabled = _check_tpl(data)
    t = await a.ctx.store.update_template(tid, key=key, text=text, subject=subject, enabled=enabled)
    if t is None:
        raise HTTPException(404, "Нет такой заготовки")
    return t.as_dict()


@router.delete("/templates/{tid}")
async def tpl_delete(tid: int, a: Admin = Depends(admin)):
    if not await a.ctx.store.delete_template(tid):
        raise HTTPException(404, "Нет такой заготовки")
    return {"ok": True}


@router.post("/templates/preview")
async def tpl_preview(data: dict = Depends(body), a: Admin = Depends(admin)):
    text = str(data.get("text") or "")
    err = validate_phrase(text)
    return {"ok": err is None, "error": err, "text": render_phrase(text, SAMPLE) if not err else ""}


# ───────────────────────── рассылки ─────────────────────────

def _targets(ctx, target: str, admin_id: int) -> list:
    users = [u for u in ctx.store.users.values() if u.status not in ("banned", "bot_blocked")]
    if target == "me":
        u = ctx.store.get_user(admin_id)
        return [u] if u else [admin_id]
    if target == "active":
        return [u for u in users if u.status == "active" and u.connected]
    if target == "bad_password":
        return [u for u in users if u.status == "bad_password"]
    if target == "not_connected":
        return [u for u in users if not u.connected]
    if target.startswith("class:"):
        cls = target[6:]
        return [u for u in users if u.class_name == cls]
    return users


async def _send_broadcast(ctx, bid: int, text: str, targets: list) -> None:
    sent = failed = 0
    for i, u in enumerate(targets):
        msg = await ctx.notifier.send(u, text)
        if msg is None:
            failed += 1
        else:
            sent += 1
        if i % 25 == 24:
            await ctx.store.execute(update(D.broadcasts).where(D.broadcasts.c.id == bid).values(sent=sent, failed=failed))
    await ctx.store.execute(update(D.broadcasts).where(D.broadcasts.c.id == bid).values(
        sent=sent, failed=failed, status="done", finished_at=now()))
    ctx.store.event("broadcast", f"рассылка #{bid}: доставлено {sent}, не дошло {failed}")


@router.post("/broadcast")
async def broadcast(data: dict = Depends(body), a: Admin = Depends(admin)):
    ctx = a.ctx
    raw = str(data.get("text") or "").strip()
    if not raw or len(raw) > 3500:
        raise HTTPException(400, "Текст от 1 до 3500 символов")
    target = str(data.get("target") or "all")
    text = raw if data.get("html") else html.escape(raw, quote=False)
    if data.get("title"):
        text = f"📣 <b>{html.escape(str(data['title'])[:100], quote=False)}</b>\n\n{text}"
    targets = _targets(ctx, target, a.id)
    if data.get("dry_run"):
        return {"ok": True, "count": len(targets)}
    if ctx.bot is None:
        raise HTTPException(503, "Бот не подключён")
    res = await ctx.store.execute(D.broadcasts.insert().values(
        text=text, target=target, status="sending", total=len(targets), sent=0, failed=0, created_at=now()))
    bid = int(res.inserted_primary_key[0])
    background(_send_broadcast(ctx, bid, text, targets))
    ctx.store.event("broadcast", f"рассылка #{bid} запущена: {len(targets)} получателей")
    return {"ok": True, "id": bid, "count": len(targets)}


@router.get("/broadcasts")
async def broadcasts(a: Admin = Depends(admin)):
    rows = await a.ctx.store.query(select(D.broadcasts).order_by(desc(D.broadcasts.c.id)).limit(30))
    return {"items": [{**{k: r[k] for k in ("id", "text", "target", "status", "total", "sent", "failed")},
                       "created": iso(r["created_at"]), "finished": iso(r["finished_at"])} for r in rows]}


# ───────────────────────── обращения ─────────────────────────

@router.get("/feedback")
async def feedback_list(status: str = "", a: Admin = Depends(admin)):
    return await FB.listing(a.ctx, status or None, limit=200)


@router.post("/feedback/{fid}/reply")
async def feedback_reply(fid: int, data: dict = Depends(body), a: Admin = Depends(admin)):
    text = str(data.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "Пустой ответ")
    res = await FB.reply(a.ctx, fid, text)
    if res != "ok":
        raise HTTPException(409, {"missing": "Обращение не найдено", "gone": "Ученик удалил аккаунт",
                                  "failed": "Не доставлено — бот заблокирован?"}[res])
    return {"ok": True}


@router.post("/feedback/{fid}/close")
async def feedback_close(fid: int, a: Admin = Depends(admin)):
    await FB.close(a.ctx, fid)
    return {"ok": True}


# ───────────────────────── живые настройки ─────────────────────────

@router.get("/runtime")
async def runtime(a: Admin = Depends(admin)):
    return {"values": a.ctx.store.runtime(), "defaults": RUNTIME_DEFAULTS, "help": RUNTIME_HELP}


@router.post("/runtime")
async def runtime_set(data: dict = Depends(body), a: Admin = Depends(admin)):
    ctx = a.ctx
    try:
        patch = clean_runtime(data)
    except ValidationError as e:
        raise HTTPException(400, str(e))
    cur = ctx.store.runtime()
    if "calc_sure" in patch or "calc_maybe" in patch:
        sure = patch.get("calc_sure", cur["calc_sure"])
        maybe = patch.get("calc_maybe", cur["calc_maybe"])
        if maybe > sure:
            raise HTTPException(400, "«можно вытянуть» должно быть не больше «точно»")
    values = ctx.store.set_runtime(patch)
    ctx.breaker.configure(int(values["breaker_streak"]), int(values["breaker_cooldown"]))
    ctx.store.event("runtime", "настройки: " + ", ".join(sorted(patch))[:300])
    return {"ok": True, "values": values}


@router.post("/sync")
async def sync_control(data: dict = Depends(body), a: Admin = Depends(admin)):
    ctx = a.ctx
    act = data.get("action")
    if act == "pause":
        ctx.store.set_runtime({"sync_paused": True})
        ctx.store.event("sync_paused", "опрос на паузе (админка)", level="warn")
    elif act in ("resume", "reset_breaker"):
        from ..bot.handlers_admin import resume_sync
        resume_sync(ctx)
    elif act == "flush":
        n = await ctx.store.flush()
        return {"ok": True, "rows": n}
    else:
        raise HTTPException(400, "Неизвестное действие")
    return {"ok": True, "breaker": ctx.breaker.as_dict(), "paused": ctx.store.rt("sync_paused")}


# ───────────────────────── приглашения ─────────────────────────

def _inv(ctx, inv: dict) -> dict:
    link = f"https://t.me/{ctx.bot_username}?start=inv_{inv['code']}" if ctx.bot_username else None
    return {"code": inv["code"], "note": inv.get("note") or "", "max_uses": inv.get("max_uses") or 1,
            "uses": inv.get("uses") or 0, "active": bool(inv.get("active")), "created": iso(inv.get("created_at")),
            "expires": iso(inv.get("expires_at")), "link": link, "ok": ctx.store.invite_ok(inv["code"])}


@router.get("/invites")
async def invites(a: Admin = Depends(admin)):
    items = sorted(a.ctx.store.invites.values(), key=lambda x: str(x.get("created_at") or ""), reverse=True)
    return {"items": [_inv(a.ctx, i) for i in items], "registration": a.ctx.store.rt("registration")}


@router.post("/invites")
async def invite_add(data: dict = Depends(body), a: Admin = Depends(admin)):
    ctx = a.ctx
    code = "".join(ch for ch in str(data.get("code") or "") if ch.isalnum())[:24] or secrets.token_urlsafe(6) \
        .replace("-", "").replace("_", "")[:8]
    try:
        max_uses = max(1, min(1000, int(data.get("max_uses") or 30)))
        days = int(data.get("days") or 0)
    except (TypeError, ValueError):
        raise HTTPException(400, "Неверные числа")
    inv = {"code": code, "note": str(data.get("note") or "")[:100], "max_uses": max_uses, "uses": 0,
           "active": True, "created_at": now(), "expires_at": now() + timedelta(days=days) if days > 0 else None}
    if code in ctx.store.invites:
        raise HTTPException(409, "Такой код уже есть")
    await ctx.store.save_invite(inv)
    return _inv(ctx, inv)


@router.post("/invites/{code}/toggle")
async def invite_toggle(code: str, a: Admin = Depends(admin)):
    inv = a.ctx.store.invites.get(code)
    if inv is None:
        raise HTTPException(404, "Нет такого кода")
    inv = {**inv, "active": not inv.get("active")}
    await a.ctx.store.save_invite(inv)
    return _inv(a.ctx, inv)


@router.delete("/invites/{code}")
async def invite_delete(code: str, a: Admin = Depends(admin)):
    await a.ctx.store.delete_invite(code)
    return {"ok": True}


# ───────────────────────── журнал ─────────────────────────

@router.get("/events")
async def events(level: str = "", kind: str = "", user_id: int | None = None, source: str = "memory",
                 limit: int = 200, a: Admin = Depends(admin)):
    ctx = a.ctx
    limit = max(1, min(limit, 1000))
    if source == "db":
        q = select(D.events).order_by(desc(D.events.c.id)).limit(limit)
        if level:
            q = q.where(D.events.c.level == level)
        if kind:
            q = q.where(D.events.c.kind == kind)
        if user_id:
            q = q.where(D.events.c.user_id == user_id)
        rows = [_ev(r) for r in await ctx.store.query(q)]
    else:
        rows = []
        for e in reversed(ctx.store.recent_events):
            if level and e["level"] != level:
                continue
            if kind and e["kind"] != kind:
                continue
            if user_id and e.get("user_id") != user_id:
                continue
            rows.append(_ev(e))
            if len(rows) >= limit:
                break
    kinds = sorted({e["kind"] for e in ctx.store.recent_events})
    return {"items": rows, "kinds": kinds}
