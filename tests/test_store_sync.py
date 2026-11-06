"""Хранилище (сброс в БД и восстановление), опрос демо-NetSchool с уведомлениями, сводки, ночная пауза, предохранитель."""
from datetime import datetime, timedelta

import httpx

from app.demo import DemoNetSchool
from app.timeutil import TZ, set_fake_now
from support import ADMIN_ID, running_app


async def test_flush_and_reload(tmp_path):
    set_fake_now(datetime(2026, 12, 8, 10, 0, tzinfo=TZ))
    async with running_app(tmp_path) as (app, ctx, c, bot):
        u = ctx.store.get_user(1)
        u.settings["morning_time"] = "07:05"
        u.display_name = "Лекс"
        ctx.store.save_user(u)
        n_tpl = len(ctx.store.templates)
        assert await ctx.store.flush() > 0
    async with running_app(tmp_path) as (app, ctx, c, bot):                 # «перезапуск» на той же базе
        u = ctx.store.get_user(1)
        assert u.opt("morning_time") == "07:05" and u.display_name == "Лекс"
        assert ctx.store.snap(1, "diary") is not None and ctx.store.snap(1, "report") is not None
        assert len(ctx.store.templates) == n_tpl                              # стартовые фразы не задвоились


async def test_timestamps_roundtrip_in_utc(tmp_path):
    """SQLite не хранит пояс: время обращений/заготовок не должно «уезжать» на +5 ч."""
    from app import feedback as FB
    moment = datetime(2026, 12, 8, 10, 5, tzinfo=TZ)
    set_fake_now(moment)
    async with running_app(tmp_path) as (app, ctx, c, bot):
        await FB.submit(ctx, ctx.store.get_user(1), "Проверка времени", source="app")
        item = (await FB.listing(ctx))["items"][0]
        assert datetime.fromisoformat(item["created_at"]) == moment
        assert item["created_at"].endswith("+05:00")
        await ctx.store.add_template("time_check", "Текст")
        await ctx.store.flush()
    async with running_app(tmp_path) as (app, ctx, c, bot):
        tpl = next(x for x in ctx.store.templates.values() if x.key == "time_check")
        assert tpl.created_at == moment
        assert ctx.store.get_user(1).created_at is not None


async def test_sync_new_marks_notify(tmp_path):
    set_fake_now(datetime(2026, 12, 8, 8, 0, tzinfo=TZ))
    async with running_app(tmp_path) as (app, ctx, c, bot):
        u = ctx.store.get_user(1)
        u.settings["quiet"] = False
        bot.clear()
        set_fake_now(datetime(2026, 12, 10, 16, 0, tzinfo=TZ))              # прошло два учебных дня
        out = await ctx.sync.run(u, reason="test")
        assert out.ok
        texts = " ".join(m.text or "" for m in bot.of("SendMessage") if m.chat_id == 1)
        assert texts, "после новых оценок/ДЗ должно прийти уведомление"
        assert u.last_ok_at is not None and u.fail_count == 0


async def test_morning_digest_once(tmp_path):
    set_fake_now(datetime(2026, 12, 8, 6, 0, tzinfo=TZ))
    async with running_app(tmp_path) as (app, ctx, c, bot):
        bot.clear()
        t = datetime(2026, 12, 8, 7, 31, tzinfo=TZ)
        set_fake_now(t)
        await ctx.scheduler._digests(t)
        sent = [m for m in bot.of("SendMessage") if m.chat_id == 1]
        assert len(sent) == 1
        await ctx.scheduler._digests(t + timedelta(minutes=2))
        assert len([m for m in bot.of("SendMessage") if m.chat_id == 1]) == 1     # второй раз не шлём
        t2 = datetime(2026, 12, 8, 19, 1, tzinfo=TZ)
        set_fake_now(t2)
        await ctx.scheduler._digests(t2)
        assert len([m for m in bot.of("SendMessage") if m.chat_id == 1]) == 2     # вечерняя


async def test_night_pause_planning(tmp_path):
    async with running_app(tmp_path) as (app, ctx, c, bot):
        late = datetime(2026, 12, 8, 22, 50, tzinfo=TZ)
        nxt = ctx.scheduler.plan_next(1, late)
        assert nxt.date() == late.date() + timedelta(days=1) and 6 <= nxt.hour <= 7
        day = datetime(2026, 12, 8, 12, 0, tzinfo=TZ)
        nxt = ctx.scheduler.plan_next(1, day)
        assert timedelta(minutes=20) <= nxt - day <= timedelta(minutes=40)


class DownNetSchool(DemoNetSchool):
    async def login(self, *a, **kw):
        raise httpx.ConnectError("connection refused")


async def test_breaker_pauses_and_alerts_admin(tmp_path):
    set_fake_now(datetime(2026, 12, 8, 12, 0, tzinfo=TZ))
    async with running_app(tmp_path, client_factory=DownNetSchool) as (app, ctx, c, bot):
        u = ctx.store.get_user(1)
        streak = int(ctx.store.rt("breaker_streak"))
        for _ in range(streak):
            await ctx.sync.run(u, reason="test")
        assert ctx.breaker.state == "open"
        ctx.scheduler._alerts()
        import asyncio
        for _ in range(50):
            if any(m.chat_id == ADMIN_ID for m in bot.of("SendMessage")):
                break
            await asyncio.sleep(0.02)
        alert = [m for m in bot.of("SendMessage") if m.chat_id == ADMIN_ID]
        assert alert and alert[0].text.startswith("⛔")
        # пока пауза — планировщик никого не опрашивает
        from app.timeutil import now
        ctx.scheduler.next_at[1] = now() - timedelta(minutes=1)
        ctx.scheduler.request(1)
        ctx.scheduler._dispatch(now())
        assert 1 not in ctx.sync.running and not ctx.scheduler.tasks
        before = u.fail_count
        await asyncio.sleep(0.05)
        assert u.fail_count == before
        # и новый ученик не может «пробить» паузу входом: бот даже не идёт на сайт
        from test_bot import cb, feed, msg
        await feed(ctx, msg(77, "/start"))
        await feed(ctx, cb(77, "consent:yes"))
        await feed(ctx, msg(77, "newbie"))
        bot.clear()
        await feed(ctx, msg(77, "pass1234"))
        assert bot.of("DeleteMessage") and "на паузе" in bot.of("SendMessage")[-1].text
        assert not ctx.store.get_user(77).connected


async def test_breaker_half_open_probe_released_on_bad_password(tmp_path):
    from app.breaker import Breaker
    b = Breaker(streak=2, cooldown_min=5)
    b._notify = lambda *_: None
    b.failure("x")
    b.failure("x")
    b.opened_at -= timedelta(minutes=6)
    assert b.allow() and b.state == "half" and not b.allow()
    b.login_failure(5)                       # ученик ошибся паролем — проба ничего не доказала
    assert b.allow()                         # можно пробовать снова, без вечного зависания
    b.probe_at -= timedelta(minutes=11)      # проба «пропала» (упала с багом)
    assert b.allow()
