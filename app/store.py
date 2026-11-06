"""Хранилище: всё нужное держим в памяти, в БД пишем пачками.

Зачем так: у Neon Free ограничены часы работы базы (compute спит, когда к ней не ходят).
Поэтому чтение — только из памяти, а изменения копятся и сбрасываются раз в
DB_FLUSH_SECONDS (по умолчанию 10 минут) одной транзакцией. Важное (регистрация,
пароль, удаление) пишется сразу. При остановке сервиса (SIGTERM) всё сбрасывается.

500 учеников ≈ 20–30 МБ памяти: снимки храним JSON-строками и разбираем по запросу.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections import Counter, deque
from dataclasses import dataclass, field, fields
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from . import db as D
from .schemas import RUNTIME_DEFAULTS, THEME_DEFAULTS, USER_DEFAULTS
from .timeutil import now, today

log = logging.getLogger(__name__)

ACTIVE = "active"
STATUSES = ("new", "active", "paused", "bad_password", "banned", "bot_blocked")


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True, default=str)


def digest(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _aware(v: Any) -> Any:
    if isinstance(v, datetime) and v.tzinfo is None:
        return v.replace(tzinfo=timezone.utc)
    return v


def _utc(v: datetime | None) -> datetime | None:
    if v is None:
        return None
    if v.tzinfo is None:
        v = v.replace(tzinfo=timezone.utc)
    return v.astimezone(timezone.utc)


@dataclass
class User:
    id: int
    username: str | None = None
    first_name: str | None = None
    display_name: str | None = None
    status: str = "new"
    ns_login: str | None = None
    ns_cred: str | None = None
    ns_name: str | None = None
    class_name: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    last_seen_at: datetime | None = None
    last_sync_at: datetime | None = None
    last_ok_at: datetime | None = None
    last_error: str | None = None
    fail_count: int = 0
    paused_until: datetime | None = None
    pinned_message_id: int | None = None
    pinned_hash: str | None = None
    morning_sent: str | None = None
    evening_sent: str | None = None
    archive_sent: str | None = None
    archive_hash: str | None = None
    consent_at: datetime | None = None
    invite_code: str | None = None
    settings: dict = field(default_factory=dict)
    theme: dict = field(default_factory=dict)
    meta: dict = field(default_factory=dict)
    pending: list = field(default_factory=list)

    # ── удобные свойства ──
    def opt(self, key: str) -> Any:
        return self.settings.get(key, USER_DEFAULTS.get(key))

    def full_settings(self) -> dict:
        return {**USER_DEFAULTS, **{k: v for k, v in self.settings.items() if k in USER_DEFAULTS}}

    def full_theme(self) -> dict:
        return {**THEME_DEFAULTS, **{k: v for k, v in self.theme.items() if k in THEME_DEFAULTS}}

    @property
    def name(self) -> str:
        """Как обращаться: своё имя из настроек → имя из NetSchool → имя в Telegram."""
        if self.display_name:
            return self.display_name
        if self.ns_name:
            parts = self.ns_name.split()
            if len(parts) >= 2:
                return parts[1]
        return (self.first_name or "").strip() or "друг"

    @property
    def connected(self) -> bool:
        return bool(self.ns_login and self.ns_cred)

    @property
    def syncable(self) -> bool:
        if not self.connected or self.status != ACTIVE:
            return False
        return not (self.paused_until and self.paused_until > now())

    @property
    def tg_title(self) -> str:
        return ("@" + self.username) if self.username else (self.first_name or str(self.id))


USER_FIELDS = [f.name for f in fields(User)]


@dataclass
class Template:
    id: int
    key: str
    text: str
    subject: str | None = None
    enabled: bool = True
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def as_dict(self) -> dict:
        return {"id": self.id, "key": self.key, "text": self.text, "subject": self.subject or "",
                "enabled": self.enabled,
                "updated_at": self.updated_at.isoformat() if self.updated_at else None}


class Store:
    def __init__(self, engine: AsyncEngine, flush_seconds: int = 600):
        self.engine = engine
        self.flush_seconds = flush_seconds
        self.users: dict[int, User] = {}
        self._snaps: dict[tuple[int, str], tuple[str, str, datetime]] = {}   # (json, hash, updated)
        self._kv: dict[str, Any] = {}
        self._details: dict[str, tuple[str, datetime]] = {}
        self._files: dict[str, dict] = {}
        self.templates: dict[int, Template] = {}
        self.invites: dict[str, dict] = {}
        self._dirty_users: set[int] = set()
        self._dirty_snaps: set[tuple[int, str]] = set()
        self._dirty_kv: set[str] = set()
        self._dirty_details: set[str] = set()
        self._dirty_files: set[str] = set()
        self._events_buf: list[dict] = []
        self.recent_events: deque[dict] = deque(maxlen=3000)
        self.stats: dict[str, Counter] = {}
        self._dirty_stats: set[str] = set()
        self._lock = asyncio.Lock()
        self._soon: asyncio.TimerHandle | None = None
        self._flusher: asyncio.Task | None = None
        self.last_flush_at: datetime | None = None
        self.flush_count = 0
        self.loaded = False

    @property
    def dialect(self) -> str:
        return self.engine.dialect.name

    # ───────────────────────── загрузка ─────────────────────────

    async def load(self) -> None:
        await D.init_db(self.engine)
        async with self.engine.connect() as conn:
            for row in (await conn.execute(select(D.users))).mappings():
                data = {k: _aware(row[k]) for k in USER_FIELDS if k in row}
                for k in ("settings", "theme", "meta"):
                    data[k] = data.get(k) or {}
                data["pending"] = data.get("pending") or []
                data["fail_count"] = data.get("fail_count") or 0
                self.users[row["id"]] = User(**data)
            for row in (await conn.execute(select(D.snapshots))).mappings():
                self._snaps[(row["user_id"], row["kind"])] = (row["data"], row["hash"] or digest(row["data"]),
                                                              _aware(row["updated_at"]) or now())
            for row in (await conn.execute(select(D.kv))).mappings():
                try:
                    self._kv[row["key"]] = json.loads(row["value"])
                except ValueError:
                    log.warning("kv %s: битый JSON — пропускаю", row["key"])
            border = now() - timedelta(days=60)
            for row in (await conn.execute(select(D.details))).mappings():
                fetched = _aware(row["fetched_at"]) or now()
                if fetched >= border:
                    self._details[row["key"]] = (row["data"], fetched)
            for row in (await conn.execute(select(D.files))).mappings():
                self._files[row["key"]] = {"file_id": row["file_id"], "name": row["name"], "size": row["size"]}
            for row in (await conn.execute(select(D.templates))).mappings():
                self.templates[row["id"]] = Template(
                    id=row["id"], key=row["key"], text=row["text"], subject=row["subject"],
                    enabled=bool(row["enabled"]), created_at=_aware(row["created_at"]),
                    updated_at=_aware(row["updated_at"]))
            for row in (await conn.execute(select(D.invites))).mappings():
                self.invites[row["code"]] = {k: _aware(row[k]) for k in row.keys()}
            day = today().isoformat()
            row = (await conn.execute(select(D.stats_daily).where(D.stats_daily.c.day == day))).mappings().first()
            if row and row["data"]:
                self.stats[day] = Counter(row["data"])
        self.loaded = True
        log.info("Загружено: %d учеников, %d снимков, %d заготовок", len(self.users), len(self._snaps),
                 len(self.templates))

    # ───────────────────────── ученики ─────────────────────────

    def get_user(self, uid: int) -> User | None:
        return self.users.get(uid)

    def new_user(self, uid: int, *, username: str | None, first_name: str | None) -> User:
        t = now()
        u = User(id=uid, username=username, first_name=first_name, created_at=t, updated_at=t, last_seen_at=t)
        self.users[uid] = u
        self.save_user(u, urgent=True)
        self.stat("new_users")
        return u

    def save_user(self, u: User, *, urgent: bool = False) -> None:
        u.updated_at = now()
        self._dirty_users.add(u.id)
        if urgent:
            self.flush_soon()

    async def delete_user(self, uid: int) -> None:
        """Полное удаление: ученик, снимки, обращения. Сразу в БД."""
        self.users.pop(uid, None)
        for key in [k for k in self._snaps if k[0] == uid]:
            self._snaps.pop(key, None)
        self._dirty_snaps = {k for k in self._dirty_snaps if k[0] != uid}
        self._dirty_users.discard(uid)
        async with self._lock:
            async with self.engine.begin() as conn:
                await conn.execute(delete(D.snapshots).where(D.snapshots.c.user_id == uid))
                await conn.execute(delete(D.feedback).where(D.feedback.c.user_id == uid))
                await conn.execute(delete(D.users).where(D.users.c.id == uid))
                await conn.execute(update(D.events).where(D.events.c.user_id == uid).values(user_id=None))
        for e in self.recent_events:
            if e.get("user_id") == uid:
                e["user_id"] = None

    def drop_snaps(self, uid: int, kinds: list[str] | None = None) -> None:
        for key in [k for k in self._snaps if k[0] == uid and (kinds is None or k[1] in kinds)]:
            self._snaps.pop(key, None)
            self._dirty_snaps.add(key)       # при сбросе — удалится из БД

    # ───────────────────────── снимки ─────────────────────────

    def snap(self, uid: int, kind: str, default: Any = None) -> Any:
        item = self._snaps.get((uid, kind))
        if item is None:
            return default
        return json.loads(item[0])

    def snap_time(self, uid: int, kind: str) -> datetime | None:
        item = self._snaps.get((uid, kind))
        return item[2] if item else None

    def snap_hash(self, uid: int, kind: str) -> str | None:
        item = self._snaps.get((uid, kind))
        return item[1] if item else None

    def set_snap(self, uid: int, kind: str, data: Any) -> bool:
        """Сохраняет снимок; True — если он поменялся."""
        raw = dumps(data)
        h = digest(raw)
        old = self._snaps.get((uid, kind))
        if old and old[1] == h:
            return False
        self._snaps[(uid, kind)] = (raw, h, now())
        self._dirty_snaps.add((uid, kind))
        return True

    def snap_kinds(self, uid: int) -> list[str]:
        return [k for (u, k) in self._snaps if u == uid]

    # ───────────────────────── общий kv ─────────────────────────

    def kv_get(self, key: str, default: Any = None) -> Any:
        return self._kv.get(key, default)

    def kv_set(self, key: str, value: Any, *, persist: bool = True) -> None:
        self._kv[key] = value
        if persist:
            self._dirty_kv.add(key)

    def runtime(self) -> dict:
        saved = self._kv.get("runtime") or {}
        return {**RUNTIME_DEFAULTS, **{k: v for k, v in saved.items() if k in RUNTIME_DEFAULTS}}

    def rt(self, key: str) -> Any:
        saved = self._kv.get("runtime") or {}
        return saved.get(key, RUNTIME_DEFAULTS.get(key))

    def set_runtime(self, patch: dict) -> dict:
        saved = dict(self._kv.get("runtime") or {})
        saved.update(patch)
        self.kv_set("runtime", saved)
        self.flush_soon()
        return self.runtime()

    # ───────────────────────── подробности ДЗ и файлы ─────────────────────────

    def detail_get(self, key: str) -> dict | None:
        item = self._details.get(key)
        return json.loads(item[0]) if item else None

    def detail_age(self, key: str) -> timedelta | None:
        item = self._details.get(key)
        return (now() - item[1]) if item else None

    def detail_set(self, key: str, data: dict) -> None:
        self._details[key] = (dumps(data), now())
        self._dirty_details.add(key)

    def file_get(self, key: str) -> dict | None:
        return self._files.get(key)

    def file_set(self, key: str, file_id: str, name: str | None = None, size: int | None = None) -> None:
        self._files[key] = {"file_id": file_id, "name": name, "size": size}
        self._dirty_files.add(key)

    # ───────────────────────── события и статистика ─────────────────────────

    def event(self, kind: str, message: str, *, user_id: int | None = None, level: str = "info") -> None:
        item = {"ts": now(), "user_id": user_id, "kind": kind, "level": level, "message": message[:500]}
        self._events_buf.append(item)
        self.recent_events.append(item)
        if level in ("warn", "error"):
            log.warning("[%s] %s (user=%s)", kind, message, user_id)

    def stat(self, name: str, n: int = 1) -> None:
        day = today().isoformat()
        self.stats.setdefault(day, Counter())[name] += n
        self._dirty_stats.add(day)

    def stats_today(self) -> dict:
        return dict(self.stats.get(today().isoformat(), Counter()))

    # ───────────────────────── заготовки ─────────────────────────

    async def add_template(self, key: str, text_: str, subject: str | None = None, enabled: bool = True) -> Template:
        t = now()
        async with self.engine.begin() as conn:
            res = await conn.execute(D.templates.insert().values(
                key=key, text=text_, subject=subject or None, enabled=enabled, created_at=t, updated_at=t))
            tid = res.inserted_primary_key[0]
        tpl = Template(id=tid, key=key, text=text_, subject=subject or None, enabled=enabled, created_at=t, updated_at=t)
        self.templates[tid] = tpl
        return tpl

    async def add_templates(self, items: list[tuple[str, str, str | None]]) -> int:
        t = now()
        rows = [{"key": k, "text": x, "subject": s, "enabled": True, "created_at": t, "updated_at": t}
                for k, x, s in items]
        if not rows:
            return 0
        async with self.engine.begin() as conn:
            await conn.execute(D.templates.insert(), rows)
            fresh = (await conn.execute(select(D.templates))).mappings().all()
        self.templates = {r["id"]: Template(id=r["id"], key=r["key"], text=r["text"], subject=r["subject"],
                                            enabled=bool(r["enabled"]), created_at=_aware(r["created_at"]),
                                            updated_at=_aware(r["updated_at"])) for r in fresh}
        return len(rows)

    async def update_template(self, tid: int, **changes: Any) -> Template | None:
        tpl = self.templates.get(tid)
        if tpl is None:
            return None
        allowed = {k: v for k, v in changes.items() if k in ("key", "text", "subject", "enabled")}
        if "subject" in allowed:
            allowed["subject"] = allowed["subject"] or None
        allowed["updated_at"] = now()
        async with self.engine.begin() as conn:
            await conn.execute(update(D.templates).where(D.templates.c.id == tid).values(**allowed))
        for k, v in allowed.items():
            setattr(tpl, k, v)
        return tpl

    async def delete_template(self, tid: int) -> bool:
        if tid not in self.templates:
            return False
        async with self.engine.begin() as conn:
            await conn.execute(delete(D.templates).where(D.templates.c.id == tid))
        self.templates.pop(tid, None)
        return True

    # ───────────────────────── инвайты ─────────────────────────

    async def save_invite(self, inv: dict) -> None:
        self.invites[inv["code"]] = inv
        stmt = D.upsert(self.dialect, D.invites, ["code"])
        row = {c.name: inv.get(c.name) for c in D.invites.columns}
        async with self.engine.begin() as conn:
            await conn.execute(stmt, [row])

    async def delete_invite(self, code: str) -> None:
        self.invites.pop(code, None)
        async with self.engine.begin() as conn:
            await conn.execute(delete(D.invites).where(D.invites.c.code == code))

    def invite_ok(self, code: str) -> bool:
        inv = self.invites.get(code)
        if not inv or not inv.get("active"):
            return False
        if inv.get("expires_at") and _aware(inv["expires_at"]) < now():
            return False
        return (inv.get("uses") or 0) < (inv.get("max_uses") or 1)

    # ───────────────────────── сброс в БД ─────────────────────────

    @property
    def dirty(self) -> bool:
        return bool(self._dirty_users or self._dirty_snaps or self._dirty_kv or self._dirty_details
                    or self._dirty_files or self._events_buf or self._dirty_stats)

    def dirty_counts(self) -> dict:
        return {"users": len(self._dirty_users), "snapshots": len(self._dirty_snaps), "kv": len(self._dirty_kv),
                "details": len(self._dirty_details), "events": len(self._events_buf)}

    def flush_soon(self, delay: float = 2.0) -> None:
        """Сбросить в БД в ближайшие секунды (несколько вызовов подряд — один сброс)."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if self._soon is not None:
            return

        def fire() -> None:
            self._soon = None
            loop.create_task(self._safe_flush())

        self._soon = loop.call_later(delay, fire)

    async def _safe_flush(self) -> None:
        try:
            await self.flush()
        except Exception:  # noqa: BLE001
            log.exception("Сброс в БД не удался — повторю позже")

    async def flush(self) -> int:
        if not self.dirty:
            return 0
        async with self._lock:
            users_ids, self._dirty_users = self._dirty_users, set()
            snap_keys, self._dirty_snaps = self._dirty_snaps, set()
            kv_keys, self._dirty_kv = self._dirty_kv, set()
            det_keys, self._dirty_details = self._dirty_details, set()
            file_keys, self._dirty_files = self._dirty_files, set()
            evs, self._events_buf = self._events_buf, []
            stat_days, self._dirty_stats = self._dirty_stats, set()
            try:
                n = await self._write(users_ids, snap_keys, kv_keys, det_keys, file_keys, evs, stat_days)
            except Exception:
                # вернуть «грязные» отметки, чтобы не потерять изменения
                self._dirty_users |= users_ids
                self._dirty_snaps |= snap_keys
                self._dirty_kv |= kv_keys
                self._dirty_details |= det_keys
                self._dirty_files |= file_keys
                self._events_buf = evs + self._events_buf
                self._dirty_stats |= stat_days
                raise
        self.last_flush_at = now()
        self.flush_count += 1
        log.debug("БД: записано %d строк", n)
        return n

    async def _write(self, users_ids, snap_keys, kv_keys, det_keys, file_keys, evs, stat_days) -> int:
        d = self.dialect
        total = 0
        async with self.engine.begin() as conn:
            rows = []
            for uid in users_ids:
                u = self.users.get(uid)
                if u is None:
                    continue
                row = {k: getattr(u, k) for k in USER_FIELDS}
                for k in ("created_at", "updated_at", "last_seen_at", "last_sync_at", "last_ok_at",
                          "paused_until", "consent_at"):
                    row[k] = _utc(row[k])
                rows.append(row)
            for part in D.chunks(rows):
                await conn.execute(D.upsert(d, D.users, ["id"]), part)
            total += len(rows)

            rows, gone = [], []
            for key in snap_keys:
                item = self._snaps.get(key)
                if item is None:
                    gone.append(key)
                    continue
                if key[0] not in self.users:
                    continue
                rows.append({"user_id": key[0], "kind": key[1], "data": item[0], "hash": item[1],
                             "updated_at": _utc(item[2])})
            for part in D.chunks(rows, 100):
                await conn.execute(D.upsert(d, D.snapshots, ["user_id", "kind"]), part)
            for uid, kind in gone:
                await conn.execute(delete(D.snapshots).where(
                    (D.snapshots.c.user_id == uid) & (D.snapshots.c.kind == kind)))
            total += len(rows) + len(gone)

            rows = [{"key": k, "value": dumps(self._kv[k]), "updated_at": _utc(now())} for k in kv_keys if k in self._kv]
            if rows:
                await conn.execute(D.upsert(d, D.kv, ["key"]), rows)
            total += len(rows)

            rows = [{"key": k, "data": self._details[k][0], "fetched_at": _utc(self._details[k][1])}
                    for k in det_keys if k in self._details]
            for part in D.chunks(rows):
                await conn.execute(D.upsert(d, D.details, ["key"]), part)
            total += len(rows)

            rows = [{"key": k, "file_id": v["file_id"], "name": (v.get("name") or "")[:256], "size": v.get("size"),
                     "created_at": _utc(now())} for k in file_keys if (v := self._files.get(k))]
            if rows:
                await conn.execute(D.upsert(d, D.files, ["key"]), rows)
            total += len(rows)

            if evs:
                rows = [{**e, "ts": _utc(e["ts"])} for e in evs]
                for part in D.chunks(rows, 500):
                    await conn.execute(D.events.insert(), part)
                total += len(evs)

            rows = [{"day": day, "data": dict(self.stats.get(day, {}))} for day in stat_days]
            if rows:
                await conn.execute(D.upsert(d, D.stats_daily, ["day"]), rows)
            total += len(rows)
        return total

    async def run_flusher(self) -> None:
        while True:
            await asyncio.sleep(self.flush_seconds)
            await self._safe_flush()

    def start(self) -> None:
        if self._flusher is None:
            self._flusher = asyncio.get_running_loop().create_task(self.run_flusher())

    async def close(self) -> None:
        if self._flusher is not None:
            self._flusher.cancel()
            self._flusher = None
        if self._soon is not None:
            self._soon.cancel()
            self._soon = None
        try:
            await self.flush()
        except Exception:  # noqa: BLE001
            log.exception("Финальный сброс в БД не удался")

    # ───────────────────────── обслуживание ─────────────────────────

    async def cleanup(self) -> dict:
        """Раз в сутки: старые события (>30 дней), подробности ДЗ (>60), статистика (>400)."""
        t = now()
        async with self.engine.begin() as conn:
            r1 = await conn.execute(delete(D.events).where(D.events.c.ts < _utc(t - timedelta(days=30))))
            r2 = await conn.execute(delete(D.details).where(D.details.c.fetched_at < _utc(t - timedelta(days=60))))
            r3 = await conn.execute(delete(D.stats_daily).where(
                D.stats_daily.c.day < (t - timedelta(days=400)).date().isoformat()))
        border = t - timedelta(days=60)
        for k in [k for k, v in self._details.items() if v[1] < border]:
            self._details.pop(k, None)
        for day in [d for d in self.stats if d < (t - timedelta(days=2)).date().isoformat()]:
            if day not in self._dirty_stats:
                self.stats.pop(day, None)
        return {"events": r1.rowcount, "details": r2.rowcount, "stats": r3.rowcount}

    async def query(self, stmt) -> list[dict]:
        async with self.engine.connect() as conn:
            return [dict(r) for r in (await conn.execute(stmt)).mappings().all()]

    async def execute(self, stmt) -> Any:
        async with self.engine.begin() as conn:
            return await conn.execute(stmt)
