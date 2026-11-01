#!/usr/bin/env python3
"""HTML-отчёт по тратам за период из CSV-выгрузки базы Notion «Расходы». Только стандартная библиотека.

    python3 build_report.py --csv rows.csv --title "Октябрь 2026" \\
        [--findings findings.html] [--out report.html] [--config rules.json]

CSV — одна строка базы на строку, UTF-8, разделитель «;» (также понимает «,» и TAB):

    date;item;category;necessity;where;amount;details

  date       Дата: YYYY-MM-DD (время после даты отбрасывается; DD.MM.YYYY, YYYY/MM/DD тоже понимаются)
  item       Товар
  category   Категория
  necessity  Необходимость: «Базовое» / «Необязательное»
  where      Где
  amount     Сумма, ₽ («1 234,50», «1,234.50», «₽99» тоже понимаются)
  details    Детали
Необязательные колонки: qty (Количество), type (Тип), gift и guessed (да/нет — если их нет,
распознаются по тексту). Вместо английских имён колонок можно русские названия свойств
(Дата, Товар, Категория, Необходимость, Где, Сумма, Детали, Количество, Тип).

Подарок — «(в подарок)» или «(за <кого>)» в названии, название «Подарок…», детали «в подарок…».
Подобранный состав — слово «подобран» в деталях.
Крупная покупка — строка дороже big_amount (2000 ₽) или из категорий big_categories («Электроника»):
входит в итог, но не в «Траты по дням», «Необязательное» и «Где».

--config — необязательный JSON с правилами отчёта (образец — config.example.json рядом):
    {"big_amount": 3000,
     "big_categories": ["Электроника", "Одежда"],
     "group_by": "category",                     # или "type" — группы по свойству «Тип»
     "groups": {"Подписки": "Цифровое"},          # переименовать или слить категории
     "base_label": "Базовое",
     "habits": [{"label": "Фастфуд", "match": ["Шаурма", "Бургер"], "unit": "раз"},
                {"label": "Кофе с собой", "match": ["Капучино", "Латте"], "where": ["Кофейня"]}]}
  Можно передать и просто список habits. В правиле: match — подстроки названия (без учёта регистра),
  categories и where — необязательные фильтры; строка попадает в первое подходящее правило,
  подарки не считаются. unit «раз» считает визиты (день + место), остальные единицы — штуки (qty).

--findings — HTML-фрагмент с карточками «Выводы» / «Что улучшить» и подписью источника; его пишет
агент под конкретный месяц по образцу references/findings-template.html.

Печатает путь к HTML, затем итоги в JSON и списки групп — для выводов.
"""
import argparse
import collections
import csv
import datetime as dt
import html
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
          "сентября", "октября", "ноября", "декабря"]
EN_MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august",
             "september", "october", "november", "december"]
GROCERIES = ("Мясо и птица", "Рыба", "Молочное и яйца", "Бакалея", "Хлеб", "Овощи", "Фрукты")
DEFAULTS = {
    "big_amount": 2000,
    "big_categories": ["Электроника"],
    "group_by": "category",
    "groups": {c: "Продукты" for c in GROCERIES},
    "base_label": "Базовое",
    "habits": [],
}
KNOWN_KEYS = set(DEFAULTS)
ALIASES = {
    "date": "date", "дата": "date", "date:дата:start": "date",
    "item": "item", "товар": "item", "name": "item", "название": "item",
    "category": "category", "cat": "category", "категория": "category",
    "necessity": "necessity", "need": "necessity", "необходимость": "necessity",
    "where": "where", "где": "where",
    "amount": "amount", "sum": "amount", "сумма": "amount",
    "details": "details", "детали": "details",
    "qty": "qty", "quantity": "qty", "количество": "qty",
    "type": "type", "тип": "type",
    "gift": "gift", "подарок": "gift",
    "guessed": "guessed", "подобрано": "guessed",
}
REQUIRED = ("date", "item", "amount")
TRUE = {"1", "true", "yes", "y", "да", "x", "+", "✓", "✔", "__yes__"}
FALSE = {"0", "false", "no", "n", "нет", "-", "__no__"}
GIFT_FOR = re.compile(r"\(за [^)]+\)", re.IGNORECASE)
TIMES = re.compile(r"×\s?(\d+)")
WHERE_TOP = 10  # мест в «Где», остальное — одной строкой


def fail(msg):
    print(f"build_report: {msg}", file=sys.stderr)
    sys.exit(2)


# --- разбор входа -----------------------------------------------------------------------------

