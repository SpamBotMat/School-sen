"""Настройки из переменных окружения (.env для локального запуска).

Всё, что админ может менять «на лету» (интервал опроса, тихие часы и т.п.), живёт
не здесь, а в app/runtime.py (хранится в БД и правится из админки).
"""
from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
log = logging.getLogger(__name__)


def _load_dotenv(path: Path) -> None:
    """Мини-загрузчик .env (без зависимостей). Уже заданные переменные не трогает."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)


def _bool(name: str, default: bool = False) -> bool:
    v = os.environ.get(name)
    if v is None or v == "":
        return default
    return v.strip().lower() in ("1", "true", "yes", "on", "да")


def _int_list(raw: str) -> tuple[int, ...]:
    out = []
    for part in raw.replace(";", ",").replace(" ", ",").split(","):
        part = part.strip()
        if part.lstrip("-").isdigit():
            out.append(int(part))
    return tuple(out)


@dataclass(frozen=True)
class Config:
    env: str = "prod"                      # prod | dev | test
    bot_token: str = ""
    public_url: str = ""                   # https://<сервис>.onrender.com (без / в конце)
    webhook_secret: str = ""
    database_url: str = ""
    credentials_key: str = ""              # Fernet-ключ (tools/genkey.py)
    admin_ids: tuple[int, ...] = ()
    tick_key: str = ""
    netschool_url: str = "http://school.trsosh.edu.tm"
    school_name: str = "СТРСОШ им. А.С. Пушкина"
    city_name: str = "Ашхабад"
    weather_lat: float = 37.95
    weather_lon: float = 58.38
    tz_name: str = "Asia/Ashgabat"
    demo_mode: bool = False                # фейковый NetSchool и данные — для разработки и скриншотов
    dev_mode: bool = False                 # разрешает ?dev_user= в API Mini App (НИКОГДА не включай на проде)
    use_polling: bool = False              # локально без вебхука: бот через long polling
    scheduler_enabled: bool = True
    db_flush_seconds: int = 600            # как часто сбрасывать изменения в БД (Neon спит, пока мы молчим)
    log_level: str = "INFO"
    data_dir: Path = field(default=ROOT / "data")

    @property
    def is_test(self) -> bool:
        return self.env == "test"

    @property
    def webapp_url(self) -> str:
        return f"{self.public_url}/app/" if self.public_url else ""

    @property
    def admin_url(self) -> str:
        return f"{self.public_url}/admin/" if self.public_url else ""

    @property
    def secure_cookies(self) -> bool:
        return self.public_url.startswith("https://")

    def problems(self) -> list[str]:
        """Что не так с настройками (показываем в логах при старте)."""
        out = []
        if not self.bot_token and not self.demo_mode:
            out.append("BOT_TOKEN не задан — бот работать не будет")
        if not self.credentials_key:
            out.append("CREDENTIALS_KEY не задан — пароли некуда шифровать (сгенерирован временный ключ: "
                       "после перезапуска все входы слетят!)")
        if not self.database_url and self.env == "prod":
            out.append("DATABASE_URL не задан — используется локальная SQLite (на Render она стирается)")
        if not self.admin_ids:
            out.append("ADMIN_IDS не задан — админки не будет")
        if os.environ.get("DEV_MODE", "").strip() not in ("", "0") and self.env == "prod":
            out.append("DEV_MODE на проде игнорируется — убери переменную")
        return out


def load_config() -> Config:
    _load_dotenv(ROOT / ".env")
    env = os.environ.get("APP_ENV", "prod").strip().lower() or "prod"
    token = os.environ.get("BOT_TOKEN", "").strip()
    public = (os.environ.get("PUBLIC_URL") or os.environ.get("RENDER_EXTERNAL_URL") or "").strip().rstrip("/")
    secret = os.environ.get("WEBHOOK_SECRET", "").strip()
    if not secret and token:
        # детерминированный секрет из токена — чтобы не было лишней переменной
        secret = hashlib.sha256(("webhook:" + token).encode()).hexdigest()[:40]
    data_dir = Path(os.environ.get("DATA_DIR") or ROOT / "data")
    return Config(
        env=env,
        bot_token=token,
        public_url=public,
        webhook_secret=secret,
        database_url=os.environ.get("DATABASE_URL", "").strip(),
        credentials_key=os.environ.get("CREDENTIALS_KEY", "").strip(),
        admin_ids=_int_list(os.environ.get("ADMIN_IDS", "")),
        tick_key=os.environ.get("TICK_KEY", "").strip(),
        netschool_url=os.environ.get("NETSCHOOL_URL", "http://school.trsosh.edu.tm").strip().rstrip("/"),
        school_name=os.environ.get("SCHOOL_NAME", "СТРСОШ им. А.С. Пушкина").strip(),
        city_name=os.environ.get("CITY_NAME", "Ашхабад").strip(),
        weather_lat=float(os.environ.get("WEATHER_LAT", "37.95")),
        weather_lon=float(os.environ.get("WEATHER_LON", "58.38")),
        demo_mode=_bool("DEMO_MODE"),
        dev_mode=_bool("DEV_MODE") and env != "prod",     # на проде — никогда, даже если включили по ошибке
        use_polling=_bool("USE_POLLING"),
        scheduler_enabled=_bool("SCHEDULER_ENABLED", env != "test"),
        db_flush_seconds=max(30, int(os.environ.get("DB_FLUSH_SECONDS", "600"))),
        log_level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        data_dir=data_dir,
    )


_config: Config | None = None


def get_config() -> Config:
    global _config
    if _config is None:
        _config = load_config()
    return _config


def set_config(cfg: Config) -> None:
    """Для тестов."""
    global _config
    _config = cfg
