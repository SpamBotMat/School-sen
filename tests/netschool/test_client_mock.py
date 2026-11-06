"""Клиент против «поддельного» сервера на фикстурах (httpx.MockTransport).

Проверяет порядок запросов, параметры (AT, VER, RP, md5 пароля) и перелогин.
"""
import asyncio
import hashlib
from datetime import date
from urllib.parse import parse_qsl

import httpx
import pytest

from conftest import FIXTURES
from netschool import Attachment, LoginError, NetSchoolClient, NetSchoolError, TotalMarksReport
from netschool.client import password_md5

pytestmark = pytest.mark.skipif(not (FIXTURES / "Assignments.asp.html").exists(),
                                reason="нет tests/fixtures — запусти tools/har_to_fixtures.py")

AT = "0" * 23           # такой AT оставляет в фикстурах har_to_fixtures.py
SALT, PASSWORD = "1234567890", "секрет1"
PAGES = {
    "/asp/Curriculum/Assignments.asp": "Assignments.asp.html",
    "/asp/Calendar/DayViewS.asp": "DayViewS.asp.html",
    "/asp/Calendar/WeekViewTimeS.asp": "WeekViewTimeS.asp.html",
    "/asp/Calendar/MonthViewS.asp": "MonthViewS.asp.html",
    "/asp/SetupSchool/Calendar/YearView.asp": "YearView.asp.html",
    "/asp/Reports/Reports.asp": "Reports.asp.html",
    "/asp/Reports/ReportStudentTotal.asp": "ReportStudentTotal.asp.html",
    "/asp/Reports/ReportParentInfoLetter.asp": "ReportParentInfoLetter.asp.html",
    "/asp/Reports/ReportStudentTotalMarks.asp": "ReportStudentTotalMarks.asp.html",
}
REPORTS = {
    "/asp/Reports/StudentTotal.asp": "StudentTotal.asp.html",
    "/asp/Reports/ParentInfoLetter.asp": "ParentInfoLetter.asp.html",
    "/asp/Reports/StudentTotalMarks.asp": "StudentTotalMarks.asp.html",
}


class FakeServer:
    def __init__(self, expire_after_login: int = 0):
        self.calls: list[tuple[str, dict, httpx.Headers]] = []
        self.expire = expire_after_login     # сколько следующих запросов ответить 401
        self.extra: dict = {}                # path -> handler(form) для синтетических страниц

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        form = dict(parse_qsl(request.content.decode(), keep_blank_values=True))
        self.calls.append((path, form, request.headers))
        if path == "/webapi/auth/getdata":
            return httpx.Response(200, json={"lt": "42", "ver": "777", "salt": SALT})
        if path == "/asp/postlogin.asp":
            pw2 = hashlib.md5((SALT + password_md5(PASSWORD)).encode()).hexdigest()
            if form.get("pw2") != pw2 or form.get("PW") != pw2[:len(PASSWORD)] or form.get("ver") != "777":
                return httpx.Response(400, json={"message": "Неправильный пароль или имя пользователя"})
            return httpx.Response(200, json={"AT": AT, "EntryPoint": "/asp/Curriculum/Assignments.asp",
                                             "RequestData": {}})
        if path == "/asp/logout.asp":
            return httpx.Response(200, text="<html></html>")
        if self.expire > 0 and path in PAGES:
            self.expire -= 1
            return httpx.Response(200, text='<html><link href="/vendor/pages/about/css/start-page.min.css"></html>')
        if path == "/asp/ajax/Assignments/GetAssignmentInfo.asp":
            assert request.headers["at"] == AT and form["AT"] == AT
            name = "GetAssignmentInfo.asp.json" if form["TP"] == "3" else "GetAssignmentInfo.asp__2.json"
            return httpx.Response(200, text=(FIXTURES / name).read_text(encoding="utf-8"))
        if path in REPORTS:
            assert request.headers["x-requested-with"] == "XMLHttpRequest" and request.headers["at"] == AT
            assert form["RP"] == "" and form["SID"] and form["PCLID"]
            return httpx.Response(200, text=(FIXTURES / REPORTS[path]).read_text(encoding="utf-8"))
        if path in self.extra:
            return self.extra[path](form)
        if path in PAGES:
            assert form.get("AT") == AT or form.get("at") == AT, path
            return httpx.Response(200, text=(FIXTURES / PAGES[path]).read_text(encoding="utf-8"))
        return httpx.Response(404, text="not found")

    def paths(self) -> list[str]:
        return [c[0] for c in self.calls]


def make(server: FakeServer) -> NetSchoolClient:
    return NetSchoolClient(transport=httpx.MockTransport(server), min_interval=0)


def test_full_flow():
    server = FakeServer()

    async def go():
        async with make(server) as ns:
            await ns.login("Ученик", PASSWORD)
            assert ns.state.at == AT and ns.state.pclid_iup
            n = len(server.calls)
            diary = await ns.diary(date(2026, 9, 23))
            # стартовая страница после входа — дневник этой недели: лишнего запроса нет
            assert diary and len(server.calls) == n
            again = await ns.diary(date(2026, 9, 23))
            assert again == diary and server.calls[-1][1]["DATE"] == "21.09.26"
            details = await ns.assignment_details(diary[1])
            assert details.date
            assert await ns.day_schedule(date(2026, 9, 21))
            assert await ns.week_schedule(date(2026, 9, 21))
            assert (await ns.year_calendar()).days
            assert await ns.reports()
            total = await ns.student_total(date(2026, 9, 1), date(2026, 9, 30))
            assert total.subjects and server.calls[-1][1]["ADT"] == "1.09.26"
            letter = await ns.parent_letter(date(2026, 9, 24))
            assert letter.rows
            assert server.calls[-1][1]["DATE"] == server.calls[-1][1]["dtWeek"] == "21.09.26"
            assert isinstance(await ns.total_marks(), TotalMarksReport)
            assert server.calls[-1][1]["ISTF"] == "0"
        assert server.paths()[-1] == "/asp/logout.asp"
        login_form = server.calls[1][1]
        assert login_form["UN"] == "Ученик" and login_form["SCID"] == "1" and login_form["LoginType"] == "1"
        entry = server.calls[2]
        assert entry[0] == "/asp/Curriculum/Assignments.asp" and entry[1] == {"at": AT}

    asyncio.run(go())


def test_login_with_stored_hash():
    server = FakeServer()

    async def go():
        async with make(server) as ns:
            await ns.login("Ученик", password_md5=password_md5(PASSWORD), password_len=len(PASSWORD))
            assert ns.logged_in

    asyncio.run(go())


def test_wrong_password_is_not_retried():
    server = FakeServer()

    async def go():
        async with make(server) as ns:
            with pytest.raises(LoginError, match="Неправильный"):
                await ns.login("Ученик", "не тот")
        assert server.paths().count("/asp/postlogin.asp") == 1

    asyncio.run(go())


def test_relogin_on_expired_session():
    server = FakeServer()

    async def go():
        async with make(server) as ns:
            await ns.login("Ученик", PASSWORD)
            server.expire = 1                     # следующий запрос страницы — «страница входа»
            assert await ns.week_schedule(date(2026, 9, 21))
        assert server.paths().count("/webapi/auth/getdata") == 2

    asyncio.run(go())


LOGIN_PAGE = '<html><link href="/vendor/pages/about/css/start-page.min.css"></html>'


def full_page(body: str, menu: int = 12, tab: int = 18) -> str:
    """Минимальная «страница после входа»: MenuForm с AT/VER + содержимое."""
    return ('<html><body><form name="MenuForm">'
            f'<input type="hidden" name="AT" value="{AT}"><input type="hidden" name="VER" value="1">'
            f'<input type="hidden" name="MenuItem" value="{menu}"><input type="hidden" name="TabItem" value="{tab}">'
            f'</form>{body}</body></html>')


BIRTH_PAGE = full_page("""<select name="MonthYear"><option value="11,2026" selected>Ноябрь 2026</option></select>
    <table><tr><th>Класс</th><th>Роль</th><th>Дата рождения</th><th>Имя на экране</th></tr>
    <tr><td>11В</td><td>Ученик</td><td>23.11.08</td><td>Тестов Тест</td></tr></table>""")
ADS_PAGE = full_page("""<div class="advertisement"><div class="adver-body"><h3><span>Тема:</span> План</h3>
    <div class="adver-info"><span>5.11.26</span></div><div class="adver-content">Текст<br>
    <a href="JavaScript:openAttachment('/doc/План уроков.pdf', 42)">План уроков.pdf</a></div></div></div>""",
                     menu=0, tab=25)
PDF = b"%PDF-1.4 test"


def test_calendar_announcements_and_download():
    server = FakeServer()
    server.extra["/asp/Calendar/MonthBirth.asp"] = lambda form: httpx.Response(200, text=BIRTH_PAGE)
    server.extra["/asp/Announce/ViewAnnouncements.asp"] = lambda form: httpx.Response(200, text=ADS_PAGE)
    downloads = []

    def doc(form):
        downloads.append(form)
        if len(downloads) == 1:                      # первая попытка — «сессия истекла»
            return httpx.Response(200, text=LOGIN_PAGE, headers={"Content-Type": "text/html"})
        disposition = 'attachment; filename="План уроков.pdf"'.encode("cp1251")
        return httpx.Response(200, content=PDF, headers=[(b"Content-Type", b"application/pdf"),
                                                         (b"Content-Disposition", disposition)])
    server.extra["/doc/План уроков.pdf"] = doc

    async def go():
        async with make(server) as ns:
            await ns.login("Ученик", PASSWORD)
            events = await ns.month_events(9, 2026)
            form = server.calls[-1][1]
            assert server.paths()[-1] == "/asp/Calendar/MonthViewS.asp"
            assert (form["ViewType"], form["Month"], form["Year"], form["MonthYear"]) == ("0", "9", "2026", "9,2026")
            assert form["PCLID"] and all(e.date.month == 9 for e in events)

            [b] = await ns.birthdays(11, 2026)
            assert (b.name, b.birth_date) == ("Тестов Тест", date(2008, 11, 23))
            assert server.paths()[-1] == "/asp/Calendar/MonthBirth.asp" and server.calls[-1][1]["ViewType"] == "1"

            [ad] = await ns.announcements()
            assert ad.title == "План" and ns.state.menu_item == "0"
            await ns.reports()                       # из «Объявлений» в отчёты — с правильным MenuItem
            assert (server.calls[-1][1]["MenuItem"], server.calls[-1][1]["TabItem"]) == ("14", "24")

            got = await ns.download_attachment(ad.attachments[0])
            assert (got.name, got.content, got.content_type, got.size) == ("План уроков.pdf", PDF, "application/pdf", len(PDF))
            assert ns.last_text is None              # бинарный ответ не декодируется в текст
        assert server.paths().count("/webapi/auth/getdata") == 2      # перелогин после страницы входа
        assert downloads[-1]["attachmentId"] == "42" and downloads[-1]["at"] == AT and downloads[-1]["VER"].isdigit()

    asyncio.run(go())


def test_download_rejects_bad_attachments():
    server = FakeServer()
    server.extra["/doc/x.pdf"] = lambda form: httpx.Response(200, text=full_page("ошибка"),
                                                             headers={"Content-Type": "text/html"})

    async def go():
        async with make(server) as ns:
            await ns.login("Ученик", PASSWORD)
            with pytest.raises(ValueError):
                await ns.download_attachment(Attachment(name="x", url="/doc/x.pdf"))          # нет id
            with pytest.raises(ValueError):
                await ns.download_attachment(Attachment(name="x", url="http://evil.example/doc/x", attachment_id=1))
            with pytest.raises(NetSchoolError, match="вместо файла"):
                await ns.download_attachment(Attachment(name="x", url="/doc/x.pdf", attachment_id=1))
        assert not any("evil" in str(c[2].get("host", "")) for c in server.calls)

    asyncio.run(go())
