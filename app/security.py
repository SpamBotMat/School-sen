"""Проверка initData Mini App, одноразовые коды и сессии админки."""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass, field
from urllib.parse import parse_qsl


# ───────────────────────────── Mini App initData ─────────────────────────────

def validate_init_data(init_data: str, bot_token: str, *, max_age: int = 7 * 86400) -> dict | None:
    """Проверка подписи Telegram (HMAC-SHA256 от «WebAppData»+токен). Возвращает user-dict или None."""
    if not init_data or not bot_token:
        return None
    try:
        pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        return None
    data = dict(pairs)
    received = data.pop("hash", None)
    if not received:
        return None
    check = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received):
        return None
    try:
        auth_date = int(data.get("auth_date", "0"))
    except ValueError:
        return None
    if max_age and time.time() - auth_date > max_age:
        return None
    try:
        user = json.loads(data.get("user") or "{}")
    except ValueError:
        return None
    if not isinstance(user, dict) or not isinstance(user.get("id"), int):
        return None
    user["_start_param"] = data.get("start_param")
    return user


def sign_init_data(user: dict, bot_token: str, auth_date: int | None = None) -> str:
    """Для тестов: собрать валидный initData."""
    from urllib.parse import urlencode
    data = {"auth_date": str(auth_date or int(time.time())), "query_id": "AAH-test",
            "user": json.dumps(user, separators=(",", ":"), ensure_ascii=False)}
    check = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    data["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(data)


# ───────────────────────────── админка ─────────────────────────────

def _h(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


@dataclass
class _Code:
    digest: str
    expires: float
    attempts: int = 0


@dataclass
class AdminSession:
    admin_id: int
    csrf: str
    created: float
    expires: float
    ip: str = ""
    meta: dict = field(default_factory=dict)


class AdminAuth:
    CODE_TTL = 300
    CODE_ATTEMPTS = 5
    SESSION_TTL = 12 * 3600

    def __init__(self) -> None:
        self._codes: dict[int, _Code] = {}
        self._last_sent: dict[int, float] = {}
        self._sessions: dict[str, AdminSession] = {}
        self._fails: dict[str, list[float]] = {}

    def can_send(self, admin_id: int) -> int:
        """0 — можно слать код, иначе сколько секунд подождать."""
        last = self._last_sent.get(admin_id, 0)
        left = int(60 - (time.time() - last))
        return max(0, left)

    def issue(self, admin_id: int) -> str:
        code = f"{secrets.randbelow(10 ** 6):06d}"
        self._codes[admin_id] = _Code(_h(f"{admin_id}:{code}"), time.time() + self.CODE_TTL)
        self._last_sent[admin_id] = time.time()
        return code

    def ip_blocked(self, ip: str) -> bool:
        fails = [t for t in self._fails.get(ip, []) if t > time.time() - 900]
        self._fails[ip] = fails
        return len(fails) >= 20

    def fail(self, ip: str) -> None:
        self._fails.setdefault(ip, []).append(time.time())

    def verify(self, admin_id: int, code: str, ip: str = "") -> tuple[str, AdminSession] | None:
        """Верный код → (токен сессии для cookie, сессия). Код одноразовый, 5 попыток."""
        c = self._codes.get(admin_id)
        ok = False
        if c is not None and time.time() <= c.expires and c.attempts < self.CODE_ATTEMPTS:
            c.attempts += 1
            ok = hmac.compare_digest(c.digest, _h(f"{admin_id}:{(code or '').strip()}"))
        if not ok:
            self.fail(ip)
            if c is not None and c.attempts >= self.CODE_ATTEMPTS:
                self._codes.pop(admin_id, None)
            return None
        self._codes.pop(admin_id, None)
        token = secrets.token_urlsafe(32)
        s = AdminSession(admin_id=admin_id, csrf=secrets.token_urlsafe(24), created=time.time(),
                         expires=time.time() + self.SESSION_TTL, ip=ip)
        self._sessions[_h(token)] = s
        return token, s

    def session(self, token: str | None) -> AdminSession | None:
        if not token:
            return None
        s = self._sessions.get(_h(token))
        if s is None:
            return None
        if time.time() > s.expires:
            self._sessions.pop(_h(token), None)
            return None
        return s

    def revoke(self, token: str | None) -> None:
        if token:
            self._sessions.pop(_h(token), None)

    def revoke_all(self, admin_id: int | None = None) -> None:
        for k in [k for k, s in self._sessions.items() if admin_id is None or s.admin_id == admin_id]:
            self._sessions.pop(k, None)

    def active(self) -> int:
        now_ = time.time()
        return sum(1 for s in self._sessions.values() if s.expires > now_)
