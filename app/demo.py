"""DEMO_MODE: ненастоящий NetSchool с правдоподобными данными.

Нужен для разработки интерфейса, скриншотов и тестов — без обращения к сайту школы.
Данные зависят от даты и времени: днём «появляются» оценки, поэтому видно и уведомления.
"""
from __future__ import annotations

import hashlib
import random
from datetime import date, datetime, time, timedelta

from netschool.client import prepare_password
from netschool.errors import LoginError
from netschool.models import (Announcement, Assignment, AssignmentDetails, Attachment, AttachmentFile, Birthday,
                              CalendarDay, DayCell, Lesson, MonthEvent, StudentTotalReport, Subject,
                              SubjectPerformance, SubjectTotals, TotalMarksReport, YearCalendar)

from .timeutil import monday_of, now, today

ROOMS = {
    "Алгебра": "201А Кабинет математики", "Геометрия": "201А Кабинет математики", "Физика": "314 Кабинет физики",
    "Химия": "311 Кабинет химии", "Биология": "305 Кабинет биологии", "География": "210 Кабинет географии",
    "История": "205 Кабинет истории", "Обществознание": "102 Кабинет обществознания",
    "Русский язык": "118 Кабинет русского языка", "Литература": "118 Кабинет русского языка",
    "Английский язык": "303А Кабинет английского языка", "Информатика и ИКТ": "401 Компьютерный класс",
    "Физкультура": "Спортивный зал 1", "Туркменский язык": "124 Кабинет туркменского языка",
}
SUBJECTS = list(ROOMS)
WEEK = [
    ["Алгебра", "Физика", "Английский язык", "Русский язык", "История", "Физкультура"],
    ["Геометрия", "Химия", "Литература", "Английский язык", "Обществознание", "Туркменский язык"],
    ["Алгебра", "Биология", "Русский язык", "Информатика и ИКТ", "География"],
    ["Физика", "Алгебра", "История", "Литература", "Английский язык", "Физкультура"],
    ["Обществознание", "История", "Физика", "Химия", "Английский язык", "Геометрия"],
    ["Русский язык", "Литература", "Туркменский язык", "Алгебра", "Биология"],
]
BELLS = [(time(9, 0), time(9, 45)), (time(9, 55), time(10, 40)), (time(10, 50), time(11, 35)),
         (time(11, 55), time(12, 40)), (time(12, 55), time(13, 40)), (time(13, 50), time(14, 35)),
         (time(14, 45), time(15, 30))]
HW = {
    "Алгебра": ["№ 245, 247, 251", "§ 14, № 312–315", "Повторить формулы сокращённого умножения", "Самостоятельная по вариантам"],
    "Геометрия": ["§ 9, задачи 3–5", "Выучить теорему Пифагора с доказательством", "№ 518, 520"],
    "Физика": ["§ 21, упр. 12 (1–3)", "Подготовиться к лабораторной", "Задачи на стр. 88"],
    "Химия": ["§ 17, вопросы 1–4", "Уравнения реакций, стр. 64", "Выучить таблицу растворимости"],
    "Биология": ["§ 23, конспект", "Подготовить сообщение о клетке"],
    "География": ["§ 12, контурная карта", "Выучить столицы Азии"],
    "История": ["§ 15–16, даты в тетрадь", "Подготовить доклад о реформах"],
    "Обществознание": ["§ 8, вопросы после параграфа", "Эссе на тему «Право и мораль»"],
    "Русский язык": ["Упр. 214, 216", "Словарные слова стр. 102", "Сочинение-рассуждение"],
    "Литература": ["Прочитать главы 3–5", "Выучить стихотворение наизусть", "Анализ эпизода письменно"],
    "Английский язык": ["Ex. 5, 6 p. 47", "Learn new words (Unit 4)", "Write an essay about your hobby"],
    "Информатика и ИКТ": ["Задачи на циклы в тетради", "Презентация по теме «Сети»"],
    "Физкультура": ["Форма на следующий урок"],
    "Туркменский язык": ["Упр. 34, выучить слова", "Пересказ текста стр. 51"],
}


def _rng(*parts: object) -> random.Random:
    seed = hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()
    return random.Random(int(seed[:12], 16))


def term_bounds(d: date) -> tuple[date, date, str]:
    y = d.year if d.month >= 9 else d.year - 1
    terms = [
        (date(y, 9, 1), date(y, 10, 25), "1 четверть"),
        (date(y, 11, 2), date(y + 1, 1, 10), "2 четверть"),
        (date(y + 1, 1, 11), date(y + 1, 3, 20), "3 четверть"),
        (date(y + 1, 4, 1), date(y + 1, 5, 25), "4 четверть"),
    ]
    for start, end, name in terms:
        if d <= end:
            return start, end, name
    return terms[-1]


