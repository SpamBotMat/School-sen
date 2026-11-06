"""Логи без секретов: токен бота, ключи, секрет вебхука и ?key= вырезаются из любой строки."""
from __future__ import annotations

import logging
import re
import sys

_QUERY_KEY = re.compile(r"([?&](?:key|code|token)=)[^&\s\"]+", re.I)
_WEBHOOK = re.compile(r"(/tg/webhook/)[0-9a-f]+")
_BOT_TOKEN = re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{30,}\b")


class RedactingFormatter(logging.Formatter):
    def __init__(self, fmt: str, secrets: list[str]):
        super().__init__(fmt, datefmt="%Y-%m-%d %H:%M:%S")
        self.secrets = [s for s in secrets if s and len(s) >= 6]

    def format(self, record: logging.LogRecord) -> str:
        text = super().format(record)
        for s in self.secrets:
            text = text.replace(s, "***")
        text = _BOT_TOKEN.sub("***", text)
        text = _QUERY_KEY.sub(r"\1***", text)
        return _WEBHOOK.sub(r"\1***", text)


def setup_logging(cfg) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(RedactingFormatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        [cfg.bot_token, cfg.credentials_key, cfg.webhook_secret, cfg.tick_key, cfg.database_url]))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(cfg.log_level if cfg.log_level in ("DEBUG", "INFO", "WARNING", "ERROR") else "INFO")
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers = []
        lg.propagate = True
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)     # без журнала каждого запроса
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
