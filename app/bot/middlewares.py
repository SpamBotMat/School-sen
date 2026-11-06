"""Мидлварь: ученик из хранилища, бан, техработы, «бот снова разблокирован»."""
from __future__ import annotations

from datetime import timedelta
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from ..context import get_ctx
from ..timeutil import now


class UserMiddleware(BaseMiddleware):
    async def __call__(self, handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
                       event: TelegramObject, data: dict[str, Any]) -> Any:
        ctx = get_ctx()
        data["ctx"] = ctx
        tg = data.get("event_from_user")
        chat = data.get("event_chat")
        if chat is not None and chat.type != "private":
            return None                      # в группах не работаем
        u = None
        if tg is not None and not tg.is_bot:
            u = ctx.store.get_user(tg.id)
            if u is not None:
                changed = False
                if u.username != tg.username or u.first_name != tg.first_name:
                    u.username, u.first_name = tg.username, tg.first_name
                    changed = True
                if u.status == "bot_blocked":
                    u.status = u.meta.pop("status_before_block", None) or ("active" if u.connected else "new")
                    changed = True
                t = now()
                if not u.last_seen_at or t - u.last_seen_at > timedelta(minutes=30):
                    u.last_seen_at = t
                    changed = True
                if changed:
                    ctx.store.save_user(u)
        data["user"] = u
        ctx.store.stat("updates")
        is_admin = ctx.is_admin(tg.id if tg else None)
        if u is not None and u.status == "banned" and not is_admin:
            if isinstance(event, Message):
                await event.answer("Доступ к боту закрыт администратором.")
            elif isinstance(event, CallbackQuery):
                await event.answer("Доступ закрыт", show_alert=True)
            return None
        if ctx.store.rt("maintenance") and not is_admin:
            text = ctx.store.rt("maintenance_text") or "Идут техработы 🛠 Скоро вернусь!"
            if isinstance(event, Message):
                await event.answer(text)
            elif isinstance(event, CallbackQuery):
                await event.answer(text, show_alert=True)
            return None
        return await handler(event, data)
