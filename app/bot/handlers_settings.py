"""Настройки в чате: сводки и их время, уведомления, тихие часы, имя, пауза, отключение."""
from __future__ import annotations

import re
from datetime import timedelta

from aiogram import F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from .. import render
from ..schemas import USER_DEFAULTS, ValidationError, clean_user_settings
from ..tasks import background
from ..timeutil import now
from . import texts
from .keyboards import cancel_kb, confirm_kb, is_menu, settings_kb, time_kb
from .states import SettingsFlow

router = Router(name="settings")

LABELS = {
    "morning": "Утренняя сводка", "evening": "Вечерняя сводка", "n_marks": "Оценки",
    "hw_instant": "ДЗ сразу", "n_hw": "Новое ДЗ", "n_attendance": "Пропуски", "n_debts": "Долги",
    "n_news": "Объявления", "n_totals": "Итоговые", "quiet": "Тихие часы", "reactions": "Реакции",
    "cheatsheet": "Шпаргалка", "weekly_archive": "Архив по воскресеньям", "weather": "Погода утром",
}
_NAME_RE = re.compile(r"^[\w\- .ʼ'ёЁ]{1,24}$", re.UNICODE)


def _paused(u) -> bool:
    return bool(u.paused_until and u.paused_until > now())


def settings_text(ctx, u) -> str:
    o = u.opt
    on = lambda k: "вкл" if o(k) else "выкл"  # noqa: E731
    lines = ["⚙️ <b>Настройки</b>", "",
             f"☀️ Утро — {o('morning_time')} ({on('morning')}): погода, уроки, что сдавать",
             f"🌙 Вечер — {o('evening_time')} ({on('evening')}): ДЗ на завтра",
             f"😴 Тихие часы {o('quiet_from')}–{o('quiet_to')} ({on('quiet')}): уведомления придут утром одним "
             "сообщением",
             f"👤 Обращаюсь: <b>{render.esc(u.name)}</b>"]
    if _paused(u):
        lines.append(f"⏸ На паузе до {u.paused_until.strftime('%H:%M')} — не захожу в NetSchool")
    if u.status == "bad_password":
        lines.append("🔑 Пароль не подходит — нажми «Сменить аккаунт»")
    lines += ["", "Нажимай, чтобы включить/выключить 👇"]
    return "\n".join(lines)


def time_text(u, which: str) -> str:
    if which == "morning":
        head = "☀️ <b>Утренняя сводка</b>\nПогода, советы, уроки и что сдавать сегодня."
    else:
        head = "🌙 <b>Вечерняя сводка</b>\nДЗ на завтра и что ещё не сдано."
    state = "включена ✅" if u.opt(which) else "выключена 🔕"
    return f"{head}\n\nСейчас: <b>{u.opt(which + '_time')}</b>, {state}\nВыбери время:"


async def _edit(call: CallbackQuery, text: str, markup) -> None:
    try:
        await call.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest as e:
        if "not modified" not in str(e).lower():
            await call.message.answer(text, reply_markup=markup)


async def _no_user(call: CallbackQuery, user) -> bool:
    if user is None:
        await call.answer("Сначала нажми /start", show_alert=True)
        return True
    return False


@router.message(Command("settings"))
async def cmd_settings(message: Message, state: FSMContext, ctx, user):
    await state.clear()
    if user is None:
        await message.answer("Сначала нажми /start 👋")
        return
    await message.answer(settings_text(ctx, user), reply_markup=settings_kb(ctx, user))


@router.callback_query(F.data == "set:open")
async def cb_open(call: CallbackQuery, state: FSMContext, ctx, user):
    if await _no_user(call, user):
        return
    await state.clear()
    await call.answer()
    await call.message.answer(settings_text(ctx, user), reply_markup=settings_kb(ctx, user))


@router.callback_query(F.data == "set:main")
async def cb_main(call: CallbackQuery, state: FSMContext, ctx, user):
    if await _no_user(call, user):
        return
    await state.clear()
    await call.answer()
    await _edit(call, settings_text(ctx, user), settings_kb(ctx, user))


@router.callback_query(F.data.startswith("set:t:"))
async def cb_toggle(call: CallbackQuery, ctx, user):
    if await _no_user(call, user):
        return
    key = call.data[6:]
    if not isinstance(USER_DEFAULTS.get(key), bool):
        await call.answer()
        return
    val = not user.opt(key)
    user.settings[key] = val
    ctx.store.save_user(user)
    if key == "cheatsheet":
        if val and user.connected:
            background(ctx.notifier.update_cheatsheet(user, render.cheatsheet(ctx, user), force=True))
        elif not val:
            background(ctx.notifier.unpin_cheatsheet(user))
    await call.answer(f"{LABELS.get(key, key)}: {'вкл ✅' if val else 'выкл'}")
    if key in ("morning", "evening"):
        await _edit(call, time_text(user, key), time_kb(key, user.opt(key + "_time"), val))
    else:
        await _edit(call, settings_text(ctx, user), settings_kb(ctx, user))


@router.callback_query(F.data.startswith("set:time:"))
async def cb_time(call: CallbackQuery, ctx, user):
    if await _no_user(call, user):
        return
    which = call.data[9:]
    if which not in ("morning", "evening"):
        await call.answer()
        return
    await call.answer()
    await _edit(call, time_text(user, which), time_kb(which, user.opt(which + "_time"), user.opt(which)))


