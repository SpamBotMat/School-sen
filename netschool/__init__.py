"""Клиент и парсеры для старого NetSchool 5.10 (ИрТех) — проект School Sensor."""
from .errors import LoginError, NetSchoolError, SessionExpired
from .models import (
    Announcement, Assignment, AssignmentDetails, Attachment, AttachmentFile, Birthday,
    CalendarDay, DayCell, Debt, GenericTable, Lesson, LetterRow, MonthEvent, PageState,
    ParentLetter, ReportLink, SelectOption, StudentTotalReport, Subject, SubjectPerformance,
    SubjectTotals, TotalMarksReport, YearCalendar,
)
from . import parsers

__all__ = [
    "LoginError", "NetSchoolError", "SessionExpired", "parsers",
    "Announcement", "Assignment", "AssignmentDetails", "Attachment", "AttachmentFile", "Birthday",
    "CalendarDay", "DayCell", "Debt", "GenericTable", "Lesson", "LetterRow", "MonthEvent",
    "PageState", "ParentLetter", "ReportLink", "SelectOption", "StudentTotalReport", "Subject",
    "SubjectPerformance", "SubjectTotals", "TotalMarksReport", "YearCalendar",
]

try:  # клиенту нужен httpx; парсеры работают и без него
    from .client import NetSchoolClient, SchoolParams, password_md5, prepare_password  # noqa: F401
    __all__ += ["NetSchoolClient", "SchoolParams", "password_md5", "prepare_password"]
except ImportError:  # pragma: no cover
    pass