def year_days(d: date) -> YearCalendar:
    y = d.year if d.month >= 9 else d.year - 1
    vac = [(date(y, 10, 26), date(y, 11, 1)), (date(y, 12, 28), date(y + 1, 1, 10)),
           (date(y + 1, 2, 15), date(y + 1, 2, 21)), (date(y + 1, 3, 21), date(y + 1, 3, 31)),
           (date(y + 1, 5, 26), date(y + 1, 8, 31)),
           (date(y, 9, 27), date(y, 9, 27)), (date(y, 10, 6), date(y, 10, 6)), (date(y, 12, 12), date(y, 12, 12)),
           (date(y + 1, 5, 18), date(y + 1, 5, 18))]
    red = {date(y + 1, 2, 23), date(y + 1, 3, 8), date(y + 1, 5, 1), date(y, 11, 4)}
    days, cur = [], date(y, 9, 1)
    while cur <= date(y + 1, 8, 31):
        v = any(a <= cur <= b for a, b in vac)
        days.append(CalendarDay(date=cur, weekend=cur.weekday() == 6 or cur in red, vacation=v))
        cur += timedelta(days=1)
    return YearCalendar(days=days, summary={})


def is_school(d: date) -> bool:
    cal = year_days(d)
    day = cal.get(d)
    return bool(day and day.school_day)


