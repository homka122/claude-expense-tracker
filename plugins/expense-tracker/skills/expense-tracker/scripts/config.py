#!/usr/bin/env python3
"""Пути и настройки expense-tracker. Только стандартная библиотека.

Личные данные живут вне репозитория, в EXPENSE_TRACKER_HOME (по умолчанию ~/.expense-tracker):

    config.json       {"notion": {"data_source": "collection://…", "default_view": "https://www.notion.so/…?v=…"},
                       "timezone": "<IANA, например Europe/…>", "cdp_port": 9223}
    profile.md        что скилл узнал о пользователе: магазины, привычки, люди, правила
    chrome-profile/   отдельный профиль Chrome для банка, Ленты и Яндекс Еды
    ypay-feed-hash    кэш operationId GraphQL банка

Порт CDP: EXPENSE_TRACKER_CDP_PORT, затем "cdp_port" из config.json, иначе 9223.
Часовой пояс: "timezone" из config.json, иначе системный (TZ, /etc/localtime), иначе UTC.

    python3 config.py show                               # пути и действующие настройки (JSON)
    python3 config.py check                              # exit 0 — config.json валиден, 2 — нет
    python3 config.py set notion.data_source "collection://…"
    python3 config.py set cdp_port 9230
    python3 config.py path [home|config|profile|chrome-profile|ypay-feed-hash]
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

try:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones
except ImportError:  # Python < 3.9
    ZoneInfo = None
    ZoneInfoNotFoundError = Exception
    available_timezones = None

DEFAULT_PORT = 9223
EXIT_INVALID = 2


class ConfigError(Exception):
    pass


# --- пути -------------------------------------------------------------------------------------

_warned_relative_home = False


def home():
    global _warned_relative_home
    raw = os.environ.get("EXPENSE_TRACKER_HOME", "").strip()
    if not raw:
        return Path.home() / ".expense-tracker"
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = p.resolve()
        if not _warned_relative_home:
            print(f"предупреждение: EXPENSE_TRACKER_HOME={raw!r} — относительный путь, беру {p} "
                  "от текущего каталога; задай абсолютный путь", file=sys.stderr)
            _warned_relative_home = True
    return p


def config_path():
    return home() / "config.json"


def profile_path():
    return home() / "profile.md"


def chrome_profile():
    return home() / "chrome-profile"


def feed_hash_path():
    return home() / "ypay-feed-hash"


PATHS = {
    "home": home,
    "config": config_path,
    "profile": profile_path,
    "chrome-profile": chrome_profile,
    "ypay-feed-hash": feed_hash_path,
}


def ensure_home():
    """Каталог данных только для владельца (0700): там cookies банка и личный профиль."""
    h = home()
    h.mkdir(mode=0o700, parents=True, exist_ok=True)
    return h


# --- чтение -----------------------------------------------------------------------------------

def read_config():
    """config.json как dict; {} если файла нет; ConfigError если он битый."""
    p = config_path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ConfigError(f"{p}: не читается как JSON ({e})") from None
    if not isinstance(data, dict):
        raise ConfigError(f"{p}: ожидался JSON-объект")
    return data


def load(strict=False):
    """Для скриптов: битый config.json не роняет выгрузку, а даёт предупреждение и значения по умолчанию."""
    try:
        return read_config()
    except ConfigError as e:
        if strict:
            raise
        print(f"предупреждение: {e}; беру значения по умолчанию", file=sys.stderr)
        return {}


def get(key, default=None, cfg=None):
    node = load() if cfg is None else cfg
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def _valid_port(v):
    return isinstance(v, int) and not isinstance(v, bool) and 1024 <= v <= 65535


def cdp_port_source(cfg=None):
    raw = os.environ.get("EXPENSE_TRACKER_CDP_PORT", "").strip()
    if raw:
        if raw.isdigit() and _valid_port(int(raw)):
            return int(raw), "env EXPENSE_TRACKER_CDP_PORT"
        print(f"предупреждение: EXPENSE_TRACKER_CDP_PORT={raw!r} — не порт 1024–65535, игнорирую",
              file=sys.stderr)
    v = get("cdp_port", cfg=cfg)
    if _valid_port(v):
        return v, "config.json"
    return DEFAULT_PORT, "default"


def cdp_port(cfg=None):
    return cdp_port_source(cfg)[0]


IANA_NAME = re.compile(r"^(UTC|[A-Za-z_]+(/[A-Za-z0-9_+-]+)+)$")
_tz_db = None  # есть ли в системе база часовых поясов (на Windows без пакета tzdata её нет)


def _has_tz_db():
    global _tz_db
    if _tz_db is None:
        try:
            _tz_db = bool(available_timezones and available_timezones())
        except OSError:
            _tz_db = False
    return _tz_db


def _valid_tz(name):
    if not isinstance(name, str) or not name.strip():
        return False
    if ZoneInfo is None:
        return bool(IANA_NAME.match(name))
    try:
        ZoneInfo(name)
        return True
    except ZoneInfoNotFoundError:
        # Без базы поясов проверить имя нельзя — принимаем по форме Area/Location.
        return not _has_tz_db() and bool(IANA_NAME.match(name))
    except (ValueError, OSError):
        return False


def system_timezone():
    """IANA-имя системного часового пояса или UTC, если определить не вышло."""
    tz = os.environ.get("TZ", "").lstrip(":").strip()
    if tz and _valid_tz(tz):
        return tz
    try:
        link = os.path.realpath("/etc/localtime")
        if "zoneinfo/" in link:
            name = link.split("zoneinfo/", 1)[1]
            if _valid_tz(name):
                return name
    except OSError:
        pass
    try:
        name = Path("/etc/timezone").read_text(encoding="utf-8").strip()
        if _valid_tz(name):
            return name
    except OSError:
        pass
    return "UTC"


def timezone_source(cfg=None):
    v = get("timezone", cfg=cfg)
    if _valid_tz(v):
        return v, "config.json"
    return system_timezone(), "system"


def timezone(cfg=None):
    return timezone_source(cfg)[0]


def tzinfo(cfg=None):
    """tzinfo для перевода времени операций; None — тогда системный пояс через astimezone()."""
    if ZoneInfo is None:
        return None
    try:
        return ZoneInfo(timezone(cfg))
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return None


# --- проверка ---------------------------------------------------------------------------------

def validate(cfg):
    """(ошибки, предупреждения) для содержимого config.json."""
    errors, warnings = [], []
    notion = cfg.get("notion")
    if not isinstance(notion, dict):
        errors.append('нет раздела "notion" (объект с data_source и default_view)')
    else:
        ds = notion.get("data_source")
        if not isinstance(ds, str) or not ds.strip():
            errors.append('нет notion.data_source (data source базы «Расходы», вида "collection://…")')
        elif not ds.startswith("collection://"):
            warnings.append('notion.data_source обычно начинается с "collection://"')
        view = notion.get("default_view")
        if view is None or view == "":
            warnings.append("нет notion.default_view — когда кончится квота SQL, читать базу будет не через что")
        elif not isinstance(view, str) or not view.startswith(("https://", "http://", "view://")):
            errors.append("notion.default_view должен быть URL представления (https://www.notion.so/…?v=…)")
    if "timezone" in cfg and not _valid_tz(cfg["timezone"]):
        errors.append(f"timezone {cfg['timezone']!r} — не IANA-имя часового пояса")
    elif "timezone" in cfg and ZoneInfo is not None and tzinfo(cfg) is None:
        warnings.append("в системе нет базы часовых поясов (на Windows — пакет tzdata): "
                        "timezone принят по форме, не проверен")
    if "cdp_port" in cfg and not _valid_port(cfg["cdp_port"]):
        errors.append(f"cdp_port {cfg['cdp_port']!r} — нужен целый порт 1024–65535")
    return errors, warnings


# --- запись -----------------------------------------------------------------------------------

def _coerce(key, raw):
    """Значение из командной строки: известные ключи — по типу, остальное — JSON, если парсится."""
    if key == "cdp_port":
        if not raw.strip().isdigit() or not _valid_port(int(raw)):
            raise ConfigError("cdp_port — целый порт 1024–65535")
        return int(raw)
    if key == "timezone":
        if not _valid_tz(raw):
            raise ConfigError(f"{raw!r} — не IANA-имя часового пояса (например, Asia/…, Europe/…)")
        return raw
    if key.startswith("notion."):
        return raw
    try:
        return json.loads(raw)
    except ValueError:
        return raw


def set_value(key, raw):
    parts = [p for p in key.split(".") if p]
    if not parts:
        raise ConfigError("пустой ключ")
    value = _coerce(key, raw)
    cfg = read_config()
    node = cfg
    for i, part in enumerate(parts[:-1]):
        nxt = node.setdefault(part, {})
        if not isinstance(nxt, dict):
            raise ConfigError(f"{'.'.join(parts[:i + 1])} — не объект, вложенный ключ не записать")
        node = nxt
    node[parts[-1]] = value
    write_config(cfg)
    return value


def write_config(cfg):
    ensure_home()
    p = config_path()
    tmp = p.with_name(p.name + ".tmp")
    # Файл сразу создаётся с правами 0600, без окна, когда он читается всеми.
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n")
    try:
        os.chmod(tmp, 0o600)  # если .tmp остался от прошлого раза с другими правами
    except OSError:
        pass
    os.replace(tmp, p)


# --- CLI --------------------------------------------------------------------------------------

def cmd_show():
    try:
        cfg, problem = read_config(), None
    except ConfigError as e:
        cfg, problem = {}, str(e)
    port, port_src = cdp_port_source(cfg)
    tz, tz_src = timezone_source(cfg)
    out = {
        "home": str(home()),
        "config": {"path": str(config_path()), "exists": config_path().exists()},
        "profile": {"path": str(profile_path()), "exists": profile_path().exists()},
        "chrome_profile": {"path": str(chrome_profile()), "exists": chrome_profile().exists()},
        "ypay_feed_hash": {"path": str(feed_hash_path()), "exists": feed_hash_path().exists()},
        "cdp_port": {"value": port, "source": port_src},
        "timezone": {"value": tz, "source": tz_src},
        "settings": cfg,
    }
    if problem:
        out["config"]["error"] = problem
    print(json.dumps(out, ensure_ascii=False, indent=2))


def cmd_check():
    p = config_path()
    if not p.exists():
        print(f"нет {p} — нужна первичная настройка (скилл expense-tracker-setup)", file=sys.stderr)
        return EXIT_INVALID
    try:
        cfg = read_config()
    except ConfigError as e:
        print(e, file=sys.stderr)
        return EXIT_INVALID
    errors, warnings = validate(cfg)
    for w in warnings:
        print(f"предупреждение: {w}", file=sys.stderr)
    if errors:
        for e in errors:
            print(f"ошибка: {e}", file=sys.stderr)
        return EXIT_INVALID
    print(f"ok: {p}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("show", help="пути и действующие настройки, JSON")
    sub.add_parser("check", help="exit 0 — config.json валиден, 2 — нет")
    s = sub.add_parser("set", help="записать значение в config.json (ключ в точечной нотации)")
    s.add_argument("key", help="например notion.data_source, notion.default_view, timezone, cdp_port")
    s.add_argument("value")
    pth = sub.add_parser("path", help="напечатать путь (по умолчанию каталог данных)")
    pth.add_argument("name", nargs="?", default="home", choices=list(PATHS))
    args = ap.parse_args(argv)

    if args.cmd == "show":
        cmd_show()
        return 0
    if args.cmd == "check":
        return cmd_check()
    if args.cmd == "path":
        print(PATHS[args.name]())
        return 0
    try:
        value = set_value(args.key, args.value)
    except ConfigError as e:
        print(f"ошибка: {e}", file=sys.stderr)
        return EXIT_INVALID
    print(f"{args.key} = {json.dumps(value, ensure_ascii=False)}  ({config_path()})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
