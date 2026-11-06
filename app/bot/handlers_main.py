"""Основные экраны: сегодня, завтра, ДЗ, оценки, долги, архив, файлы, «обновить»."""
from __future__ import annotations

import asyncio
import logging

import httpx
from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from netschool.errors import NetSchoolError

from .. import archive, render
from ..diffing import a_key
from ..tasks import background
from ..timeutil import today
from . import texts
from .keyboards import (BTN_ARCHIVE, BTN_HW, BTN_MARKS, BTN_MORE, BTN_TODAY, BTN_TOMORROW, archive_kb,
                        hw_files_kb, main_kb, more_kb, open_app_kb)

log = logging.getLogger(__name__)
router = Router(name="main")


async def _need(message: Message, user) -> bool:
    if user is None or not user.connected:
        await message.answer(texts.NOT_CONNECTED)
        return False
    if user.status == "bad_password":
        await message.answer("🔑 Пароль от NetSchool не подходит — обнови его: /login\n"
                             "Пока показываю то, что успел сохранить.")
    return True


def _files_kb(ctx, user):
    """Кнопки «📎 файл» для ДЗ ближайших дней (если у задания есть вложения)."""
    items = []
    t = today().isoformat()
    from ..diffing import all_assignments
    for a in all_assignments(ctx.store.snap(user.id, "diary")):
        if a["date"] < t:
            continue
        det = ctx.store.detail_get(a_key(a))
        for att in (det or {}).get("attachments", [])[:2]:
            if att.get("id") is not None:
                items.append((f"{a['subject']}: {att['name']}", f"f:{a_key(a)}|{att['id']}"))
    return hw_files_kb(items) if items else None


@router.message(Command("today"))
@router.message(F.text == BTN_TODAY)
async def show_today(message: Message, ctx, user):
    if await _need(message, user):
        await message.answer(render.text_today(ctx, user), reply_markup=open_app_kb(ctx, "home"))


@router.message(Command("tomorrow"))
@router.message(F.text == BTN_TOMORROW)
async def show_tomorrow(message: Message, ctx, user):
    if await _need(message, user):
        await message.answer(render.text_tomorrow(ctx, user), reply_markup=open_app_kb(ctx, "diary"))


@router.message(Command("hw"))
@router.message(F.text == BTN_HW)
async def show_hw(message: Message, ctx, user):
    if await _need(message, user):
        await message.answer(render.text_homework(ctx, user), reply_markup=_files_kb(ctx, user) or open_app_kb(ctx, "diary"))


@router.message(Command("marks"))
@router.message(F.text == BTN_MARKS)
async def show_marks(message: Message, ctx, user):
    if await _need(message, user):
        await message.answer(render.text_marks(ctx, user), reply_markup=open_app_kb(ctx, "marks", "🧮 Калькулятор и детали"))


@router.message(Command("debts"))
async def show_debts(message: Message, ctx, user):
    if await _need(message, user):
        await message.answer(render.text_debts(ctx, user))


@router.message(Command("totals"))
async def show_totals(message: Message, ctx, user):
    if await _need(message, user):
        await message.answer(render.text_totals(ctx, user))


@router.message(Command("holidays"))
async def show_holidays(message: Message, ctx, user):
    await message.answer(render.text_holidays(ctx, user))


@router.message(Command("birthdays"))
async def show_birthdays(message: Message, ctx, user):
    if await _need(message, user):
        await message.answer(render.text_birthdays(ctx, user))


@router.message(Command("news"))
async def show_news(message: Message, ctx, user):
    await message.answer(render.text_news(ctx))


@router.message(Command("calc"))
async def show_calc(message: Message, ctx, user):
    kb = open_app_kb(ctx, "calc", "🧮 Открыть калькулятор")
    await message.answer("Калькулятор «что если» живёт в приложении: добавь воображаемые оценки и посмотри, "
                         "что выйдет в четверти 👇", reply_markup=kb)


