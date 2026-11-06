"""Чистые функции разбора страниц NetSchool 5.10 (ИрТех, классический ASP).

Ничего не скачивают: на вход строка HTML/JSON, на выход — модели из models.py.
Поэтому их легко тестировать на сохранённых страницах (tests/fixtures).
Используется встроенный парсер "html.parser" (lxml не обязателен).
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, time, timedelta
from typing import Iterable
from urllib.parse import unquote

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

from .errors import NetSchoolError
from .models import (
    Announcement, Assignment, AssignmentDetails, Attachment, Birthday, CalendarDay, DayCell,
    Debt, GenericTable, Lesson, LetterRow, MonthEvent, PageState, ParentLetter, ReportLink,
    SelectOption, StudentTotalReport, Subject, SubjectPerformance, SubjectTotals,
    TotalMarksReport, YearCalendar,
)

try:                       # lxml в 1.5–2 раза быстрее (на 500 образцах с сайта — те же результаты)
    import lxml  # noqa: F401
    PARSER = "lxml"
except ImportError:        # pragma: no cover
    PARSER = "html.parser"
NBSP = "\xa0"

RU_MONTHS_GEN = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}
RU_MONTHS_NOM = {
    "январь": 1, "февраль": 2, "март": 3, "апрель": 4, "май": 5, "июнь": 6,
    "июль": 7, "август": 8, "сентябрь": 9, "октябрь": 10, "ноябрь": 11, "декабрь": 12,
}
WEEKDAYS = {"пн": 0, "вт": 1, "ср": 2, "чт": 3, "пт": 4, "сб": 5, "вс": 6}

# Коды из легенды дневника (на случай, если легенда не найдётся на странице).
ASSIGNMENT_TYPES = {
    "А": "Практическая работа", "В": "Срезовая работа", "Д": "Домашняя работа",
    "К": "Контрольная работа", "С": "Самостоятельная работа", "Л": "Лабораторная работа",
    "П": "Проект", "Н": "Диктант", "Р": "Реферат", "О": "Ответ на уроке",
    "Ч": "Сочинение", "И": "Изложение", "З": "Зачёт", "Т": "Тестирование",
    "Сд": "Словарный диктант", "Дт": "Домашняя тетрадь",
}
# Коды посещаемости из легенды отчёта об успеваемости и посещаемости.
ATTENDANCE_CODES = {
    "УП": "Пропуск по уважительной причине", "Б": "Пропуск по болезни",
    "НП": "Пропуск по неуважительной причине", "ОТ": "Отсутствовал",
    "ОП": "Опоздал", "ОСВ": "Освобожден",
}
DEBT_SIGNS = ("·", "•")

_DATE_RE = re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{2,4})")
_MARK_RE = re.compile(r"^[1-5][+-]?$")
_SUBM_RE = re.compile(r"SubmAssign\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)")
_GROUP_RE = re.compile(r"^\d{1,2}\s*[А-ЯЁA-Z]")
_ROOM_RE = re.compile(r"\s*\[([^\]]*)\]\s*$")
_STAMP_RE = re.compile(r"Состояние на\s*(\d{1,2}\.\d{1,2}\.\d{4})\s+(\d{1,2}:\d{2}(?::\d{2})?)")
_ATTACH_RE = re.compile(r"""openAttachment\(\s*(['"])((?:\\.|(?!\1).)*)\1\s*,\s*(\d+)\s*\)""")
_UNSAFE_FILENAME_RE = re.compile(r'[\x00-\x1f<>:"/\\|?*]+')
_EVENT_RE = re.compile(r"EditEvent\(\s*(\d+)\s*,\s*(\d+)\s*\)")
_BLOCK_TAGS = {"div", "p", "li", "ul", "ol", "tr", "table", "h1", "h2", "h3", "h4", "h5", "h6"}


# ───────────────────────────── общие помощники ─────────────────────────────

def soup_of(html: str | BeautifulSoup) -> BeautifulSoup:
    return html if isinstance(html, BeautifulSoup) else BeautifulSoup(html, PARSER)


def clean(text: str | None) -> str:
    """Убирает &nbsp; и лишние пробелы."""
    if not text:
        return ""
    return re.sub(r"\s+", " ", text.replace(NBSP, " ")).strip()


def snippet(text: str, limit: int = 200) -> str:
    """Короткий кусок ответа для сообщений об ошибках (без тегов)."""
    plain = re.sub(r"<[^>]+>", " ", text or "")
    return clean(plain)[:limit]


def cell_lines(tag: Tag) -> list[str]:
    """Текст ячейки, разбитый по <br> и блочным тегам (div, p…). Пробелы/&nbsp; НЕ чистятся.

    Пустые строки в середине сохраняются (важно для выравнивания по номерам уроков).
    """
    lines = [""]

    def walk(node: Tag) -> None:
        for child in node.children:
            if isinstance(child, Comment):
                continue
            if isinstance(child, NavigableString):
                lines[-1] += str(child)
            elif isinstance(child, Tag):
                if child.name == "br":
                    lines.append("")
                elif child.name in ("script", "style"):
                    continue
                elif child.name in _BLOCK_TAGS:
                    if clean(lines[-1]):
                        lines.append("")
                    walk(child)
                    if clean(lines[-1]):
                        lines.append("")
                else:
                    walk(child)

    walk(tag)
    while lines and not clean(lines[-1]):
        lines.pop()
    return lines


def parse_attachment_links(tag: Tag) -> list[Attachment]:
    """Ссылки вида JavaScript:$.openAttachment('/doc/файл.pdf', 123)."""
    found = []
    for a in tag.find_all("a"):
        m = _ATTACH_RE.search((a.get("onclick") or "") + " " + (a.get("href") or ""))
        if m:
            url = re.sub(r"\\(.)", r"\1", m.group(2))          # JS-экранирование: \' -> '
            found.append(Attachment(name=clean(a.get_text()) or unquote(url.rsplit("/", 1)[-1]),
                                    url=url, attachment_id=int(m.group(3)), raw=str(a)))
    return found


def safe_filename(name: str | None) -> str:
    """Имя файла без пути и запрещённых символов (для сохранения на диск / отправки в Telegram)."""
    name = (name or "").replace("\\", "/").rsplit("/", 1)[-1]
    return _UNSAFE_FILENAME_RE.sub("_", name).strip(" .")[:180]


def filename_from_disposition(value: bytes | str | None) -> str | None:
    """Имя файла из Content-Disposition: filename*=UTF-8''…, filename="…" (сырые байты UTF-8 или cp1251)."""
    if not value:
        return None
    if isinstance(value, bytes):
        for encoding in ("utf-8", "cp1251", "latin-1"):
            try:
                value = value.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
    m = re.search(r"filename\*\s*=\s*([\w-]*)'[^']*'([^;\s]+)", value, re.I)
    if m:
        try:
            name = safe_filename(unquote(m.group(2).strip('"'), encoding=m.group(1) or "utf-8", errors="strict"))
            if name:
                return name
        except (LookupError, UnicodeDecodeError):
            pass
    m = re.search(r'filename\s*=\s*"((?:\\.|[^"\\])*)"', value, re.I) or re.search(r"filename\s*=\s*([^;]+)", value, re.I)
    if not m:
        return None
    name = re.sub(r"\\(.)", r"\1", m.group(1).strip())
    if re.search(r"%[0-9A-Fa-f]{2}", name):
        try:
            name = unquote(name, errors="strict")
        except UnicodeDecodeError:
            pass
    return safe_filename(name) or None


def parse_date(text: str) -> date:
    """'21.09.26' / '1.09.2026' -> date. Бросает ValueError, если даты нет."""
    m = _DATE_RE.search(text or "")
    if not m:
        raise ValueError(f"нет даты в {text!r}")
    d, mth, y = (int(g) for g in m.groups())
    if y < 100:
        y += 2000
    return date(y, mth, d)


def try_parse_date(text: str | None) -> date | None:
    try:
        return parse_date(text or "")
    except ValueError:
        return None


def format_date(d: date) -> str:
    """date -> '21.09.26' (формат, который ждёт сервер)."""
    return f"{d.day}.{d.month:02d}.{d.year % 100:02d}"


def week_monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def parse_ru_date(text: str | None) -> date | None:
    """'21 сентября 2026 г.' -> date(2026, 9, 21)."""
    m = re.search(r"(\d{1,2})\s+([а-яё]+)\s+(\d{4})", (text or "").lower())
    if m and m.group(2) in RU_MONTHS_GEN:
        return date(int(m.group(3)), RU_MONTHS_GEN[m.group(2)], int(m.group(1)))
    return try_parse_date(text)


def parse_number(text: str | None) -> float | None:
    """'3,29' -> 3.29; '' / '-' -> None."""
    t = clean(text).replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return None


def parse_int(text: str | None, default: int = 0) -> int:
    t = clean(text)
    return int(t) if t.isdigit() else default


def parse_time(text: str | None) -> time | None:
    m = re.search(r"(\d{1,2}):(\d{2})", text or "")
    return time(int(m.group(1)), int(m.group(2))) if m else None


def parse_time_range(text: str | None) -> tuple[time | None, time | None]:
    """'9:00 - 9:45' -> (time(9,0), time(9,45))."""
    times = re.findall(r"\d{1,2}:\d{2}", clean(text))
    start = parse_time(times[0]) if times else None
    end = parse_time(times[1]) if len(times) > 1 else None
    return start, end


def parse_stamp(html: str) -> datetime | None:
    """'Состояние на 27.09.2026 11:12:24' внизу отчётов."""
    m = _STAMP_RE.search(clean(html))
    if not m:
        return None
    d = parse_date(m.group(1))
    parts = [int(x) for x in m.group(2).split(":")] + [0]
    return datetime(d.year, d.month, d.day, parts[0], parts[1], parts[2])


def parse_legend(html: str | BeautifulSoup) -> dict[str, str]:
    """Легенда «Условные обозначения»: {'Д': 'Домашняя работа', 'ОТ': 'Отсутствовал'}."""
    soup = soup_of(html)
    legend: dict[str, str] = {}
    for label in soup.find_all("span", class_="legend-label"):
        code = clean(label.get_text())
        desc_tag = label.find_next_sibling("span", class_="legend-description")
        if not code or desc_tag is None:
            continue
        legend[code] = clean(desc_tag.get_text()).lstrip("—-– ").strip()
    return legend


def split_subject(text: str) -> Subject:
    """'Ин.яз./Английский язык/11В_техн./Базовый [303А Кабинет]' -> Subject.

    Группа — первая часть, похожая на класс ('11В', '11В_техн.'); всё, что
    до неё, — предмет (последний кусок пути = короткое имя как в дневнике).
    """
    text = clean(text)
    room = None
    m = _ROOM_RE.search(text)
    if m:
        room = clean(m.group(1)) or None
        text = text[:m.start()].strip()
    parts = [p.strip() for p in text.split("/") if p.strip()]
    if not parts:
        return Subject(name=text, full_name=text, room=room)
    gi = next((i for i, p in enumerate(parts) if i > 0 and _GROUP_RE.match(p)), None)
    if gi is None:
        return Subject(name=parts[-1], full_name="/".join(parts), room=room)
    return Subject(
        name=parts[gi - 1],
        full_name="/".join(parts[:gi]),
        group=parts[gi],
        level="/".join(parts[gi + 1:]) or None,
        room=room,
    )


def is_full_page(html: str) -> bool:
    """Обычная страница после входа всегда содержит форму меню MenuForm."""
    return bool(re.search(r"name\s*=\s*\"?MenuForm", html or "", re.I))


def looks_like_login_page(html: str) -> bool:
    text = html or ""
    markers = ("about/templates/loginform", "start-page.min.css", "/webapi/auth/getdata",
               "Ваш сеанс работы был завершен", "kTimeOutOccured")
    return any(m in text for m in markers)


def raise_for_json_error(text: str) -> None:
    """Отчёты и AJAX иногда отвечают JSON {message, isError} вместо HTML."""
    t = (text or "").lstrip("\ufeff \r\n\t")
    if not t.startswith("{"):
        return
    try:
        data = json.loads(t)
    except ValueError:
        return
    if isinstance(data, dict) and (data.get("isError") or data.get("message")):
        raise NetSchoolError(data.get("message") or f"errorCode={data.get('errorCode')}")


# ───────────────────────────── формы и состояние ─────────────────────────────

def serialize_form(form: Tag) -> list[tuple[str, str]]:
    """Как jQuery.serialize(): пары (имя, значение) в порядке документа.

    disabled-поля, кнопки и неотмеченные checkbox/radio пропускаются.
    """
    pairs: list[tuple[str, str]] = []
    for el in form.find_all(["input", "select", "textarea"]):
        name = el.get("name")
        if not name or el.has_attr("disabled"):
            continue
        if el.name == "input":
            typ = (el.get("type") or "text").lower()
            if typ in ("submit", "button", "image", "reset", "file"):
                continue
            if typ in ("checkbox", "radio") and not el.has_attr("checked"):
                continue
            pairs.append((name, el.get("value", "on" if typ in ("checkbox", "radio") else "")))
        elif el.name == "select":
            options = el.find_all("option")
            chosen = [o for o in options if o.has_attr("selected")]
            if not chosen and options and not el.has_attr("multiple"):
                chosen = options[:1]
            for o in chosen:
                pairs.append((name, o.get("value", clean(o.get_text()))))
        else:
            pairs.append((name, el.get_text()))
    return pairs


def parse_forms(html: str | BeautifulSoup) -> dict[str, list[tuple[str, str]]]:
    """{имя формы: [(поле, значение), ...]} для всех форм страницы."""
    soup = soup_of(html)
    forms: dict[str, list[tuple[str, str]]] = {}
    for i, form in enumerate(soup.find_all("form")):
        name = form.get("name") or form.get("id") or f"form{i}"
        forms[name] = serialize_form(form)
    return forms


def override_pairs(pairs: Iterable[tuple[str, str]], values: dict[str, object]) -> list[tuple[str, str]]:
    """Заменяет значения существующих полей и дописывает новые в конец."""
    result: list[tuple[str, str]] = []
    left = {k: "" if v is None else str(v) for k, v in values.items()}
    for name, value in pairs:
        if name in left:
            result.append((name, left.pop(name)))
        else:
            result.append((name, value))
    result.extend(left.items())
    return result


def parse_page_state(html: str | BeautifulSoup) -> PageState:
    """AT, VER, PCLID_IUP, MenuItem/TabItem текущей страницы."""
    soup = soup_of(html)

    def value(name: str, scope: Tag | None = None) -> str | None:
        tag = (scope or soup).find("input", attrs={"name": name})
        return tag.get("value") if tag is not None else None

    menu = soup.find("form", attrs={"name": "MenuForm"})
    return PageState(
        at=value("AT", menu) or value("AT"),
        ver=value("VER", menu) or value("VER"),
        pclid_iup=value("PCLID_IUP"),
        pclid=value("PCLID"),
        menu_item=value("MenuItem", menu) if menu else None,
        tab_item=value("TabItem", menu) if menu else None,
        has_menu=menu is not None,
    )


def parse_select(html: str | BeautifulSoup, name: str) -> list[SelectOption]:
    soup = soup_of(html)
    select = soup.find("select", attrs={"name": name})
    if select is None:
        return []
    return [SelectOption(value=o.get("value", ""), text=clean(o.get_text()), selected=o.has_attr("selected"))
            for o in select.find_all("option")]


def parse_weeks(html: str | BeautifulSoup, name: str = "DATE") -> list[tuple[date, date, int, bool]]:
    """Список недель из select DATE: (понедельник, воскресенье, номер, выбрана ли)."""
    weeks = []
    for opt in parse_select(html, name):
        dates = _DATE_RE.findall(opt.text)
        num = re.search(r":\s*(\d+)\s*$", opt.text)
        if len(dates) >= 2:
            start = parse_date(".".join(dates[0]))
            end = parse_date(".".join(dates[1]))
            weeks.append((start, end, int(num.group(1)) if num else 0, opt.selected))
    return weeks


def selected_week(html: str | BeautifulSoup, name: str = "DATE") -> date | None:
    for opt in parse_select(html, name):
        if opt.selected:
            return try_parse_date(opt.value)
    return None


def parse_reports_list(html: str) -> list[ReportLink]:
    links = []
    for m in re.finditer(r"GoToLink\('([^']+)'\s*,\s*'?(\d+)'?\s*,\s*'?(\d+)'?\)\"?[^>]*>([^<]+)<", html):
        links.append(ReportLink(page=m.group(1), thm_id=int(m.group(2)), rpt_id=int(m.group(3)),
                                title=clean(m.group(4))))
    # одна и та же ссылка встречается и в href, и в onclick
    unique = {(l.page, l.thm_id, l.rpt_id): l for l in links}
    return list(unique.values())


# ───────────────────────────── дневник ─────────────────────────────

def _find_table_with_header(soup: BeautifulSoup, header: str) -> Tag | None:
    for table in soup.find_all("table"):
        th = table.find("th")
        if th is not None and any(header in clean(x.get_text()) for x in table.find_all("th")):
            return table
    return None


def _date_in(tag: Tag) -> date | None:
    for a in tag.find_all("a"):
        js = (a.get("onclick") or "") + " " + (a.get("href") or "")
        m = re.search(r"dayschedule\(\s*'([\d.]+)'", js)
        if m:
            return try_parse_date(m.group(1))
    return try_parse_date(clean(tag.get_text()))


def parse_diary(html: str) -> list[Assignment]:
    """Дневник за неделю (Assignments.asp).

    Особенности вёрстки: дата есть только в первой строке дня (rowspan),
    плюс отдельные «мобильные» строки с датой; просроченные задания —
    строки bgcolor=#FFBBBB, они показываются и из прошлых недель.
    """
    soup = soup_of(html)
    legend = {**ASSIGNMENT_TYPES, **parse_legend(soup)}
    table = _find_table_with_header(soup, "Срок сдачи")
    if table is None:
        return []
    result: list[Assignment] = []
    current: date | None = None
    for tr in table.find_all("tr"):
        if tr.find("th") is not None:
            continue
        tds = tr.find_all("td", recursive=False)
        if "visible-scr-row-sm" in (tr.get("class") or []) or len(tds) < 4:
            current = _date_in(tr) or current
            continue
        if len(tds) >= 5:
            current = _date_in(tds[0]) or current
        subj_td, type_td, topic_td, mark_td = tds[-4:]
        if current is None:
            continue
        aid = cid = tp = None
        link = topic_td.find("a")
        if link is not None:
            m = _SUBM_RE.search((link.get("onclick") or "") + " " + (link.get("href") or ""))
            if m:
                aid, cid, tp = (int(x) for x in m.groups())
        code = clean(type_td.get_text())
        mark = clean(mark_td.get_text())
        result.append(Assignment(
            date=current,
            subject=clean(subj_td.get_text()),
            type_code=code,
            type_name=legend.get(code),
            title=clean(topic_td.get_text()),
            mark=None if mark in ("", "-") else mark,
            overdue=(tr.get("bgcolor") or "").upper() == "#FFBBBB",
            aid=aid, cid=cid, tp=tp,
        ))
    return result


def parse_assignment_details(payload: str | dict) -> AssignmentDetails:
    """JSON из /asp/ajax/Assignments/GetAssignmentInfo.asp."""
    data = json.loads(payload.lstrip("\ufeff")) if isinstance(payload, str) else payload
    if data.get("isError"):
        raise NetSchoolError(data.get("message") or "GetAssignmentInfo: isError")
    body = data.get("data") or {}
    title = clean(body.get("strTitle"))
    kind, subject, group, teacher = title, None, None, None
    m = re.match(r"^(?P<kind>.*?)\s+для\s+ученика:\s*(?P<subj>.*?)\s*(?:\((?P<teacher>[^()]*)\))?$", title)
    if m:
        kind = m.group("kind")
        subj = split_subject(m.group("subj"))
        subject, group = subj.name, subj.group
        teacher = clean(m.group("teacher")) or None

    fields: dict[str, str] = {}
    attachments: list[Attachment] = []
    soup = soup_of(body.get("strTable") or "")
    for tr in soup.find_all("tr"):
        th, td = tr.find("th"), tr.find("td")
        if th is None or td is None:
            continue
        key = clean(th.get_text())
        fields[key] = clean(td.get_text())
        if "файл" in key.lower():
            links = parse_attachment_links(td)
            if not links:
                for a in td.find_all("a"):
                    href = a.get("href") or ""
                    url = None if href.lower().startswith("javascript") or not href else href
                    links.append(Attachment(name=clean(a.get_text()) or href, url=url, raw=str(a)))
            if not links and fields[key]:
                links.append(Attachment(name=fields[key], raw=str(td)))
            attachments.extend(links)

    def first(*keys: str) -> str | None:
        for k in keys:
            if fields.get(k):
                return fields[k]
        return None

    return AssignmentDetails(
        kind=kind, subject=subject, group=group, teacher=teacher,
        topic=first("Домашнее задание", "Тема задания", "Задание"),
        date=parse_ru_date(first("Срок сдачи", "Дата урока")),
        notes=first("Подробности от учителя", "Примечания", "Комментарий"),
        attachments=attachments, fields=fields,
        script=body.get("strScript") or None,
    )


# ───────────────────────────── расписание ─────────────────────────────

def parse_day_schedule(html: str) -> list[Lesson]:
    """Расписание на день (DayViewS.asp): время + 'Урок: Предмет/группа [кабинет]'."""
    soup = soup_of(html)
    date_input = soup.find("input", attrs={"name": "DATE"})
    day = try_parse_date(date_input.get("value")) if date_input is not None else None
    table = soup.find("table", class_="schedule-table")
    lessons: list[Lesson] = []
    if table is None:
        return lessons
    slot = 0
    for tr in table.find_all("tr"):
        tds = tr.find_all("td", recursive=False)
        if len(tds) < 2:
            continue
        start, end = parse_time_range(tds[0].get_text())
        if start is None:
            continue
        slot += 1
        for raw in cell_lines(tds[1]):
            line = clean(raw)
            if not line or line == "-":
                continue
            m = re.match(r"^([^:/\[\]]{2,40}):\s*(.+)$", line)
            kind, rest = (clean(m.group(1)), m.group(2)) if m else (None, line)
            subject = split_subject(rest) if kind in (None, "Урок") else None
            lessons.append(Lesson(date=day, number=slot, start=start, end=end,
                                  kind=kind, subject=subject, raw=line))
    return lessons


def parse_week_schedule(html: str) -> list[Lesson]:
    """Расписание на неделю (WeekViewTimeS.asp, ViewType=0).

    В ячейке уроки идут через <br>, а каждый урок повторён несколько раз
    через ',&nbsp;' — повторы убираются, разные уроки в одном слоте остаются.
    """
    soup = soup_of(html)
    monday = selected_week(soup)
    table = soup.find("table", class_="schedule-table")
    lessons: list[Lesson] = []
    if table is None:
        return lessons
    for tr in table.find_all("tr"):
        th = tr.find("th")
        if th is None:
            continue
        wd = WEEKDAYS.get(clean(th.get_text()).lower()[:2])
        tds = tr.find_all("td", recursive=False)
        if wd is None or len(tds) < 2:
            continue
        day = monday + timedelta(days=wd) if monday else None
        numbers = [clean(x) for x in cell_lines(tds[0])]
        for i, raw in enumerate(cell_lines(tds[1])):
            if clean(raw) in ("", "-"):
                continue
            num = int(numbers[i]) if i < len(numbers) and numbers[i].isdigit() else i + 1
            entries = dict.fromkeys(clean(e) for e in raw.split("," + NBSP))
            for entry in entries:
                if entry and entry != "-":
                    lessons.append(Lesson(date=day, number=num, start=None, end=None,
                                          kind="Урок", subject=split_subject(entry), raw=entry))
    return lessons


def parse_year_calendar(html: str) -> YearCalendar:
    """Календарь на год (SetupSchool/Calendar/YearView.asp)."""
    soup = soup_of(html)
    days: dict[date, CalendarDay] = {}
    for div in soup.find_all("div", class_="school-day"):
        a = div.find("a")
        if a is None:
            continue
        m = re.search(r"Go\(\s*0\s*,\s*'([\d.]+)'", (a.get("onclick") or "") + " " + (a.get("href") or ""))
        if not m:
            continue
        cls = div.get("class") or []
        d = parse_date(m.group(1))
        days[d] = CalendarDay(date=d, weekend="weekend" in cls, vacation="vacation" in cls)
    summary: dict[str, int] = {}
    box = soup.find("div", class_="summary-information")
    if box is not None:
        for row in box.find_all("div", recursive=False):
            span = row.find("span", recursive=False)
            label = clean(row.get_text()).split(":")[0]
            if span is not None and clean(span.get_text()).isdigit():
                summary[label] = int(clean(span.get_text()))
    return YearCalendar(days=sorted(days.values(), key=lambda x: x.date), summary=summary)


def parse_month_events(html: str) -> list[MonthEvent]:
    """Мероприятия из календаря на месяц (MonthViewS.asp, ViewType=0).

    В ячейке: номер дня, затем '- <a onclick="EditEvent(3,25)">Осенние каникулы</a><br>'.
    """
    soup = soup_of(html)
    table = soup.find("table", class_="schedule-table")
    events: list[MonthEvent] = []
    if table is None:
        return events
    for td in table.find_all("td"):
        td_cls = td.get("class") or []
        if "week" in td_cls or td.find("a") is None:
            continue
        day = _date_in(td)
        if day is None:
            continue
        links = []
        for a in td.find_all("a"):
            m = _EVENT_RE.search((a.get("onclick") or "") + " " + (a.get("href") or ""))
            if m:
                links.append(MonthEvent(date=day, title=clean(a.get_text()), event_type=int(m.group(1)),
                                        event_id=int(m.group(2)),
                                        vacation="vacation-day" in ((a.get("class") or []) + td_cls)))
        if not links:   # на всякий случай: мероприятие без ссылки — просто текст
            for raw in cell_lines(td):
                line = clean(raw).lstrip("-–— ").strip()
                if line and line != str(day.day):
                    links.append(MonthEvent(date=day, title=line, vacation="vacation-day" in td_cls))
        events.extend(links)
    return events


def _birth_date(text: str, today: date | None = None) -> date | None:
    """'23.11.08' -> 2008-11-23, '15.03.75' -> 1975-03-15 (двузначный год)."""
    m = _DATE_RE.search(text or "")
    if not m:
        return None
    d, mth, y = (int(g) for g in m.groups())
    if y < 100:
        y += 2000 if 2000 + y <= (today or date.today()).year else 1900
    try:
        return date(y, mth, d)
    except ValueError:
        return None


def parse_birthdays(html: str, today: date | None = None) -> list[Birthday]:
    """Именинники месяца (Calendar/MonthBirth.asp): Класс | Роль | Дата рождения | Имя на экране."""
    soup = soup_of(html)
    table = _find_table_with_header(soup, "Дата рождения")
    if table is None:
        return []
    rows = table.find_all("tr")
    header = [clean(th.get_text()) for th in rows[0].find_all("th")]
    col = {name: i for i, name in enumerate(header)}

    def get(tds: list[Tag], *names: str) -> str:
        for name in names:
            if name in col and col[name] < len(tds):
                return clean(tds[col[name]].get_text())
        return ""

    result = []
    for tr in rows[1:]:
        tds = tr.find_all("td", recursive=False)
        if not tds:
            continue
        name = get(tds, "Имя на экране", "ФИО", "Имя")
        if not name:
            continue
        result.append(Birthday(name=name, birth_date=_birth_date(get(tds, "Дата рождения"), today),
                               role=get(tds, "Роль") or None, class_name=get(tds, "Класс") or None))
    return result


def parse_announcements(html: str) -> list[Announcement]:
    """Доска объявлений (Announce/ViewAnnouncements.asp)."""
    soup = soup_of(html)
    result = []
    for ad in soup.find_all("div", class_="advertisement"):
        body = ad.find("div", class_="adver-body") or ad
        title = ""
        h3 = body.find("h3")
        if h3 is not None:
            title = clean(h3.get_text())
            label = h3.find("span")
            if label is not None and title.startswith(clean(label.get_text())):
                title = title[len(clean(label.get_text())):].strip()
        author_tag = ad.find(class_="username-coloured")
        info = body.find("div", class_="adver-info")
        content = body.find("div", class_="adver-content")
        text = ""
        if content is not None:
            text = "\n".join(clean(line) for line in cell_lines(content))
            text = re.sub(r"\n{3,}", "\n\n", text).strip()
        uid = re.search(r"UID=(\d+)", str(ad.find(class_="profile-image") or ""))
        result.append(Announcement(
            title=title,
            author=clean(author_tag.get_text()) if author_tag is not None else None,
            date=try_parse_date(clean(info.get_text())) if info is not None else None,
            text=text,
            html=content.decode_contents().strip() if content is not None else "",
            attachments=parse_attachment_links(ad),
            author_uid=int(uid.group(1)) if uid else None,
        ))
    return result


# ───────────────────────────── отчёты ─────────────────────────────

def _academic_year_start(info: dict[str, str], fallback: date | None) -> int:
    m = re.search(r"(\d{4})\s*/\s*\d{4}", info.get("Учебный год", ""))
    if m:
        return int(m.group(1))
    if fallback:
        return fallback.year if fallback.month >= 8 else fallback.year - 1
    return date.today().year


def parse_cell(text: str, day: date, attendance_codes: Iterable[str] = ATTENDANCE_CODES) -> DayCell:
    """'4 3' / 'ОТ' / '·' -> DayCell с оценками, посещаемостью и «точкой»-долгом."""
    codes = {c.upper() for c in attendance_codes}
    cell = DayCell(date=day)
    for token in clean(text).split():
        for sign in DEBT_SIGNS:
            if sign in token:
                cell.debt = True
                token = token.replace(sign, "")
        if not token:
            continue
        if _MARK_RE.match(token):
            cell.marks.append(token)
        elif token.upper() in codes:
            cell.attendance.append(token.upper())
        else:
            cell.other.append(token)
    return cell


def _report_info(soup: BeautifulSoup) -> dict[str, str]:
    info: dict[str, str] = {}
    for span in soup.find_all("span", class_="select"):
        b = span.find("b")
        if b is None:
            continue
        key = clean(b.get_text()).rstrip(":").strip()
        full = clean(span.get_text())
        info[key] = full[len(clean(b.get_text())):].strip()
    return info


def parse_student_total(html: str) -> StudentTotalReport:
    """Отчёт об успеваемости и посещаемости (POST /asp/Reports/StudentTotal.asp)."""
    raise_for_json_error(html)
    soup = soup_of(html)
    info = _report_info(soup)
    period = re.findall(r"\d{1,2}\.\d{1,2}\.\d{2,4}", info.get("Период", ""))
    period_start = try_parse_date(period[0]) if period else None
    period_end = try_parse_date(period[1]) if len(period) > 1 else None
    year1 = _academic_year_start(info, period_start)
    legend = {**ATTENDANCE_CODES, **parse_legend(soup)}

    table = soup.find("table", class_="table-print")
    subjects: list[SubjectPerformance] = []
    if table is None:
        return StudentTotalReport(info, period_start, period_end, subjects, legend, parse_stamp(html))
    rows = table.find_all("tr")
    months: list[int] = []
    for th in rows[0].find_all("th"):
        month = RU_MONTHS_NOM.get(clean(th.get_text()).lower())
        if month:
            months += [month] * int(th.get("colspan") or 1)
    day_numbers = [parse_int(th.get_text()) for th in rows[1].find_all("th")] if len(rows) > 1 else []
    dates = [date(year1 if m >= 8 else year1 + 1, m, d) for m, d in zip(months, day_numbers) if d]

    for tr in rows[2:]:
        tds = tr.find_all("td", recursive=False)
        if len(tds) < 2:
            continue
        cells = []
        for d, td in zip(dates, tds[1:1 + len(dates)]):
            cell = parse_cell(td.get_text(), d, legend.keys())
            if cell.marks or cell.attendance or cell.debt or cell.other:
                cells.append(cell)
        avg = parse_number(tds[1 + len(dates)].get_text()) if len(tds) > 1 + len(dates) else None
        subjects.append(SubjectPerformance(subject=clean(tds[0].get_text()), cells=cells,
                                           average=avg or None))
    return StudentTotalReport(info, period_start, period_end, subjects, legend, parse_stamp(html))


def parse_parent_letter(html: str) -> ParentLetter:
    """Информационное письмо для родителей (POST /asp/Reports/ParentInfoLetter.asp, ReportType=1)."""
    raise_for_json_error(html)
    soup = soup_of(html)
    table = soup.find("table", class_="table-print")
    rows_out: list[LetterRow] = []
    totals = None
    week_start = week_end = None
    if table is not None:
        rows = table.find_all("tr")
        header_dates = re.findall(r"\d{1,2}\.\d{1,2}\.\d{2,4}", clean(rows[0].get_text(" ")))
        week_start = try_parse_date(header_dates[0]) if header_dates else None
        week_end = try_parse_date(header_dates[1]) if len(header_dates) > 1 else None
        day_numbers = [parse_int(th.get_text()) for th in rows[1].find_all("th")[5:]] if len(rows) > 1 else []
        week = [week_start + timedelta(days=i) for i in range(7)] if week_start else []
        day_dates = [next((d for d in week if d.day == n), None) for n in day_numbers]

        for tr in rows[2:]:
            tds = tr.find_all("td", recursive=False)
            if len(tds) < 6:
                continue
            first = clean(tds[0].get_text())
            counts = {mark: parse_int(td.get_text()) for mark, td in zip((5, 4, 3, 2), tds[1:5])}
            week_cells: list[DayCell] = []
            for d, td in zip(day_dates, tds[6:]):
                cell = parse_cell(td.get_text(), d) if d else None
                if cell is not None and (cell.marks or cell.attendance or cell.debt or cell.other):
                    week_cells.append(cell)
            subj = split_subject(first)
            row = LetterRow(subject=subj.name, group=subj.group, counts=counts,
                            average=parse_number(tds[5].get_text()), week=week_cells)
            if "totals" in (tr.get("class") or []) or first.lower().startswith("итого"):
                totals = row
            else:
                rows_out.append(row)

    debts: list[Debt] = []
    for b in soup.find_all("b"):
        if clean(b.get_text()).startswith("Задолженности"):
            box = b.parent
            for raw in cell_lines(box):
                line = clean(raw)
                if not line or line.startswith("Задолженности") or ":" not in line:
                    continue
                subject, text = line.split(":", 1)
                debts.append(Debt(subject=subject.strip(), text=text.strip()))
            break

    class_teacher = None
    for td in soup.find_all("td"):
        if clean(td.get_text()).startswith("Классный руководитель"):
            nxt = td.find_next_sibling("td")
            class_teacher = clean(nxt.get_text()) if nxt is not None else None
            break

    return ParentLetter(week_start=week_start, week_end=week_end, rows=rows_out, totals=totals,
                        debts=debts, class_teacher=class_teacher or None, generated_at=parse_stamp(html))


def table_grid(table: Tag) -> GenericTable:
    """Таблица с раскрытыми colspan/rowspan: шапка (строки из <th>) и данные."""
    header: list[list[str]] = []
    body: list[list[str]] = []
    pending: dict[int, tuple[str, int]] = {}   # колонка -> (текст, сколько строк ещё)
    for tr in table.find_all("tr"):
        cells = tr.find_all(["td", "th"], recursive=False)
        if not cells:
            continue
        row: list[str] = []
        col = 0
        queue = list(cells)
        while queue or col in pending:
            if col in pending:
                text, left = pending[col]
                row.append(text)
                if left > 1:
                    pending[col] = (text, left - 1)
                else:
                    del pending[col]
                col += 1
                continue
            cell = queue.pop(0)
            text = clean(cell.get_text(" "))
            colspan = int(cell.get("colspan") or 1)
            rowspan = int(cell.get("rowspan") or 1)
            for _ in range(colspan):
                row.append(text)
                if rowspan > 1:
                    pending[col] = (text, rowspan - 1)
                col += 1
        (header if all(c.name == "th" for c in cells) else body).append(row)
    return GenericTable(header=header, rows=body)


def parse_total_marks(html: str) -> TotalMarksReport:
    """Итоговые отметки (POST /asp/Reports/StudentTotalMarks.asp).

    Таблица table-print-num: № | Предмет | 1..4 четверть | Год. | Экз. | Итог.
    Колонки ищутся по названиям, поэтому полугодия/триместры тоже разберутся.
    """
    raise_for_json_error(html)
    soup = soup_of(html)
    info = _report_info(soup)
    stamp = parse_stamp(html)
    table = soup.find("table", class_=re.compile(r"^table-print"))
    if table is None or "Оценки не проставлены" in html:
        return TotalMarksReport(info=info, period_names=[], subjects=[], generated_at=stamp)
    grid = table_grid(table)
    columns = grid.header[-1] if grid.header else []

    def find(pred) -> int | None:
        return next((i for i, c in enumerate(columns) if pred(c.lower())), None)

    subj_i = find(lambda c: c.startswith("предмет"))
    subj_i = 1 if subj_i is None else subj_i
    period_cols = [i for i, c in enumerate(columns)
                   if re.search(r"четверт|полугод|триместр|модул|период", c, re.I)]
    year_i = find(lambda c: c.startswith("год"))
    exam_i = find(lambda c: "экз" in c)
    final_i = find(lambda c: c.startswith("итог"))

    def cell(row: list[str], i: int | None) -> str | None:
        return (row[i] or None) if i is not None and i < len(row) else None

    subjects = []
    for row in grid.rows:
        name = cell(row, subj_i)
        if not name:
            continue
        subjects.append(SubjectTotals(
            subject=name,
            periods={columns[i]: row[i] for i in period_cols if i < len(row) and row[i]},
            year=cell(row, year_i), exam=cell(row, exam_i), final=cell(row, final_i),
        ))
    return TotalMarksReport(info=info, period_names=[columns[i] for i in period_cols],
                            subjects=subjects, generated_at=stamp)


# ───────────────────────────── отпечаток страницы ─────────────────────────────

_VOLATILE = [
    re.compile(r"<input[^>]*type\s*=\s*\"?hidden[^>]*>", re.I),          # AT, VER, PCLID и т.п.
    re.compile(r"\bAT=[0-9A-Za-z]+", re.I),                                 # фото автора: photo.asp?AT=...
    re.compile(r"\b(VER|at)\s*[:=]\s*['\"]?\d{6,}['\"]?", re.I),           # VER в скриптах
    re.compile(r"Состояние\s+на[^<]{0,40}", re.I),                         # время формирования отчёта
    re.compile(r"\d{1,2}:\d{2}:\d{2}"),                                     # прочие часы:минуты:секунды
    re.compile(r"\s+"),
]


def content_fingerprint(html: str) -> str:
    """Хэш содержимого страницы без «шумящих» частей (AT, VER, время формирования).

    Одинаковый отпечаток = на странице ничего не поменялось → её можно не разбирать заново.
    """
    import hashlib
    text = html or ""
    for rx in _VOLATILE:
        text = rx.sub(" ", text)
    return hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()
