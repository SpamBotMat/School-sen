"""Тесты без фикстур — работают у всех."""
import hashlib
from datetime import date

from netschool import parsers as P
from netschool.changes import assignment_from_dict, assignment_to_dict, diff_assignments
from netschool.client import password_md5, prepare_password
from netschool.models import Assignment


def test_dates():
    assert P.parse_date("1.09.26") == date(2026, 9, 1)
    assert P.parse_date("Срок 21.09.2026") == date(2026, 9, 21)
    assert P.format_date(date(2026, 9, 1)) == "1.09.26"
    assert P.week_monday(date(2026, 9, 27)) == date(2026, 9, 21)
    assert P.parse_ru_date("21 сентября 2026 г.") == date(2026, 9, 21)
    assert P.parse_number("3,29") == 3.29 and P.parse_number("") is None


def test_split_subject():
    s = P.split_subject("Ин.яз./Английский язык/11В_техн./Базовый\xa0[303А Кабинет английского языка]")
    assert (s.name, s.full_name, s.group, s.level, s.room) == (
        "Английский язык", "Ин.яз./Английский язык", "11В_техн.", "Базовый", "303А Кабинет английского языка")
    s = P.split_subject("Обществознание/11В [102 Кабинет]")
    assert (s.name, s.group, s.level, s.room) == ("Обществознание", "11В", None, "102 Кабинет")
    assert P.split_subject("Биология").name == "Биология"


def test_parse_cell():
    c = P.parse_cell("4\xa03", date(2026, 9, 21))
    assert c.marks == ["4", "3"] and not c.attendance
    c = P.parse_cell("ОТ ·", date(2026, 9, 21))
    assert c.attendance == ["ОТ"] and c.debt


def test_password_md5_matches_site_js():
    # для обычных паролей это md5 в cp1251
    assert password_md5("Пароль№1") == hashlib.md5("Пароль№1".encode("cp1251")).hexdigest()
    # символы вне таблицы сайта превращаются в байт 0 (как charCodeAt_ в md5r.min.js)
    assert password_md5("€") == hashlib.md5(b"\x00").hexdigest()
    assert prepare_password("abc")[1] == 3


def test_override_pairs_keeps_order():
    pairs = [("A", "1"), ("RP", "R"), ("B", "2")]
    assert P.override_pairs(pairs, {"RP": "", "C": 3}) == [("A", "1"), ("RP", ""), ("B", "2"), ("C", "3")]


def test_table_grid_spans():
    html = ("<table><tr><th rowspan=2>Предмет</th><th colspan=2>Сентябрь</th></tr>"
            "<tr><th>1</th><th>2</th></tr><tr><td>Физика</td><td>5</td><td>4</td></tr></table>")
    t = P.table_grid(P.soup_of(html).find("table"))
    assert t.header == [["Предмет", "Сентябрь", "Сентябрь"], ["Предмет", "1", "2"]]
    assert t.rows == [["Физика", "5", "4"]]


def test_details_with_attachment():
    payload = {"data": {"strTitle": "Домашнее задание для ученика: Физика/11В_техн. (Учитель)",
                        "strTable": "<table><tr><th>Домашнее задание</th><td>№1</td></tr>"
                                    "<tr><th>Срок сдачи</th><td>21 сентября 2026 г.</td></tr>"
                                    "<tr><th>Присоединенный файл</th><td><a href='/asp/a.asp?id=1'>f.pdf</a></td></tr></table>"},
               "isError": False}
    d = P.parse_assignment_details(payload)
    assert d.subject == "Физика" and d.group == "11В_техн." and d.teacher == "Учитель"
    assert d.date == date(2026, 9, 21) and d.attachments[0].name == "f.pdf"