def parse_number(s):
    """«1 234,50», «1234.5», «1,234.50», «1.234,50», «₽1,234.50», «RUB 99» → float; иначе None."""
    s = str(s or "").strip().replace("\u2212", "-")  # «−» (минус) → «-»
    s = re.sub(r"[^\d.,+-]", "", s)  # пробелы, валюта, «руб.»
    if "," in s and "." in s:
        # Оба разделителя: десятичный — тот, что правее, другой — разделитель тысяч.
        thousands = "," if s.rfind(",") < s.rfind(".") else "."
        s = s.replace(thousands, "")
    elif s.count(",") > 1:
        s = s.replace(",", "")  # 1,234,567
    elif s.count(".") > 1:
        s = s.replace(".", "")  # 1.234.567
    s = s.replace(",", ".")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def parse_date(s):
    s = str(s or "").strip().split("→")[0].strip()
    if not s:
        return None
    try:
        return dt.date.fromisoformat(s[:10])
    except ValueError:
        pass
    m = re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.(\d{4}).*", s)
    if m:
        try:
            return dt.date(int(m[3]), int(m[2]), int(m[1]))
        except ValueError:
            return None
    m = re.fullmatch(r"(\d{4})/(\d{1,2})/(\d{1,2}).*", s)  # 2026/10/07
    if m:
        try:
            return dt.date(int(m[1]), int(m[2]), int(m[3]))
        except ValueError:
            return None
    m = re.fullmatch(r"(\d{1,2})\s+([а-яё]+)\s+(\d{4}).*", s.lower())  # 5 октября 2026
    if m and m[2] in MONTHS:
        return dt.date(int(m[3]), MONTHS.index(m[2]) + 1, int(m[1]))
    m = re.fullmatch(r"([a-z]+)\s+(\d{1,2}),\s*(\d{4}).*", s.lower())  # October 5, 2026
    if m and m[1] in EN_MONTHS:
        return dt.date(int(m[3]), EN_MONTHS.index(m[1]) + 1, int(m[2]))
    return None


def flag(value):
    v = str(value or "").strip().lower()
    if v in TRUE:
        return True
    if v in FALSE:
        return False
    return None


def load(path, cfg):
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except OSError as e:
        fail(f"не открыть CSV: {e}")
    except UnicodeDecodeError as e:
        fail(f"CSV не в UTF-8 ({e}). Пересохраните файл в UTF-8")
    lines = text.splitlines()
    if not lines or not lines[0].strip():
        fail("CSV пустой")
    head = lines[0]
    delimiter = max((";", ",", "\t"), key=lambda d: (head.count(d), d == ";"))
    reader = csv.DictReader(text.splitlines(), delimiter=delimiter)
    columns = {}
    for name in reader.fieldnames or []:
        key = ALIASES.get((name or "").strip().lower())
        if key and key not in columns:
            columns[key] = name
    missing = [c for c in REQUIRED if c not in columns]
    if missing:
        fail(f"в CSV нет колонок {', '.join(missing)} (есть: {', '.join(reader.fieldnames or [])})")
    if cfg["group_by"] == "type" and "type" not in columns:
        fail('group_by "type", но в CSV нет колонки type (Тип)')

    base = cfg["base_label"].strip().lower()
    big_cats = set(cfg["big_categories"])
    rows, skipped = [], []
    for n, raw in enumerate(reader, start=2):
        r = {k: (raw.get(columns[k]) or "").strip() if k in columns else "" for k in ALIASES.values()}
        d, amount = parse_date(r["date"]), parse_number(r["amount"])
        if d is None or amount is None:
            if any((v or "").strip() for v in raw.values()):
                skipped.append(n)
            continue
        r["date"], r["amount"] = d.isoformat(), amount
        item, details = r["item"], r["details"].lower()
        gift = flag(r["gift"])
        r["gift"] = gift if gift is not None else (
            "(в подарок)" in item.lower() or bool(GIFT_FOR.search(item))
            or item.lower().startswith("подарок") or details.startswith(("в подарок", "подарок")))
        guessed = flag(r["guessed"])
        r["guessed"] = guessed if guessed is not None else "подобран" in details
        r["big"] = r["category"] in big_cats or amount >= cfg["big_amount"]
        r["base"] = r["necessity"].lower() == base
        rows.append(r)
    if skipped:
        shown = ", ".join(map(str, skipped[:10])) + (" …" if len(skipped) > 10 else "")
        print(f"предупреждение: пропущено строк без даты или суммы: {len(skipped)} (номера в CSV: {shown})",
              file=sys.stderr)
    if not rows:
        fail("в CSV нет ни одной строки с датой и суммой")
    return rows


