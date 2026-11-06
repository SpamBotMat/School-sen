# NetSchool 5.10.35574 — карта запросов (school.trsosh.edu.tm)

Собрано по HAR от 27.09.2026, **проверено на живом сервере 06.11.2026** (вход, дневник,
расписание дня/недели/месяца, год, отчёты, письмо, итоговые, именинники, объявления).
Сервер: IIS 8.5 / classic ASP, **только HTTP**.
Ответы в UTF-8, запросы — `POST application/x-www-form-urlencoded` (UTF-8).
Даты сервер пишет и принимает как `d.mm.yy` (`1.09.26`, `21.09.26`).

## Общие правила

- **Сессия** = токен `AT` (из `postlogin.asp`) + cookies.
- В каждой странице: `LoginType=0`, `AT`, `VER`. `VER` — скрытое поле
  последней страницы (секунды); JS-вызовы `postTo()` шлют `Date.now()` в мс.
  Клиент передаёт `VER` дальше, как браузер.
- **Меню**: `MenuItem` (раздел), `TabItem` (пункт), `optional=optional`. Сервер сам выставляет
  меню по адресу страницы, но клиент шлёт правильные пары, как браузер:
  `0/25` объявления · `12/9` год · `12/18` месяц · `12/19` неделя · `12/20` день ·
  `14/24` отчёты · `16/30` дневник.
- **AJAX**: заголовки `X-Requested-With: XMLHttpRequest` и `at: <AT>`; поле `AT` в теле.
  Истёкшая сессия → HTTP 401 + заголовок `auth-error: SessionExpired`.
- Названия полей ASP не зависят от регистра (`at` = `AT`).

## Вход и выход

1. `POST /webapi/auth/getdata` (пустое тело) → `{lt, ver, salt}`.
2. `pw2 = md5(salt + md5(пароль))`, hex в нижнем регистре. Внутренний `md5(пароль)` —
   как в `/asp/md5r.min.js`: кириллица → cp1251, символы 0–255 как есть,
   Ё/ё/№ → 168/184/185, всё остальное → байт 0. `PW = pw2[:длина пароля]`.
3. `POST /asp/postlogin.asp`: `LoginType=1, CID=3, SID=87, PID=-1, CN=3, SFT=2, SCID=1,
   UN, PW, lt, pw2, ver` → JSON `{AT, EntryPoint, RequestData}`.
   Ошибка — HTTP 4xx/5xx и JSON `{message}`.
4. `POST <EntryPoint>` c `RequestData` + `at=<AT>` → первая страница
   (по умолчанию доска объявлений, у тебя в настройках — дневник).
5. Выход: `POST /asp/logout.asp` (`at`, `VER`).

Параметры школы — из `ajaxmethods_login.asp?method=InitLoginForm`:
Туркменистан (3) → Ахал (87) → Все (-1) → Ашхабад (3) → Общеобразовательная (2) → СТРСОШ (1).

## Разделы

| Раздел | Запрос | Поля (кроме LoginType/AT/VER) | Парсер |
|---|---|---|---|
| Дневник | `/asp/Curriculum/Assignments.asp` | меню: `TabItem=30`; неделя: `DATE=<пн>`, `PCLID_IUP` | `parse_diary`, `parse_weeks` |
| Подробности ДЗ | `/asp/ajax/Assignments/GetAssignmentInfo.asp` (AJAX) | `AID, CID, TP` из `SubmAssign(...)`, `PCLID_IUP` | `parse_assignment_details` |
| День | `/asp/Calendar/DayViewS.asp` | `EventType=, EventID=, BackPage, PCLID_IUP, DATE` | `parse_day_schedule` |
| Неделя | `/asp/Calendar/WeekViewTimeS.asp` | `ViewType=0, DATE=<пн>, Relay=-1, PCLID_IUP` | `parse_week_schedule` |
| Месяц (мероприятия) | `/asp/Calendar/MonthViewS.asp` | `EventType=, EventID=, BackPage, ViewType=0, PCLID, Month, Year, MonthYear=11,2026` | `parse_month_events` |
| Именинники | `/asp/Calendar/MonthBirth.asp` | та же форма, `ViewType=1` (как `changeView()`) | `parse_birthdays` |
| Год | `/asp/SetupSchool/Calendar/YearView.asp` | меню: `TabItem=9` | `parse_year_calendar` |
| Список отчётов | `/asp/Reports/Reports.asp` | меню: `TabItem=24` | `parse_reports_list` |
| Объявления | `/asp/Announce/ViewAnnouncements.asp` | — | `parse_announcements` |
| Файл (ДЗ/объявление) | `POST /doc/<имя файла>` | `at, VER, attachmentId` (как `postTo`) | `download_attachment` (**эксп.**) |

### Отчёты — всегда два шага

1. Форма: `POST /asp/Reports/Report<X>.asp` с `RPTID, ThmID` → форма `Reports`
   (в ней `SID` — id ученика, `PCLID` — id класса, `PP`, `BACK`, `A, NA, TA, RT, RP`).
2. Генерация (AJAX): `POST /asp/Reports/<X>.asp` со всеми полями формы.
   Ответ — HTML-фрагмент или JSON `{message, isError}`.

> ⚠️ `RP=R` — «Разослать отчёт всем родителям». Клиент всегда шлёт `RP=` пустым.