def test_diff_assignments():
    base = Assignment(date(2026, 9, 21), "Физика", "Д", "Домашняя работа", "№1", None, False, 1, 2, 3)
    graded = assignment_from_dict({**assignment_to_dict(base), "mark": "5"})
    oral = Assignment(date(2026, 9, 21), "Физика", "О", "Ответ на уроке", "Тема", "4", False, 9, 2, 10)
    kinds = [c.kind for c in diff_assignments([base], [graded, oral])]
    assert kinds == ["mark", "mark"]
    assert [c.kind for c in diff_assignments([], [base])] == ["new"]


# ───────────── новые разделы (синтетические образцы по структуре живых страниц) ─────────────

from bs4 import BeautifulSoup  # noqa: E402

from netschool.models import Birthday  # noqa: E402


def soup(html: str):
    return BeautifulSoup(html, "html.parser")


def test_cell_lines_breaks_on_br_and_blocks():
    td = soup("<td><div align=right><a>1</a></div>- <a>Каникулы</a><br>второе<br><br>после пустой</td>").td
    assert [P.clean(x) for x in P.cell_lines(td)] == ["1", "- Каникулы", "второе", "", "после пустой"]


def test_birth_date_two_digit_year():
    today = date(2026, 11, 6)
    assert P._birth_date("23.11.08", today) == date(2008, 11, 23)
    assert P._birth_date("15.03.75", today) == date(1975, 3, 15)
    assert P._birth_date("1.01.26", today) == date(2026, 1, 1)
    assert P._birth_date("1.01.27", today) == date(1927, 1, 1)
    assert P._birth_date("3.04.1999", today) == date(1999, 4, 3)
    assert P._birth_date("", today) is None and P._birth_date("31.02.10", today) is None


def test_birthday_helpers():
    b = Birthday(name="Тест", birth_date=date(2008, 2, 29))
    assert b.next_birthday(date(2026, 11, 6)) == date(2027, 3, 1)       # 2027 — не високосный
    assert b.next_birthday(date(2027, 12, 1)) == date(2028, 2, 29)
    assert b.age_on(date(2026, 2, 28)) == 17 and b.age_on(date(2026, 3, 1)) == 18
    assert Birthday(name="x", birth_date=None).next_birthday(date(2026, 1, 1)) is None


def test_attachment_links():
    td = soup("""<td><a href="JavaScript:$.openAttachment('/doc/%D0%94%D0%97.pdf', 1234)">ДЗ.pdf</a>
        <a href="#" onclick="openAttachment('/doc/it\\'s.docx', 77);return false"></a>
        <a href='JavaScript:$.openAttachment("/doc/a b.txt", 5)'>a b.txt</a>
        <a href="SubmAssign(1,2,3)">не файл</a></td>""").td
    got = [(a.name, a.url, a.attachment_id) for a in P.parse_attachment_links(td)]
    assert got == [("ДЗ.pdf", "/doc/%D0%94%D0%97.pdf", 1234), ("it's.docx", "/doc/it's.docx", 77),
                   ("a b.txt", "/doc/a b.txt", 5)]


def test_filename_from_disposition():
    f = P.filename_from_disposition
    assert f(b'attachment; filename="report.pdf"') == "report.pdf"
    assert f("attachment; filename*=UTF-8''%D0%94%D0%97%20%E2%84%961.docx") == "ДЗ №1.docx"
    assert f('attachment; filename="Домашка.docx"'.encode("cp1251")) == "Домашка.docx"
    assert f('attachment; filename="Домашка.docx"'.encode("utf-8")) == "Домашка.docx"
    assert f(b"attachment; filename=../../etc/passwd") == "passwd"
    assert f(b"attachment") is None and f(None) is None
    assert P.safe_filename('a/b\\c:d?.pdf ') == "c_d_.pdf" and P.safe_filename("...") == ""


