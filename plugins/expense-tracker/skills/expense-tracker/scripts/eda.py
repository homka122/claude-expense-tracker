# /// script
# requires-python = ">=3.10"
# dependencies = ["playwright", "tzdata; sys_platform == 'win32'"]
# ///
"""Заказы Яндекс Еды и магазинов в ней (eda.yandex.ru) через отдельный профиль Chrome (см. browser.py).

    uv run eda.py orders              # список заказов: номер, место, дата, сумма (JSON Lines)
    uv run eda.py order <номер>       # состав, доставка, сборы, итог (JSON); номер — из orders
    uv run eda.py login               # развернуть окно для входа в Яндекс ID (с согласия)

Вход — тот же Яндекс ID, что у банка: после `ypay.py login` отдельный вход обычно не нужен.
В банке такие платежи видны как «Яндекс.Еда»; возврат за непривезённый товар приходит отдельной операцией.
Выход с кодом 3 — нет входа в Яндекс ID (или заказов нет: страница не запросила историю).
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import browser  # noqa: E402

ORDERS = "https://eda.yandex.ru/orders"
LIST = "/eats/v1/orders-info/v1/orders"
DETAILS = "/eats/v1/orders-info/v1/desktop/order-details"
EXIT_LOGIN = 3
STYLE = {"normal", "semibold", "bold", "crossed", "title", "title_with_headtitle", "title_with_subtitle"}


def headers(page):
    h = browser.capture_headers(page, ORDERS, "/orders-info/")
    if not h:
        print("Нет входа в Яндекс ID (или заказов нет): страница не запросила orders-info. "
              "Нужен eda.py login (окно развернётся).", file=sys.stderr)
        sys.exit(EXIT_LOGIN)
    return h


def strings(v, out):
    """Все короткие строки из виджета — номер, место, дата, сумма, статус."""
    if isinstance(v, dict):
        for k, x in v.items():
            if k not in ("icon", "image", "url", "color", "deeplink", "action"):
                strings(x, out)
    elif isinstance(v, list):
        for x in v:
            strings(x, out)
    elif isinstance(v, str) and 0 < len(v) <= 80 and not v.startswith(("http", "#", "$")) and v not in STYLE:
        out.append(v)
    return out


def orders(page):
    h = headers(page)
    status, body = browser.fetch_json(page, LIST, h, "POST", {"goods_items_limit": 0})
    if status != 200:
        sys.exit(f"orders: HTTP {status}")
    return [{"order": o["order_nr"], "info": list(dict.fromkeys(strings(o.get("widgets"), [])))[:8]}
            for o in (body or {}).get("orders", [])]


def money(price):
    v = (price or {}).get("price_value")
    return float(v) if v not in (None, "") else None


def order(page, nr):
    h = headers(page)
    status, body = browser.fetch_json(page, DETAILS, h, "POST", {"order_nr": nr})
    if status != 200 or not (body or {}).get("order"):
        sys.exit(f"order-details: HTTP {status}")
    o = body["order"]
    out = {"order": o.get("order_nr"), "created_at": o.get("header", {}).get("created_at"), "items": [], "totals": []}
    for section in o.get("sections", []):
        payload = section.get("payload") or {}
        for block in payload.get("blocks") or []:
            for row in (block.get("payload") or {}).get("rows") or []:
                p = (row.get("payload") or {}).get("product")
                if p:
                    pr = p.get("price") or {}
                    out["items"].append({
                        "name": (p.get("name") or {}).get("value"),
                        "sum": money(pr.get("actual_price")),
                        "sum_before": money(pr.get("original_price")),  # для весовых — цена за заказанный вес
                        "unit_price": (pr.get("price_detail") or {}).get("price_formatted", "")
                        .replace("$SIGN$", "").replace("$CURRENCY$", "₽"),
                    })
        for row in payload.get("rows") or []:
            rp = row.get("payload") or {}
            label = strings(rp.get("left_part"), [])
            right = ((rp.get("right_part") or {}).get("text") or {}).get("payload") or {}
            if label and right:
                out["totals"].append({"label": " / ".join(label), "sum": money(right.get("actual_price")),
                                       "sum_before": money(right.get("original_price"))})
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("orders", help="список заказов, JSON Lines")
    sub.add_parser("order", help="состав заказа, JSON").add_argument("nr", help="номер заказа из orders")
    sub.add_parser("login", help="развернуть окно для входа в Яндекс ID")
    args = ap.parse_args()

    if args.cmd == "login":
        with browser.session(show=True) as (_, page):
            page.goto(ORDERS, wait_until="load", timeout=40000)
        return print("Окно Яндекс Еды развёрнуто: войди в Яндекс ID, потом его можно свернуть.")
    with browser.session() as (_, page):
        if args.cmd == "orders":
            for r in orders(page):
                print(json.dumps(r, ensure_ascii=False))
        else:
            print(json.dumps(order(page, args.nr), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    browser.run_cli(main)
