"""Общий контекст приложения: все сервисы в одном месте (создаётся при старте)."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .breaker import Breaker
from .config import Config
from .crypto import Vault
from .phrases import Phrases
from .ratelimit import RateLimits
from .school import SchoolCalendar
from .store import Store
from .weather import WeatherService


@dataclass
class AppContext:
    cfg: Config
    store: Store
    vault: Vault
    phrases: Phrases
    calendar: SchoolCalendar
    weather: WeatherService
    breaker: Breaker
    limits: RateLimits
    client_factory: Callable[[], Any]
    bot: Any = None
    dp: Any = None
    bot_username: str | None = None
    notifier: Any = None
    sync: Any = None
    scheduler: Any = None
    admin_auth: Any = None
    started_at: float = field(default_factory=time.time)

    def is_admin(self, uid: int | None) -> bool:
        return uid is not None and uid in self.cfg.admin_ids


_ctx: AppContext | None = None


def get_ctx() -> AppContext:
    if _ctx is None:
        raise RuntimeError("контекст ещё не создан")
    return _ctx


def set_ctx(ctx: AppContext | None) -> None:
    global _ctx
    _ctx = ctx


def build_context(cfg: Config, store: Store, *, bot: Any = None, client_factory: Callable[[], Any] | None = None,
                  ) -> AppContext:
    from .notifier import Notifier
    from .scheduler import Scheduler
    from .security import AdminAuth
    from .sync import SyncService

    if client_factory is None:
        if cfg.demo_mode:
            from .demo import DemoNetSchool
            client_factory = DemoNetSchool
        else:
            from netschool import NetSchoolClient

            def client_factory() -> Any:   # noqa: F811
                return NetSchoolClient(cfg.netschool_url, min_interval=0.6, timeout=40)
    rt = store.runtime()
    ctx = AppContext(
        cfg=cfg, store=store, vault=Vault(cfg.credentials_key), phrases=Phrases(store),
        calendar=SchoolCalendar(store), weather=WeatherService(cfg.weather_lat, cfg.weather_lon, cfg.city_name),
        breaker=Breaker(streak=int(rt["breaker_streak"]), cooldown_min=int(rt["breaker_cooldown"])),
        limits=RateLimits(), client_factory=client_factory, bot=bot,
    )
    ctx.notifier = Notifier(ctx)
    ctx.sync = SyncService(ctx)
    ctx.scheduler = Scheduler(ctx)
    ctx.admin_auth = AdminAuth()
    set_ctx(ctx)
    return ctx