def test_month_events_synthetic():
    html = """<table class="schedule-table"><tr><th>Пн</th></tr><tr>
      <td valign="top" class="vacation-day"><div align=right><a href="JavaScript:dayschedule('2.11.26')">2</a></div>
        - <a href="JavaScript:EditEvent(3,25)" onclick="EditEvent(3,25);return false" class="vacation-day">Осенние каникулы</a><br>
        - <a href="JavaScript:EditEvent(1,7)" onclick="EditEvent(1,7);return false">Родительское собрание</a><br></td>
      <td><div align=right><a href="JavaScript:dayschedule('3.11.26')">3</a></div></td><td></td></tr></table>"""
    events = P.parse_month_events(html)
    assert [(e.date, e.title, e.event_type, e.event_id, e.vacation) for e in events] == [
        (date(2026, 11, 2), "Осенние каникулы", 3, 25, True),
        (date(2026, 11, 2), "Родительское собрание", 1, 7, True),   # ячейка дня — каникулярная
    ]


def test_birthdays_synthetic():
    html = """<table class="table"><tr><th>Класс</th><th>Роль</th><th>Дата рождения</th><th>Имя на экране</th></tr>
      <tr><td>11В</td><td>Ученик</td><td>29.02.08</td><td>Тестов Тест</td></tr>
      <tr><td>&nbsp;</td><td>Учитель</td><td>3.11.75</td><td>Учителева А.</td></tr></table>"""
    items = P.parse_birthdays(html, today=date(2026, 11, 6))
    assert [(b.class_name, b.role, b.birth_date, b.name) for b in items] == [
        ("11В", "Ученик", date(2008, 2, 29), "Тестов Тест"),
        (None, "Учитель", date(1975, 11, 3), "Учителева А."),
    ]


def test_announcements_synthetic():
    html = """<div class="advertisement"><div class="adver-body">
        <h3><span>Тема:</span> Экскурсия</h3>
        <div class="visible-xs"><a href="#" class="username-coloured">Иванова И. И.</a></div>
        <div class="adver-info"><span>5.11.26</span></div>
        <div class="adver-content">Строка 1<BR>Строка 2<br><br><br>Строка 3
          <a href="JavaScript:openAttachment('/doc/plan.pdf', 42)">plan.pdf</a></div></div>
      <div class="adver-profile hidden-xs"><div class="profile-image">
        <a style="background: url(/asp/SetupSchool/photo.asp?AT=0&SVER=1&UID=7) center"></a></div>
        <div><span class="username-coloured">Иванова И. И.</span></div></div></div>"""
    [ad] = P.parse_announcements(html)
    assert (ad.title, ad.author, ad.date, ad.author_uid) == ("Экскурсия", "Иванова И. И.", date(2026, 11, 5), 7)
    assert ad.text == "Строка 1\nСтрока 2\n\nСтрока 3 plan.pdf"
    assert [(a.url, a.attachment_id) for a in ad.attachments] == [("/doc/plan.pdf", 42)]
    assert ad.key == "2026-11-05|Иванова И. И.|Экскурсия"


def test_total_marks_synthetic():
    html = """<div>Учебный год: 2026/2027</div><table class="table-print-num">
      <tr><th rowspan="2">№<br>п/п</th><th rowspan="2">Предмет</th><th colspan="4">Учебные периоды</th>
          <th rowspan="2">Год.<br>оценка</th><th colspan="1">Экз.<br>оценка</th><th rowspan="2">Итог.<br>оценка</th></tr>
      <tr><th>1 четверть</th><th>2 четверть</th><th>3 четверть</th><th>4 четверть</th><th>Экз.оценка</th></tr>
      <tr><td>1</td><td>Физика</td><td>4</td><td>&nbsp;</td><td></td><td></td><td>5</td><td></td><td>5</td></tr>
      <tr><td>2</td><td>Биология</td><td></td><td></td><td></td><td></td><td></td><td></td><td></td></tr></table>"""
    t = P.parse_total_marks(html)
    assert t.period_names == ["1 четверть", "2 четверть", "3 четверть", "4 четверть"]
    phys, bio = t.subjects
    assert (phys.subject, phys.periods, phys.year, phys.exam, phys.final) == ("Физика", {"1 четверть": "4"}, "5", None, "5")
    assert (bio.periods, bio.year) == ({}, None)
    assert P.parse_total_marks("<p>Оценки не проставлены</p>").subjects == []
