"""Клавиатуры бота."""
from __future__ import annotations

from aiogram.types import (InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup,
                           WebAppInfo)

BTN_TODAY, BTN_TOMORROW = "📅 Сегодня", "➡️ Завтра"
BTN_HW, BTN_MARKS = "📚 ДЗ", "⭐ Оценки"
BTN_ARCHIVE, BTN_MORE = "📦 Архив", "☰ Ещё"
MENU_BUTTONS = frozenset({BTN_TODAY, BTN_TOMORROW, BTN_HW, BTN_MARKS, BTN_ARCHIVE, BTN_MORE})


def is_menu(text: str | None) -> bool:
    """Нажата кнопка меню или команда — значит, ученик передумал вводить текст."""
    return bool(text) and (text.startswith("/") or text in MENU_BUTTONS)


def main_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=BTN_TODAY), KeyboardButton(text=BTN_TOMORROW)],
                  [KeyboardButton(text=BTN_HW), KeyboardButton(text=BTN_MARKS)],
                  [KeyboardButton(text=BTN_ARCHIVE), KeyboardButton(text=BTN_MORE)]],
        resize_keyboard=True, is_persistent=True, input_field_placeholder="Что глянем?")


def ib(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def app_button(ctx, text: str = "🚀 Открыть приложение", screen: str | None = None) -> InlineKeyboardButton | None:
    url = ctx.cfg.webapp_url
    if not url:
        return None
    if screen:
        url += f"?s={screen}"        # не #: в хэш Telegram кладёт свои параметры (tgWebAppData)
    return InlineKeyboardButton(text=text, web_app=WebAppInfo(url=url))


def open_app_kb(ctx, screen: str | None = None, text: str = "🚀 Открыть в приложении") -> InlineKeyboardMarkup | None:
    b = app_button(ctx, text, screen)
    return InlineKeyboardMarkup(inline_keyboard=[[b]]) if b else None


def cheatsheet_kb(ctx) -> InlineKeyboardMarkup | None:
    rows = [[ib("🔄 Обновить", "cs:refresh"), ib("📦 Архив", "arc:menu")]]
    b = app_button(ctx, "🚀 Приложение")
    if b:
        rows.append([b])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def consent_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[ib("✅ Погнали", "consent:yes")],
                                                 [ib("🔒 Как храню данные", "privacy")]])


def more_kb(ctx) -> InlineKeyboardMarkup:
    rows = [
        [ib("⚠️ Долги", "m:debts"), ib("🏁 Итоговые", "m:totals")],
        [ib("🏖 Каникулы", "m:holidays"), ib("🎂 Именинники", "m:birthdays")],
        [ib("📢 Объявления", "m:news"), ib("🔄 Обновить сейчас", "m:sync")],
        [ib("⚙️ Настройки", "set:open"), ib("✉️ Написать админу", "m:feedback")],
        [ib("❓ Помощь", "m:help")],
    ]
    calc = app_button(ctx, "🧮 Калькулятор оценок", "calc")
    if calc:
        rows.insert(2, [calc])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def archive_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [ib("⭐ Оценки", "arc:marks"), ib("🚪 Посещаемость", "arc:attendance")],
        [ib("📚 ДЗ на неделю", "arc:hw"), ib("🗓 Расписание", "arc:schedule")],
        [ib("📌 Обновить шпаргалку", "cs:refresh")],
    ])


def mark(on: bool) -> str:
    return "✅" if on else "▫️"


def settings_kb(ctx, u) -> InlineKeyboardMarkup:
    from ..timeutil import now
    o = u.opt
    paused = bool(u.paused_until and u.paused_until > now())
    rows = [
        [ib(f"☀️ Утро {o('morning_time')} {mark(o('morning'))}", "set:time:morning")],
        [ib(f"🌙 Вечер {o('evening_time')} {mark(o('evening'))}", "set:time:evening")],
        [ib(f"⭐ Оценки {mark(o('n_marks'))}", "set:t:n_marks"), ib(f"📚 ДЗ сразу {mark(o('hw_instant'))}", "set:t:hw_instant")],
        [ib(f"🚪 Пропуски {mark(o('n_attendance'))}", "set:t:n_attendance"), ib(f"⚠️ Долги {mark(o('n_debts'))}", "set:t:n_debts")],
        [ib(f"📢 Объявления {mark(o('n_news'))}", "set:t:n_news"), ib(f"🏁 Итоговые {mark(o('n_totals'))}", "set:t:n_totals")],
        [ib(f"😴 Тихо {o('quiet_from')}–{o('quiet_to')} {mark(o('quiet'))}", "set:t:quiet"),
         ib(f"💬 Реакции {mark(o('reactions'))}", "set:t:reactions")],
        [ib(f"📌 Шпаргалка {mark(o('cheatsheet'))}", "set:t:cheatsheet"),
         ib(f"📦 Архив по вс {mark(o('weekly_archive'))}", "set:t:weekly_archive")],
        [ib(f"🌤 Погода утром {mark(o('weather'))}", "set:t:weather"), ib("👤 Как звать", "set:name")],
        [ib("▶️ Снять паузу" if paused else "⏸ Пауза на 2 часа", "set:pause")],
        [ib("🔑 Сменить аккаунт", "set:relogin"), ib("🚪 Отключить", "set:logout")],
        [ib("🗑 Удалить мои данные", "set:delete")],
    ]
    b = app_button(ctx, "🎨 Темы и вид — в приложении", "settings")
    if b:
        rows.append([b])
    return InlineKeyboardMarkup(inline_keyboard=rows)


MORNING_TIMES = ["06:30", "06:45", "07:00", "07:15", "07:30", "07:45", "08:00", "08:15", "08:30"]
EVENING_TIMES = ["17:00", "18:00", "18:30", "19:00", "19:30", "20:00", "20:30", "21:00", "21:30"]


def time_kb(which: str, current: str, enabled: bool) -> InlineKeyboardMarkup:
    times = MORNING_TIMES if which == "morning" else EVENING_TIMES
    rows, row = [], []
    for t in times:
        row.append(ib(("• " if t == current else "") + t, f"set:at:{which}:{t}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([ib("✏️ Своё время", f"set:custom:{which}"),
                 ib("🔕 Выключить" if enabled else "🔔 Включить", f"set:t:{which}")])
    rows.append([ib("← Назад", "set:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirm_kb(action: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[ib("Да, точно", f"{action}:yes"), ib("Отмена", "set:main")]])


def hw_files_kb(items: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[ib(f"📎 {name[:40]}", data)] for name, data in items[:8]])


def feedback_admin_kb(fid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[ib("✉️ Ответить", f"fb:reply:{fid}"), ib("✔️ Закрыть", f"fb:close:{fid}")]])


def breaker_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[ib("▶️ Снять паузу сейчас", "adm:resume")]])


def cancel_kb(data: str = "cancel") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[ib("Отмена", data)]])
