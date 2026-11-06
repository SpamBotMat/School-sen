"""Тесты на реальных страницах: tests/fixtures (из HAR) и dumps/ (с живого сервера).

Каждый тест гоняется на обоих источниках (параметр har/live). Проверяются
инварианты, а не конкретные ФИО/оценки, поэтому тесты переживут новые
записи. Нет файла — тест пропускается. Ни fixtures, ни dumps не коммить.
"""
from datetime import date, timedelta

import pytest

from conftest import DUMPS, FIXTURES
from netschool import parsers as P

# имя в fixtures (как в HAR) -> имя в dumps/ (как сохраняет tools/dump_pages.py)
LIVE_NAMES = {
    "Assignments.asp.html": "diary.html",
    "DayViewS.asp.html": "day.html",
    "WeekViewTimeS.asp.html": "week.html",
    "YearView.asp.html": "year.html",
    "MonthViewS.asp.html": "month_events.html",
    "MonthBirth.asp.html": "birthdays.html",
    "ViewAnnouncements.asp.html": "announcements.html",
    "Reports.asp.html": "reports.html",
    "StudentTotal.asp.html": "student_total.html",
    "ParentInfoLetter.asp.html": "parent_letter.html",
    "StudentTotalMarks.asp.html": "total_marks.html",
}


@pytest.fixture(params=["har", "live"])
def fx(request):
    def read(name: str) -> str:
        if request.param == "har":
            path = FIXTURES / name
        else:
            path = DUMPS / LIVE_NAMES[name] if name in LIVE_NAMES else None
        if path is None or not path.exists() or path.stat().st_size == 0:
            pytest.skip(f"{request.param}: нет образца {name}")
        return path.read_text(encoding="utf-8")
    return read


def selected_month(html: str) -> tuple[int, int]:
    opt = next((o for o in P.parse_select(html, "MonthYear") if o.selected), None)
    assert opt is not None, "на странице нет выбранного месяца"
    month, year = (int(x) for x in opt.value.split(","))
    return month, year


def test_page_state(fx):
    st = P.parse_page_state(fx("Assignments.asp.html"))
    assert st.at and st.ver and st.pclid_iup and st.has_menu


def test_diary(fx):
    html = fx("Assignments.asp.html")
    items = P.parse_diary(html)
    assert items, "дневник пуст"
    monday = P.selected_week(html)
    assert monday and monday.weekday() == 0
    links = html.count('onclick="SubmAssign(')
    assert len(items) == links                         # ни одной строки не потеряли (rowspan)
    assert len({a.key for a in items}) == len(items)
    for a in items:
        assert a.aid is not None and a.type_name, a
        assert a.date <= monday + timedelta(days=6)
        if not a.overdue:
            assert a.date >= monday                    # из прошлых недель — только просроченные
        else:
            assert a.mark is None                      # розовые = без оценки


def test_weeks(fx):
    weeks = P.parse_weeks(fx("Assignments.asp.html"))
    assert len(weeks) > 30 and all(w[0].weekday() == 0 for w in weeks)
    assert sum(w[3] for w in weeks) == 1


def test_assignment_details(fx):
    d = P.parse_assignment_details(fx("GetAssignmentInfo.asp.json"))
    assert d.subject and d.group and d.date and d.topic
    assert "Присоединенный файл" in d.fields


def test_day_and_week_schedule_agree(fx):
    day = P.parse_day_schedule(fx("DayViewS.asp.html"))
    week = P.parse_week_schedule(fx("WeekViewTimeS.asp.html"))
    assert day and week
    assert all(a.start < b.start for a, b in zip(day, day[1:]))
    assert len({(l.date, l.number, l.raw) for l in week}) == len(week)   # повторы убраны
    same_day = [l.subject.name for l in week if l.date == day[0].date]
    if same_day:
        assert same_day == [l.subject.name for l in day]


def test_year_calendar(fx):
    cal = P.parse_year_calendar(fx("YearView.asp.html"))
    s = cal.summary
    assert len(cal.days) == s["Количество дней в учебном году"]
    assert sum(d.vacation for d in cal.days) == s["Каникулярных дней"]
    assert {0, 1, 2, 3, 4} <= cal.usual_weekdays()
    assert cal.vacation_periods()