def str_list(value, key):
    """Список строк из --config: строку оборачивает в список, null — пустой список, остальное — ошибка."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(x, (str, int, float)) and not isinstance(x, bool)
                                       for x in value):
        return [str(x) for x in value]
    fail(f"--config: {key} — список строк")


def load_config(path):
    cfg = json.loads(json.dumps(DEFAULTS))
    if not path:
        return cfg
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        fail(f"--config: {e}")
    if isinstance(data, list):
        data = {"habits": data}
    if not isinstance(data, dict):
        fail("--config: ожидался JSON-объект или список правил habits")
    unknown = set(data) - KNOWN_KEYS
    if unknown:
        print(f"предупреждение: в --config неизвестные ключи {sorted(unknown)} — пропускаю", file=sys.stderr)
    if "big_amount" in data:
        if not isinstance(data["big_amount"], (int, float)) or isinstance(data["big_amount"], bool):
            fail("--config: big_amount — число")
        cfg["big_amount"] = data["big_amount"]
    if "big_categories" in data:
        cfg["big_categories"] = str_list(data["big_categories"], "big_categories")
    if "group_by" in data:
        if data["group_by"] not in ("category", "type"):
            fail('--config: group_by — "category" или "type"')
        cfg["group_by"] = data["group_by"]
        if data["group_by"] == "type":
            cfg["groups"] = {}
    if "groups" in data:
        if not isinstance(data["groups"], dict):
            fail("--config: groups — объект {«Категория»: «Группа»}")
        cfg["groups"].update({str(k): str(v) for k, v in data["groups"].items()})
    if "base_label" in data:
        if not isinstance(data["base_label"], str) or not data["base_label"].strip():
            fail("--config: base_label — непустая строка, например «Базовое»")
        cfg["base_label"] = data["base_label"]
    raw_habits = data.get("habits") or []
    if not isinstance(raw_habits, list):
        fail("--config: habits — список правил")
    habits = []
    for i, h in enumerate(raw_habits):
        if not isinstance(h, dict) or not h.get("label"):
            fail(f"--config: habits[{i}] — нужен объект с label")
        rule = {"label": str(h["label"]), "unit": str(h.get("unit") or "шт")}
        for key in ("match", "categories", "where"):
            rule[key] = [x.lower() for x in str_list(h.get(key), f"habits[{i}].{key}")]
        if not (rule["match"] or rule["categories"] or rule["where"]):
            fail(f"--config: habits[{i}] — укажи match, categories или where")
        habits.append(rule)
    cfg["habits"] = habits
    return cfg


# --- расчёт -----------------------------------------------------------------------------------

def group(r, cfg):
    if cfg["group_by"] == "type":
        key = r["type"] or "Без типа"
    else:
        key = r["category"] or "Без категории"
    return cfg["groups"].get(key, key)


def habit(r, cfg):
    if r["gift"]:
        return None
    item = r["item"].lower()
    for h in cfg["habits"]:
        if h["match"] and not any(m in item for m in h["match"]):
            continue
        if h["categories"] and r["category"].lower() not in h["categories"]:
            continue
        if h["where"] and r["where"].lower() not in h["where"]:
            continue
        return h
    return None


def qty(r):
    q = parse_number(r.get("qty")) or 0
    if q >= 1 and q == int(q):
        return int(q)
    m = TIMES.search(r["item"])
    return int(m[1]) if m and int(m[1]) > 0 else 1


def human_date(d, year=False):
    return f"{d.day} {MONTHS[d.month - 1]}" + (f" {d.year}" if year else "")


def short_list(rows, limit=3):
    parts = [f"{r['item']} {dt.date.fromisoformat(r['date']).strftime('%d.%m')}" for r in rows[:limit]]
    if len(rows) > limit:
        parts.append(f"и ещё {len(rows) - limit}")
    return ", ".join(parts)


def bars(counter):
    return [{"label": k, "value": round(v, 2)} for k, v in counter.most_common()]


def build(rows, cfg):
    dates = sorted(dt.date.fromisoformat(r["date"]) for r in rows)
    first, last = dates[0], dates[-1]
    days = (last - first).days + 1
    total = sum(r["amount"] for r in rows)
    big = sorted((r for r in rows if r["big"]), key=lambda r: (r["date"], -r["amount"]))
    big_sum = sum(r["amount"] for r in big)
    small = [r for r in rows if not r["big"]]
    everyday = total - big_sum

    by_group = collections.Counter()
    for r in rows:
        by_group[group(r, cfg)] += r["amount"]

    daily = {(first + dt.timedelta(days=i)).isoformat(): {"base": 0.0, "opt": 0.0} for i in range(days)}
    for r in small:
        daily[r["date"]]["base" if r["base"] else "opt"] += r["amount"]

    optional = collections.Counter()
    for r in small:
        if not r["base"]:
            optional[group(r, cfg)] += r["amount"]
    optional_total = sum(optional.values())

    habit_sum, habit_cnt, habit_visits, units = (collections.Counter(), collections.Counter(),
                                                 collections.defaultdict(set), {})
    for r in rows:
        h = habit(r, cfg)
        if not h:
            continue
        label = h["label"]
        units[label] = h["unit"]
        habit_sum[label] += r["amount"]
        if h["unit"] == "раз":
            habit_visits[label].add((r["date"], r["where"] or r["item"]))
        else:
            habit_cnt[label] += qty(r)
    for label, visits in habit_visits.items():
        habit_cnt[label] = len(visits)
    habits_total = sum(habit_sum.values())

    by_where = collections.Counter()
    for r in small:
        by_where[r["where"] or r["category"] or "Без места"] += r["amount"]
    top = by_where.most_common(WHERE_TOP)
    rest = sum(v for k, v in by_where.items() if k not in dict(top))
    if rest:
        top.append(("Остальное", rest))

    if first == last:
        period = human_date(first, year=True)
    elif first.year != last.year:
        period = f"{human_date(first, year=True)} – {human_date(last, year=True)}"
    elif first.month == last.month:
        period = f"{first.day}–{human_date(last)} {last.year}"
    else:
        period = f"{human_date(first)} – {human_date(last)} {last.year}"
    big_items = short_list(big)
    gifts = [r for r in rows if r["gift"]]
    return {
        "period": period,
        "days": days,
        "rows": len(rows),
        "groupBy": cfg["group_by"],
        "total": round(total, 2),
        "everyday": round(everyday, 2),
        "perDay": round(everyday / days, 2),
        "big": round(big_sum, 2),
        "bigCount": len(big),
        "bigItems": big_items,
        "bigNote": (f"Без крупных покупок ({big_items}) — они смазали бы масштаб" if big else "Все траты по дням"),
        "bigThreshold": cfg["big_amount"],
        "optional": round(optional_total, 2),
        "optionalShare": round(optional_total / everyday, 4) if everyday else 0,
        "gifts": round(sum(r["amount"] for r in gifts), 2),
        "giftRows": len(gifts),
        "guessedRows": sum(r["guessed"] for r in rows),
        "habitsTotal": round(habits_total, 2),
        "groups": bars(by_group),
        "daily": [{"date": k, "base": round(v["base"], 2), "opt": round(v["opt"], 2)} for k, v in daily.items()],
        "optionalGroups": bars(optional),
        "habits": [{"label": k, "value": round(v, 2), "count": habit_cnt[k], "unit": units[k]}
                   for k, v in habit_sum.most_common()],
        "where": [{"label": k, "value": round(v, 2)} for k, v in top],
    }


# --- вывод ------------------------------------------------------------------------------------

def render(data, title, findings):
    template = (HERE / "template.html").read_text(encoding="utf-8")
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    parts = {"/*TITLE*/": html.escape(title), "/*DATA*/null": payload, "<!--FINDINGS-->": findings}
    return re.sub(r"/\*TITLE\*/|/\*DATA\*/null|<!--FINDINGS-->", lambda m: parts[m[0]], template)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", required=True, help="выгрузка строк базы, разделитель «;»")
    ap.add_argument("--title", required=True, help="заголовок отчёта, например «Октябрь 2026»")
    ap.add_argument("--findings", help="HTML-фрагмент с выводами; без него — только графики")
    ap.add_argument("--out", help="куда записать HTML (по умолчанию рядом с CSV, .html)")
    ap.add_argument("--config", help="JSON с правилами отчёта: порог крупных, группы, привычки")
    args = ap.parse_args()

    cfg = load_config(args.config)
    data = build(load(args.csv, cfg), cfg)
    findings = ""
    if args.findings:
        try:
            findings = Path(args.findings).read_text(encoding="utf-8")
        except OSError as e:
            fail(f"--findings: {e}")
    out = Path(args.out) if args.out else Path(args.csv).with_suffix(".html")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(data, args.title, findings), encoding="utf-8")
    print(out)
    print(json.dumps({k: v for k, v in data.items() if not isinstance(v, list)}, ensure_ascii=False, indent=1))
    for k in ("groups", "optionalGroups", "habits", "where"):
        print(k, [(x["label"], x["value"]) + ((f"{x['count']} {x['unit']}",) if "count" in x else ())
                  for x in data[k]])


if __name__ == "__main__":
    main()
