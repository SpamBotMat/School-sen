"""Бот: онбординг (согласие → логин → пароль удаляется → подключено), настройки, обращения, админ-команды."""
import itertools
from datetime import datetime

from aiogram.types import Update

from app.bot.keyboards import BTN_TODAY
from app.tasks import drain
from netschool.client import prepare_password
from support import ADMIN_ID, running_app

_ids = itertools.count(100)


def msg(uid, text, name="Тим"):
    return Update.model_validate({"update_id": next(_ids), "message": {
        "message_id": next(_ids), "date": int(datetime.now().timestamp()),
        "chat": {"id": uid, "type": "private"},
        "from": {"id": uid, "is_bot": False, "first_name": name, "username": f"user{uid}"}, "text": text}})


def cb(uid, data, name="Тим"):
    return Update.model_validate({"update_id": next(_ids), "callback_query": {
        "id": str(next(_ids)), "chat_instance": "ci", "data": data,
        "from": {"id": uid, "is_bot": False, "first_name": name},
        "message": {"message_id": next(_ids), "date": int(datetime.now().timestamp()),
                    "chat": {"id": uid, "type": "private"}, "text": "…"}}})


async def feed(ctx, update):
    await ctx.dp.feed_update(ctx.bot, update)


def markup_data(m):
    rm = getattr(m, "reply_markup", None)
    rows = getattr(rm, "inline_keyboard", None) or []
    return [b.callback_data for row in rows for b in row]


async def test_onboarding_deletes_password_and_connects(tmp_path):
    async with running_app(tmp_path) as (app, ctx, c, bot):
        await feed(ctx, msg(7, "/start"))
        first = bot.of("SendMessage")[-1]
        assert "consent:yes" in markup_data(first)
        await feed(ctx, cb(7, "consent:yes"))
        assert "логин" in bot.of("SendMessage")[-1].text.lower()
        await feed(ctx, msg(7, "student7"))
        assert "пароль" in bot.of("SendMessage")[-1].text.lower()
        bot.clear()
        pw = msg(7, "s3cret-Pass")
        await feed(ctx, pw)
        deleted = bot.of("DeleteMessage")
        assert deleted and deleted[0].message_id == pw.message.message_id        # пароль стёрт из чата
        u = ctx.store.get_user(7)
        assert u.connected and u.status == "active" and u.ns_login == "student7"
        assert ctx.vault.open(u.ns_cred) == prepare_password("s3cret-Pass")
        assert "s3cret" not in (u.ns_cred or "")
        await drain(10)                                                            # первая загрузка дневника
        assert ctx.store.snap(7, "diary") is not None
        assert ctx.sync.connect_gate._value == 2                                  # очередь входов освобождена


async def test_wrong_password_and_login_with_spaces(tmp_path):
    async with running_app(tmp_path) as (app, ctx, c, bot):
        await feed(ctx, msg(8, "/start"))
        await feed(ctx, cb(8, "consent:yes"))
        bot.clear()
        await feed(ctx, msg(8, "login password"))          # и логин, и пароль одним сообщением
        assert bot.of("DeleteMessage") and "только логин" in bot.of("SendMessage")[-1].text
        await feed(ctx, msg(8, "student8"))
        await feed(ctx, msg(8, "wrong"))
        assert "не пускает" in bot.of("EditMessageText")[-1].text
        assert not ctx.store.get_user(8).connected
        assert ctx.sync.connect_gate._value == 2


async def test_connect_queue_limits_parallel_logins(tmp_path):
    """Пять учеников жмут «подключить» одновременно — на сайт школы идут не больше двух входов разом."""
    import asyncio

    async with running_app(tmp_path) as (app, ctx, c, bot):
        active = peak = 0
        real_open = ctx.sync.open_session

        async def slow_open(login, md5, plen):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.05)
            try:
                return await real_open(login, md5, plen)
            finally:
                active -= 1

        ctx.sync.open_session = slow_open
        uids = list(range(20, 25))
        for uid in uids:
            await feed(ctx, msg(uid, "/start"))
            await feed(ctx, cb(uid, "consent:yes"))
            await feed(ctx, msg(uid, f"student{uid}"))
        await asyncio.gather(*(feed(ctx, msg(uid, "pass-" + str(uid))) for uid in uids))
        await drain(10)
        assert peak <= 2
        assert all(ctx.store.get_user(uid).connected for uid in uids)
        assert ctx.sync.connect_gate._value == 2
        assert any("в очереди" in (m.text or "") for m in bot.of("SendMessage"))


async def test_menu_button_escapes_login_state(tmp_path):
    async with running_app(tmp_path) as (app, ctx, c, bot):
        await feed(ctx, msg(9, "/start"))
        await feed(ctx, cb(9, "consent:yes"))
        await feed(ctx, msg(9, "student9"))
        bot.clear()
        await feed(ctx, msg(9, BTN_TODAY))                  # передумал — нажал кнопку меню
        assert not bot.of("DeleteMessage")
        assert ctx.store.get_user(9).ns_login is None


async def test_settings_and_feedback(tmp_path):
    async with running_app(tmp_path) as (app, ctx, c, bot):
        await feed(ctx, msg(1, "/settings", name="Алекс"))
        s = bot.of("SendMessage")[-1]
        assert any(d and d.startswith("set:") for d in markup_data(s))
        before = ctx.store.get_user(1).opt("n_marks")
        await feed(ctx, cb(1, "set:t:n_marks", name="Алекс"))
        assert ctx.store.get_user(1).opt("n_marks") is (not before)
        bot.clear()
        await feed(ctx, msg(1, "/feedback", name="Алекс"))
        await feed(ctx, msg(1, "Хочу тёмную тему в картинках", name="Алекс"))
        admin_msgs = [m for m in bot.of("SendMessage") if m.chat_id == ADMIN_ID]
        assert admin_msgs and "тёмную тему" in admin_msgs[-1].text
        assert any(d and d.startswith("fb:reply:") for d in markup_data(admin_msgs[-1]))


async def test_admin_commands(tmp_path):
    async with running_app(tmp_path) as (app, ctx, c, bot):
        await feed(ctx, msg(ADMIN_ID, "/start", name="Админ"))
        bot.clear()
        await feed(ctx, msg(ADMIN_ID, "/stats", name="Админ"))
        assert bot.of("SendMessage") and "учен" in bot.of("SendMessage")[-1].text.lower()
        bot.clear()
        await feed(ctx, msg(5, "/stats"))                    # не админ — ничего секретного
        assert not any("учен" in (m.text or "").lower() and "опрос" in (m.text or "").lower() for m in bot.of("SendMessage"))
        bot.clear()
        await feed(ctx, msg(ADMIN_ID, "/pause", name="Админ"))
        assert ctx.store.rt("sync_paused") is True
        await feed(ctx, msg(ADMIN_ID, "/resume", name="Админ"))
        assert ctx.store.rt("sync_paused") is False


async def test_logout_and_delete(tmp_path):
    async with running_app(tmp_path) as (app, ctx, c, bot):
        await feed(ctx, msg(1, "/logout", name="Алекс"))
        assert any(d and "yes" in d for d in markup_data(bot.of("SendMessage")[-1]))
        await feed(ctx, cb(1, "logout:yes", name="Алекс"))
        u = ctx.store.get_user(1)
        assert not u.connected and u.ns_cred is None and ctx.store.snap(1, "diary") is None
        await feed(ctx, cb(1, "delete:yes", name="Алекс"))
        assert ctx.store.get_user(1) is None
