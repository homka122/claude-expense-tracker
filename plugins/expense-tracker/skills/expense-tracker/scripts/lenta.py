# /// script
# requires-python = ">=3.10"
# dependencies = ["playwright", "tzdata; sys_platform == 'win32'"]
# ///
"""Чеки и онлайн-заказы Ленты (lenta.com) через отдельный профиль Chrome (см. browser.py).

    uv run lenta.py shop [--since 2026-10-01] [--until 2026-10-31]   # покупки в магазине по «Карте №1»
    uv run lenta.py shop-detail 123/45/67/89                          # состав чека (id из shop)
    uv run lenta.py orders                                            # онлайн-заказы: доставка и самовывоз
    uv run lenta.py order 1234567890                                  # состав онлайн-заказа (текст страницы)
    uv run lenta.py login                                             # развернуть окно для входа (с согласия)

По умолчанию shop берёт последние 31 день. Выход с кодом 3 — нет входа в Ленту.
shop / orders печатают JSON Lines, shop-detail — JSON, order — текст блока «Товары».
"""
import argparse
import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent))
import browser  # noqa: E402

HIST = "https://lenta.com/my-account/order/"
ACCOUNT = "https://lenta.com/my-account/"
EXIT_LOGIN = 3


def api_headers(page):
    headers = browser.capture_headers(page, HIST, "/api-gateway/v1/users/")
    if not headers:
        print("Нет входа в Ленту: нужен lenta.py login (окно развернётся).", file=sys.stderr)
        sys.exit(EXIT_LOGIN)
    return headers


def page_lines(page):
    main = page.query_selector("main") or page.query_selector("body")
    return [l.strip() for l in main.inner_text().splitlines() if l.strip()]


def shop(page, since, until):
    headers = api_headers(page)
    offset, out = 0, []
    while True:
        path = (f"/api-gateway/v1/purchases?startDate={since}&endDate={until}"
                f"&operationType=all&limit=20&offset={offset}")
        status, body = browser.fetch_json(page, path, headers)
        if status != 200:
            sys.exit(f"purchases: HTTP {status}")
        batch = (body or {}).get("OrderList") or []
        for o in batch:
            out.append({"id": o["Id"].rsplit("/", 1)[0], "datetime": o["DeliveryDateTime"][:16],
                        "sum": o["PaySum"], "store": o.get("DeliveryStoreTitle"),
                        "address": o.get("DeliveryAddress")})
        if len(batch) < 20:
            return out
        offset += 20


def shop_detail(page, pid):
    headers = api_headers(page)
    status, body = browser.fetch_json(page, f"/api-gateway/v1/purchases/details?id={quote(pid, safe='')}", headers)
    if status != 200 or not (body or {}).get("OrderList"):
        sys.exit(f"details: HTTP {status}")
    o = body["OrderList"][0]
    return {
        "datetime": o["DeliveryDateTime"][:16],  # уже местное время, хоть и с суффиксом Z
        "pay_sum": o["PaySum"],
        "items": [{"name": i["Name"], "qty": i["Quantity"], "weighted": i["FlagWeightGood"] not in ("0", 0, None),
                   "price": i["Price"], "price_per_kg": i["PriceWeight"], "sum": i["PositionValue"]}
                  for i in o["OrderItemList"]],
    }


def orders(page):
    api_headers(page)
    # Сайт помнит вкладку истории в localStorage.HISTORY_FILTER; без ключа открывается
    # «Доставка и самовывоз». Кликать по вкладке в свёрнутом окне ненадёжно.
    page.evaluate("localStorage.removeItem('HISTORY_FILTER')")
    page.goto(HIST, wait_until="load", timeout=40000)
    for _ in range(40):
        if any(re.fullmatch(r"№\d{6,}", l) for l in page_lines(page)):
            break
        page.wait_for_timeout(250)
    lines = page_lines(page)
    out = []
    for i, l in enumerate(lines):
        if re.fullmatch(r"№\d{6,}", l):
            rest = [x for x in lines[i + 1:i + 6] if not re.fullmatch(r"\+\d+", x)]
            total = next((x for x in rest if x.endswith("₽")), None)
            out.append({"order": l[1:], "status": lines[i - 1] if i else None,
                        "when": rest[0] if rest else None,
                        "address": rest[1] if len(rest) > 1 else None, "sum": total})
    return out


def order(page, num):
    api_headers(page)
    page.goto(f"{HIST}{num}/", wait_until="load", timeout=40000)
    page.wait_for_timeout(2500)
    lines = page_lines(page)
    start = next((i for i, l in enumerate(lines) if l == "Товары"), None)
    if start is None:
        sys.exit("на странице заказа нет блока «Товары» (номер неверный или вёрстка сайта поменялась)")
    end = next((i for i, l in enumerate(lines[start:], start) if l.startswith("Экономия")), len(lines) - 2)
    # Весовые: после «Цена и вес товара изменились» идёт факт (сумма · вес), потом заказанное.
    return lines[start:end + 2]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("shop", help="покупки в магазине по карте лояльности, JSON Lines")
    s.add_argument("--since", type=date.fromisoformat, default=date.today() - timedelta(days=31),
                   help="YYYY-MM-DD, по умолчанию 31 день назад")
    s.add_argument("--until", type=date.fromisoformat, default=date.today() + timedelta(days=1),
                   help="YYYY-MM-DD, по умолчанию завтра")
    sub.add_parser("shop-detail", help="состав чека, JSON").add_argument("id", help="id из вывода shop")
    sub.add_parser("orders", help="онлайн-заказы, JSON Lines")
    sub.add_parser("order", help="состав онлайн-заказа, текст").add_argument("num", help="номер заказа без №")
    sub.add_parser("login", help="развернуть окно для входа")
    args = ap.parse_args()

    if args.cmd == "login":
        with browser.session(show=True) as (_, page):
            page.goto(ACCOUNT, wait_until="load", timeout=40000)
        return print("Окно Ленты развёрнуто: войди, потом его можно свернуть.")
    with browser.session() as (_, page):
        if args.cmd == "shop":
            for r in shop(page, args.since, args.until):
                print(json.dumps(r, ensure_ascii=False))
        elif args.cmd == "shop-detail":
            print(json.dumps(shop_detail(page, args.id), ensure_ascii=False, indent=1))
        elif args.cmd == "orders":
            for r in orders(page):
                print(json.dumps(r, ensure_ascii=False))
        else:
            print("\n".join(order(page, args.num)))


if __name__ == "__main__":
    browser.run_cli(main)
