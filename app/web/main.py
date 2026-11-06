"""Веб-сервис: вебхук Telegram, API Mini App и админки, статика, /tick для пингов и /health."""
from __future__ import annotations

import asyncio
import hashlib
import os
import hmac
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from aiogram.types import Update
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.middleware.gzip import GZipMiddleware
from starlette.staticfiles import StaticFiles

from .. import __version__
from ..config import Config, get_config
from ..context import build_context, set_ctx
from ..db import make_engine
from ..logs import setup_logging
from ..store import Store
from ..tasks import background, drain
from . import admin_api, api

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "static"

CSP_APP = ("default-src 'self'; script-src 'self' https://telegram.org; style-src 'self' 'unsafe-inline'; "
           "img-src 'self' data: blob:; font-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'none'; "
           "frame-ancestors 'self' https://web.telegram.org https://*.telegram.org")
CSP_ADMIN = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
             "font-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")


def asset_version() -> str:
    h = hashlib.sha1(__version__.encode())
    for p in sorted(STATIC.rglob("*")):
        if p.is_file() and p.suffix in (".js", ".css", ".svg", ".png", ".woff2"):
            h.update(p.read_bytes())
    return h.hexdigest()[:10]


def _page(path: Path, version: str) -> str:
    try:
        return path.read_text(encoding="utf-8").replace("__V__", version)
    except OSError:
        return "<!doctype html><meta charset=utf-8><title>School Sensor</title><p>Нет файлов интерфейса."


@asynccontextmanager
async def lifespan(app: FastAPI):
    from ..bot.setup import configure_telegram, make_bot, make_dispatcher
    from ..seed_phrases import SEED
    cfg: Config = app.state.cfg
    if not app.state.quiet_logs:
        setup_logging(cfg)
    for p in cfg.problems():
        log.warning("Настройки: %s", p)
    engine = make_engine(cfg.database_url, cfg.data_dir)
    store = Store(engine, cfg.db_flush_seconds)
    await store.load()
    if not store.templates:
        n = await store.add_templates(SEED)
        log.info("Заготовки фраз: добавлено %d стартовых", n)
    bot = app.state.bot_override
    if bot is None and cfg.bot_token:
        bot = make_bot(cfg.bot_token)
    ctx = build_context(cfg, store, bot=bot, client_factory=app.state.client_factory)
    if bot is not None:
        ctx.dp = make_dispatcher()
    app.state.ctx = ctx
    store.start()
    polling = None
    if bot is not None and app.state.telegram_setup:
        try:
            await configure_telegram(ctx, webhook=not cfg.use_polling)
        except Exception:  # noqa: BLE001
            log.exception("Telegram: настройка не удалась (проверь BOT_TOKEN) — продолжаю без неё")
        if cfg.use_polling:
            try:
                await bot.delete_webhook(drop_pending_updates=False)
                polling = asyncio.get_running_loop().create_task(
                    ctx.dp.start_polling(bot, handle_signals=False, close_bot_session=False))
                log.info("Бот: режим long polling")
            except Exception:  # noqa: BLE001
                log.exception("Не удалось запустить polling")
    if cfg.demo_mode and os.environ.get("FAKE_NOW"):
        from datetime import datetime
        from ..timeutil import set_fake_now
        set_fake_now(datetime.fromisoformat(os.environ["FAKE_NOW"]))     # только для скриншотов демо
    if cfg.demo_mode:
        from ..demo import seed_demo
        try:
            await seed_demo(ctx)
        except Exception:  # noqa: BLE001
            log.exception("Демо-данные не загрузились")
    if cfg.scheduler_enabled:
        ctx.scheduler.start()
    if not cfg.is_test:
        background(ctx.weather.refresh())
    store.event("startup", f"запуск v{__version__}")
    log.info("School Sensor %s запущен: %d учеников", __version__, len(store.users))
    try:
        yield
    finally:
        log.info("Останавливаюсь…")
        await ctx.scheduler.stop()
        if polling is not None:
            try:
                await ctx.dp.stop_polling()
            except (RuntimeError, LookupError):
                pass
            polling.cancel()
        await drain(8)
        await store.close()
        if bot is not None and app.state.bot_override is None:
            await bot.session.close()
        await engine.dispose()
        set_ctx(None)
        app.state.ctx = None