def _apply_time(user, which: str, value: str) -> str:
    patch = clean_user_settings({f"{which}_time": value})        # ValidationError, если не так
    user.settings.update(patch)
    user.settings[which] = True
    return patch[f"{which}_time"]


@router.callback_query(F.data.startswith("set:at:"))
async def cb_at(call: CallbackQuery, ctx, user):
    if await _no_user(call, user):
        return
    try:
        _, _, which, value = call.data.split(":", 3)
        t = _apply_time(user, which, value)
    except (ValueError, ValidationError):
        await call.answer("Не то время 🤔", show_alert=True)
        return
    ctx.store.save_user(user)
    await call.answer(f"Готово: {t} ✅")
    await _edit(call, time_text(user, which), time_kb(which, t, True))


@router.callback_query(F.data.startswith("set:custom:"))
async def cb_custom(call: CallbackQuery, state: FSMContext, ctx, user):
    if await _no_user(call, user):
        return
    which = call.data[11:]
    if which not in ("morning", "evening"):
        await call.answer()
        return
    await state.set_state(SettingsFlow.time)
    await state.update_data(which=which)
    await call.answer()
    hint = "с 05:00 до 11:00" if which == "morning" else "с 14:00 до 23:30"
    await call.message.answer(f"Напиши время в формате ЧЧ:ММ ({hint}), например "
                              f"<code>{'07:10' if which == 'morning' else '20:15'}</code>",
                              reply_markup=cancel_kb("set:main"))


@router.message(SettingsFlow.time, F.text)
async def got_time(message: Message, state: FSMContext, ctx, user):
    if is_menu(message.text):
        await state.clear()
        raise SkipHandler()
    data = await state.get_data()
    which = data.get("which", "morning")
    raw = (message.text or "").strip().replace(".", ":").replace("-", ":").replace(" ", "")
    if raw.isdigit() and len(raw) in (3, 4):
        raw = f"{raw[:-2]}:{raw[-2:]}"
    try:
        t = _apply_time(user, which, raw)
    except ValidationError as e:
        await message.answer(f"Не получилось: {render.esc(str(e))} 🙈 Попробуй ещё раз.")
        return
    await state.clear()
    ctx.store.save_user(user)
    what = "Утренняя" if which == "morning" else "Вечерняя"
    await message.answer(f"{what} сводка теперь в <b>{t}</b> ✅")
    await message.answer(settings_text(ctx, user), reply_markup=settings_kb(ctx, user))


@router.callback_query(F.data == "set:name")
async def cb_name(call: CallbackQuery, state: FSMContext, ctx, user):
    if await _no_user(call, user):
        return
    await state.set_state(SettingsFlow.name)
    await call.answer()
    await call.message.answer("Как к тебе обращаться? Напиши имя (до 24 символов).\n"
                              "Чтобы вернуть имя из дневника — отправь <code>-</code>",
                              reply_markup=cancel_kb("set:main"))


@router.message(SettingsFlow.name, F.text)
async def got_name(message: Message, state: FSMContext, ctx, user):
    if is_menu(message.text):
        await state.clear()
        raise SkipHandler()
    raw = " ".join((message.text or "").split())
    if raw in ("-", "—", "сброс"):
        user.display_name = None
    elif _NAME_RE.match(raw) and not raw.startswith("/"):
        user.display_name = raw
    else:
        await message.answer("Только буквы, пробел и дефис, до 24 символов 🙂 Попробуй ещё раз.")
        return
    await state.clear()
    ctx.store.save_user(user)
    await message.answer(f"Окей, {render.esc(user.name)}! 🤝")


@router.callback_query(F.data == "set:pause")
async def cb_pause(call: CallbackQuery, ctx, user):
    if await _no_user(call, user):
        return
    if _paused(user):
        user.paused_until = None
        ctx.scheduler.next_at[user.id] = now() + timedelta(seconds=30)
        note = "Пауза снята ▶️"
    else:
        user.paused_until = now() + timedelta(hours=2)
        ctx.scheduler.forget(user.id)
        note = f"Пауза до {user.paused_until.strftime('%H:%M')} ⏸"
    ctx.store.save_user(user)
    await call.answer(note)
    await _edit(call, settings_text(ctx, user), settings_kb(ctx, user))


@router.callback_query(F.data == "set:relogin")
async def cb_relogin(call: CallbackQuery, state: FSMContext, ctx, user):
    if await _no_user(call, user):
        return
    from .handlers_start import _begin_login
    await call.answer()
    await _begin_login(call.message, state, ctx, user)


@router.callback_query(F.data == "set:logout")
async def cb_logout_ask(call: CallbackQuery, ctx, user):
    if await _no_user(call, user):
        return
    await call.answer()
    if not user.connected:
        await call.message.answer(texts.NOT_CONNECTED)
        return
    await _edit(call, "Отключить NetSchool? Сотру вход и данные дневника, настройки останутся.",
                confirm_kb("set:logout"))


@router.callback_query(F.data == "set:delete")
async def cb_delete_ask(call: CallbackQuery, ctx, user):
    if await _no_user(call, user):
        return
    await call.answer()
    await _edit(call, "Удалить <b>всё</b>: вход, дневник, настройки и обращения? Отменить будет нельзя.",
                confirm_kb("set:delete"))
