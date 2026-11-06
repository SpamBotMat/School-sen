"""База данных: таблицы (SQLAlchemy Core), подключение к Neon/SQLite, мини-миграции.

Postgres (Neon) — на проде, SQLite — локально и в тестах. Схема создаётся сама при старте;
новые колонки добавляются автоматически (ALTER TABLE ... ADD COLUMN), удаления — вручную.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import (
    JSON, BigInteger, Boolean, Column, Date, DateTime, Index, Integer, MetaData, String, Table, Text,
    TypeDecorator, inspect, text,
)
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

log = logging.getLogger(__name__)
metadata = MetaData()



class UTCDateTime(TypeDecorator):
    """Метка времени, которая в базе всегда в UTC.

    Postgres (timestamptz) и так хранит момент времени, а SQLite пояса не знает и
    записал бы «настенное» время Ашхабада (+5 ч при чтении). Поэтому перед записью
    переводим в UTC, а наивные значения из базы считаем UTC (как и весь остальной код).
    """
    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Any) -> Any:
        if isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            return value.astimezone(timezone.utc)
        return value

    def process_result_value(self, value: Any, dialect: Any) -> Any:
        if isinstance(value, datetime) and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value


TS = UTCDateTime()

users = Table(
    "users", metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=False),   # Telegram user id
    Column("username", String(64)),
    Column("first_name", String(128)),
    Column("display_name", String(64)),
    Column("status", String(16), nullable=False, default="new"),
    Column("ns_login", String(128)),
    Column("ns_cred", Text),                     # Fernet(md5 пароля + длина) — не пароль
    Column("ns_name", String(200)),
    Column("class_name", String(32)),
    Column("created_at", TS),
    Column("updated_at", TS),
    Column("last_seen_at", TS),
    Column("last_sync_at", TS),
    Column("last_ok_at", TS),
    Column("last_error", String(300)),
    Column("fail_count", Integer, default=0),
    Column("paused_until", TS),
    Column("pinned_message_id", BigInteger),
    Column("pinned_hash", String(64)),
    Column("morning_sent", String(10)),          # ISO-дата последней утренней сводки
    Column("evening_sent", String(10)),
    Column("archive_sent", String(10)),
    Column("archive_hash", String(64)),
    Column("consent_at", TS),
    Column("invite_code", String(32)),
    Column("settings", JSON),
    Column("theme", JSON),
    Column("meta", JSON),                        # служебное: когда что обновляли, отпечатки страниц
    Column("pending", JSON),                     # уведомления, отложенные на тихие часы
)

snapshots = Table(
    "snapshots", metadata,
    Column("user_id", BigInteger, primary_key=True, autoincrement=False),
    Column("kind", String(32), primary_key=True),
    Column("data", Text, nullable=False),        # JSON
    Column("hash", String(64)),
    Column("updated_at", TS),
)

kv = Table(
    "kv", metadata,
    Column("key", String(64), primary_key=True),
    Column("value", Text, nullable=False),       # JSON
    Column("updated_at", TS),
)

details = Table(  # «Подробности задания» — общие для всего класса, кэшируем по AID
    "details", metadata,
    Column("key", String(64), primary_key=True),
    Column("data", Text, nullable=False),
    Column("fetched_at", TS),
)

files = Table(  # file_id Telegram для уже отправленных вложений/картинок
    "files", metadata,
    Column("key", String(128), primary_key=True),
    Column("file_id", String(256), nullable=False),
    Column("name", String(256)),
    Column("size", Integer),
    Column("created_at", TS),
)

events = Table(
    "events", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("ts", TS, nullable=False),
    Column("user_id", BigInteger),
    Column("kind", String(32), nullable=False),
    Column("level", String(8), nullable=False, default="info"),
    Column("message", String(500)),
    Index("ix_events_ts", "ts"),
    Index("ix_events_user", "user_id"),
)

stats_daily = Table(
    "stats_daily", metadata,
    Column("day", String(10), primary_key=True),
    Column("data", JSON),
)

feedback = Table(
    "feedback", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("user_id", BigInteger, nullable=False),
    Column("text", Text, nullable=False),
    Column("created_at", TS),
    Column("status", String(12), default="new"),     # new | answered | closed
    Column("reply", Text),
    Column("replied_at", TS),
    Index("ix_feedback_status", "status"),
)

templates = Table(
    "templates", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("key", String(64), nullable=False),
    Column("text", Text, nullable=False),
    Column("subject", String(64)),                    # только для этого предмета (часть названия)
    Column("enabled", Boolean, default=True),
    Column("created_at", TS),
    Column("updated_at", TS),
    Index("ix_templates_key", "key"),
)

broadcasts = Table(
    "broadcasts", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("text", Text, nullable=False),
    Column("target", String(64), default="all"),
    Column("status", String(12), default="queued"),  # queued | sending | done | cancelled
    Column("total", Integer, default=0),
    Column("sent", Integer, default=0),
    Column("failed", Integer, default=0),
    Column("created_at", TS),
    Column("finished_at", TS),
)

invites = Table(
    "invites", metadata,
    Column("code", String(32), primary_key=True),
    Column("note", String(100)),
    Column("max_uses", Integer, default=1),
    Column("uses", Integer, default=0),
    Column("active", Boolean, default=True),
    Column("created_at", TS),
    Column("expires_at", TS),
)


def normalize_url(url: str, data_dir: Path) -> tuple[str, dict[str, Any]]:
    """DATABASE_URL как его дают Neon/Render → URL для SQLAlchemy async + connect_args."""
    if not url:
        data_dir.mkdir(parents=True, exist_ok=True)
        return f"sqlite+aiosqlite:///{data_dir / 'app.db'}", {}
    if url.startswith("sqlite"):
        if url.startswith("sqlite://") and "+aiosqlite" not in url:
            url = url.replace("sqlite://", "sqlite+aiosqlite://", 1)
        return url, {}
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+asyncpg://" + url[len("postgresql://"):]
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    sslmode = query.pop("sslmode", None)
    query.pop("channel_binding", None)        # asyncpg такого параметра не знает
    query.pop("options", None)
    connect_args: dict[str, Any] = {"statement_cache_size": 0}   # совместимо с пулером Neon (PgBouncer)
    host = parts.hostname or ""
    if sslmode in ("require", "verify-ca", "verify-full", "prefer") or host.endswith(".neon.tech"):
        connect_args["ssl"] = "require"
    query["prepared_statement_cache_size"] = "0"
    url = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
    return url, connect_args


def make_engine(url: str, data_dir: Path) -> AsyncEngine:
    real_url, connect_args = normalize_url(url, data_dir)
    if real_url.startswith("sqlite"):
        return create_async_engine(real_url, connect_args=connect_args)
    return create_async_engine(
        real_url, connect_args=connect_args, pool_size=3, max_overflow=2,
        pool_pre_ping=True, pool_recycle=300, pool_timeout=30,
    )


def _auto_migrate(sync_conn) -> list[str]:
    """Добавляет недостающие колонки (только ADD COLUMN, всегда nullable)."""
    done = []
    insp = inspect(sync_conn)
    for table in metadata.sorted_tables:
        if not insp.has_table(table.name):
            continue
        existing = {c["name"] for c in insp.get_columns(table.name)}
        for col in table.columns:
            if col.name in existing:
                continue
            ddl = col.type.compile(dialect=sync_conn.dialect)
            sync_conn.execute(text(f'ALTER TABLE {table.name} ADD COLUMN "{col.name}" {ddl}'))
            done.append(f"{table.name}.{col.name}")
    return done


async def init_db(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
        added = await conn.run_sync(_auto_migrate)
    if added:
        log.info("БД: добавлены колонки %s", ", ".join(added))


def upsert(dialect_name: str, table: Table, pk: Sequence[str]):
    if dialect_name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    stmt = insert(table)
    cols = {c.name: stmt.excluded[c.name] for c in table.columns if c.name not in pk}
    return stmt.on_conflict_do_update(index_elements=list(pk), set_=cols)


def chunks(rows: list, size: int = 200) -> Iterable[list]:
    for i in range(0, len(rows), size):
        yield rows[i:i + size]
