"""Сводные цифры для админки и /stats (без оценок и паролей — только служебное)."""
from __future__ import annotations

import os
import platform
import time
from collections import Counter
from datetime import timedelta

from . import __version__
from .store import STATUSES
from .timeutil import as_local, now


def rss_mb() -> float:
    try:
        with open("/proc/self/statm") as f:
            pages = int(f.read().split()[1])
        return round(pages * os.sysconf("SC_PAGE_SIZE") / 1048576, 1)
    except (OSError, ValueError, IndexError):
        import resource
        return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)


def _hhmm(dt) -> str | None:
    return as_local(dt).strftime("%d.%m %H:%M") if dt else None


def overview(ctx) -> dict:
    t = now()
    users = list(ctx.store.users.values())
    by = Counter(u.status for u in users)
    paused_self = sum(1 for u in users if u.paused_until and u.paused_until > t)
    seen = sum(1 for u in users if u.last_seen_at and t - u.last_seen_at < timedelta(hours=24))
    synced = sum(1 for u in users if u.last_ok_at and t - u.last_ok_at < timedelta(hours=3))
    classes = Counter(u.class_name for u in users if u.class_name and u.connected)
    rt = ctx.store.runtime()
    w = ctx.weather
    return {
        "version": __version__,
        "users": {
            "total": len(users), **{s: by.get(s, 0) for s in STATUSES},
            "connected": sum(1 for u in users if u.connected), "paused_self": paused_self,
            "seen_24h": seen, "synced_3h": synced, "classes": dict(classes.most_common(40)),
        },
        "sync": ctx.sync.stats(),
        "breaker": ctx.breaker.as_dict(),
        "runtime": {k: rt[k] for k in ("sync_paused", "maintenance", "registration", "sync_interval",
                                        "sync_concurrency")},
        "today": ctx.store.stats_today(),
        "scheduler": {
            "alive": bool(ctx.scheduler and ctx.scheduler.alive),
            "planned": len(ctx.scheduler.next_at) if ctx.scheduler else 0,
            "queue": len(ctx.scheduler.manual) if ctx.scheduler else 0,
            "enabled": ctx.cfg.scheduler_enabled,
        },
        "db": {
            "dialect": ctx.store.dialect, "last_flush": _hhmm(ctx.store.last_flush_at),
            "flushes": ctx.store.flush_count, "dirty": ctx.store.dirty_counts(),
            "flush_every_min": round(ctx.store.flush_seconds / 60, 1),
        },
        "weather": {"ok": bool(w.days), "updated": _hhmm(w.updated), "error": w.error},
        "process": {
            "rss_mb": rss_mb(), "uptime_h": round((time.time() - ctx.started_at) / 3600, 1),
            "python": platform.python_version(), "demo": ctx.cfg.demo_mode,
        },
    }
