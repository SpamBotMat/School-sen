"""Генерирует CREDENTIALS_KEY (Fernet) и TICK_KEY. Запуск: python tools/genkey.py"""
import base64
import os
import secrets

print("CREDENTIALS_KEY=" + base64.urlsafe_b64encode(os.urandom(32)).decode())
print("TICK_KEY=" + secrets.token_urlsafe(24))
