#!/usr/bin/env python3
"""Логинится и сохраняет сырые ответы разделов в dumps/ — для разработки и проверки парсеров.

    python tools/dump_pages.py            # все разделы
    python tools/dump_pages.py --files    # + поиск файла в ДЗ (эта и прошлая неделя) и его скачивание

Логин/пароль берутся из NS_LOGIN / NS_PASSWORD или спрашиваются.
AT в файлах заменяется нулями, но ФИО и оценки остаются — НЕ коммить dumps/.
"""
import asyncio
import getpass
import os
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from netschool import NetSchoolClient, parsers as P  # noqa: E402

OUT = Path("dumps")
MAX_DETAILS = 40          # не больше стольких «Подробностей» за запуск — сервер старый


def save(ns: NetSchoolClient, name: str, text: str | None) -> None:
    text = text or ""
    if ns.state.at:
        text = text.replace(ns.state.at, "0" * len(ns.state.at))
    (OUT / name).write_text(text, encoding="utf-8")


async def find_file(ns: NetSchoolClient) -> None:
    """Ищет первое вложение в подробностях ДЗ и пробует его скачать (экспериментальная часть)."""
    today = ns.today()
    seen: set[int] = set()
    for week in (today, today - timedelta(days=7)):
        for a in await ns.diary(week):
            if a.aid is None or a.aid in seen or len(seen) >= MAX_DETAILS:
                continue
            seen.add(a.aid)
            details = await ns.assignment_details(a)
            files = [f for f in details.attachments if f.attachment_id is not None]
            if not details.attachments:
                continue
            save(ns, "details_with_file.json", ns.last_text)
            print(f"файл в ДЗ: {a.subject} {a.date}: {[f.name for f in details.attachments]}")
            if not files:
                print("  ссылка не в формате openAttachment — пришли details_with_file.json")
                return
            try:
                got = await ns.download_attachment(files[0])
            except Exception as e:  # noqa: BLE001
                print(f"  скачать не вышло: {type(e).__name__}: {e}")
                save(ns, "download_error.html", ns.last_text)
                return
            target = OUT / "files" / P.safe_filename(got.name)
            target.parent.mkdir(exist_ok=True)
            target.write_bytes(got.content)
            print(f"  скачано: {target} ({got.size} байт, {got.content_type})")
            return
    print(f"файлов не нашлось (проверено заданий: {len(seen)})")


async def main() -> None:
    login = os.environ.get("NS_LOGIN") or input("Логин NetSchool: ")
    password = os.environ.get("NS_PASSWORD") or getpass.getpass("Пароль: ")
    OUT.mkdir(exist_ok=True)
    async with NetSchoolClient() as ns:
        await ns.login(login, password)
        jobs = {
            "diary": ns.diary,
            "day": ns.day_schedule,
            "week": ns.week_schedule,
            "year": ns.year_calendar,
            "month_events": ns.month_events,
            "birthdays": ns.birthdays,
            "announcements": ns.announcements,
            "reports": ns.reports,
            "student_total": ns.student_total,
            "parent_letter": ns.parent_letter,
            "total_marks": ns.total_marks,
            # образца ещё нет: «Итоги учебного периода» в информационном письме
            "letter_period": lambda: ns.raw_report("ReportParentInfoLetter.asp", thm_id=2, rpt_id=4,
                                                   selects={"ReportType": "2"}),
        }
        for name, job in jobs.items():
            ns.last_text = None
            try:
                result = await job()
                size = len(result) if isinstance(result, (list, str)) else None
                status = "ok" + (f" ({size})" if size is not None else "")
            except Exception as e:  # noqa: BLE001 — это отладочный скрипт
                status = f"ошибка: {type(e).__name__}: {e}"
            save(ns, f"{name}.html", ns.last_text)
            print(f"{name:<14} {status}")
        if "--files" in sys.argv:
            await find_file(ns)
    print(f"Сохранено в {OUT.resolve()}")


if __name__ == "__main__":
    asyncio.run(main())
