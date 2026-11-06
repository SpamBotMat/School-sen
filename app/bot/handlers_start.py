"""/start, согласие, подключение NetSchool (логин → пароль), отключение и удаление."""
from __future__ import annotations

import asyncio
import logging

import httpx
from aiogram import F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message, ReplyKeyboardRemove

from netschool.client import prepare_password
from netschool.errors import LoginError, NetSchoolError

from .. import grades as G
from .. import render
from ..tasks import background
from ..timeutil import now
from . import texts
from .keyboards import MENU_BUTTONS, consent_kb, ib, main_kb, open_app_kb
from .states import LoginFlow

log = logging.getLogger(__name__)
router = Router(name="start")


def _ensure_user(ctx, user, tg):
    if user is None:
        user = ctx.store.new_user(tg.id, username=tg.username, first_name=tg.first_name)
        ctx.store.event("new_user", "новый пользователь", user_id=tg.id)
    return user


@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject, state: FSMContext, ctx, user):
    await state.clear()
    user = _ensure_user(ctx, user, message.from_user)
    arg = (command.args or "").strip()
    if arg.startswith("inv_"):
        user.invite_code = arg[4:][:32]
        ctx.store.save_user(user)
    if user.connected and user.status in ("active", "paused"):
        await message.answer(f"С возвращением, {render.esc(user.name)}! 👋 Жми кнопки внизу.", reply_markup=main_kb())
        kb = open_app_kb(ctx, "home", "🚀 Открыть приложение")
        if kb:
            await message.answer("Или открой приложение — там удобнее 👇", reply_markup=kb)
        return
    await message.answer(texts.welcome(user.name, ctx.store.rt("welcome_note")), reply_markup=consent_kb())


@router.callback_query(F.data == "privacy")
async def cb_privacy(call: CallbackQuery, ctx, user):
    await call.message.answer(texts.PRIVACY)
    await call.answer()


@router.message(Command("privacy"))
async def cmd_privacy(message: Message):
    await message.answer(texts.PRIVACY)


@router.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(texts.HELP)


def _registration_gate(ctx, user) -> str | None:
    """None — можно подключаться; иначе текст отказа / 'invite' — спросить код."""
    if user.connected or user.ns_login:
        return None                           # уже был подключён — переподключение всегда можно
    mode = ctx.store.rt("registration")
    if ctx.is_admin(user.id) or mode == "open":
        return None
    if mode == "closed":
        return "Регистрация сейчас закрыта 🙈 Загляни позже."
    if user.invite_code and ctx.store.invite_ok(user.invite_code):
        return None
    return "invite"


async def _begin_login(message: Message, state: FSMContext, ctx, user) -> None:
    gate = _registration_gate(ctx, user)
    if gate == "invite":
        await state.set_state(LoginFlow.invite)
        await message.answer("Сейчас вход по приглашениям 🎟 Пришли код приглашения:")
        return
    if gate:
        await message.answer(gate)
        return
    await state.set_state(LoginFlow.login)
    await message.answer(texts.ASK_LOGIN, reply_markup=InlineKeyboardMarkup(
        inline_keyboard=[[ib("Отмена", "login:cancel")]]))


@router.callback_query(F.data == "consent:yes")
async def cb_consent(call: CallbackQuery, state: FSMContext, ctx, user):
    user = _ensure_user(ctx, user, call.from_user)
    user.consent_at = user.consent_at or now()
    ctx.store.save_user(user)
    await call.answer()
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass
    await _begin_login(call.message, state, ctx, user)


@router.message(Command("login"))
async def cmd_login(message: Message, state: FSMContext, ctx, user):
    user = _ensure_user(ctx, user, message.from_user)
    user.consent_at = user.consent_at or now()
    await _begin_login(message, state, ctx, user)


