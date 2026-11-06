"""Сборка бота: Bot + Dispatcher, роутеры, мидлвари, вебхук, команды и кнопка «Дневник»."""
from __future__ import annotations

import hashlib
import logging
import re

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (BotCommand, BotCommandScopeChat, BotCommandScopeDefault, LinkPreviewOptions,
                           MenuButtonWebApp, WebAppInfo)

from . import handlers_admin, handlers_feedback, handlers_main, handlers_settings, handlers_start
from .middlewares import UserMiddleware

log = logging.getLogger(__name__)

COMMANDS = [
    ("today", "📅 Сегодня"), ("tomorrow", "➡️ Завтра"), ("hw", "📚 Домашка на неделю"),
    ("marks", "⭐ Оценки и прогноз"), ("debts", "⚠️ Долги"), ("archive", "📦 Архив картинками"),
    ("sync", "🔄 Обновить сейчас"), ("settings", "⚙️ Настройки"), ("feedback", "✉️ Написать администратору"),
    ("help", "❓ Что я умею"), ("privacy", "🔒 Как храню данные"),
]
ADMIN_COMMANDS = [("admin", "🔐 Вход в админку"), ("stats", "📊 Сводка"), ("pause", "⏸ Пауза опроса"),
                  ("resume", "▶️ Снять паузу")]


def make_bot(token: str) -> Bot:
    return Bot(token, default=DefaultBotProperties(parse_mode=ParseMode.HTML,
                                                   link_preview=LinkPreviewOptions(is_disabled=True)))


def make_dispatcher() -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage())
    mw = UserMiddleware()
    dp.message.outer_middleware(mw)
    dp.callback_query.outer_middleware(mw)
    # порядок важен: «всё остальное» (fallback) живёт в main — он последний
    routers = [handlers_start.router, handlers_settings.router, handlers_feedback.router,
               handlers_admin.router, handlers_main.router]
    for r in routers:
        r._parent_router = None          # роутеры — модульные; позволяем пересобрать (тесты, перезапуск)
    dp.include_routers(*routers)
    return dp


def secret_token(cfg) -> str:
    """Секрет для заголовка X-Telegram-Bot-Api-Secret-Token (Telegram разрешает только A-Z a-z 0-9 _ -)."""
    return re.sub(r"[^A-Za-z0-9_-]", "", cfg.webhook_secret)[:256] or "x"


def webhook_path(cfg) -> str:
    return "/tg/webhook/" + hashlib.sha256(("path:" + cfg.webhook_secret).encode()).hexdigest()[:16]


async def configure_telegram(ctx, *, webhook: bool = True) -> None:
    bot, cfg = ctx.bot, ctx.cfg
    me = await bot.get_me()
    ctx.bot_username = me.username
    log.info("Бот: @%s", me.username)
    if webhook and cfg.public_url:
        url = cfg.public_url + webhook_path(cfg)
        info = await bot.get_webhook_info()
        if info.url != url:
            await bot.set_webhook(url, secret_token=secret_token(cfg), allowed_updates=["message", "callback_query"],
                                  max_connections=20)
            log.info("Вебхук установлен")
    try:
        await bot.set_my_commands([BotCommand(command=c, description=d) for c, d in COMMANDS],
                                  scope=BotCommandScopeDefault())
        for aid in cfg.admin_ids:
            try:
                await bot.set_my_commands([BotCommand(command=c, description=d) for c, d in COMMANDS + ADMIN_COMMANDS],
                                          scope=BotCommandScopeChat(chat_id=aid))
            except TelegramAPIError:
                pass                     # админ ещё не писал боту — не страшно
        if cfg.webapp_url:
            await bot.set_chat_menu_button(menu_button=MenuButtonWebApp(text="Дневник",
                                                                        web_app=WebAppInfo(url=cfg.webapp_url)))
    except TelegramAPIError as e:
        log.warning("Не удалось настроить команды/кнопку: %s", e)
