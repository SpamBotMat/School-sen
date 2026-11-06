"""Картинки для офлайн-архива в Telegram (PNG, 1080 px по ширине, крупный текст).

Telegram кэширует фото на телефоне — их можно открыть и без интернета/VPN.
Длинные списки режутся на страницы (≈ 9:16), страницы уходят альбомом.
Эмодзи в картинках не используем — шрифт их не рисует.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Callable

from PIL import Image, ImageDraw, ImageFont

from . import grades as G
from .config import ROOT
from .timeutil import MONTHS_GEN, WEEKDAYS, now, short_date

W = 1080
PAGE_H = 1920
PAD = 56
FONT_DIR = ROOT / "assets" / "fonts"
BRAND = ROOT / "assets" / "brand" / "mark-128.png"

INK = (31, 42, 68)          # чернильный navy
PEACH = (255, 179, 138)
PAPER = (247, 244, 238)
CARD = (255, 255, 255)
TEXT = (28, 32, 44)
MUTED = (110, 116, 132)
LINE = (228, 224, 216)
MARK_COLORS = {5: (46, 182, 125), 4: (66, 133, 244), 3: (245, 158, 11), 2: (229, 72, 77), 1: (229, 72, 77)}


# В Manrope нет эмодзи — вместо них Pillow рисует «тофу»-квадратики. Вырезаем.
_EMOJI = re.compile("[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U00002B00-\U00002BFF\uFE00-\uFE0F\u200D\u20E3]")


def plain(text: str) -> str:
    return _EMOJI.sub("", text or "").rstrip()


class _Draw(ImageDraw.ImageDraw):
    def text(self, xy, text, *args, **kwargs):  # type: ignore[override]
        return super().text(xy, plain(text) if isinstance(text, str) else text, *args, **kwargs)


@lru_cache(maxsize=32)
def font(weight: str, size: int) -> ImageFont.FreeTypeFont:
    path = FONT_DIR / f"Manrope-{weight}.ttf"
    try:
        return ImageFont.truetype(str(path), size)
    except OSError:
        for fallback in ("/usr/share/fonts/liberation-sans/LiberationSans-Bold.ttf",
                         "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf", "DejaVuSans.ttf"):
            try:
                return ImageFont.truetype(fallback, size)
            except OSError:
                continue
        return ImageFont.load_default()


def wrap(text: str, f: ImageFont.FreeTypeFont, width: int, max_lines: int = 8) -> list[str]:
    words = plain(text).replace("\n", " ").split()
    lines, cur = [], ""
    for w_ in words:
        probe = (cur + " " + w_).strip()
        if f.getlength(probe) <= width:
            cur = probe
            continue
        if cur:
            lines.append(cur)
        while f.getlength(w_) > width:            # очень длинное слово — режем
            cut = len(w_)
            while cut > 1 and f.getlength(w_[:cut]) > width:
                cut -= 1
            lines.append(w_[:cut])
            w_ = w_[cut:]
        cur = w_
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip(".,; ") + "…"
    return lines


@dataclass
class Block:
    height: int
    draw: Callable[[ImageDraw.ImageDraw, Image.Image, int], None]   # (draw, img, y)


def _mark_chip(d: ImageDraw.ImageDraw, x: int, y: int, mark: str, size: int = 50) -> int:
    v = G.values_of([mark])
    color = MARK_COLORS.get(v[0], MUTED) if v else MUTED
    f = font("ExtraBold", 28)
    w = max(size, int(f.getlength(mark)) + 24)
    d.rounded_rectangle((x, y, x + w, y + size), radius=14, fill=color)
    tw = f.getlength(mark)
    d.text((x + (w - tw) / 2, y + size / 2), mark, font=f, fill=(255, 255, 255), anchor="lm")
    return w


def _header(img: Image.Image, title: str, subtitle: str, page: str) -> int:
    d = _Draw(img)
    h = 230
    d.rectangle((0, 0, W, h), fill=INK)
    # чернильная «капля» — фирменный штрих
    d.ellipse((W - 250, -120, W + 90, 190), fill=(43, 56, 88))
    d.ellipse((W - 170, 120, W - 90, 200), fill=PEACH)
    if BRAND.is_file():
        try:
            mark = Image.open(BRAND).convert("RGBA").resize((72, 72))
            img.paste(mark, (PAD, 44), mark)
        except OSError:
            pass
    d.text((PAD + 92, 56), "School Sensor", font=font("Bold", 30), fill=PEACH)
    d.text((PAD + 92, 92), page, font=font("Medium", 24), fill=(200, 206, 222))
    d.text((PAD, 140), title, font=font("ExtraBold", 54), fill=(255, 255, 255))
    y = h + 26
    d.text((PAD, y), subtitle, font=font("Medium", 26), fill=MUTED)
    return y + 54


def _paginate(title: str, subtitle: str, blocks: list[Block]) -> list[bytes]:
    pages: list[list[Block]] = [[]]
    room = PAGE_H - 330 - 80
    used = 0
    for b in blocks:
        if used + b.height > room and pages[-1]:
            pages.append([])
            used = 0
        pages[-1].append(b)
        used += b.height
    out = []
    total = len(pages)
    for i, page in enumerate(pages, 1):
        height = max(900, 330 + sum(b.height for b in page) + 100)
        img = Image.new("RGB", (W, height), PAPER)
        y = _header(img, title, subtitle, f"стр. {i} из {total}" if total > 1 else now().strftime("%d.%m.%Y %H:%M"))
        d = _Draw(img)
        for b in page:
            b.draw(d, img, y)
            y += b.height
        d.text((PAD, height - 56), f"Сформировано {now():%d.%m.%Y %H:%M} · данные с сайта школы",
               font=font("Medium", 22), fill=MUTED)
        buf = io.BytesIO()
        img.save(buf, "PNG", compress_level=6)     # optimize=True в 5 раз дороже по CPU ради −4% размера
        out.append(buf.getvalue())
    return out


def _card(height: int, painter: Callable[[ImageDraw.ImageDraw, int, int], None], gap: int = 22) -> Block:
    def draw(d: ImageDraw.ImageDraw, img: Image.Image, y: int) -> None:
        d.rounded_rectangle((PAD, y, W - PAD, y + height), radius=28, fill=CARD, outline=LINE, width=2)
        painter(d, PAD + 32, y + 26)
    return Block(height + gap, draw)


def _who(u) -> str:
    bits = [u.name]
    if u.class_name:
        bits.append(u.class_name)
    return " · ".join(bits)


# ───────────────────────────── оценки ─────────────────────────────

def marks_images(u, report: dict | None, sure: float, maybe: float) -> list[bytes]:
    subs = G.subjects_from_report(report)
    blocks: list[Block] = []
    inner = W - 2 * PAD - 64
    for s in subs:
        vals = G.values_of(m for _, m in s["marks"])
        p = G.predict(vals, sure, maybe)
        name_lines = wrap(s["name"], font("Bold", 34), inner - 220, 2)
        chips = [m for _, m in s["marks"]]
        per_row = max(1, (inner + 10) // 62)
        rows = (len(chips) + per_row - 1) // per_row if chips else 0
        h = 26 + len(name_lines) * 44 + 40 + rows * 62 + (10 if not chips else 0) + 20

        def paint(d, x, y, s=s, p=p, name_lines=name_lines, chips=chips, per_row=per_row):
            for i, line in enumerate(name_lines):
                d.text((x, y + i * 44), line, font=font("Bold", 34), fill=TEXT)
            avg = G.fmt_avg(p.avg)
            color = MARK_COLORS.get(p.grade or 0, MUTED)
            f = font("ExtraBold", 44)
            d.text((W - PAD - 32 - f.getlength(avg), y - 4), avg, font=f, fill=color)
            yy = y + len(name_lines) * 44 + 4
            d.text((x, yy), G.prediction_text(p) + ("   " + G.need_text(p) if G.need_text(p) and p.status != "sure" else ""),
                   font=font("Medium", 26), fill=MUTED)
            yy += 44
            cx = x
            for i, m in enumerate(chips):
                if i and i % per_row == 0:
                    yy += 62
                    cx = x
                cx += _mark_chip(d, cx, yy, m) + 12
        blocks.append(_card(h, paint))
    avg = G.overall(subs)
    period = (report or {}).get("period") or ""
    subtitle = _who(u) + (f" · средний {avg:.2f}" if avg is not None else "")
    title = "Оценки" + (f" · {period}" if period and len(period) < 22 else "")
    if not blocks:
        blocks.append(_card(120, lambda d, x, y: d.text((x, y + 20), "Оценок пока нет", font=font("Bold", 34), fill=TEXT)))
    return _paginate(title, subtitle, blocks)


# ───────────────────────────── посещаемость ─────────────────────────────

ATT_NAMES = {"ОТ": "отсутствие", "ОП": "опоздание", "Б": "болезнь", "УП": "уважительная причина",
             "НП": "неуважительная причина", "ОСВ": "освобождение"}


def attendance_images(u, report: dict | None) -> list[bytes]:
    subs = G.subjects_from_report(report)
    total: dict[str, int] = {}
    blocks: list[Block] = []
    inner = W - 2 * PAD - 64
    for s in subs:
        if not s["attendance"]:
            continue
        counts: dict[str, int] = {}
        for _, code in s["attendance"]:
            counts[code] = counts.get(code, 0) + 1
            total[code] = total.get(code, 0) + 1
        summary = " · ".join(f"{code} {n}" for code, n in sorted(counts.items()))
        dates = ", ".join(f"{short_date(date.fromisoformat(d))} {code}" for d, code in s["attendance"][-20:])
        lines = wrap(dates, font("Medium", 26), inner, 6)
        h = 26 + 44 + 40 + len(lines) * 36 + 20

        def paint(d, x, y, s=s, summary=summary, lines=lines):
            d.text((x, y), s["name"][:40], font=font("Bold", 32), fill=TEXT)
            d.text((x, y + 46), summary, font=font("Bold", 26), fill=(229, 72, 77))
            for i, line in enumerate(lines):
                d.text((x, y + 86 + i * 36), line, font=font("Medium", 26), fill=MUTED)
        blocks.append(_card(h, paint))
    legend = " · ".join(f"{k} — {v}" for k, v in ATT_NAMES.items())
    if total:
        top = "Всего: " + " · ".join(f"{k} {n}" for k, n in sorted(total.items()))
    else:
        top = "Пропусков и опозданий нет"
    lines = wrap(legend, font("Medium", 24), W - 2 * PAD - 64, 4)

    def head(d, x, y, top=top, lines=lines):
        d.text((x, y), top, font=font("ExtraBold", 34), fill=TEXT)
        for i, line in enumerate(lines):
            d.text((x, y + 52 + i * 32), line, font=font("Medium", 24), fill=MUTED)
    blocks.insert(0, _card(26 + 52 + len(lines) * 32 + 20, head))
    return _paginate("Посещаемость", _who(u) + (f" · {(report or {}).get('period', '')}" if report else ""), blocks)


# ───────────────────────────── ДЗ на неделю ─────────────────────────────

def homework_images(u, items_by_day: list[tuple[date, list[dict]]], done: set[str], key_fn) -> list[bytes]:
    blocks: list[Block] = []
    inner = W - 2 * PAD - 64
    for d0, items in items_by_day:
        rows = []
        for a in items:
            title = a.get("title") or ""
            prefix = "" if a.get("type_code") == "Д" else f"{a.get('type_name') or a.get('type_code')}: "
            lines = wrap(prefix + title, font("Medium", 28), inner - 50, 6)
            rows.append((a, lines))
        h = 26 + 50 + sum(44 + len(l) * 38 + 14 for _, l in rows) + 10
        if not rows:
            h = 26 + 50 + 50

        def paint(d, x, y, d0=d0, rows=rows):
            d.text((x, y), f"{WEEKDAYS[d0.weekday()].capitalize()}, {d0.day} {MONTHS_GEN[d0.month - 1]}",
                   font=font("ExtraBold", 34), fill=INK)
            yy = y + 56
            if not rows:
                d.text((x, yy), "Ничего не задано", font=font("Medium", 28), fill=MUTED)
            for a, lines in rows:
                is_done = key_fn(a) in done
                box = (x, yy + 6, x + 32, yy + 38)
                d.rounded_rectangle(box, radius=8, outline=INK, width=3,
                                    fill=(46, 182, 125) if is_done else None)
                if is_done:
                    d.line((x + 7, yy + 22, x + 14, yy + 30, x + 26, yy + 13), fill=(255, 255, 255), width=4)
                d.text((x + 50, yy), a["subject"][:38], font=font("Bold", 30), fill=TEXT)
                if a.get("mark"):
                    _mark_chip(d, W - PAD - 32 - 50, yy - 2, a["mark"], 44)
                yy += 44
                for line in lines:
                    d.text((x + 50, yy), line, font=font("Medium", 28), fill=(60, 66, 80))
                    yy += 38
                yy += 14
        blocks.append(_card(h, paint))
    start = items_by_day[0][0] if items_by_day else date.today()
    end = items_by_day[-1][0] if items_by_day else start
    return _paginate("Домашка на неделю", f"{_who(u)} · {short_date(start)} — {short_date(end)}", blocks)


# ───────────────────────────── расписание ─────────────────────────────

def schedule_images(u, days: list[tuple[date, list[dict] | None]]) -> list[bytes]:
    blocks: list[Block] = []
    for d0, lessons in days:
        lessons = lessons or []
        h = 26 + 50 + max(1, len(lessons)) * 48 + 16

        def paint(d, x, y, d0=d0, lessons=lessons):
            d.text((x, y), f"{WEEKDAYS[d0.weekday()].capitalize()}, {d0.day} {MONTHS_GEN[d0.month - 1]}",
                   font=font("ExtraBold", 34), fill=INK)
            yy = y + 56
            if not lessons:
                d.text((x, yy), "Уроков нет", font=font("Medium", 28), fill=MUTED)
            for l in lessons:
                d.text((x, yy), str(l.get("n") or "•"), font=font("ExtraBold", 30), fill=PEACH)
                d.text((x + 44, yy), (l.get("start") or "--:--"), font=font("Bold", 28), fill=MUTED)
                name = l.get("s") or ""
                f = font("Bold", 30)
                room = l.get("room") or ""
                maxw = W - 2 * PAD - 64 - 150 - (f.getlength(room) + 20 if room else 0)
                while f.getlength(name) > maxw and len(name) > 4:
                    name = name[:-2]
                if name != (l.get("s") or ""):
                    name = name.rstrip() + "…"
                d.text((x + 150, yy), name, font=f, fill=TEXT)
                if room:
                    d.text((W - PAD - 32 - f.getlength(room), yy), room, font=font("Medium", 28), fill=MUTED)
                yy += 48
        blocks.append(_card(h, paint, gap=18))
    start = days[0][0] if days else date.today()
    return _paginate("Расписание", f"{_who(u)} · неделя с {short_date(start)}", blocks)