def create_app(cfg: Config | None = None, *, bot=None, client_factory=None, telegram_setup: bool = True,
               quiet_logs: bool = False) -> FastAPI:
    from ..bot.setup import secret_token, webhook_path
    cfg = cfg or get_config()
    app = FastAPI(title="School Sensor", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.cfg = cfg
    app.state.ctx = None
    app.state.bot_override = bot
    app.state.client_factory = client_factory
    app.state.telegram_setup = telegram_setup
    app.state.quiet_logs = quiet_logs
    version = asset_version()
    pages = {"app": _page(STATIC / "app" / "index.html", version),
             "admin": _page(STATIC / "admin" / "index.html", version),
             "sw": _page(STATIC / "app" / "sw.js", version)}
    secret = secret_token(cfg)

    app.add_middleware(GZipMiddleware, minimum_size=700)

    @app.middleware("http")
    async def headers(request: Request, call_next):
        resp = await call_next(request)
        p = request.url.path
        h = resp.headers
        h.setdefault("X-Content-Type-Options", "nosniff")
        h.setdefault("Referrer-Policy", "no-referrer")
        h.setdefault("X-Robots-Tag", "noindex, nofollow")
        if p.startswith("/api/") or p.startswith("/admin/api/"):
            h["Cache-Control"] = "no-store"
        elif p.startswith("/app"):
            h.setdefault("Content-Security-Policy", CSP_APP)
            long = request.url.query.startswith("v=") or p.endswith(".woff2")
            h.setdefault("Cache-Control", "public, max-age=2592000, immutable" if long else "no-cache")
        elif p.startswith("/admin"):
            h.setdefault("Content-Security-Policy", CSP_ADMIN)
            h["X-Frame-Options"] = "DENY"
            h.setdefault("Cache-Control", "no-cache")
        elif p.startswith("/brand/"):
            h.setdefault("Cache-Control", "public, max-age=604800")
        return resp

    app.include_router(api.router)
    app.include_router(admin_api.router)

    @app.post(webhook_path(cfg), include_in_schema=False)
    async def tg_webhook(request: Request):
        ctx = request.app.state.ctx
        if ctx is None or ctx.dp is None:
            return Response(status_code=503)
        if not hmac.compare_digest(request.headers.get("X-Telegram-Bot-Api-Secret-Token", ""), secret):
            return Response(status_code=403)
        try:
            update = Update.model_validate(await request.json(), context={"bot": ctx.bot})
        except ValueError:
            return Response(status_code=400)
        background(ctx.dp.feed_update(ctx.bot, update), name="update")
        return Response(status_code=200)

    @app.api_route("/tick", methods=["GET", "HEAD"], include_in_schema=False)
    async def tick(request: Request, key: str = ""):
        ctx = request.app.state.ctx
        if ctx is None:
            return JSONResponse({"ok": False}, status_code=503)
        restarted = False
        if ctx.cfg.scheduler_enabled and not ctx.scheduler.alive:
            ctx.scheduler.start()
            restarted = True
            ctx.store.event("watchdog", "планировщик перезапущен по /tick", level="warn")
        if cfg.tick_key and hmac.compare_digest(key, cfg.tick_key):
            return {"ok": True, "alive": ctx.scheduler.alive, "restarted": restarted, "users": len(ctx.store.users),
                    "breaker": ctx.breaker.state}
        return {"ok": True}

    @app.api_route("/health", methods=["GET", "HEAD"], include_in_schema=False)
    async def health(request: Request):
        ok = request.app.state.ctx is not None
        return JSONResponse({"ok": ok, "version": __version__}, status_code=200 if ok else 503)

    @app.get("/", include_in_schema=False)
    async def root(request: Request):
        ctx = request.app.state.ctx
        bot = ctx.bot_username if ctx else None
        link = f'<p><a href="https://t.me/{bot}">Открыть в Telegram</a></p>' if bot else ""
        return HTMLResponse("<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>"
                            "<title>School Sensor</title><body style='font:16px system-ui;padding:40px'>"
                            f"<h1>School Sensor</h1><p>Бот для школьного дневника работает 🤖</p>{link}</body>")

    @app.get("/app", include_in_schema=False)
    async def app_redirect():
        return RedirectResponse("/app/", status_code=308)

    @app.get("/app/", include_in_schema=False)
    async def app_index():
        return HTMLResponse(pages["app"], headers={"Cache-Control": "no-cache"})

    @app.get("/app/sw.js", include_in_schema=False)
    async def app_sw():
        return Response(pages["sw"], media_type="application/javascript",
                        headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/app/"})

    @app.get("/admin", include_in_schema=False)
    async def admin_redirect():
        return RedirectResponse("/admin/", status_code=308)

    @app.get("/admin/", include_in_schema=False)
    async def admin_index():
        return HTMLResponse(pages["admin"], headers={"Cache-Control": "no-cache"})

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon():
        p = STATIC / "brand" / "favicon.png"
        if p.exists():
            return Response(p.read_bytes(), media_type="image/png", headers={"Cache-Control": "public, max-age=604800"})
        return Response(status_code=404)

    for name in ("app", "admin", "brand"):
        d = STATIC / name
        d.mkdir(parents=True, exist_ok=True)
        app.mount(f"/{name}", StaticFiles(directory=d), name=f"static-{name}")
    return app
