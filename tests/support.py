"""Тестовые помощники: бот без сети, контекст на SQLite, клиент для API."""
from __future__ import annotations

import hashlib
import itertools
import os
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import httpx
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.types import Chat, Message, PhotoSize, User, WebhookInfo

from app.config import Config
from app.crypto import generate_key

BOT_TOKEN = "123456:TEST-token-for-tests"
ADMIN_ID = 42
TICK_KEY = "tick-secret"


class FakeSession(BaseSession):
    """Перехватывает все вызовы Bot API и отвечает правдоподобно — без сети."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list = []
        self._ids = itertools.count(1000)

    def _msg(self, bot, chat_id, text=None, photo=False) -> Message:
        kw = {}
        if photo:
            n = next(self._ids)
            kw["photo"] = [PhotoSize(file_id=f"photo{n}", file_unique_id=f"u{n}", width=10, height=10)]
        return Message(message_id=next(self._ids), date=datetime.now(), text=text,
                       chat=Chat(id=int(chat_id or 1), type="private"), **kw).as_(bot)

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        name = type(method).__name__
        chat_id = getattr(method, "chat_id", None) or 1
        if name == "GetMe":
            return User(id=999, is_bot=True, first_name="School Sensor", username="test_sensor_bot")
        if name == "GetWebhookInfo":
            return WebhookInfo(url="", has_custom_certificate=False, pending_update_count=0)
        if name == "SendMediaGroup":
            return [self._msg(bot, chat_id, photo=True) for _ in method.media]
        if name in ("SendPhoto",):
            return self._msg(bot, chat_id, photo=True)
        if name in ("SendMessage", "SendDocument", "EditMessageText", "EditMessageReplyMarkup", "EditMessageCaption",
                    "EditMessageMedia", "CopyMessage"):
            return self._msg(bot, chat_id, text=getattr(method, "text", None))
        return True

    async def stream_content(self, url, headers=None, timeout=30, chunk_size=65536, raise_for_status=True):
        yield b""

    async def close(self) -> None:
        pass

    # удобства для проверок
    def of(self, name: str) -> list:
        return [c for c in self.calls if type(c).__name__ == name]

    def texts(self) -> list[str]:
        return [getattr(c, "text", None) or getattr(c, "caption", None) or "" for c in self.calls
                if type(c).__name__ in ("SendMessage", "EditMessageText", "SendPhoto", "SendDocument")]

    def clear(self) -> None:
        self.calls.clear()


def make_fake_bot() -> tuple[Bot, FakeSession]:
    session = FakeSession()
    bot = Bot(BOT_TOKEN, session=session, default=DefaultBotProperties(parse_mode="HTML"))
    return bot, session


def make_config(tmp: Path, **over) -> Config:
    base = dict(env="test", bot_token=BOT_TOKEN, public_url="https://sensor.example", webhook_secret="hook-secret",
                database_url="", credentials_key=generate_key(), admin_ids=(ADMIN_ID,), tick_key=TICK_KEY,
                demo_mode=True, dev_mode=False, scheduler_enabled=False, data_dir=tmp)
    base.update(over)
    return Config(**base)


# Прогнать те же тесты на настоящем Postgres (как Neon):
#   TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:5432/postgres pytest
PG_URL = os.environ.get("TEST_DATABASE_URL", "").strip()


async def _pg_database_for(tmp: Path) -> str:
    """Отдельная база на каждый tmp_path (повторный running_app с тем же tmp видит те же данные)."""
    import asyncpg
    name = "t_" + hashlib.sha1(str(tmp).encode()).hexdigest()[:12]
    conn = await asyncpg.connect(PG_URL)
    try:
        if not await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", name):
            await conn.execute(f'CREATE DATABASE "{name}"')
    finally:
        await conn.close()
    return urlunsplit(urlsplit(PG_URL)._replace(path="/" + name))


@asynccontextmanager
async def running_app(tmp: Path, client_factory=None, **cfg_over):
    """FastAPI-приложение целиком (lifespan) + httpx-клиент + фейковый бот."""
    from app.web.main import create_app
    if PG_URL and "database_url" not in cfg_over:
        cfg_over["database_url"] = await _pg_database_for(tmp)
    bot, session = make_fake_bot()
    cfg = make_config(tmp, **cfg_over)
    app = create_app(cfg, bot=bot, client_factory=client_factory, telegram_setup=False, quiet_logs=True)
    async with app.router.lifespan_context(app):
        ctx = app.state.ctx
        ctx.bot_username = "test_sensor_bot"
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://sensor.example") as client:
            yield app, ctx, client, session
