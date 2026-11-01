#!/usr/bin/env python3
"""Подбирает, из каких известных покупок складывается сумма платежа. Только стандартная библиотека.

    python3 guess_combo.py 345 "Сэндвич=215" "Сок=65" "Йогурт=65" "Чай=40" ...
    python3 guess_combo.py 345 --prices prices.json      # [{"name": ..., "price": ...}, ...]

Цены бери из прошлых записей базы по этому же магазину. Короткие сочетания — первыми:
обычно человек берёт 1–3 вещи, а «два одинаковых товара» вероятнее «одного товара и шести мелочей».
Подобранный состав помечай в «Детали»: «состав подобран по прошлым ценам».
Код выхода 0, даже если ничего не нашлось (тогда — подсказка про --tolerance).
"""
import argparse
import json
import sys


def parse_price(raw):
    s = str(raw).replace("\u00a0", "").replace(" ", "").replace("₽", "").replace("\u2212", "-")
    return float(s.replace(",", "."))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("target", type=parse_price, help="сумма платежа, ₽")
    ap.add_argument("items", nargs="*", help="Название=цена")
    ap.add_argument("--prices", help='JSON-файл со списком {"name", "price"}')
    ap.add_argument("--max-items", type=int, default=4, help="сколько позиций максимум (по умолчанию 4)")
    ap.add_argument("--tolerance", type=float, default=0.0, help="допуск в рублях (цены плавают)")
    ap.add_argument("--top", type=int, default=12, help="сколько вариантов показать")
    args = ap.parse_args()
    if args.max_items < 1:
        ap.error("--max-items — не меньше 1")
    if args.top < 1:
        ap.error("--top — не меньше 1")
    if args.tolerance < 0:
        ap.error("--tolerance — не меньше 0")
    args.target = abs(args.target)  # из банка сумма приходит со знаком: −412
    if args.target == 0:
        ap.error("сумма платежа должна быть больше 0")

    prices = {}

    def add(name, price):
        try:
            value = parse_price(price)
        except ValueError:
            ap.error(f"не цена: {name}={price}")
        if value > 0:
            prices[f"{name.strip()} {value:g}"] = value

    for raw in args.items:
        if "=" not in raw:
            ap.error(f"ожидалось «Название=цена», получено: {raw}")
        name, price = raw.rsplit("=", 1)
        add(name, price)
    if args.prices:
        try:
            with open(args.prices, encoding="utf-8") as f:
                for p in json.load(f):
                    add(str(p["name"]), p["price"])
        except (OSError, ValueError, KeyError, TypeError) as e:
            ap.error(f"--prices: {e}")
    if not prices:
        ap.error("нет ни одной цены: передай «Название=цена» или --prices")

    # Перебор с отсечением: цены положительные, поэтому дальше суммы цели + допуска не идём.
    names = sorted(prices, key=lambda k: (prices[k], k))
    limit = args.target + args.tolerance + 1e-9
    found = []

    def walk(start, combo, total):
        if combo:
            diff = abs(total - args.target)
            if diff <= args.tolerance + 1e-9:
                found.append((diff, len(combo), len({c.rsplit(" ", 1)[0] for c in combo}), tuple(combo)))
        if len(combo) >= args.max_items:
            return
        for i in range(start, len(names)):
            nxt = total + prices[names[i]]
            if nxt > limit:
                break
            combo.append(names[i])
            walk(i, combo, nxt)
            combo.pop()

    walk(0, [], 0.0)
    found.sort(key=lambda f: (f[0], f[1], f[2], f[3]))
    if not found:
        print("точных сочетаний нет — попробуй --tolerance 5 (цены в магазине меняются)")
    for diff, _, _, combo in found[:args.top]:
        print(" + ".join(combo) + (f" (±{diff:g})" if diff else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
