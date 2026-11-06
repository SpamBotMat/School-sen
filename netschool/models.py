"""Модели данных, которые возвращают парсеры NetSchool.

Все даты — datetime.date, время — datetime.time. Оценки хранятся строками
("5", "4+", "зч"), а числовое значение доступно через свойство ``value``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time

_MARK_RE = re.compile(r"^([1-5])[+-]?$")


def mark_value(mark: str | None) -> int | None:
    """'5' -> 5, '4-' -> 4, '-'/None/'зч' -> None."""
    if not mark:
        return None
    m = _MARK_RE.match(mark.strip())
    return int(m.group(1)) if m else None


@dataclass(slots=True)
class PageState:
    """Скрытые поля страницы, которые нужно передавать в следующий запрос."""

    at: str | None = None
    ver: str | None = None
    pclid_iup: str | None = None
    pclid: str | None = None
    menu_item: str | None = None
    tab_item: str | None = None
    has_menu: bool = False


@dataclass(slots=True)
class Assignment:
    """Строка дневника (Assignments.asp)."""

    date: date                 # "Срок сдачи" (для ДЗ) или дата урока
    subject: str
    type_code: str             # "Д", "К", "О" ...
    type_name: str | None      # "Домашняя работа" ...
    title: str                 # "Тема задания"
    mark: str | None           # None, если "-"
    overdue: bool              # розовая строка: обязательное задание без оценки
    aid: int | None = None     # параметры SubmAssign(AID, CID, TP)
    cid: int | None = None
    tp: int | None = None

    @property
    def key(self) -> str:
        """Стабильный ключ для поиска изменений между опросами."""
        if self.aid is not None:
            return f"{self.aid}:{self.cid}:{self.tp}"
        return f"{self.date.isoformat()}:{self.subject}:{self.type_code}:{self.title}"

    @property
    def mark_value(self) -> int | None:
        return mark_value(self.mark)

    @property
    def is_homework(self) -> bool:
        return self.type_code == "Д"


@dataclass(slots=True)
class Attachment:
    name: str
    url: str | None = None            # '/doc/<имя файла>' из $.openAttachment(url, id) или обычная ссылка
    attachment_id: int | None = None  # id для POST url {attachmentId}
    raw: str = ""                     # исходный HTML ссылки (для отладки)


@dataclass(slots=True)
class AttachmentFile:
    """Скачанное вложение (NetSchoolClient.download_attachment)."""

    name: str
    content: bytes
    content_type: str | None = None

    @property
    def size(self) -> int:
        return len(self.content)


@dataclass(slots=True)
class AssignmentDetails:
    """Ответ GetAssignmentInfo.asp (окно «Подробности задания»)."""

    kind: str                   # "Домашнее задание" / "Задание"
    subject: str | None
    group: str | None
    teacher: str | None
    topic: str | None           # "Домашнее задание" или "Тема задания"
    date: date | None           # "Срок сдачи" или "Дата урока"
    notes: str | None           # "Подробности от учителя" / "Примечания"
    attachments: list[Attachment] = field(default_factory=list)
    fields: dict[str, str] = field(default_factory=dict)  # все строки как есть
    script: str | None = None   # data.strScript, если сервер его прислал


@dataclass(slots=True)
class Subject:
    """Разобранная строка вида 'Ин.яз./Английский язык/11В_техн./Базовый [303А ...]'."""

    name: str                   # "Английский язык" (как в дневнике)
    full_name: str              # "Ин.яз./Английский язык"
    group: str | None = None    # "11В_техн."
    level: str | None = None    # "Базовый" / "Углубленный"
    room: str | None = None     # "303А Кабинет английского языка"


@dataclass(slots=True)
class Lesson:
    """Урок или мероприятие из расписания (день или неделя)."""

    date: date | None
    number: int | None          # номер урока (только в недельном виде)
    start: time | None          # время (только в дневном виде)
    end: time | None
    kind: str | None            # "Урок", "Мероприятие" ... (только в дневном виде)
    subject: Subject | None
    raw: str


@dataclass(slots=True)
class CalendarDay:
    date: date
    weekend: bool               # красный день: выходной или праздник
    vacation: bool              # каникулы (зелёный фон)

    @property
    def school_day(self) -> bool:
        return not self.weekend and not self.vacation


@dataclass(slots=True)
class YearCalendar:
    days: list[CalendarDay]
    summary: dict[str, int] = field(default_factory=dict)

    def vacation_periods(self) -> list[tuple[date, date]]:
        """Непрерывные отрезки каникул (включая однодневные праздники-каникулы)."""
        periods: list[tuple[date, date]] = []
        start = prev = None
        for d in sorted(self.days, key=lambda x: x.date):
            if d.vacation:
                if start is None:
                    start = d.date
                elif prev is not None and (d.date - prev).days != 1:
                    periods.append((start, prev))
                    start = d.date
                prev = d.date
            elif start is not None:
                periods.append((start, prev))
                start = prev = None
        if start is not None:
            periods.append((start, prev))
        return periods

    def usual_weekdays(self) -> set[int]:
        """Дни недели (0=Пн), которые обычно учебные (у этой школы Пн–Сб)."""
        school = [0] * 7
        off = [0] * 7
        for d in self.days:
            if d.vacation:
                continue
            (school if not d.weekend else off)[d.date.weekday()] += 1
        return {wd for wd in range(7) if school[wd] > off[wd]}

    def holidays(self) -> list[date]:
        """Красные дни, выпавшие на обычно учебный день недели (вне каникул)."""
        usual = self.usual_weekdays()
        return [d.date for d in self.days
                if d.weekend and not d.vacation and d.date.weekday() in usual]

    def get(self, day: date) -> CalendarDay | None:
        for d in self.days:
            if d.date == day:
                return d
        return None


@dataclass(slots=True)
class DayCell:
    """Ячейка отчёта: оценки, отметки посещаемости и «точка» (долг)."""

    date: date
    marks: list[str] = field(default_factory=list)
    attendance: list[str] = field(default_factory=list)   # "ОТ", "Б", "УП" ...
    debt: bool = False                                    # «·» в ячейке
    other: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SubjectPerformance:
    subject: str
    cells: list[DayCell]
    average: float | None       # «Средняя оценка» (сервер пишет 0, если оценок нет)


@dataclass(slots=True)
class StudentTotalReport:
    """Отчёт об успеваемости и посещаемости (StudentTotal.asp)."""

    info: dict[str, str]                    # "Учебный год", "Класс", "Период", ...
    period_start: date | None
    period_end: date | None
    subjects: list[SubjectPerformance]
    legend: dict[str, str]                  # "ОТ" -> "Отсутствовал"
    generated_at: datetime | None


@dataclass(slots=True)
class LetterRow:
    subject: str                            # "Биология" (без группы)
    group: str | None                       # "11В_техн."
    counts: dict[int, int]                  # {5: 1, 4: 2, 3: 0, 2: 0} с начала четверти
    average: float | None                   # «Ср.балл»
    week: list[DayCell] = field(default_factory=list)   # ячейки недели: оценки, ОП/ОТ, «точки»

    @property
    def week_marks(self) -> dict[date, list[str]]:
        """Только оценки за неделю: {дата: ['4', '3']}."""
        return {c.date: c.marks for c in self.week if c.marks}


@dataclass(slots=True)
class Debt:
    subject: str
    text: str


@dataclass(slots=True)
class ParentLetter:
    """Информационное письмо для родителей (ParentInfoLetter.asp, ReportType=1)."""

    week_start: date | None
    week_end: date | None
    rows: list[LetterRow]
    totals: LetterRow | None
    debts: list[Debt]
    class_teacher: str | None
    generated_at: datetime | None


@dataclass(slots=True)
class SelectOption:
    value: str
    text: str
    selected: bool = False


@dataclass(slots=True)
class ReportLink:
    page: str                               # "ReportStudentTotal.asp"
    thm_id: int
    rpt_id: int
    title: str


@dataclass(slots=True)
class SubjectTotals:
    """Строка «Итоговых отметок»."""

    subject: str
    periods: dict[str, str]                 # {"1 четверть": "4"} — только заполненные
    year: str | None = None                 # годовая
    exam: str | None = None                 # экзаменационная
    final: str | None = None                # итоговая


@dataclass(slots=True)
class TotalMarksReport:
    info: dict[str, str]                    # "Учебный год", "Класс", ...
    period_names: list[str]                 # ["1 четверть", ..., "4 четверть"]
    subjects: list[SubjectTotals]
    generated_at: datetime | None


@dataclass(slots=True)
class MonthEvent:
    """Мероприятие из календаря на месяц (MonthViewS.asp)."""

    date: date
    title: str
    event_type: int | None = None           # EditEvent(type, id)
    event_id: int | None = None
    vacation: bool = False                  # ячейка/ссылка с классом vacation-day


@dataclass(slots=True)
class Birthday:
    """Строка «Именинники месяца» (MonthBirth.asp)."""

    name: str
    birth_date: date | None
    role: str | None = None                 # "Ученик", "Учитель", "Родитель" ...
    class_name: str | None = None

    def next_birthday(self, today: date) -> date | None:
        """Ближайший день рождения начиная с today (29.02 → 1.03 в невисокосный год)."""
        if self.birth_date is None:
            return None
        for year in (today.year, today.year + 1):
            try:
                day = self.birth_date.replace(year=year)
            except ValueError:
                day = date(year, 3, 1)
            if day >= today:
                return day
        return None

    def age_on(self, day: date) -> int | None:
        if self.birth_date is None:
            return None
        b = self.birth_date
        return day.year - b.year - ((day.month, day.day) < (b.month, b.day))


@dataclass(slots=True)
class Announcement:
    """Объявление с доски (Announce/ViewAnnouncements.asp)."""

    title: str
    author: str | None
    date: date | None
    text: str                               # абзацы через \n (из <br>)
    html: str = ""                          # исходный HTML текста
    attachments: list[Attachment] = field(default_factory=list)
    author_uid: int | None = None

    @property
    def key(self) -> str:
        """Ключ для «новое объявление?» (id у объявлений на странице нет)."""
        return f"{self.date.isoformat() if self.date else ''}|{self.author or ''}|{self.title}"


@dataclass(slots=True)
class GenericTable:
    """Таблица «как есть» (для отчётов без образца: итоговые отметки и т.п.)."""

    header: list[list[str]]
    rows: list[list[str]]