@router.message(F.text == BTN_MORE)
@router.message(Command("more"))
async def show_more(message: Message, ctx, user):
    await message.answer("Что ещё глянем? 👇", reply_markup=more_kb(ctx))


@router.callback_query(F.data.startswith("m:"))
async def cb_more(call: CallbackQuery, state: FSMContext, ctx, user):
    what = call.data[2:]
    msg = call.message
    await call.answer()
    if what == "help":
        await msg.answer(texts.HELP)
        return
    if what == "feedback":
        from .handlers_feedback import start_feedback
        await start_feedback(msg, state, ctx, user)
        return
    if what == "news":
        await msg.answer(render.text_news(ctx))
        return
    if what == "holidays":
        await msg.answer(render.text_holidays(ctx, user))
        return
    if user is None or not user.connected:
        await msg.answer(texts.NOT_CONNECTED)
        return
    if what == "debts":
        await msg.answer(render.text_debts(ctx, user))
    elif what == "totals":
        await msg.answer(render.text_totals(ctx, user))
    elif what == "birthdays":
        await msg.answer(render.text_birthdays(ctx, user))
    elif what == "sync":
        await manual_sync(msg, ctx, user)


# ───────────────────────── обновить сейчас ─────────────────────────

_manual_sem = asyncio.Semaphore(2)


async def manual_sync(message: Message, ctx, user) -> None:
    minutes = int(ctx.store.rt("manual_sync_minutes"))
    if not ctx.limits.allow(f"sync:{user.id}", 1, minutes * 60):
        left = ctx.limits.retry_after(f"sync:{user.id}", 1, minutes * 60)
        await message.answer(f"Я обновлял совсем недавно 🙂 Можно снова через {max(1, left // 60)} мин.\n"
                             "А вообще я и сам проверяю дневник каждые полчаса.")
        return
    if ctx.store.rt("sync_paused") or ctx.breaker.state != "closed":
        await message.answer("Сайт школы сейчас на паузе ⏸ (бережём его от перегрузки). Скоро продолжу сам.")
        return
    if user.id in ctx.sync.running:
        await message.answer("Уже обновляю ⏳")
        return
    wait = await message.answer("Обновляю ⏳")
    background(_run_manual(ctx, user, wait))


async def _run_manual(ctx, user, wait: Message) -> None:
    async with _manual_sem:
        out = await ctx.sync.run(user, reason="manual")
    ctx.scheduler.plan_next(user.id)
    try:
        if out.ok:
            await wait.edit_text("✅ Готово" + (", ничего нового" if not out.events else " — новое выше ☝️"))
        elif out.status == "login_failed":
            await wait.edit_text("🔑 Не пускает в NetSchool — обнови пароль: /login")
        elif out.status == "skipped":
            await wait.edit_text("Уже обновляю ⏳")
        else:
            await wait.edit_text("😕 Сайт школы не ответил. Попробую сам чуть позже.")
    except TelegramBadRequest:
        pass


@router.message(Command("sync"))
async def cmd_sync(message: Message, ctx, user):
    if await _need(message, user):
        await manual_sync(message, ctx, user)


# ───────────────────────── архив и шпаргалка ─────────────────────────

@router.message(Command("archive"))
@router.message(F.text == BTN_ARCHIVE)
async def show_archive(message: Message, ctx, user):
    if await _need(message, user):
        await message.answer("📦 <b>Архив картинками</b>\nTelegram хранит фото на телефоне — их можно открыть "
                             "даже без интернета. Что нарисовать?", reply_markup=archive_kb())


@router.callback_query(F.data.startswith("arc:"))
async def cb_archive(call: CallbackQuery, ctx, user):
    kind = call.data[4:]
    if kind == "menu":
        await call.answer()
        await call.message.answer("📦 Что нарисовать?", reply_markup=archive_kb())
        return
    if user is None or not user.connected:
        await call.answer("Сначала /login", show_alert=True)
        return
    if kind not in archive.KINDS:
        await call.answer()
        return
    if not ctx.limits.allow(f"img:{user.id}", 12, 3600):
        await call.answer("Многовато картинок за час 🙂 Попробуй чуть позже", show_alert=True)
        return
    await call.answer("Рисую… 🎨")
    try:
        await call.message.bot.send_chat_action(user.id, "upload_photo")
    except TelegramBadRequest:
        pass
    ok = await archive.send_kind(ctx, user, kind)
    if not ok:
        await call.message.answer("Не получилось нарисовать 😕 Может, данные ещё не загрузились — попробуй позже.")


