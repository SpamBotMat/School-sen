"""Предохранитель: если сайт школы начал массово отказывать — сами ставим опрос на паузу.

Никакого обхода блокировок: при признаках бана (403/429, «доступ запрещён»,
много ошибок подряд) опрос останавливается, админу уходит сообщение. Дальше пауза
удваивается (20 → 40 → 80 мин …, максимум 4 часа), пока пробный запрос не пройдёт.
Админ может снять паузу вручную в админке.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Awaitable, Callable

from .timeutil import now

log = logging.getLogger(__name__)

BLOCK_MARKERS = ("HTTP 403", "HTTP 429", "доступ запрещ", "заблокирован", "captcha", "too many requests")


PROBE_TIMEOUT = timedelta(minutes=10)


def looks_like_block(text: str) -> bool:
    low = (text or "").lower()
    return any(m.lower() in low for m in BLOCK_MARKERS)


class Breaker:
    def __init__(self, *, streak: int = 6, cooldown_min: int = 20,
                 alert: Callable[[str], Awaitable[None]] | None = None):
        self.threshold = streak
        self.base_cooldown = cooldown_min
        self.cooldown = cooldown_min
        self.state = "closed"            # closed | open | half
        self.fail_streak = 0
        self.login_fail_users: set[int] = set()
        self.opened_at: datetime | None = None
        self.reason: str | None = None
        self.opens = 0
        self.probe_in_flight = False
        self.probe_at: datetime | None = None
        self._alert = alert
        self.pending_alerts: list[str] = []

    def configure(self, streak: int, cooldown_min: int) -> None:
        self.threshold = streak
        self.base_cooldown = cooldown_min
        if self.state == "closed":
            self.cooldown = cooldown_min

    @property
    def until(self) -> datetime | None:
        if self.opened_at is None or self.state == "closed":
            return None
        return self.opened_at + timedelta(minutes=self.cooldown)

    def allow(self) -> bool:
        if self.state == "closed":
            return True
        if self.state == "open" and self.until and now() >= self.until:
            self.state = "half"
            self.probe_in_flight = False
        if self.state == "half" and self.probe_in_flight and self.probe_at and now() - self.probe_at > PROBE_TIMEOUT:
            self.probe_in_flight = False    # проба так и не отчиталась (упала с багом) — разрешаем новую
        if self.state == "half" and not self.probe_in_flight:
            self.probe_in_flight = True     # пропускаем ровно один пробный опрос
            self.probe_at = now()
            return True
        return False

    def inconclusive(self) -> None:
        """Проба ничего не показала (например, ученик ошибся в пароле) — следующую можно сразу."""
        if self.state == "half":
            self.probe_in_flight = False

    def success(self) -> None:
        self.fail_streak = 0
        self.login_fail_users.clear()
        if self.state != "closed":
            was = self.reason
            self.state = "closed"
            self.cooldown = self.base_cooldown
            self.opened_at = None
            self.reason = None
            self.probe_in_flight = False
            self._notify(f"✅ NetSchool снова отвечает — опрос возобновлён (было: {was}).")

    def failure(self, detail: str) -> None:
        self.fail_streak += 1
        if self.state == "half":
            self.cooldown = min(self.cooldown * 2, 240)
            self._open(f"пробный запрос не прошёл: {detail}", escalate=False)
        elif looks_like_block(detail):
            self._open(f"похоже на блокировку: {detail}")
        elif self.fail_streak >= self.threshold:
            self._open(f"{self.fail_streak} ошибок подряд, последняя: {detail}")

    def login_failure(self, uid: int) -> None:
        """Ошибка входа у разных учеников подряд — подозрительно (сайт поменялся или режет вход)."""
        self.inconclusive()
        self.login_fail_users.add(uid)
        if len(self.login_fail_users) >= max(4, self.threshold - 1) and self.state == "closed":
            self._open(f"{len(self.login_fail_users)} учеников подряд не смогли войти — проверь сайт школы")

    def reset(self) -> None:
        self.state = "closed"
        self.fail_streak = 0
        self.login_fail_users.clear()
        self.cooldown = self.base_cooldown
        self.opened_at = None
        self.reason = None
        self.probe_in_flight = False

    def _open(self, reason: str, escalate: bool = True) -> None:
        first = self.state == "closed"
        self.state = "open"
        self.opened_at = now()
        self.reason = reason[:300]
        self.probe_in_flight = False
        self.opens += 1
        log.warning("Предохранитель: опрос на паузе (%s мин): %s", self.cooldown, reason)
        if first:
            self._notify(f"⛔️ Опрос NetSchool на автопаузе ({self.cooldown} мин).\nПричина: {reason}\n\n"
                         "Обходить блокировки бот не будет. Если это бан — свяжись со школой. "
                         "Снять паузу: админка → Обзор.")

    def _notify(self, text: str) -> None:
        self.pending_alerts.append(text)

    def as_dict(self) -> dict:
        return {"state": self.state, "fail_streak": self.fail_streak, "reason": self.reason,
                "until": self.until.isoformat() if self.until else None, "cooldown": self.cooldown,
                "opens": self.opens}
