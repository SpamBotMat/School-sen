"""Время школы: Ашхабад, UTC+5 круглый год (перехода на летнее время нет)."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

TZ = timezone(timedelta(hours=5), "Asia/Ashgabat")

WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
WEEKDAYS_SHORT = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
WEEKDAYS_ACC = ["понедельник", "вторник", "среду", "четверг", "пятницу", "субботу", "воскресенье"]
MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
              "сентября", "октября", "ноября", "декабря"]
MONTHS_SHORT = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]
MONTHS_NOM = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август",
              "сентябрь", "октябрь", "ноябрь", "декабрь"]

_fake_now: datetime | None = None


def now() -> datetime:
    """Текущее время в Ашхабаде (aware)."""
    if _fake_now is not None:
        return _fake_now
    return datetime.now(TZ)


def utcnow() -> datetime:
    return now().astimezone(timezone.utc)


def today() -> date:
    return now().date()


def set_fake_now(value: datetime | None) -> None:
    """Для тестов и демо: подменить «сейчас»."""
    global _fake_now
    if value is not None and value.tzinfo is None:
        value = value.replace(tzinfo=TZ)
    _fake_now = value


def monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


def parse_hhmm(value: str | None, default: time | None = None) -> time | None:
    if not value:
        return default
    try:
        h, m = str(value).strip().split(":")[:2]
        return time(int(h) % 24, int(m) % 60)
    except (ValueError, TypeError):
        return default


def fmt_hhmm(t: time | None) -> str:
    return t.strftime("%H:%M") if t else ""


def in_window(t: time, start: time, end: time) -> bool:
    """t ∈ [start, end); окно может переходить через полночь (22:00–07:00)."""
    if start == end:
        return False
    if start < end:
        return start <= t < end
    return t >= start or t < end


def human_date(d: date, *, weekday: bool = True, today_: date | None = None) -> str:
    """«пт, 7 ноября» / «сегодня» / «завтра»."""
    t = today_ or today()
    if d == t:
        rel = "сегодня"
    elif d == t + timedelta(days=1):
        rel = "завтра"
    elif d == t - timedelta(days=1):
        rel = "вчера"
    else:
        rel = ""
    base = f"{d.day} {MONTHS_GEN[d.month - 1]}"
    if weekday:
        base = f"{WEEKDAYS_SHORT[d.weekday()]}, {base}"
    return f"{rel} ({base})" if rel else base


def short_date(d: date) -> str:
    return f"{d.day} {MONTHS_SHORT[d.month - 1]}"


def plural(n: int, one: str, few: str, many: str) -> str:
    """plural(5, 'день', 'дня', 'дней') -> 'дней'."""
    n = abs(n)
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def as_local(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(TZ)