@router.callback_query(F.data == "cs:refresh")
async def cb_cheatsheet(call: CallbackQuery, ctx, user):
    if user is None or not user.connected:
        await call.answer("Сначала /login", show_alert=True)
        return
    if not user.opt("cheatsheet"):
        user.settings["cheatsheet"] = True
        ctx.store.save_user(user)
    await ctx.notifier.update_cheatsheet(user, render.cheatsheet(ctx, user), force=True)
    await call.answer("Шпаргалка обновлена 📌")


# ───────────────────────── файлы из ДЗ ─────────────────────────

@router.callback_query(F.data.startswith("f:"))
async def cb_file(call: CallbackQuery, ctx, user):
    if user is None or not user.connected:
        await call.answer("Сначала /login", show_alert=True)
        return
    try:
        key, att_id = call.data[2:].rsplit("|", 1)
        att_id = int(att_id)
    except ValueError:
        await call.answer()
        return
    await send_file(ctx, user, key, att_id, notify=call)


async def send_file(ctx, user, key: str, att_id: int, notify: CallbackQuery | None = None) -> str:
    """Отправить вложение в чат. → 'ok' | 'limit' | 'error' | 'missing'."""
    if ctx.sync.find_assignment(user, key) is None:
        if notify:
            await notify.answer("Это задание уже не в дневнике", show_alert=True)
        return "missing"
    cached = ctx.store.file_get(f"att:{att_id}")
    if cached and cached.get("file_id"):
        if notify:
            await notify.answer("Отправляю 📎")
        fid = await ctx.notifier.send_document(user, cached["file_id"], cached.get("name") or "file")
        if fid:
            return "ok"
    if not ctx.limits.allow(f"files:{user.id}", int(ctx.store.rt("files_per_day")), 86400):
        if notify:
            await notify.answer("Лимит файлов на сегодня исчерпан 🙈", show_alert=True)
        return "limit"
    if notify:
        await notify.answer("Скачиваю с сайта школы… ⏳")
    try:
        f = await asyncio.wait_for(ctx.sync.download(user, key, att_id), timeout=90)
    except (NetSchoolError, httpx.HTTPError, ValueError, OSError, asyncio.TimeoutError) as e:
        log.info("Файл %s: %s", att_id, e)
        ctx.store.event("file_error", f"{type(e).__name__}: {e}"[:200], user_id=user.id, level="warn")
        await ctx.notifier.send(user, "😕 Не получилось скачать файл с сайта школы (функция пока экспериментальная). "
                                      "Открой его в NetSchool в браузере.")
        return "error"
    if f.size > 20 * 1024 * 1024:
        await ctx.notifier.send(user, "Файл слишком большой для бота (больше 20 МБ) — открой его на сайте школы.")
        return "error"
    a = ctx.sync.find_assignment(user, key) or {}
    fid = await ctx.notifier.send_document(user, f.content, f.name,
                                           caption=f"📎 {render.esc(a.get('subject', ''))}: {render.esc(f.name)}")
    if fid:
        ctx.store.file_set(f"att:{att_id}", fid, f.name, f.size)
        ctx.store.stat("files_sent")
        return "ok"
    return "error"


# ───────────────────────── всё остальное ─────────────────────────

@router.message(StateFilter(None), F.text)
async def fallback(message: Message, ctx, user):
    if user is None:
        await message.answer("Привет! Нажми /start, чтобы начать 👋")
        return
    await message.answer("Не понял 🤔 Жми кнопки внизу или /help.\nХочешь написать администратору — /feedback",
                         reply_markup=main_kb() if user.connected else None)
