"""Шифрование данных для входа в NetSchool.

Храним НЕ пароль, а md5 пароля (так его считает сам сайт в браузере) + длину,
и то и другое — зашифрованным Fernet (AES-128-CBC + HMAC). Ключ — CREDENTIALS_KEY.
Без ключа из базы ничего не достать; при утечке ключа — смени его (все войдут заново).
"""
from __future__ import annotations

import json
import logging

from cryptography.fernet import Fernet, InvalidToken

log = logging.getLogger(__name__)


class CredentialsError(Exception):
    pass


class Vault:
    def __init__(self, key: str | None):
        if not key:
            key = Fernet.generate_key().decode()
            log.warning("CREDENTIALS_KEY не задан — временный ключ (после перезапуска входы слетят)")
        try:
            self._f = Fernet(key.encode() if isinstance(key, str) else key)
        except (ValueError, TypeError) as e:
            raise CredentialsError("CREDENTIALS_KEY неверного формата — сгенерируй через tools/genkey.py") from e

    def seal(self, password_md5: str, password_len: int) -> str:
        payload = json.dumps({"m": password_md5.lower(), "l": int(password_len)}, separators=(",", ":"))
        return self._f.encrypt(payload.encode()).decode()

    def open(self, token: str) -> tuple[str, int]:
        try:
            data = json.loads(self._f.decrypt(token.encode()).decode())
            return str(data["m"]), int(data["l"])
        except (InvalidToken, ValueError, KeyError, TypeError) as e:
            raise CredentialsError("не удалось расшифровать данные входа (сменился CREDENTIALS_KEY?)") from e


def generate_key() -> str:
    return Fernet.generate_key().decode()
