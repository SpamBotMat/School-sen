"""API Mini App и служебные маршруты — через настоящее приложение (lifespan) с демо-NetSchool и ботом без сети."""
from datetime import datetime

import pytest

from app.security import sign_init_data
from app.timeutil import TZ, set_fake_now
from support import BOT_TOKEN, TICK_KEY, running_app


def H(uid=1, name="Алекс"):
    return {"X-Telegram-Init-Data": sign_init_data({"id": uid, "first_name": name}, BOT_TOKEN)}


@pytest.fixture
def school_morning():
    set_fake_now(datetime(2026, 12, 8, 10, 5, tzinfo=TZ))     # вторник, идёт 2-й урок


async def test_service_routes(tmp_path):
    async with running_app(tmp_path) as (app, ctx, c, bot):
        r = await c.get("/health")
        assert r.status_code == 200 and r.json()["ok"]
        r = await c.get("/tick")
        assert r.json() == {"ok": True}                                   # без ключа — без подробностей
        r = await c.get(f"/tick?key={TICK_KEY}")
        assert "users" in r.json()
        r = await c.get("/app/")
        assert r.status_code == 200 and "__V__" not in r.text and "app.js?v=" in r.text
        assert "frame-ancestors" in r.headers["content-security-policy"]
        r = await c.get("/app/sw.js")
        assert r.status_code == 200 and "__V__" not in r.text
        r = await c.get("/admin/")
        assert r.headers["x-frame-options"] == "DENY"
        assert (await c.get("/favicon.ico")).status_code == 200


async def test_auth_required(tmp_path):
    async with running_app(tmp_path) as (app, ctx, c, bot):
        assert (await c.get("/api/me")).status_code == 401
        assert (await c.get("/api/me?dev_user=1")).status_code == 401      # DEV_MODE выключен
        bad = {"X-Telegram-Init-Data": sign_init_data({"id": 1}, "000:wrong")}
        assert (await c.get("/api/me", headers=bad)).status_code == 401
        r = await c.get("/api/me", headers=H(uid=555))                     # ещё не нажимал /start
        assert r.status_code in (200, 404)


async def test_screens(tmp_path, school_morning):
    async with running_app(tmp_path) as (app, ctx, c, bot):
        me = (await c.get("/api/me", headers=H())).json()
        assert me["user"]["connected"] is True and "telegram" in me["palettes"]
        for path in ("dashboard", "diary", "marks", "schedule", "calendar", "news", "birthdays"):
            r = await c.get(f"/api/{path}", headers=H())
            assert r.status_code == 200, (path, r.text)
        d = (await c.get("/api/dashboard", headers=H())).json()
        assert isinstance(d, dict) and d
        m = (await c.get("/api/marks", headers=H())).json()
        assert m["subjects"] and "pred" in m["subjects"][0]
        prev = (await c.get("/api/diary?week=2026-12-01", headers=H()))
        assert prev.status_code == 200
        assert (await c.get("/api/diary?week=кривая", headers=H())).status_code in (200, 400)


async def test_settings_validation_and_theme(tmp_path):
    async with running_app(tmp_path) as (app, ctx, c, bot):
        r = await c.post("/api/settings", headers=H(), json={"settings": {"morning_time": "07:10", "n_hw": False},
                                                             "theme": {"palette": "mint", "lite": True},
                                                             "display_name": "  Алекс   Б  "})
        assert r.status_code == 200, r.text
        u = ctx.store.get_user(1)
        assert u.opt("morning_time") == "07:10" and u.opt("n_hw") is False
        assert u.display_name == "Алекс Б"
        r = await c.post("/api/settings", headers=H(), json={"settings": {"morning_time": "02:00"}})
        assert r.status_code == 400
        r = await c.post("/api/settings", headers=H(), content=b"not json", )
        assert r.status_code == 400


async def test_homework_done_and_sync_queue(tmp_path, school_morning):
    async with running_app(tmp_path) as (app, ctx, c, bot):
        diary = (await c.get("/api/diary", headers=H())).json()
        keys = [it["key"] for day in diary["days"] for it in day["items"] if it.get("hw")]
        assert keys, "в демо-дневнике должно быть ДЗ"
        r = await c.post("/api/hw/done", headers=H(), json={"key": keys[0], "done": True})
        assert r.status_code == 200 and r.json()["done"] is True
        assert (await c.post("/api/hw/done", headers=H(), json={"key": "nope", "done": True})).status_code == 404
        r = await c.post("/api/sync", headers=H())
        assert r.json()["status"] == "queued"
        r = await c.post("/api/sync", headers=H())
        assert r.json()["status"] == "limit"                               # «обновить» не чаще раза в N минут
        ctx.store.set_runtime({"sync_paused": True})
        ctx.limits.reset("sync:1")
        assert (await c.post("/api/sync", headers=H())).json()["status"] == "paused"


async def test_feedback_and_maintenance(tmp_path):
    async with running_app(tmp_path) as (app, ctx, c, bot):
        r = await c.post("/api/feedback", headers=H(), json={"text": "Не видно оценок по химии"})
        assert r.status_code == 200
        # админ получил уведомление с кнопками
        assert any("химии" in (m.text or "") for m in bot.of("SendMessage") if m.chat_id == 42)
        ctx.store.set_runtime({"maintenance": True})
        assert (await c.get("/api/me", headers=H())).status_code == 503


async def test_banned_user_forbidden(tmp_path):
    async with running_app(tmp_path) as (app, ctx, c, bot):
        u = ctx.store.get_user(1)
        u.status = "banned"
        assert (await c.get("/api/me", headers=H())).status_code == 403


async def test_logout_requires_confirmation(tmp_path):
    async with running_app(tmp_path) as (app, ctx, c, bot):
        assert (await c.post("/api/logout", headers=H(), json={})).status_code == 400
        assert (await c.post("/api/logout", headers=H(), json={"confirm": True})).status_code == 200
        u = ctx.store.get_user(1)
        assert not u.connected and u.ns_cred is None


async def test_webhook_secret(tmp_path):
    from app.bot.setup import webhook_path
    async with running_app(tmp_path) as (app, ctx, c, bot):
        path = webhook_path(ctx.cfg)
        upd = {"update_id": 1, "message": {"message_id": 5, "date": 1700000000, "chat": {"id": 7, "type": "private"},
                                           "from": {"id": 7, "is_bot": False, "first_name": "Тим"}, "text": "/help"}}
        r = await c.post(path, json=upd)
        assert r.status_code in (401, 403)
        from app.bot.setup import secret_token
        r = await c.post(path, json=upd, headers={"X-Telegram-Bot-Api-Secret-Token": secret_token(ctx.cfg)})
        assert r.status_code == 200
        import asyncio
        for _ in range(50):
            if bot.of("SendMessage"):
                break
            await asyncio.sleep(0.02)
        assert bot.of("SendMessage"), "бот должен ответить на /help"
