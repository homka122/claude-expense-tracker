# /// script
# requires-python = ">=3.10"
# dependencies = ["playwright", "tzdata; sys_platform == 'win32'"]
# ///
"""Операции по карте Яндекс Пэй через GraphQL веб-кабинета (bank.yandex.ru).

    uv run ypay.py [-n 20] [--since 2026-10-01] [--json | --csv]
    uv run ypay.py login        # развернуть окно для входа/PIN (только с согласия пользователя)

Сессия банка живёт ~15 минут без активности. Если она истекла: в терминале скрипт сам развернёт
окно и подождёт ввода кода; без терминала (из агента) — выйдет с кодом 3, тогда нужен `ypay.py login`.
Время операций — в часовом поясе из config.json (timezone), иначе в системном.
Поля --json/--csv: id, date, amount (<0 — списание, >0 — поступление), currency (PLUS — баллы),
status (CLEAR | HOLD | FAIL), title, description, cashback, cashback_currency.
"""
import argparse
import csv
import json
import sys
import time
from datetime import date, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import browser  # noqa: E402
import config  # noqa: E402

HASH_FILE = config.feed_hash_path()
GQL = "https://bank.yandex.ru/transactions/api/graphql"
HISTORY = "https://bank.yandex.ru/my/transactions-history"
OPERATION = "TransactionsFeedPfm"
DEFAULT_HASH = "9bf3cf40fd987f779649b53757e1ea3b6d24526cbcc16e193bb78ac398ca4e02"
MAX_PAGES = 40
STATUS = {"CLEAR": "", "HOLD": "холд", "FAIL": "отказ"}
EXIT_SESSION = 3
TZ_NAME = config.timezone()
TZ = config.tzinfo()


class SessionExpired(Exception):
    pass


def has_bank_session(ctx):
    """Сессия банка поверх Яндекс ID: cookie yabank-sessionuuid, ~15 минут, потом PIN/код."""
    return any(c["name"] == "yabank-sessionuuid" for c in ctx.cookies(["https://bank.yandex.ru/"]))


def load_hash():
    if HASH_FILE.exists():
        h = HASH_FILE.read_text(encoding="utf-8").strip()
        if h:
            return h
    return DEFAULT_HASH


def refresh_hash(ctx, page):
    """Банк выкатил новую версию фронта — берём operationId из запроса самой страницы."""
    found = []

    def on_request(req):
        if req.url.startswith(GQL) and OPERATION in (req.post_data or ""):
            found.extend(parse_qs(urlsplit(req.url).query).get("operationId", []))

    page.on("request", on_request)
    page.goto(HISTORY, wait_until="load", timeout=30000)
    deadline = time.time() + 15
    while not found and time.time() < deadline:
        page.wait_for_timeout(500)
    page.remove_listener("request", on_request)
    if not found and not has_bank_session(ctx):
        raise SessionExpired
    if not found:
        raise RuntimeError("запрос ленты на странице истории не найден — фронт банка поменялся сильнее")
    config.ensure_home()
    HASH_FILE.write_text(found[0], encoding="utf-8")
    return found[0]


def feed_page(ctx, h, cursor):
    inp = {"categories": []}
    if cursor:
        inp["cursor"] = cursor
    resp = ctx.request.post(
        f"{GQL}?operationId={h}",
        data={"operationName": OPERATION, "variables": {"input": inp},
              "extensions": {"persistedQuery": {"version": 1, "sha256Hash": h}}},
        headers={"x-yabank-time-zone": TZ_NAME, "origin": "https://bank.yandex.ru",
                 "referer": HISTORY},
    )
    try:
        body = resp.json()
    except Exception:
        if resp.status in (401, 403) or not has_bank_session(ctx):
            raise SessionExpired from None
        raise RuntimeError(f"банк ответил не JSON (HTTP {resp.status})") from None
    errors = [e.get("message", "") for e in body.get("errors") or []]
    if any("PersistedQuery" in e for e in errors):
        return None
    if resp.status in (401, 403) or any("uid is not defined" in e for e in errors) \
            or (errors and not has_bank_session(ctx)):
        raise SessionExpired
    if errors:
        raise RuntimeError(f"GraphQL: {errors[0][:200]}")
    return body["data"]["getTransactionsFeedPfm"]


def value(v):
    """{money: {amount, currency}, plus: "12.34"} → (сумма, валюта); plus — баллы Плюса."""
    v = v or {}
    if v.get("money"):
        return float(v["money"]["amount"]), v["money"]["currency"]
    if v.get("plus"):
        return float(v["plus"]), "PLUS"
    return None, None


