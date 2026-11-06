"""Запуск: `python -m app` (сервер), `python -m app genkey` (ключ шифрования), `python -m app check` (настройки)."""
from __future__ import annotations

import os
import sys


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "serve"
    if cmd == "genkey":
        from .crypto import generate_key
        print(generate_key())
        return
    from .config import get_config
    cfg = get_config()
    if cmd == "check":
        problems = cfg.problems()
        print("Всё выглядит нормально ✅" if not problems else "\n".join("⚠️ " + p for p in problems))
        print(f"Mini App: {cfg.webapp_url or '— (нет PUBLIC_URL)'}\nАдминка: {cfg.admin_url or '—'}")
        return
    if cmd != "serve":
        print(__doc__)
        sys.exit(2)
    import uvicorn

    from .logs import setup_logging
    from .web.main import create_app
    setup_logging(cfg)
    uvicorn.run(create_app(cfg, quiet_logs=True), host=os.environ.get("HOST", "0.0.0.0"),
                port=int(os.environ.get("PORT", "8000")), log_config=None, access_log=False, proxy_headers=True,
                forwarded_allow_ips="*", timeout_graceful_shutdown=20, server_header=False)


if __name__ == "__main__":
    main()
