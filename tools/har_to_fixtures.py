#!/usr/bin/env python3
"""HAR (из DevTools → Network → Save all as HAR) → tests/fixtures.

    python tools/har_to_fixtures.py "Парсинг нетскул/" -o tests/fixtures

Сохраняет ответы страниц /asp/* и /webapi/* (HTML/JSON), заменяя токен AT на нули,
и пишет requests.txt со списком запросов и параметров (логин, пароль, pw2, lt,
salt и AT замаскированы).

ВНИМАНИЕ: в фикстурах остаются ФИО, класс, оценки, имена учителей и родителей.
Папка tests/fixtures в .gitignore — не публикуй её и сами HAR-файлы.
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path
from urllib.parse import parse_qsl, unquote_plus, urlparse

SECRET_PARAMS = {"UN", "PW", "pw2", "lt", "salt", "AT", "at"}
SKIP_PATHS = ("/asp/scripts/", "/asp/SetupSchool/photo.asp")


def entries(paths: list[Path]):
    files: list[Path] = []
    for p in paths:
        files += sorted(p.glob("*.har")) + sorted(p.glob("*.txt")) if p.is_dir() else [p]
    for f in files:
        try:
            har = json.loads(f.read_text(encoding="utf-8-sig"))
        except (ValueError, UnicodeDecodeError):
            print(f"пропускаю (не HAR): {f.name}", file=sys.stderr)
            continue
        for e in har.get("log", {}).get("entries", []):
            yield f, e


def body_of(entry: dict) -> str:
    content = entry.get("response", {}).get("content", {})
    text = content.get("text") or ""
    if content.get("encoding") == "base64":
        text = base64.b64decode(text).decode("utf-8", "replace")
    return text


def request_params(entry: dict) -> list[tuple[str, str]]:
    post = entry["request"].get("postData") or {}
    if post.get("params"):
        return [(p["name"], unquote_plus(p.get("value", ""))) for p in post["params"]]
    return parse_qsl(post.get("text") or "", keep_blank_values=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="+", type=Path, help="HAR-файлы или папки с ними")
    ap.add_argument("-o", "--out", type=Path, default=Path("tests/fixtures"))
    args = ap.parse_args()

    all_entries = list(entries(args.inputs))
    # 1) собираем все значения AT, чтобы вычистить их из ответов
    tokens: set[str] = set()
    for _, e in all_entries:
        for h in e["request"].get("headers", []):
            if h["name"].lower() == "at" and h["value"]:
                tokens.add(h["value"])
        for name, value in request_params(e):
            if name in ("AT", "at") and value:
                tokens.add(value)
        if urlparse(e["request"]["url"]).path.endswith("postlogin.asp"):
            try:
                tokens.add(str(json.loads(body_of(e))["AT"]))
            except (ValueError, KeyError):
                pass

    def scrub(text: str) -> str:
        for t in sorted(tokens, key=len, reverse=True):
            text = text.replace(t, "0" * len(t))
        return text

    args.out.mkdir(parents=True, exist_ok=True)
    seen: dict[str, int] = {}
    log_lines: list[str] = []
    for f, e in all_entries:
        req, resp = e["request"], e["response"]
        path = urlparse(req["url"]).path
        if not path.startswith(("/asp/", "/webapi/")) or path.startswith(SKIP_PATHS):
            continue
        params = [(n, "***" if n in SECRET_PARAMS else v) for n, v in request_params(e)]
        log_lines.append(f"[{f.stem[:60]}] {req['method']} {resp.get('status')} {path}")
        if params:
            log_lines.append("    " + "&".join(f"{n}={v}" for n, v in params))
        text = body_of(e)
        if not text:
            continue
        ext = ".json" if text.lstrip("\ufeff \r\n").startswith("{") else ".html"
        base = Path(path).name or "index"
        seen[base] = seen.get(base, 0) + 1
        name = base + (f"__{seen[base]}" if seen[base] > 1 else "") + ext
        (args.out / name).write_text(scrub(text), encoding="utf-8")
        print("  ", name)
    (args.out / "requests.txt").write_text("\n".join(log_lines) + "\n", encoding="utf-8")
    print(f"Готово: {sum(seen.values())} ответов → {args.out}/ (+ requests.txt)")
    print("Напоминание: в фикстурах есть личные данные — не коммить их.")


if __name__ == "__main__":
    main()