| Отчёт | ThmID/RPTID | Доп. поля | Парсер |
|---|---|---|---|
| Итоговые отметки `StudentTotalMarks` | 1/0 | `ISTF=0` | `parse_total_marks` |
| Средний балл `StudentAverageMark` | 1/1 | ? | — |
| Динамика среднего балла `StudentAverageMarkDyn` | 1/2 | ? | — |
| Отчёт об успеваемости `StudentGrades` | 2/0 | ? | — |
| Успеваемость и посещаемость `StudentTotal` | 2/1 | `ADT, DDT` | `parse_student_total` |
| Итоги успеваемости `StudentAttendanceGrades` | 2/2 | ? | — |
| Доступ к журналу `JournalAccess` | 2/3 | ? | — |
| Письмо для родителей `ParentInfoLetter` | 2/4 | `ReportType=1/2, TERMID=5..8, DATE=dtWeek=<пн>` | `parse_parent_letter` |

Смена `select` с `OnChangeSelect` (TERMID, ReportType) перезагружает форму —
клиент делает это так же (`_report_reselect`).

## Особенности вёрстки (на что легко наступить)

- **Дневник**: дата только в первой строке дня (`rowspan`) + отдельные «мобильные»
  строки с датой. Просроченные — `bgcolor="#FFBBBB"`, показываются и из прошлых недель.
  `-` = нет оценки. Тип — код из легенды (`Д`, `О`, `Дт`, …).
- **Неделя**: каждый урок повторён ×4 через `,&nbsp;`, пустой слот — `-`,
  `</td>` местами не закрыт. Время есть только в виде «День».
- **Названия предметов** разные: в дневнике «Английский язык», в расписании
  «Ин.яз./Английский язык/11В_техн./Базовый [303А …]». `split_subject()` приводит
  к одному виду: `name`, `group`, `level`, `room`.
- **Средняя оценка** в «Успеваемости и посещаемости» — простое среднее без весов,
  `0` = оценок нет. В письме «Ср.балл» = среднее по счётчикам 5/4/3/2.
- В ячейках отчёта: `·` — «точка» (долг), коды посещаемости `УП, Б, НП, ОТ, ОП, ОСВ`.
- **Письмо**: блок «Задолженности по предметам» — готовый список долгов; строка «Итого».
- **Год**: праздники отмечены или однодневными «каникулами» (27.09, 6.10, 12.12, 18.05),
  или «выходным» в будний день (4.11, 23.02, 8.03, 1.05). Суббота — учебный день.
- Подписи «Состояние на 27.09.2026 11:12:24» — время формирования отчёта.
- **Итоговые отметки**: `table.table-print-num`, шапка в 2 строки:
  `№ п/п | Предмет | Учебные периоды (1–4 четверть) | Год. | Экз. | Итог.`; пустая ячейка = нет оценки.
  Если оценок нет вообще — ответ «Оценки не проставлены».
- **Месяц**: в ячейке `<div align=right><a ...dayschedule('1.11.26')>1</a></div>`, затем
  `- <a onclick="EditEvent(3,25)" class="vacation-day">Осенние каникулы</a><br>`
  (EditEvent(тип, id); у `td` каникулярного дня класс `vacation-day`). `EditEvent` есть и в `href`, и в `onclick`.
- **Именинники**: таблица `Класс | Роль | Дата рождения | Имя на экране`, дата `23.11.08`
  (год двузначный). У формы `View` там свои поля: `From_MonthBirth=1`, флажки
  `BIRTH_STAFF=1, BIRTH_STUDENT=2, BIRTH_PARENT=4`. На ноябрь видна только своя запись.
- **Объявления**: `div.advertisement` → `div.adver-body`: `h3` (`<span>Тема:</span>` + заголовок),
  `.username-coloured` (автор), `div.adver-info` (дата `5.11.26`), `div.adver-content` (текст с `<br>`).
  id у объявлений нет → ключ «дата|автор|заголовок». **Эмодзи сервер хранит как `??`**.
  ⚠️ В `style` фото автора (`photo.asp?AT=...&UID=...`) есть **AT** — сырой HTML не логируй и не пересылай.
- **Файлы**: ссылка `JavaScript:$.openAttachment('/doc/<FileName>', <FileAttachmentId>)`
  (fileAttachments.min.js), в объявлениях — `openAttachment(url, id)` → обычный POST формы.
- **Письмо**: в ячейках недели бывают не только оценки, но и коды посещаемости (`ОП` и т.п.) —
  они в `LetterRow.week[].attendance`.

## Не проверено — нужны образцы

- Скачивание файла: ДЗ с прикреплённым файлом (`python tools/dump_pages.py --files`).
- Письмо с `ReportType=2` («Итоги учебного периода») — `dump_pages.py` сохраняет `letter_period.html`.
- Именинники в месяце, где есть чужие ДР (кого вообще показывает сервер ученику).
- Объявления с вложениями.
- Как выглядит ответ при истёкшей сессии (сейчас любой «чужой» ответ = `SessionExpired` → один перелогин).
- Не выкидывает ли вход бота твою сессию в браузере/телефоне.
- Страница-предупреждение после входа (слабый пароль и т.п.) — тогда `EntryPoint` другой.