@router.callback_query(F.data == "login:cancel")
async def cb_login_cancel(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.answer("Окей, отменил")
    try:
        await call.message.edit_text("Подключение отменено. Вернуться — /login")
    except TelegramBadRequest:
        pass


@router.message(LoginFlow.invite, F.text)
async def got_invite(message: Message, state: FSMContext, ctx, user):
    if (message.text or "").startswith("/") or (message.text or "") in MENU_BUTTONS:
        await state.clear()
        raise SkipHandler()
    code = (message.text or "").strip().removeprefix("inv_")[:32]
    if not ctx.store.invite_ok(code):
        await message.answer("Такого кода нет или он закончился 🤔 Проверь и пришли ещё раз.")
        return
    user.invite_code = code
    ctx.store.save_user(user)
    await state.set_state(LoginFlow.login)
    await message.answer("Код принят ✅\n\n" + texts.ASK_LOGIN)


@router.message(LoginFlow.login, F.text)
async def got_login(message: Message, state: FSMContext, ctx, user):
    text = (message.text or "").strip()
    if text in MENU_BUTTONS:
        await state.clear()
        raise SkipHandler()
    if text.startswith("/"):
        await state.clear()
        await message.answer("Подключение отменено. Вернуться — /login")
        return
    if len(text.split()) > 1:
        try:
            await message.delete()
        except TelegramBadRequest:
            pass
        await message.answer("Похоже, тут и логин, и пароль 🙈 Сообщение на всякий случай удалил.\n"
                             "Пришли <b>только логин</b>, пароль спрошу следующим сообщением.")
        return
    if len(text) > 64:
        await message.answer("Что-то длинновато для логина 🤔 Пришли ещё раз.")
        return
    await state.update_data(login=text)
    await state.set_state(LoginFlow.password)
    await message.answer(texts.ASK_PASSWORD, reply_markup=InlineKeyboardMarkup(
        inline_keyboard=[[ib("Отмена", "login:cancel")]]))


@router.message(LoginFlow.password)
async def got_password(message: Message, state: FSMContext, ctx, user):
    if (message.text or "") in MENU_BUTTONS:
        await state.clear()
        raise SkipHandler()
    # Первым делом — удалить сообщение с паролем.
    try:
        await message.delete()
    except TelegramBadRequest:
        log.warning("Не удалось удалить сообщение с паролем (user %s)", message.from_user.id)
    raw = message.text or ""
    data = await state.get_data()
    login = data.get("login")
    await state.clear()
    if not raw or not login:
        await message.answer("Не вижу пароль 🤔 Давай заново: /login")
        return
    md5, plen = prepare_password(raw)
    del raw
    if not ctx.limits.allow(f"login:{user.id}", 5, 3600):
        await message.answer("Слишком много попыток входа подряд. Подожди около часа — сайт школы может "
                             "заблокировать учётку за частые ошибки 🙏")
        return
    if ctx.store.rt("sync_paused") or not ctx.breaker.allow():
        # сайт школы сейчас на паузе (сбои или админ остановил опрос) — не стучимся туда даже ради входа
        await message.answer("Сообщение с паролем удалил 🔒\n\nСайт школы сейчас на паузе от запросов ⏸ — бережём его, "
                             "пока он сбоит. Попробуй подключиться чуть позже: /login")
        return
    gate = ctx.sync.connect_gate
    queued = gate.locked()
    wait = await message.answer("Сообщение с паролем удалил 🔒 " + (
        "Сейчас подключаются ещё ребята — ты в очереди, это пара минут ⏳" if queued else "Проверяю вход… ⏳"))
    await gate.acquire()               # первые входы тоже не больше 2 разом — бережём сайт школы
    handed_over = False
    try:
        handed_over = await _connect(message, ctx, user, login, md5, plen, wait, gate, queued)
    finally:
        if not handed_over:
            gate.release()


async def _connect(message: Message, ctx, user, login: str, md5: str, plen: int, wait, gate, queued: bool) -> bool:
    """Вход + сохранение. True — дальше работает _finish_connect (он и освободит очередь)."""
    if queued and (ctx.store.rt("sync_paused") or ctx.breaker.state == "open"):
        await wait.edit_text("Сайт школы сейчас на паузе ⏸ — бережём его, пока он сбоит. "
                             "Попробуй подключиться чуть позже: /login")
        return False
    if queued:
        await wait.edit_text("Сообщение с паролем удалил 🔒 Проверяю вход… ⏳")
    try:
        ns = await asyncio.wait_for(ctx.sync.open_session(login, md5, plen), timeout=60)
    except LoginError as e:
        ctx.store.stat("connect_fail")
        if e.bad_credentials:
            ctx.breaker.inconclusive()
        else:
            ctx.breaker.failure("вход: " + str(e)[:200])
        if e.bad_credentials:
            reason = render.esc(e.server_message or "неверный логин или пароль")
            await wait.edit_text(f"😕 NetSchool не пускает: <i>{reason}</i>\n\nПроверь логин и пароль и попробуй "
                                 "ещё раз — /login\n<i>Подсказка: логин и пароль — как на сайте школы.</i>")
        else:
            await wait.edit_text("Сайт школы ответил что-то странное 🤔 Попробуй чуть позже — /login")
        return False
    except (NetSchoolError, httpx.HTTPError, OSError, asyncio.TimeoutError) as e:
        log.info("connect: %s", e)
        ctx.breaker.failure(f"вход: {type(e).__name__}: {e}"[:300])
        await wait.edit_text("Сайт школы сейчас не отвечает 😴 Попробуй через пару минут — /login")
        return False
    try:
        await _save_connection(ctx, user, login, md5, plen, wait)
    except BaseException:
        await ns.aclose()              # не оставляем висеть сессию на сайте школы
        raise
    background(_finish_connect(ctx, user, ns, message, gate))
    return True


async def _save_connection(ctx, user, login: str, md5: str, plen: int, wait) -> None:
    store = ctx.store
    changed = bool(user.ns_login and user.ns_login.lower() != login.lower())
    if changed or not user.connected:
        store.drop_snaps(user.id, ["diary", "report", "schedule", "totals", "birthdays", "hw_done"])
        user.meta = {}
        user.ns_name = user.class_name = None
    user.ns_login = login
    user.ns_cred = ctx.vault.seal(md5, plen)
    user.status = "active"
    user.paused_until = None
    user.fail_count = 0
    user.last_error = None
    user.consent_at = user.consent_at or now()
    if user.invite_code and user.invite_code in store.invites and not user.meta.get("invite_used"):
        inv = store.invites[user.invite_code]
        inv["uses"] = (inv.get("uses") or 0) + 1
        user.meta["invite_used"] = user.invite_code
        background(store.save_invite(inv))
    store.save_user(user, urgent=True)
    store.event("connected", "подключил NetSchool", user_id=user.id)
    store.stat("connected")
    hello = ctx.phrases.pick("welcome.connected", uid=user.id, name=user.name) or "Готово! Я на связи 🤝"
    await wait.edit_text(f"{render.esc(hello)}\nЗагружаю дневник — это до минуты ⏳")


async def _finish_connect(ctx, user, ns, message: Message, gate=None) -> None:
    try:
        await _finish_connect_inner(ctx, user, ns, message)
    finally:
        if gate is not None:
            gate.release()


async def _finish_connect_inner(ctx, user, ns, message: Message) -> None:
    out = await ctx.sync.run(user, baseline=True, client=ns, reason="connect")
    ctx.scheduler.plan_next(user.id)
    if not out.ok:
        ctx.scheduler.next_at[user.id] = now()
        await message.answer("Вход работает ✅, но дневник с первого раза не загрузился. Попробую ещё раз сам "
                             "в ближайшие минуты — напишу сводку, как будет готово.", reply_markup=main_kb())
        return
    subs = render.subjects(ctx.store, user.id)
    avg = G.overall(subs)
    with_marks = sum(1 for s in subs if s["marks"])
    lines = ["<b>Всё готово!</b> ✅"]
    who = " · ".join(x for x in (user.class_name and f"класс {user.class_name}", ) if x)
    if who:
        lines.append(who)
    if avg is not None:
        lines.append(f"Предметов с оценками: {with_marks} · средний <b>{avg:.2f}</b>")
    lines.append("\nТеперь сам буду писать, когда появятся оценки, а утром и вечером пришлю сводку.")
    lines.append("Кнопки внизу — быстрые ответы, а приложение (кнопка «Дневник» слева от поля ввода) — "
                 "всё подробно.")
    await message.answer("\n".join(lines), reply_markup=main_kb())
    kb = open_app_kb(ctx, "home", "🚀 Открыть приложение")
    if kb:
        await message.answer("Загляни 👇", reply_markup=kb)
    if user.opt("cheatsheet"):
        await ctx.notifier.update_cheatsheet(user, render.cheatsheet(ctx, user), force=True)


# ───────────────────────── отключение / удаление ─────────────────────────

async def disconnect(ctx, user) -> None:
    await ctx.notifier.unpin_cheatsheet(user)
    user.ns_login = user.ns_cred = None
    user.ns_name = user.class_name = None
    user.status = "new"
    user.meta = {}
    user.pending = []
    ctx.store.drop_snaps(user.id)
    ctx.scheduler.forget(user.id)
    ctx.store.save_user(user, urgent=True)
    ctx.store.event("disconnected", "отключил NetSchool", user_id=user.id)


async def delete_everything(ctx, user) -> None:
    await ctx.notifier.unpin_cheatsheet(user)
    ctx.scheduler.forget(user.id)
    await ctx.store.delete_user(user.id)
    ctx.store.event("deleted", "удалил аккаунт")


@router.message(Command("logout"))
async def cmd_logout(message: Message, ctx, user):
    if not user or not user.connected:
        await message.answer("NetSchool и так не подключён. Подключить — /login")
        return
    await message.answer("Отключить NetSchool? Я сотру вход и данные дневника (настройки останутся).",
                         reply_markup=InlineKeyboardMarkup(inline_keyboard=[[ib("Да, отключить", "logout:yes"),
                                                                             ib("Отмена", "noop")]]))


@router.callback_query(F.data.in_({"logout:yes", "set:logout:yes"}))
async def cb_logout(call: CallbackQuery, ctx, user):
    if user and user.connected:
        await disconnect(ctx, user)
    await call.answer("Готово")
    await call.message.edit_text("Отключил NetSchool и стёр данные дневника. Подключиться снова — /login")


@router.message(Command("delete"))
async def cmd_delete(message: Message, ctx, user):
    if not user:
        await message.answer("У меня и так нет твоих данных 🙂")
        return
    await message.answer("Удалить <b>всё</b>: вход, дневник, настройки, обращения? Это нельзя отменить.",
                         reply_markup=InlineKeyboardMarkup(inline_keyboard=[[ib("🗑 Удалить всё", "delete:yes"),
                                                                             ib("Отмена", "noop")]]))


@router.callback_query(F.data.in_({"delete:yes", "set:delete:yes"}))
async def cb_delete(call: CallbackQuery, ctx, user):
    if user:
        await delete_everything(ctx, user)
    await call.answer("Удалено")
    await call.message.edit_text("Удалил всё 🧹 Если захочешь вернуться — /start")
    await call.message.answer("Пока! 👋", reply_markup=ReplyKeyboardRemove())


@router.callback_query(F.data == "noop")
async def cb_noop(call: CallbackQuery):
    await call.answer("Окей")
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass
