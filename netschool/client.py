"""Асинхронный клиент NetSchool 5.10 (ИрТех) на httpx.

Повторяет то, что делает браузер: логин через /webapi/auth/getdata +
/asp/postlogin.asp, дальше обычные POST-формы с AT/VER. Все запросы одной
сессии идут строго по очереди (asyncio.Lock) и с паузой min_interval —
сервер старый, не нагружай его.

Пример:
    async with NetSchoolClient() as ns:
        await ns.login("логин", "пароль")
        week = await ns.diary()
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time as _time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Awaitable, Callable, TypeVar
from urllib.parse import unquote, urlencode, urlsplit

import httpx

from . import parsers as P
from .errors import LoginError, NetSchoolError, SessionExpired
from .models import (
    Announcement, Assignment, AssignmentDetails, Attachment, AttachmentFile, Birthday, Lesson,
    MonthEvent, PageState, ParentLetter, ReportLink, StudentTotalReport, TotalMarksReport,
    YearCalendar,
)

log = logging.getLogger(__name__)
T = TypeVar("T")

DEFAULT_BASE_URL = "http://school.trsosh.edu.tm"   # HTTPS у сервера нет
ASHGABAT_TZ = timezone(timedelta(hours=5), "Asia/Ashgabat")  # UTC+5 без перехода на летнее время
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/129.0 Safari/537.36")

# Пункты верхнего меню (MenuItem) и вкладки (TabItem) — как в MenuForm на живых страницах.
MENU_ANNOUNCE, MENU_SCHEDULE, MENU_REPORTS, MENU_DIARY = 0, 12, 14, 16
TAB_YEAR, TAB_MONTH, TAB_WEEK, TAB_DAY, TAB_REPORTS, TAB_ANNOUNCE, TAB_DIARY = 9, 18, 19, 20, 24, 25, 30


@dataclass(frozen=True, slots=True)
class SchoolParams:
    """Параметры школы со страницы входа (ajaxmethods_login.asp?method=InitLoginForm)."""

    cid: int = 3     # страна: Туркменистан
    sid: int = 87    # регион: Ахал (НЕ путать с SID ученика в отчётах)
    pid: int = -1    # район: «Все»
    cn: int = 3      # город: Ашхабад
    sft: int = 2     # тип: общеобразовательная
    scid: int = 1    # школа: СТРСОШ им. А.С. Пушкина


def _ns_byte(unit: int) -> int:
    """Байт символа как в md5r.min.js (charCodeAt_): Latin-1 как есть, кириллица → cp1251, прочее → 0."""
    if unit <= 255:
        return unit
    if 1040 <= unit <= 1103:          # А..я
        return unit - 848
    return {1025: 168, 1105: 184, 8470: 185}.get(unit, 0)   # Ё, ё, №


def _utf16_units(text: str) -> list[int]:
    raw = text.encode("utf-16-le")
    return [int.from_bytes(raw[i:i + 2], "little") for i in range(0, len(raw), 2)]


def password_md5(password: str) -> str:
    """hexMD5_(password) как в браузере — именно его стоит хранить (зашифрованным), а не пароль."""
    return hashlib.md5(bytes(_ns_byte(u) for u in _utf16_units(password))).hexdigest()


def prepare_password(password: str) -> tuple[str, int]:
    """(md5 пароля, длина пароля в JS-символах) — всё, что нужно для входа без самого пароля."""
    return password_md5(password), len(_utf16_units(password))


class NetSchoolClient:
    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        *,
        school: SchoolParams = SchoolParams(),
        timeout: float = 30.0,
        min_interval: float = 0.5,
        auto_relogin: bool = True,
        tz: timezone = ASHGABAT_TZ,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.school = school
        self.min_interval = min_interval
        self.auto_relogin = auto_relogin
        self.tz = tz
        self.state = PageState()
        self.entry_point: str | None = None
        self.last_html: str | None = None       # последняя полная страница — удобно для отладки
        self.entry_html: str | None = None      # первая страница после входа (дневник/объявления) — можно не запрашивать ещё раз
        self.last_text: str | None = None       # последний текстовый ответ (в т.ч. отчёта); файлы сюда не попадают
        self._creds: tuple[str, str, int] | None = None
        self._referer = "/"
        self._lock = asyncio.Lock()
        self._last_request = 0.0
        self._http = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=timeout,
            follow_redirects=True,
            transport=transport,
            headers={"User-Agent": USER_AGENT, "Accept-Language": "ru-RU,ru;q=0.9"},
        )

    # ───────────────────────── жизненный цикл ─────────────────────────

    async def __aenter__(self) -> "NetSchoolClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.logout()
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    @property
    def logged_in(self) -> bool:
        return bool(self.state.at)

    def today(self) -> date:
        return datetime.now(self.tz).date()

    # ───────────────────────── вход / выход ─────────────────────────

    async def login(
        self,
        username: str,
        password: str | None = None,
        *,
        password_md5: str | None = None,
        password_len: int | None = None,
    ) -> None:
        """Вход. Можно передать пароль или (password_md5, password_len) из БД."""
        if password is not None:
            password_md5, password_len = prepare_password(password)
        if not password_md5 or not password_len:
            raise ValueError("нужен password или пара password_md5 + password_len")
        self._creds = (username, password_md5.lower(), int(password_len))
        async with self._lock:
            await self._do_login()

    async def logout(self) -> None:
        """Закрывает сессию на сервере (освобождает место в «В системе работает N»)."""
        if not self.state.at:
            return
        async with self._lock:
            try:
                await self._post("/asp/logout.asp", [("at", self.state.at), ("VER", self._ms())], retries=0)
            except NetSchoolError as e:
                log.debug("logout: %s", e)
            finally:
                self.state = PageState()
                self.entry_html = None

    async def _do_login(self) -> None:
        assert self._creds is not None
        username, pmd5, plen = self._creds
        self._http.cookies.clear()
        self.state = PageState()
        self.entry_html = None
        self._referer = "/"

        r = await self._post("/webapi/auth/getdata", [], xhr=True)
        try:
            data = r.json()
            salt, lt, ver = str(data["salt"]), str(data["lt"]), str(data["ver"])
        except (ValueError, KeyError, TypeError) as e:
            raise NetSchoolError(f"getdata: неожиданный ответ: {P.snippet(r.text)}") from e

        pw2 = hashlib.md5((salt + pmd5).encode("ascii")).hexdigest()
        s = self.school
        pairs = [
            ("LoginType", "1"), ("CID", str(s.cid)), ("SID", str(s.sid)), ("PID", str(s.pid)),
            ("CN", str(s.cn)), ("SFT", str(s.sft)), ("SCID", str(s.scid)),
            ("UN", username), ("PW", pw2[:plen]), ("lt", lt), ("pw2", pw2), ("ver", ver),
        ]
        # retries=0: повтор входа может засчитаться как ещё одна неудачная попытка
        r = await self._post("/asp/postlogin.asp", pairs, xhr=True, retries=0, check=False)
        try:
            answer = r.json()
        except ValueError:
            raise LoginError(f"вход не удался (HTTP {r.status_code}): {P.snippet(r.text)}",
                             status=r.status_code) from None
        if r.status_code >= 400 or not isinstance(answer, dict):
            msg = answer.get("message") if isinstance(answer, dict) else None
            raise LoginError(msg or f"вход не удался (HTTP {r.status_code})", status=r.status_code,
                             server_message=msg)
        at = answer.get("AT")
        if not at:
            msg = answer.get("message")
            raise LoginError(msg or "сервер не выдал AT (неверный логин или пароль?)", status=r.status_code,
                             server_message=msg)

        self.state = PageState(at=str(at))
        self.entry_point = answer.get("EntryPoint") or "/asp/Announce/ViewAnnouncements.asp"
        request_data = answer.get("RequestData") or {}
        pairs = [(str(k), str(v)) for k, v in request_data.items()] + [("at", self.state.at)]
        r = await self._post(self.entry_point, pairs)
        if P.is_full_page(r.text):
            self._remember(r.text, self.entry_point)
            self.entry_html = r.text
        else:
            # например, страница-предупреждение о слабом пароле; AT при этом уже рабочий
            log.warning("NetSchool: после входа неожиданная страница %s: %s",
                        self.entry_point, P.snippet(r.text))

    # ───────────────────────── низкий уровень ─────────────────────────

    @staticmethod
    def _ms() -> str:
        return str(int(_time.time() * 1000))

    async def _throttle(self) -> None:
        wait = self._last_request + self.min_interval - _time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        self._last_request = _time.monotonic()

    async def _post(
        self,
        path: str,
        pairs: list[tuple[str, str]] | None = None,
        *,
        xhr: bool = False,
        at_header: bool = False,
        referer: str | None = None,
        retries: int = 2,
        check: bool = True,
    ) -> httpx.Response:
        headers = {
            "Content-Type": "application/x-www-form-urlencoded" + ("; charset=UTF-8" if xhr else ""),
            "Referer": self.base_url + (referer or self._referer),
        }
        if xhr:
            headers["X-Requested-With"] = "XMLHttpRequest"
        if at_header and self.state.at:
            headers["at"] = self.state.at
        body = urlencode(pairs or []).encode("ascii")
        for attempt in range(retries + 1):
            await self._throttle()
            try:
                r = await self._http.post(path, content=body, headers=headers)
                break
            except httpx.TransportError as e:
                if attempt >= retries:
                    raise NetSchoolError(f"сеть: {type(e).__name__} на {path}") from e
                await asyncio.sleep(2 * (attempt + 1))
        ctype = r.headers.get("content-type", "").lower()
        is_text = not ctype or any(t in ctype for t in ("text", "json", "javascript", "xml"))
        self.last_text = r.text if is_text else None
        if not check:
            return r
        if r.status_code == 401:
            raise SessionExpired(r.headers.get("auth-error") or f"HTTP 401 на {path}")
        if r.status_code >= 400:
            raise NetSchoolError(f"HTTP {r.status_code} на {path}: {P.snippet(r.text)}")
        return r

    def _base(self) -> list[tuple[str, str]]:
        if not self.state.at:
            raise SessionExpired("нет активной сессии — сначала login()")
        return [("LoginType", "0"), ("AT", self.state.at), ("VER", self.state.ver or self._ms())]

    def _remember(self, html: str, path: str) -> None:
        st = P.parse_page_state(html)
        old = self.state
        self.state = PageState(
            at=st.at or old.at,
            ver=st.ver or old.ver,
            pclid_iup=st.pclid_iup or old.pclid_iup,
            pclid=st.pclid or old.pclid,
            menu_item=st.menu_item or old.menu_item,
            tab_item=st.tab_item or old.tab_item,
            has_menu=st.has_menu,
        )
        self._referer = path
        self.last_html = html

    async def _page(self, path: str, pairs: list[tuple[str, str]]) -> str:
        """Обычная страница: проверяем, что это не страница входа, и запоминаем AT/VER."""
        r = await self._post(path, pairs)
        html = r.text
        if not P.is_full_page(html):
            if P.looks_like_login_page(html):
                raise SessionExpired(f"{path}: сервер вернул страницу входа")
            raise SessionExpired(f"{path}: неожиданный ответ: {P.snippet(html)}")
        self._remember(html, path)
        return html

    async def _nav(self, path: str, tab_item: int, menu_item: int | None = None) -> str:
        """Переход по меню — как SetSelectedTab() в браузере."""
        menu = str(menu_item) if menu_item is not None else (self.state.menu_item or str(MENU_SCHEDULE))
        pairs = self._base() + [
            ("MenuItem", menu), ("TabItem", str(tab_item)), ("optional", "optional"),
        ]
        return await self._page(path, pairs)

    async def _run(self, fn: Callable[..., Awaitable[T]], *args: object) -> T:
        """Выполняет операцию под замком; при истёкшей сессии один раз входит заново."""
        async with self._lock:
            try:
                return await fn(*args)
            except SessionExpired:
                if not (self.auto_relogin and self._creds):
                    raise
                log.info("NetSchool: сессия истекла — вход заново")
                await self._do_login()
                return await fn(*args)

    async def _ensure_pclid(self) -> None:
        if not self.state.pclid_iup:
            await self._nav("/asp/Curriculum/Assignments.asp", TAB_DIARY, MENU_DIARY)

    # ───────────────────────── дневник ─────────────────────────

    async def diary(self, week: date | None = None) -> list[Assignment]:
        """Задания за неделю, в которую входит `week` (по умолчанию — текущая)."""
        return P.parse_diary(await self._run(self._diary_html, week))

    async def diary_html(self, week: date | None = None) -> str:
        """Сырая страница дневника за неделю — чтобы не разбирать её, если ничего не поменялось
        (сравни parsers.content_fingerprint с прошлым разом)."""
        return await self._run(self._diary_html, week)

    async def _diary_html(self, week: date | None) -> str:
        monday = P.week_monday(week or self.today())
        entry, self.entry_html = self.entry_html, None
        if entry and (self.entry_point or "").endswith("Assignments.asp") and P.selected_week(entry) == monday:
            return entry          # стартовая страница ученика — и есть дневник этой недели: запрос не нужен
        if not self.state.pclid_iup:
            html = await self._nav("/asp/Curriculum/Assignments.asp", TAB_DIARY, MENU_DIARY)
            if P.selected_week(html) == monday:
                return html
        pairs = self._base() + [("DATE", P.format_date(monday)), ("PCLID_IUP", self.state.pclid_iup or "")]
        return await self._page("/asp/Curriculum/Assignments.asp", pairs)

    async def assignment_details(self, assignment: Assignment) -> AssignmentDetails:
        """Окно «Подробности задания»: текст от учителя, срок, файлы."""
        if assignment.aid is None:
            raise ValueError("у задания нет AID (нет ссылки на подробности)")
        return await self._run(self._details, assignment.aid, assignment.cid, assignment.tp)

    async def _details(self, aid: int, cid: int, tp: int) -> AssignmentDetails:
        await self._ensure_pclid()
        pairs = [("AT", self.state.at or ""), ("AID", str(aid)), ("CID", str(cid)), ("TP", str(tp)),
                 ("PCLID_IUP", self.state.pclid_iup or "")]
        r = await self._post("/asp/ajax/Assignments/GetAssignmentInfo.asp", pairs, xhr=True,
                             at_header=True, referer="/asp/Curriculum/Assignments.asp")
        text = r.text
        if not text.lstrip("\ufeff \r\n").startswith("{"):
            raise SessionExpired(f"GetAssignmentInfo: не JSON: {P.snippet(text)}")
        return P.parse_assignment_details(text)

    # ───────────────────────── расписание ─────────────────────────

    async def day_schedule(self, day: date | None = None) -> list[Lesson]:
        return await self._run(self._day_schedule, day)

    async def _day_schedule(self, day: date | None) -> list[Lesson]:
        await self._ensure_pclid()
        pairs = self._base() + [
            ("EventType", ""), ("EventID", ""), ("BackPage", "/asp/Calendar/DayViewS.asp"),
            ("PCLID_IUP", self.state.pclid_iup or ""), ("DATE", P.format_date(day or self.today())),
        ]
        return P.parse_day_schedule(await self._page("/asp/Calendar/DayViewS.asp", pairs))

    async def week_schedule(self, week: date | None = None) -> list[Lesson]:
        return await self._run(self._week_schedule, week)

    async def _week_schedule(self, week: date | None) -> list[Lesson]:
        await self._ensure_pclid()
        monday = P.week_monday(week or self.today())
        pairs = self._base() + [
            ("ViewType", "0"), ("DATE", P.format_date(monday)), ("Relay", "-1"),
            ("PCLID_IUP", self.state.pclid_iup or ""),
        ]
        return P.parse_week_schedule(await self._page("/asp/Calendar/WeekViewTimeS.asp", pairs))

    async def year_calendar(self) -> YearCalendar:
        """Учебные дни, выходные/праздники и каникулы на весь год."""
        return await self._run(self._year_calendar)

    async def _year_calendar(self) -> YearCalendar:
        return P.parse_year_calendar(await self._nav("/asp/SetupSchool/Calendar/YearView.asp", TAB_YEAR, MENU_SCHEDULE))

    async def month_events(self, month: int | None = None, year: int | None = None) -> list[MonthEvent]:
        """Мероприятия месяца (каникулы, праздники, школьные события) — Расписание → Месяц."""
        return await self._run(self._month_view, month, year, False)

    async def birthdays(self, month: int | None = None, year: int | None = None) -> list[Birthday]:
        """Именинники месяца (Расписание → Месяц → «Дни рождения»).

        Видно только тех, кого сервер показывает этой учётке (на живых данных — пока лишь свой ДР).
        """
        return await self._run(self._month_view, month, year, True)

    async def _month_view(self, month: int | None, year: int | None, birthdays: bool) -> list:
        today = self.today()
        month, year = month or today.month, year or today.year
        if not 1 <= month <= 12:
            raise ValueError(f"month должен быть 1..12, а не {month}")
        if not self.state.pclid:
            await self._nav("/asp/Calendar/MonthViewS.asp", TAB_MONTH, MENU_SCHEDULE)
        # Ровно форма View со страницы MonthViewS.asp (её же браузер шлёт и на MonthBirth.asp — changeView()).
        pairs = self._base() + [
            ("EventType", ""), ("EventID", ""), ("BackPage", "/asp/Calendar/MonthViewS.asp"),
            ("ViewType", "1" if birthdays else "0"), ("PCLID", self.state.pclid or ""),
            ("Month", str(month)), ("Year", str(year)), ("MonthYear", f"{month},{year}"),
        ]
        if birthdays:
            return P.parse_birthdays(await self._page("/asp/Calendar/MonthBirth.asp", pairs), today)
        return P.parse_month_events(await self._page("/asp/Calendar/MonthViewS.asp", pairs))

    # ───────────────────────── объявления и файлы ─────────────────────────

    async def announcements(self) -> list[Announcement]:
        """Доска объявлений (то, что видно после входа или по кнопке «Объявления»)."""
        return await self._run(self._announcements)

    async def _announcements(self) -> list[Announcement]:
        entry, self.entry_html = self.entry_html, None
        if entry and (self.entry_point or "").endswith("ViewAnnouncements.asp"):
            return P.parse_announcements(entry)   # стартовая страница — доска объявлений
        return P.parse_announcements(await self._page("/asp/Announce/ViewAnnouncements.asp", self._base()))

    async def download_attachment(self, attachment: Attachment) -> AttachmentFile:
        """ЭКСПЕРИМЕНТ: скачивает файл из ДЗ или объявления.

        Повторяет $.openAttachment(url, id) → postTo(url, {attachmentId}): POST url с at, VER и attachmentId.
        На живом файле ещё не проверено.
        """
        if not attachment.url or attachment.attachment_id is None:
            raise ValueError("у вложения нет url/attachment_id — скачать нельзя")
        host = urlsplit(attachment.url).netloc
        if host and host != urlsplit(self.base_url).netloc:
            raise ValueError(f"чужой адрес вложения: {attachment.url}")   # AT на сторонний сайт не шлём
        return await self._run(self._download, attachment)

    async def _download(self, attachment: Attachment) -> AttachmentFile:
        if not self.state.at:
            raise SessionExpired("нет активной сессии — сначала login()")
        pairs = [("at", self.state.at), ("VER", self._ms()), ("attachmentId", str(attachment.attachment_id))]
        r = await self._post(attachment.url or "", pairs)
        ctype = r.headers.get("content-type", "")
        raw_disposition = next((v for k, v in r.headers.raw if k.lower() == b"content-disposition"), None)
        if "text/html" in ctype.lower() and raw_disposition is None:
            text = r.text
            if P.looks_like_login_page(text):
                raise SessionExpired(f"{attachment.url}: сервер вернул страницу входа")
            raise NetSchoolError(f"вместо файла пришла страница: {P.snippet(text)}")
        fallback = attachment.name or unquote(urlsplit(attachment.url or "").path.rsplit("/", 1)[-1])
        name = P.filename_from_disposition(raw_disposition) or P.safe_filename(fallback) or "file"
        return AttachmentFile(name=name, content=r.content, content_type=ctype or None)

    # ───────────────────────── отчёты ─────────────────────────

    async def reports(self) -> list[ReportLink]:
        return await self._run(self._reports)

    async def _reports(self) -> list[ReportLink]:
        return P.parse_reports_list(await self._nav("/asp/Reports/Reports.asp", TAB_REPORTS, MENU_REPORTS))

    async def student_total(self, start: date | None = None, end: date | None = None) -> StudentTotalReport:
        """Отчёт об успеваемости и посещаемости: оценки по дням, пропуски, опоздания, «точки».

        Без дат — период по умолчанию из формы (обычно текущая четверть).
        """
        return await self._run(self._student_total, start, end)

    async def student_total_html(self, start: date | None = None, end: date | None = None) -> str:
        """Сырой HTML отчёта «Успеваемость и посещаемость» (для content_fingerprint)."""
        return await self._run(self._student_total_html, start, end)

    async def _student_total(self, start: date | None, end: date | None) -> StudentTotalReport:
        return P.parse_student_total(await self._student_total_html(start, end))

    async def _student_total_html(self, start: date | None, end: date | None) -> str:
        page = "ReportStudentTotal.asp"
        pairs, _ = await self._report_form(page, thm_id=2, rpt_id=1)
        changes = {}
        if start:
            changes["ADT"] = P.format_date(start)
        if end:
            changes["DDT"] = P.format_date(end)
        return await self._report_generate(page, P.override_pairs(pairs, changes))

    async def parent_letter(self, week: date | None = None, *, term_id: int | None = None) -> ParentLetter:
        """Информационное письмо: оценки с начала четверти, оценки за неделю и ЗАДОЛЖЕННОСТИ.

        term_id: 5..8 = 1..4 четверть (по умолчанию — текущая, как в форме).
        """
        return await self._run(self._parent_letter, week, term_id)

    async def _parent_letter(self, week: date | None, term_id: int | None) -> ParentLetter:
        page = "ReportParentInfoLetter.asp"
        pairs, html = await self._report_form(page, thm_id=2, rpt_id=4)
        selects = {"ReportType": "1"}   # 2 = «Итоги учебного периода» — образца пока нет
        if term_id is not None:
            selects["TERMID"] = str(term_id)
        pairs, html = await self._report_reselect(page, pairs, html, selects)
        if week is not None:
            monday = P.format_date(P.week_monday(week))
            options = [o.value for o in P.parse_select(html, "DATE")]
            if options and monday not in options:
                raise ValueError(f"неделя {monday} не входит в выбранную четверть")
            pairs = P.override_pairs(pairs, {"DATE": monday, "dtWeek": monday})
        return P.parse_parent_letter(await self._report_generate(page, pairs))

    async def total_marks(self) -> TotalMarksReport:
        """Итоговые отметки: четвертные, годовая, экзаменационная, итоговая (по всем предметам)."""
        return await self._run(self._total_marks)

    async def _total_marks(self) -> TotalMarksReport:
        page = "ReportStudentTotalMarks.asp"
        pairs, _ = await self._report_form(page, thm_id=1, rpt_id=0)
        return P.parse_total_marks(await self._report_generate(page, pairs, extra={"ISTF": "0"}))

    async def _report_form(self, page: str, *, thm_id: int, rpt_id: int) -> tuple[list[tuple[str, str]], str]:
        html = await self._page(f"/asp/Reports/{page}", self._base() + [("RPTID", str(rpt_id)), ("ThmID", str(thm_id))])
        pairs = P.parse_forms(html).get("Reports")
        if not pairs:
            raise NetSchoolError(f"{page}: на странице нет формы Reports")
        return pairs, html

    async def _report_reselect(self, page: str, pairs: list[tuple[str, str]], html: str,
                               selects: dict[str, str]) -> tuple[list[tuple[str, str]], str]:
        """Смена select'а с OnChangeSelect перезагружает форму — повторяем это."""
        for name, value in selects.items():
            if dict(pairs).get(name) == value:
                continue
            html = await self._page(f"/asp/Reports/{page}", P.override_pairs(pairs, {name: value}))
            pairs = P.parse_forms(html).get("Reports") or []
            if dict(pairs).get(name) != value:
                raise ValueError(f"{page}: сервер не принял {name}={value}")
        return pairs, html

    async def _report_generate(self, page: str, pairs: list[tuple[str, str]],
                               extra: dict[str, str] | None = None) -> str:
        # RP="R" — «разослать отчёт всем родителям». Никогда не отправляем.
        pairs = P.override_pairs(pairs, {"RP": "", **(extra or {})})
        action = "/asp/Reports/" + (page[len("Report"):] if page.startswith("Report") else page)
        r = await self._post(action, pairs, xhr=True, at_header=True, referer=f"/asp/Reports/{page}")
        P.raise_for_json_error(r.text)
        return r.text

    # ───────────────────────── отладка ─────────────────────────

    async def raw_page(self, path: str, fields: dict[str, str] | None = None) -> str:
        """Произвольная страница с LoginType/AT/VER — для исследования новых разделов."""
        async def go() -> str:
            return await self._page(path, self._base() + [(k, str(v)) for k, v in (fields or {}).items()])
        return await self._run(go)

    async def raw_report(self, page: str, *, thm_id: int, rpt_id: int,
                         selects: dict[str, str] | None = None,
                         fields: dict[str, str] | None = None) -> str:
        """Сырой ответ отчёта — для исследования (например, письмо с ReportType=2).

        page: "ReportParentInfoLetter.asp"; selects — поля с перезагрузкой формы (ReportType, TERMID);
        fields — остальные поля формы. RP всегда пустой: «разослать родителям» не отправляется.
        """
        async def go() -> str:
            pairs, html = await self._report_form(page, thm_id=thm_id, rpt_id=rpt_id)
            pairs, _ = await self._report_reselect(page, pairs, html, selects or {})
            return await self._report_generate(page, P.override_pairs(pairs, fields or {}))
        return await self._run(go)
