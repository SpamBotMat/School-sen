"""Обращения к администратору и анонимные ответы на них (кнопками прямо в чате админа)."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from .. import feedback as FB
from .keyboards import cancel_kb, is_menu
from .states import AdminFlow, FeedbackFlow

router = Router(name="feedback")


async def start_feedback(message: Message, state: FSMContext, ctx, user) -> None:
    if user is None:
        await message.answer("Сначала нажми /start 👋")
        return
    await state.set_state(FeedbackFlow.text)
    await message.answer("✉️ Напиши администратору одним сообщением: вопрос, идея, что сломалось.\n"
                         "Ответ придёт сюда же. Администратор увидит твоё имя в Telegram, "
                         "но не пароль и не оценки.", reply_markup=cancel_kb("fb:cancel"))


@router.message(Command("feedback"))
async def cmd_feedback(message: Message, state: FSMContext, ctx, user):
    await start_feedback(message, state, ctx, user)


@router.callback_query(F.data == "fb:cancel")
async def cb_cancel(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.answer("Окей")
    try:
        await call.message.edit_text("Ничего не отправил 👌")
    except TelegramBadRequest:
        pass


@router.message(FeedbackFlow.text)
async def got_feedback(message: Message, state: FSMContext, ctx, user):
    if is_menu(message.text):
        await state.clear()
        raise SkipHandler()
    text = (message.text or message.caption or "").strip()
    if not text:
        await message.answer("Пока принимаю только текст 🙏 Напиши словами.")
        return
    await state.clear()
    try:
        await FB.submit(ctx, user, text, source="bot")
    except FB.FeedbackLimit:
        await message.answer("На сегодня сообщений уже много 🙏 Напиши завтра.")
        return
    await message.answer("Отправил ✅ Администратор ответит здесь, в чате.")


# ───────────────────────── ответы админа ─────────────────────────

def _fid(data: str) -> int | None:
    try:
        return int(data.rsplit(":", 1)[1])
    except (IndexError, ValueError):
        return None


@router.callback_query(F.data.startswith("fb:reply:"))
async def cb_reply(call: CallbackQuery, state: FSMContext, ctx, user):
    if not ctx.is_admin(call.from_user.id):
        await call.answer()
        return
    fid = _fid(call.data)
    await state.set_state(AdminFlow.reply)
    await state.update_data(fid=fid)
    await call.answer()
    await call.message.answer(f"Напиши ответ на обращение #{fid}. Уйдёт от имени «Администратор».",
                              reply_markup=cancel_kb("fb:cancel"))


@router.message(AdminFlow.reply)
async def got_reply(message: Message, state: FSMContext, ctx, user):
    if not ctx.is_admin(message.from_user.id):
        await state.clear()
        return
    if is_menu(message.text):
        await state.clear()
        raise SkipHandler()
    text = (message.text or "").strip()
    if not text:
        await message.answer("Нужен текст ответа.")
        return
    fid = (await state.get_data()).get("fid")
    await state.clear()
    res = await FB.reply(ctx, int(fid or 0), text)
    await message.answer({"ok": "Ответ отправлен ✅", "missing": "Обращение не найдено 🤔",
                          "gone": "Ученик уже удалил аккаунт.",
                          "failed": "Не доставлено — ученик, похоже, заблокировал бота."}[res])


@router.callback_query(F.data.startswith("fb:close:"))
async def cb_close(call: CallbackQuery, ctx, user):
    if not ctx.is_admin(call.from_user.id):
        await call.answer()
        return
    fid = _fid(call.data)
    if fid is not None:
        await FB.close(ctx, fid)
    await call.answer("Закрыто ✔️")
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass
