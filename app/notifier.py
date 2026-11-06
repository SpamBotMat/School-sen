"""Отправка сообщений: лимит скорости Telegram, тихие часы, «бот заблокирован», закреп «Шпаргалки»."""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import Any

from aiogram.exceptions import (TelegramAPIError, TelegramBadRequest, TelegramForbiddenError,
                                TelegramNetworkError, TelegramNotFound, TelegramRetryAfter)
from aiogram.types import BufferedInputFile, InputMediaPhoto, LinkPreviewOptions

from .store import digest
from .timeutil import in_window, now, parse_hhmm

log = logging.getLogger(__name__)
NO_PREVIEW = LinkPreviewOptions(is_disabled=True)


class Bucket:
    """Не больше `rate` отправок в секунду на весь бот (у Telegram лимит ~30/с)."""

    def __init__(self, rate: int = 25):
        self.rate = rate
        self.sent: deque[float] = deque()
        self.lock = asyncio.Lock()

    async def wait(self) -> None:
        async with self.lock:
            while True:
                t = time.monotonic()
                while self.sent and self.sent[0] <= t - 1.0:
                    self.sent.popleft()
                if len(self.sent) < self.rate:
                    self.sent.append(t)
                    return
                await asyncio.sleep(self.sent[0] + 1.0 - t + 0.01)


class Notifier:
    def __init__(self, ctx):
        self.ctx = ctx
        self.bucket = Bucket()

    @property
    def bot(self):
        return self.ctx.bot

    # ───────────────────────── базовая отправка ─────────────────────────

    async def _call(self, u, fn, *args: Any, **kwargs: Any) -> Any:
        if self.bot is None:
            return None
        for attempt in range(3):
            await self.bucket.wait()
            try:
                return await fn(*args, **kwargs)
            except TelegramRetryAfter as e:
                await asyncio.sleep(min(e.retry_after, 30) + 0.5)
            except TelegramForbiddenError:
                self._blocked(u)
                return None
            except TelegramNotFound:
                self._blocked(u)
                return None
            except TelegramBadRequest as e:
                msg = str(e).lower()
                if "chat not found" in msg or "user is deactivated" in msg:
                    self._blocked(u)
                    return None
                raise
            except TelegramNetworkError:
                if attempt == 2:
                    raise
                await asyncio.sleep(1 + attempt * 2)
        return None

    def _blocked(self, u) -> None:
        user = self.ctx.store.get_user(u if isinstance(u, int) else u.id)
        if user and user.status != "bot_blocked":
            user.meta["status_before_block"] = user.status
            user.status = "bot_blocked"
            self.ctx.store.save_user(user)
            self.ctx.store.event("bot_blocked", "ученик заблокировал бота", user_id=user.id)

    async def send(self, u, text: str, *, markup=None, silent: bool = False) -> Any:
        uid = u if isinstance(u, int) else u.id
        try:
            msg = await self._call(u, self.bot.send_message, uid, text, reply_markup=markup,
                                   disable_notification=silent, link_preview_options=NO_PREVIEW) if self.bot else None
        except TelegramAPIError as e:
            log.warning("send %s: %s", uid, e)
            self.ctx.store.event("send_error", str(e)[:200], user_id=uid, level="warn")
            return None
        if msg is not None:
            self.ctx.store.stat("messages_out")
        return msg

    # ───────────────────────── тихие часы ─────────────────────────

    def quiet_now(self, u) -> bool:
        if not u.opt("quiet"):
            return False
        start = parse_hhmm(u.opt("quiet_from"))
        end = parse_hhmm(u.opt("quiet_to"))
        return bool(start and end and in_window(now().time(), start, end))

    async def notify(self, u, text: str, *, markup=None) -> bool:
        """Уведомление: в тихие часы откладываем до утра (одним сообщением)."""
        if self.quiet_now(u):
            u.pending = (u.pending or [])[-19:] + [text]
            self.ctx.store.save_user(u)
            return False
        await self.send(u, text, markup=markup)
        self.ctx.store.stat("notifications")
        return True

    def take_pending(self, u) -> list[str]:
        items, u.pending = list(u.pending or []), []
        if items:
            self.ctx.store.save_user(u)
        return items

    # ───────────────────────── медиа ─────────────────────────

    async def send_photo(self, u, png: bytes | str, *, filename: str = "image.png", caption: str | None = None,
                         markup=None) -> str | None:
        """Отправляет картинку; возвращает file_id (чтобы потом слать повторно без загрузки)."""
        photo = png if isinstance(png, str) else BufferedInputFile(png, filename=filename)
        try:
            msg = await self._call(u, self.bot.send_photo, u if isinstance(u, int) else u.id, photo,
                                   caption=caption, reply_markup=markup) if self.bot else None
        except TelegramAPIError as e:
            log.warning("send_photo: %s", e)
            return None
        if msg is None or not getattr(msg, "photo", None):
            return None
        return msg.photo[-1].file_id

    async def send_album(self, u, images: list[tuple[str, bytes | str]], caption: str | None = None) -> list[str]:
        """До 10 картинок одним альбомом. images: [(имя, png-байты или file_id)]."""
        if not images or self.bot is None:
            return []
        if len(images) == 1:
            fid = await self.send_photo(u, images[0][1], filename=images[0][0], caption=caption)
            return [fid] if fid else []
        media = []
        for i, (name, data) in enumerate(images[:10]):
            src = data if isinstance(data, str) else BufferedInputFile(data, filename=name)
            media.append(InputMediaPhoto(media=src, caption=caption if i == 0 else None))
        try:
            msgs = await self._call(u, self.bot.send_media_group, u if isinstance(u, int) else u.id, media)
        except TelegramAPIError as e:
            log.warning("send_album: %s", e)
            return []
        out = []
        for m in msgs or []:
            if getattr(m, "photo", None):
                out.append(m.photo[-1].file_id)
        return out

    async def send_document(self, u, data: bytes | str, filename: str, caption: str | None = None) -> str | None:
        doc = data if isinstance(data, str) else BufferedInputFile(data, filename=filename)
        try:
            msg = await self._call(u, self.bot.send_document, u if isinstance(u, int) else u.id, doc,
                                   caption=caption) if self.bot else None
        except TelegramAPIError as e:
            log.warning("send_document: %s", e)
            return None
        if msg is None or not getattr(msg, "document", None):
            return None
        return msg.document.file_id

    # ───────────────────────── «Шпаргалка» в закрепе ─────────────────────────

    async def update_cheatsheet(self, u, text: str, *, force: bool = False) -> bool:
        """Редактирует закреплённое сообщение; если его нет — шлёт новое и закрепляет."""
        if self.bot is None or not u.opt("cheatsheet"):
            return False
        body = text.split("\n", 1)[1] if "\n" in text else text      # без строки со временем
        h = digest(body)
        if not force and u.pinned_hash == h and u.pinned_message_id:
            return False
        from .bot.keyboards import cheatsheet_kb
        kb = cheatsheet_kb(self.ctx)
        if u.pinned_message_id:
            try:
                await self._call(u, self.bot.edit_message_text, text=text, chat_id=u.id,
                                 message_id=u.pinned_message_id, reply_markup=kb, link_preview_options=NO_PREVIEW)
                u.pinned_hash = h
                self.ctx.store.save_user(u)
                return True
            except TelegramBadRequest as e:
                if "not modified" in str(e).lower():
                    u.pinned_hash = h
                    self.ctx.store.save_user(u)
                    return False
                log.info("Шпаргалка %s: %s — отправлю заново", u.id, e)
            except TelegramAPIError as e:
                log.warning("Шпаргалка %s: %s", u.id, e)
                return False
        msg = await self.send(u, text, markup=kb, silent=True)
        if msg is None:
            return False
        try:
            await self._call(u, self.bot.pin_chat_message, chat_id=u.id, message_id=msg.message_id,
                             disable_notification=True)
        except TelegramAPIError as e:
            log.info("Не удалось закрепить: %s", e)
        u.pinned_message_id = msg.message_id
        u.pinned_hash = h
        self.ctx.store.save_user(u)
        return True

    async def unpin_cheatsheet(self, u) -> None:
        if self.bot is None or not u.pinned_message_id:
            return
        try:
            await self._call(u, self.bot.unpin_chat_message, chat_id=u.id, message_id=u.pinned_message_id)
        except TelegramAPIError:
            pass
        u.pinned_message_id = None
        u.pinned_hash = None
        self.ctx.store.save_user(u)

    # ───────────────────────── админам ─────────────────────────

    async def admins(self, text: str, *, html_ok: bool = False, markup=None) -> None:
        import html as _html
        body = text if html_ok else _html.escape(text, quote=False)
        for aid in self.ctx.cfg.admin_ids:
            try:
                if self.bot is not None:
                    await self.bucket.wait()
                    await self.bot.send_message(aid, body, reply_markup=markup, link_preview_options=NO_PREVIEW)
            except TelegramAPIError as e:
                log.warning("Админу %s не ушло: %s", aid, e)
