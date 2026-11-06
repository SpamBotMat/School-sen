"""Админ в чате: вход в веб-админку одноразовым кодом, быстрая статистика, стоп-кран опроса."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from ..stats import overview

router = Router(name="admin")


def _is_admin(ctx, tg) -> bool:
    return tg is not None and ctx.is_admin(tg.id)


async def send_admin_code(ctx, admin_id: int) -> bool:
    """Выдать одноразовый код и прислать его админу (кнопка сразу входит)."""
    code = ctx.admin_auth.issue(admin_id)
    rows = []
    if ctx.cfg.admin_url and ctx.cfg.secure_cookies:
        rows.append([InlineKeyboardButton(text="🔐 Войти в админку",
                                          url=f"{ctx.cfg.admin_url}#id={admin_id}&code={code}")])
    text = (f"🔐 Код входа в админку: <code>{code}</code>\nДействует 5 минут, одноразовый.\n"
            + (f"Адрес: {ctx.cfg.admin_url}" if ctx.cfg.admin_url else "")
            + "\n\n<i>Никому не пересылай это сообщение.</i>")
    msg = await ctx.notifier.send(admin_id, text, markup=InlineKeyboardMarkup(inline_keyboard=rows) if rows else None)
    ctx.store.event("admin_code", "выдан код входа в админку", user_id=admin_id)
    return msg is not None


@router.message(Command("admin"))
async def cmd_admin(message: Message, ctx, user):
    if not _is_admin(ctx, message.from_user):
        await message.answer("Не понял 🤔 Жми /help")
        return
    wait = ctx.admin_auth.can_send(message.from_user.id)
    if wait:
        await message.answer(f"Код уже отправлен, новый — через {wait} с.")
        return
    await send_admin_code(ctx, message.from_user.id)


def stats_text(ctx) -> str:
    o = overview(ctx)
    u, s, br = o["users"], o["sync"], o["breaker"]
    lines = [
        "📊 <b>Сводка</b>",
        f"Ученики: {u['total']} · активны {u['active']} · пароль ✖ {u['bad_password']} · пауза {u['paused']}"
        f" · бот заблокирован {u['bot_blocked']} · бан {u['banned']}",
        f"Активны за сутки: {u['seen_24h']} · новых сегодня: {o['today'].get('new_users', 0)}",
        f"Опрос за час: {s['last_hour']} (ошибок {s['errors_last_hour']}), в среднем {s['avg_seconds'] or '—'} с",
        f"Предохранитель: {br['state']}" + (f" — {br['reason']}" if br.get("reason") else ""),
        f"Опрос: {'⏸ на паузе' if o['runtime']['sync_paused'] else '▶️ идёт'} · регистрация: {o['runtime']['registration']}",
        f"Память: {o['process']['rss_mb']} МБ · аптайм {o['process']['uptime_h']} ч · БД: сброс {o['db']['last_flush'] or '—'}",
    ]
    return "\n".join(lines)


@router.message(Command("stats"))
async def cmd_stats(message: Message, ctx, user):
    if not _is_admin(ctx, message.from_user):
        await message.answer("Не понял 🤔 Жми /help")
        return
    await message.answer(stats_text(ctx))


def resume_sync(ctx) -> None:
    ctx.breaker.reset()
    ctx.store.set_runtime({"sync_paused": False})
    ctx.store.event("sync_resumed", "опрос возобновлён админом")


@router.message(Command("pause"))
async def cmd_pause(message: Message, ctx, user):
    if not _is_admin(ctx, message.from_user):
        await message.answer("Не понял 🤔 Жми /help")
        return
    ctx.store.set_runtime({"sync_paused": True})
    ctx.store.event("sync_paused", "опрос поставлен на паузу админом", level="warn")
    await message.answer("⏸ Опрос NetSchool на паузе. Вернуть — /resume")


@router.message(Command("resume"))
async def cmd_resume(message: Message, ctx, user):
    if not _is_admin(ctx, message.from_user):
        await message.answer("Не понял 🤔 Жми /help")
        return
    resume_sync(ctx)
    await message.answer("▶️ Опрос снова идёт.")


@router.callback_query(F.data == "adm:resume")
async def cb_resume(call: CallbackQuery, ctx, user):
    if not _is_admin(ctx, call.from_user):
        await call.answer()
        return
    resume_sync(ctx)
    await call.answer("Опрос возобновлён ▶️")
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass
