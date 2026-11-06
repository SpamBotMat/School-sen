"""Админка: вход по одноразовому коду, CSRF, действия. Пароли и оценки наружу не отдаются."""
import pytest

from support import ADMIN_ID, running_app


async def login(ctx, c):
    code = ctx.admin_auth.issue(ADMIN_ID)
    r = await c.post("/admin/api/login/verify", json={"admin_id": ADMIN_ID, "code": code})
    assert r.status_code == 200, r.text
    assert "ss_admin" in r.headers.get("set-cookie", "") and "HttpOnly" in r.headers["set-cookie"]
    return {"x-csrf-token": r.json()["csrf"]}


async def test_login_flow_and_csrf(tmp_path):
    async with running_app(tmp_path) as (app, ctx, c, bot):
        assert (await c.get("/admin/api/overview")).status_code == 401
        # одинаковый ответ для админа и не-админа
        r1 = await c.post("/admin/api/login/request", json={"admin_id": ADMIN_ID})
        r2 = await c.post("/admin/api/login/request", json={"admin_id": 31337})
        assert r1.json() == r2.json() == {"ok": True}
        assert (await c.post("/admin/api/login/verify", json={"admin_id": 31337, "code": "123456"})).status_code == 401
        h = await login(ctx, c)
        assert (await c.get("/admin/api/overview")).status_code == 200
        # без CSRF-заголовка менять ничего нельзя
        assert (await c.post("/admin/api/sync", json={"action": "pause"})).status_code == 403
        assert (await c.post("/admin/api/sync", json={"action": "pause"}, headers=h)).status_code == 200
        assert ctx.store.rt("sync_paused") is True
        r = await c.post("/admin/api/sync", json={"action": "resume"}, headers=h)
        assert r.json()["paused"] is False
        assert (await c.post("/admin/api/logout", headers=h)).status_code == 200
        assert (await c.get("/admin/api/overview")).status_code == 401


async def test_code_sent_by_bot(tmp_path):
    async with running_app(tmp_path) as (app, ctx, c, bot):
        await c.post("/admin/api/login/request", json={"admin_id": ADMIN_ID})
        import asyncio
        for _ in range(50):
            if bot.of("SendMessage"):
                break
            await asyncio.sleep(0.02)
        sent = [m for m in bot.of("SendMessage") if m.chat_id == ADMIN_ID]
        assert sent and any(ch.isdigit() for ch in sent[0].text)


async def test_users_no_secrets(tmp_path):
    async with running_app(tmp_path) as (app, ctx, c, bot):
        await login(ctx, c)
        r = await c.get("/admin/api/users")
        assert r.json()["total"] >= 1
        d = (await c.get("/admin/api/users/1")).json()
        blob = r.text + str(d)
        u = ctx.store.get_user(1)
        assert u.ns_cred and u.ns_cred not in blob                       # шифротекст пароля не уходит
        for k in ("marks", "subjects", "report", "diary_items"):
            assert k not in d
        assert "snapshots" in d and "events" in d


async def test_user_actions(tmp_path):
    async with running_app(tmp_path) as (app, ctx, c, bot):
        h = await login(ctx, c)
        act = lambda a, **kw: c.post("/admin/api/users/1/action", json={"action": a, **kw}, headers=h)  # noqa: E731
        assert (await act("pause")).json()["user"]["status"] == "paused"
        assert (await act("resume")).json()["user"]["status"] == "active"
        assert (await act("block")).json()["user"]["status"] == "banned"
        assert (await act("unblock")).json()["user"]["status"] == "active"
        bot.clear()
        assert (await act("message", text="Привет <b>")).status_code == 200
        assert "&lt;b&gt;" in bot.of("SendMessage")[0].text                 # текст экранируется
        assert (await act("message", text="  ")).status_code == 400
        assert (await act("wat")).status_code == 400
        assert (await act("delete")).json()["deleted"] is True
        assert ctx.store.get_user(1) is None


async def test_runtime_and_templates(tmp_path):
    async with running_app(tmp_path) as (app, ctx, c, bot):
        h = await login(ctx, c)
        r = await c.post("/admin/api/runtime", json={"sync_interval": 45, "calc_sure": 0.7}, headers=h)
        assert r.status_code == 200 and r.json()["values"]["sync_interval"] == 45
        assert (await c.post("/admin/api/runtime", json={"sync_interval": 2}, headers=h)).status_code == 400
        assert (await c.post("/admin/api/runtime", json={"calc_maybe": 0.9}, headers=h)).status_code == 400
        tpl = (await c.get("/admin/api/templates")).json()
        assert tpl["catalog"] and tpl["items"]
        r = await c.post("/admin/api/templates", json={"key": "mark.5", "text": "Огонь, {subject}! 🔥"}, headers=h)
        assert r.status_code == 200
        tid = r.json()["id"]
        assert (await c.post("/admin/api/templates", json={"key": "nope", "text": "x"}, headers=h)).status_code == 400
        assert (await c.post("/admin/api/templates", json={"key": "mark.5", "text": "{x.__class__}"}, headers=h)).status_code == 400
        r = await c.put(f"/admin/api/templates/{tid}", json={"key": "mark.5", "text": "Ура, {subject}", "enabled": False}, headers=h)
        assert r.json()["enabled"] is False
        pv = (await c.post("/admin/api/templates/preview", json={"text": "Пятёрка по {subject}"}, headers=h)).json()
        assert pv["ok"] and "Алгебра" in pv["text"]
        assert (await c.delete(f"/admin/api/templates/{tid}", headers=h)).status_code == 200


async def test_invites_broadcast_feedback_events(tmp_path):
    async with running_app(tmp_path) as (app, ctx, c, bot):
        h = await login(ctx, c)
        inv = (await c.post("/admin/api/invites", json={"note": "пилот", "max_uses": 5, "days": 7}, headers=h)).json()
        assert inv["link"].startswith("https://t.me/test_sensor_bot?start=inv_")
        assert (await c.post(f"/admin/api/invites/{inv['code']}/toggle", headers=h)).json()["active"] is False
        assert (await c.delete(f"/admin/api/invites/{inv['code']}", headers=h)).status_code == 200
        dry = (await c.post("/admin/api/broadcast", json={"text": "Тест", "target": "active", "dry_run": True}, headers=h)).json()
        assert dry["count"] == 1
        bot.clear()
        r = await c.post("/admin/api/broadcast", json={"text": "Завтра техработы", "title": "Важно", "target": "all"}, headers=h)
        assert r.status_code == 200
        import asyncio
        for _ in range(50):
            if bot.of("SendMessage"):
                break
            await asyncio.sleep(0.02)
        assert "Важно" in bot.of("SendMessage")[0].text
        # обращение → ответ админа уходит ученику
        from app import feedback as FB
        fid = await FB.submit(ctx, ctx.store.get_user(1), "Вопрос по расписанию", source="bot")
        bot.clear()
        assert (await c.post(f"/admin/api/feedback/{fid}/reply", json={"text": "Уже чиним"}, headers=h)).status_code == 200
        assert any("Уже чиним" in m.text for m in bot.of("SendMessage") if m.chat_id == 1)
        lst = (await c.get("/admin/api/feedback?status=answered")).json()
        assert lst["total"] == 1
        ev = (await c.get("/admin/api/events?level=info")).json()
        assert ev["items"] and "kinds" in ev
        assert (await c.get("/admin/api/classes")).json()["classes"] == ["11В"]