class DemoNetSchool:
    """Повторяет нужную часть интерфейса NetSchoolClient."""

    def __init__(self, *_, **__):
        self.user: str | None = None
        self.entry_point = "/asp/Curriculum/Assignments.asp"

    async def login(self, username: str, password: str | None = None, *, password_md5: str | None = None,
                    password_len: int | None = None) -> None:
        if password is not None:
            password_md5, password_len = prepare_password(password)
        bad, _ = prepare_password("wrong")
        if not username or password_md5 == bad:
            raise LoginError("Неправильный пароль или имя пользователя", status=400,
                             server_message="Неправильный пароль или имя пользователя")
        self.user = username

    async def logout(self) -> None:
        self.user = None

    async def aclose(self) -> None:
        pass

    # ── данные ──
    def _lessons(self, d: date) -> list[str]:
        if d.weekday() > 5 or not is_school(d):
            return []
        return WEEK[d.weekday()]

    def _mark_for(self, d: date, subject: str, slot: int) -> str | None:
        """Есть ли оценка за урок (и появилась ли она уже — после конца урока)."""
        r = _rng(self.user, d, subject, slot)
        if r.random() > 0.38:
            return None
        if d == today():
            end = BELLS[min(slot, len(BELLS) - 1)][1]
            if now().time() < (datetime.combine(d, end) + timedelta(minutes=20)).time():
                return None
        return r.choices(["5", "4", "3", "2"], weights=[42, 36, 17, 5])[0]

    async def diary(self, week: date | None = None) -> list[Assignment]:
        monday = monday_of(week or today())
        out: list[Assignment] = []
        aid = int(monday.strftime("%y%m%d")) * 100
        for i in range(6):
            d = monday + timedelta(days=i)
            for slot, s in enumerate(self._lessons(d)):
                r = _rng(self.user, "hw", d, s)
                aid += 1
                mark = self._mark_for(d, s, slot) if d <= today() else None
                if s != "Физкультура" or r.random() < 0.2:
                    title = r.choice(HW[s])
                    overdue = d < today() - timedelta(days=2) and mark is None and r.random() < 0.08
                    out.append(Assignment(date=d, subject=s, type_code="Д", type_name="Домашняя работа",
                                          title=title, mark=mark if r.random() < 0.5 else None, overdue=overdue,
                                          aid=aid, cid=7, tp=3))
                if mark and r.random() < 0.6:
                    aid += 1
                    out.append(Assignment(date=d, subject=s, type_code="О", type_name="Ответ на уроке",
                                          title="Работа на уроке", mark=mark, overdue=False, aid=aid, cid=7, tp=2))
                if d > today() and r.random() < 0.06:
                    aid += 1
                    out.append(Assignment(date=d, subject=s, type_code="К", type_name="Контрольная работа",
                                          title="Контрольная по теме", mark=None, overdue=False, aid=aid, cid=7, tp=1))
        return out

    async def assignment_details(self, a: Assignment) -> AssignmentDetails:
        r = _rng("det", a.aid)
        atts = [Attachment(name="Задание.pdf", url="/doc/zadanie.pdf", attachment_id=(a.aid or 0) * 10 + 1)] \
            if r.random() < 0.3 else []
        return AssignmentDetails(kind="Домашнее задание", subject=a.subject, group=None, teacher="Учитель предмета",
                                 topic=a.title, date=a.date,
                                 notes=r.choice(["Выполнить в тетради, оформить аккуратно.",
                                                 "Смотри пример в учебнике на стр. 45.", None]),
                                 attachments=atts)

    async def download_attachment(self, att: Attachment) -> AttachmentFile:
        return AttachmentFile(name="Задание.txt", content="Демо-файл задания School Sensor\n".encode(),
                              content_type="text/plain")

    async def week_schedule(self, week: date | None = None) -> list[Lesson]:
        monday = monday_of(week or today())
        out = []
        for i in range(6):
            d = monday + timedelta(days=i)
            for n, s in enumerate(self._lessons(d), 1):
                room = ROOMS[s]
                out.append(Lesson(date=d, number=n, start=None, end=None, kind="Урок",
                                  subject=Subject(name=s, full_name=s, group="11В", level="Базовый", room=room),
                                  raw=s))
        return out

    async def day_schedule(self, day: date | None = None) -> list[Lesson]:
        d = day or today()
        return [Lesson(date=d, number=n, start=BELLS[n - 1][0], end=BELLS[n - 1][1], kind="Урок",
                       subject=Subject(name=s, full_name=s, room=ROOMS[s]), raw=s)
                for n, s in enumerate(self._lessons(d), 1)]

    async def student_total(self, start: date | None = None, end: date | None = None) -> StudentTotalReport:
        t0, t1, name = term_bounds(today())
        subjects = []
        for s in SUBJECTS:
            cells = []
            d = t0
            while d <= min(today(), t1):
                lessons = self._lessons(d)
                for slot, ss in enumerate(lessons):
                    if ss != s:
                        continue
                    mark = self._mark_for(d, s, slot)
                    r = _rng(self.user, "att", d, s)
                    att = []
                    if r.random() < 0.025:
                        att.append("ОП")
                    elif r.random() < 0.015:
                        att.append("Б")
                    debt = r.random() < 0.01
                    if mark or att or debt:
                        cells.append(DayCell(date=d, marks=[mark] if mark else [], attendance=att, debt=debt))
                d += timedelta(days=1)
            vals = [int(m) for c in cells for m in c.marks]
            subjects.append(SubjectPerformance(subject=s, cells=cells,
                                               average=round(sum(vals) / len(vals), 2) if vals else None))
        return StudentTotalReport(info={"Учебный год": f"{t0.year}/{t0.year + 1}", "Класс": "11В",
                                        "Период": f"{name}", "Ученик": "Демо Алекс"},
                                  period_start=t0, period_end=t1, subjects=subjects,
                                  legend={"ОП": "Опоздал", "Б": "Пропуск по болезни"}, generated_at=now())

    async def total_marks(self) -> TotalMarksReport:
        t0, _, name = term_bounds(today())
        y = t0.year if t0.month >= 9 else t0.year - 1
        done_terms = []
        for start, end, nm in [(date(y, 9, 1), date(y, 10, 25), "1 четверть"),
                               (date(y, 11, 2), date(y + 1, 1, 10), "2 четверть"),
                               (date(y + 1, 1, 11), date(y + 1, 3, 20), "3 четверть")]:
            if today() > end:
                done_terms.append(nm)
        subs = []
        for s in SUBJECTS:
            periods = {}
            for nm in done_terms:
                periods[nm] = _rng(self.user, "tot", s, nm).choices(["5", "4", "3"], weights=[40, 45, 15])[0]
            subs.append(SubjectTotals(subject=s, periods=periods))
        return TotalMarksReport(info={}, period_names=["1 четверть", "2 четверть", "3 четверть", "4 четверть"],
                                subjects=subs, generated_at=now())

    async def birthdays(self, month: int | None = None, year: int | None = None) -> list[Birthday]:
        t = today()
        return [Birthday(name="Демо Алекс", birth_date=date(t.year - 16, t.month, min(28, (t.day + 3) % 28 + 1)),
                         role="Ученик", class_name="11В")]

    async def year_calendar(self) -> YearCalendar:
        return year_days(today())

    async def month_events(self, month: int | None = None, year: int | None = None) -> list[MonthEvent]:
        return []

    async def announcements(self) -> list[Announcement]:
        t = today()
        return [
            Announcement(title="Осенний кросс", author="Администрация", date=t - timedelta(days=1),
                         text="В субботу в 10:00 — школьный кросс. Форма спортивная, приходи болеть за своих!"),
            Announcement(title="Олимпиада по математике", author="Методический совет", date=t - timedelta(days=4),
                         text="Записаться на школьный этап можно у классного руководителя до пятницы."),
            Announcement(title="Библиотека", author="Библиотека", date=t - timedelta(days=9),
                         text="Верните, пожалуйста, учебники прошлого года. Спасибо!"),
        ]


async def seed_demo(ctx, uid: int = 1, first_name: str = "Алекс") -> None:
    """DEMO_MODE: завести подключённого демо-ученика и загрузить ему «дневник»."""
    store = ctx.store
    u = store.get_user(uid)
    if u is None:
        u = store.new_user(uid, username="demo_student", first_name=first_name)
    if not u.connected:
        md5, plen = prepare_password("demo1234")
        u.ns_login, u.ns_cred, u.status, u.consent_at = "demo", ctx.vault.seal(md5, plen), "active", now()
        store.save_user(u)
    if store.snap(uid, "diary") is None:
        await ctx.sync.run(u, baseline=True, reason="demo")