def test_student_total_average_is_simple_mean(fx):
    rep = P.parse_student_total(fx("StudentTotal.asp.html"))
    assert rep.subjects and rep.period_start and rep.generated_at
    for s in rep.subjects:
        marks = [int(m[0]) for c in s.cells for m in c.marks]
        if marks:
            assert s.average == pytest.approx(sum(marks) / len(marks), abs=0.006), s.subject
        else:
            assert s.average is None


def test_parent_letter(fx):
    letter = P.parse_parent_letter(fx("ParentInfoLetter.asp.html"))
    assert letter.rows and letter.totals and letter.week_start
    for mark in (5, 4, 3, 2):
        assert letter.totals.counts[mark] == sum(r.counts[mark] for r in letter.rows)
    for r in letter.rows:
        n = sum(r.counts.values())
        if n:
            assert r.average == pytest.approx(sum(k * v for k, v in r.counts.items()) / n, abs=0.006)
    for d in letter.debts:
        assert d.subject and d.text
    for r in letter.rows:
        assert all(letter.week_start <= c.date <= letter.week_end for c in r.week)
        assert len({c.date for c in r.week}) == len(r.week)
        assert r.week_marks == {c.date: c.marks for c in r.week if c.marks}


def test_total_marks(fx):
    t = P.parse_total_marks(fx("StudentTotalMarks.asp.html"))
    if not t.subjects:                       # образец из HAR: «Оценки не проставлены»
        assert t.period_names == []
        return
    assert len(t.period_names) >= 2 and t.info.get("Учебный год") and t.generated_at
    assert len({s.subject for s in t.subjects}) == len(t.subjects)
    assert any(s.periods for s in t.subjects)
    for s in t.subjects:
        assert set(s.periods) <= set(t.period_names)
        for v in [*s.periods.values(), s.year, s.exam, s.final]:
            assert v is None or (v and v == v.strip())


def test_month_events(fx):
    html = fx("MonthViewS.asp.html")
    month, year = selected_month(html)
    events = P.parse_month_events(html)
    table = P.soup_of(html).find("table", class_="schedule-table")
    links = [a for a in table.find_all("a") if "EditEvent(" in (a.get("href") or "") + (a.get("onclick") or "")]
    assert sum(e.event_id is not None for e in events) == len(links)
    for e in events:
        assert e.title and (e.date.year, e.date.month) == (year, month)
        assert "<" not in e.title


def test_birthdays(fx):
    html = fx("MonthBirth.asp.html")
    month, _ = selected_month(html)
    items = P.parse_birthdays(html)
    assert items
    for b in items:
        assert b.name and b.role and b.birth_date, b
        assert b.birth_date.month == month and 1920 < b.birth_date.year < date.today().year


def test_announcements(fx):
    html = fx("ViewAnnouncements.asp.html")
    ads = P.parse_announcements(html)
    assert ads and len(ads) == html.count('class="advertisement"')
    assert len({a.key for a in ads}) == len(ads)
    for a in ads:
        assert a.title and a.date and a.author and a.author_uid, a.title
        assert not a.title.startswith("Тема")
        assert "<" not in a.text and "\n\n\n" not in a.text and a.text == a.text.strip()
    assert any("\n" in a.text for a in ads)          # <br> превратились в переносы


def test_report_forms(fx):
    for name, extra in (("ReportStudentTotal.asp.html", {"ADT", "DDT"}),
                        ("ReportParentInfoLetter.asp.html", {"TERMID", "DATE", "dtWeek", "ReportType"}),
                        ("ReportStudentTotalMarks.asp.html", set())):
        form = dict(P.parse_forms(fx(name))["Reports"])
        assert {"AT", "VER", "SID", "PCLID", "ThmID", "RPTID"} | extra <= set(form)
        assert form["RP"] == ""


def test_reports_list(fx):
    pages = {r.page for r in P.parse_reports_list(fx("Reports.asp.html"))}
    assert {"ReportStudentTotal.asp", "ReportParentInfoLetter.asp", "ReportStudentTotalMarks.asp"} <= pages