def normalize(item):
    amount, currency = value(item.get("amount"))
    cashback, cashback_currency = value((item.get("summaryCashback") or {}).get("totalValue"))
    sign = 1 if item.get("direction") == "CREDIT" else -1
    when = datetime.fromisoformat(item["date"].replace("Z", "+00:00")).astimezone(TZ)
    return {
        "id": item.get("id"),
        "date": when.isoformat(timespec="seconds"),
        "amount": sign * (amount or 0),
        "currency": currency,
        "status": item.get("statusCode"),
        "title": (item.get("title") or {}).get("plain"),
        "description": item.get("description"),
        "cashback": cashback,
        "cashback_currency": cashback_currency,
    }


def key(it):
    return it["id"] or (it["date"], it["amount"], it["title"])


def fetch(ctx, page, limit, since):
    h, cursor, out, seen = load_hash(), None, [], set()
    since_s = since.isoformat() if since else None
    for _ in range(MAX_PAGES):
        feed = feed_page(ctx, h, cursor)
        if feed is None:
            h = refresh_hash(ctx, page)
            feed = feed_page(ctx, h, cursor)
            if feed is None:
                raise RuntimeError("свежий operationId тоже не принят")
        pending = [normalize(i) for i in feed.get("pendingItems") or []]
        items = [normalize(i) for i in feed.get("items") or []]
        # Холды идут отдельным списком и могут быть старше --since: их пропускаем, а не обрываем чтение.
        for it in pending:
            if key(it) in seen or (since_s and it["date"][:10] < since_s):
                continue
            seen.add(key(it))
            out.append(it)
            if not since and len(out) >= limit:
                return out
        for it in items:
            if key(it) in seen:
                continue
            if since_s and it["date"][:10] < since_s:
                return out
            seen.add(key(it))
            out.append(it)
            if not since and len(out) >= limit:
                return out
        cursor = feed.get("cursor")
        if not cursor or not (pending or items):
            return out
    print(f"предупреждение: прочитано {MAX_PAGES} страниц, дальше не листаю — сузь --since", file=sys.stderr)
    return out


def wait_for_login(limit, since, timeout=300):
    with browser.session(show=True) as (ctx, page):
        page.goto(HISTORY, wait_until="load", timeout=30000)
        print("Банк просит вход — введи код в окне Chrome, жду…", file=sys.stderr)
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                rows = fetch(ctx, page, limit, since)
                browser.set_window(page, "minimized")
                return rows
            except SessionExpired:
                page.wait_for_timeout(3000)
    sys.exit("Вход в банк не завершён за 5 минут.")


def print_table(rows):
    for r in rows:
        status = STATUS.get(r["status"], r["status"] or "")
        what = r["title"] or ""
        if r["description"]:
            what += f" · {r['description']}"
        amount = f"{r['amount']:>11,.2f}".replace(",", " ")
        print(f"{r['date'][:16].replace('T', ' ')}  {amount} {r['currency'] or '':<3}  {status:<5}  {what}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", choices=["login"], help="развернуть окно банка для входа")
    ap.add_argument("-n", "--limit", type=int, default=20, help="сколько последних операций (по умолчанию 20)")
    ap.add_argument("--since", type=date.fromisoformat, help="все операции начиная с даты YYYY-MM-DD (-n не действует)")
    fmt = ap.add_mutually_exclusive_group()
    fmt.add_argument("--json", action="store_true", help="JSON Lines")
    fmt.add_argument("--csv", action="store_true")
    args = ap.parse_args()

    if args.command == "login":
        with browser.session(show=True) as (_, page):
            page.goto(HISTORY, wait_until="load", timeout=30000)
        return print("Окно банка развёрнуто: войди и введи код. Потом его можно свернуть.")
    try:
        try:
            with browser.session() as (ctx, page):
                rows = fetch(ctx, page, args.limit, args.since)
        except SessionExpired:
            if not sys.stderr.isatty():
                print("Сессия банка истекла: нужен вход с PIN/кодом (ypay.py login).", file=sys.stderr)
                sys.exit(EXIT_SESSION)
            rows = wait_for_login(args.limit, args.since)
    except browser.BrowserError:
        raise
    except RuntimeError as e:
        sys.exit(f"ypay: {e}")

    if args.json:
        for r in rows:
            print(json.dumps(r, ensure_ascii=False))
    elif args.csv:
        w = csv.DictWriter(sys.stdout, fieldnames=list(rows[0]) if rows else ["id"])
        w.writeheader()
        w.writerows(rows)
    else:
        print_table(rows)


if __name__ == "__main__":
    browser.run_cli(main)
