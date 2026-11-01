"""Отдельный Chrome со своим профилем: через него скрипты ходят в банк, Ленту и Яндекс Еду.

Профиль — <EXPENSE_TRACKER_HOME>/chrome-profile (см. config.py), порт CDP — 9223 или
EXPENSE_TRACKER_CDP_PORT. Используется установленный Google Chrome; браузеры Playwright не нужны,
headless и подмена User-Agent не используются.

Окно держим свёрнутым. Новые вкладки не создаём: на macOS новая вкладка разворачивает окно,
а переход в существующей вкладке его не трогает. Окно разворачиваем только для входа
(PIN банка, логин в Ленте или Яндекс ID) и только с согласия пользователя.

Запуск Chrome:
  macOS   — open -g -j -na "Google Chrome" --args … (без фокуса, скрыто);
  Linux   — google-chrome / google-chrome-stable / chromium / chromium-browser из PATH,
            отдельным процессом; нужна графическая сессия (DISPLAY или WAYLAND_DISPLAY),
            на сервере — xvfb-run;
  Windows — chrome.exe из стандартных мест (не проверялось).
EXPENSE_TRACKER_CHROME — свой путь к бинарю Chrome/Chromium (на macOS можно имя или путь .app).
EXPENSE_TRACKER_CHROME_ARGS — дополнительные флаги, например "--password-store=basic"
или "--ozone-platform=x11".
"""
import os
import shlex
import shutil
import subprocess
import sys
import time
import urllib.request
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config  # noqa: E402

PROFILE = config.chrome_profile()
PORT = config.cdp_port()
CDP = f"http://127.0.0.1:{PORT}"
LAUNCH_TIMEOUT = 20  # секунд на подъём CDP
LINUX_CANDIDATES = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser")


class BrowserError(RuntimeError):
    """Chrome не найден, не запустился или к нему не подключиться."""


def cdp_up():
    try:
        urllib.request.urlopen(CDP + "/json/version", timeout=1)
        return True
    except OSError:
        return False


def chrome_flags():
    flags = [f"--user-data-dir={PROFILE}", f"--remote-debugging-port={PORT}",
             "--no-first-run", "--no-default-browser-check"]
    flags += shlex.split(os.environ.get("EXPENSE_TRACKER_CHROME_ARGS", ""))
    return flags + ["about:blank"]


def _windows_chrome():
    for base in (os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)"),
                 os.environ.get("LOCALAPPDATA")):
        if base:
            p = Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe"
            if p.exists():
                return str(p)
    return shutil.which("chrome") or shutil.which("chrome.exe")


def _spawn(binary):
    """Chrome отдельным процессом, отвязанным от скрипта: он живёт между вызовами."""
    kwargs = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if os.name == "nt":
        kwargs["creationflags"] = (getattr(subprocess, "DETACHED_PROCESS", 0)
                                   | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 7  # SW_SHOWMINNOACTIVE: свёрнуто и без фокуса
        kwargs["startupinfo"] = si
    else:
        kwargs["start_new_session"] = True
    try:
        return subprocess.Popen([binary, *chrome_flags()], **kwargs)
    except OSError as e:
        raise BrowserError(f"не удалось запустить {binary}: {e}") from None


def _launch_macos(override):
    app = override or "Google Chrome"
    if override and not override.endswith(".app") and "/" in override:
        return _spawn(override)  # путь к бинарю, например Chromium
    r = subprocess.run(["open", "-g", "-j", "-na", app, "--args", *chrome_flags()],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise BrowserError(f"не удалось открыть {app!r} ({(r.stderr or '').strip()[:200]}). "
                           "Установи Google Chrome или укажи EXPENSE_TRACKER_CHROME.")
    return None


def _is_snap(binary):
    """Snap-сборка: /snap/bin/… или обёртка-скрипт, который запускает snap (chromium-browser в Ubuntu)."""
    path, real = os.path.abspath(binary), os.path.realpath(binary)
    if path.startswith("/snap/") or real.startswith("/snap/") or os.path.basename(real) == "snap":
        return True
    try:
        with open(real, "rb") as f:
            head = f.read(4096)
    except OSError:
        return False
    return head.startswith(b"#!") and b"/snap/" in head


def _snap_can_use(profile):
    """Snap-интерфейс home пускает только в нескрытые каталоги внутри $HOME."""
    try:
        rel = Path(os.path.abspath(profile)).relative_to(Path.home())
    except ValueError:
        return False
    return not any(part.startswith(".") for part in rel.parts)


def _launch_linux(override):
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        raise BrowserError("нет графической сессии (DISPLAY/WAYLAND_DISPLAY). На сервере запускай "
                           "через xvfb-run; headless-режим не используется.")
    binary = override or next((b for b in map(shutil.which, LINUX_CANDIDATES) if b), None)
    if not binary:
        raise BrowserError("Chrome не найден: нет " + ", ".join(LINUX_CANDIDATES) +
                           " в PATH. Установи Google Chrome или Chromium или укажи EXPENSE_TRACKER_CHROME.")
    if _is_snap(binary) and not _snap_can_use(PROFILE):
        raise BrowserError(f"{binary} — snap-сборка, а она не может открыть профиль {PROFILE} "
                           "(snap пускает только в нескрытые каталоги внутри домашнего). Поставь Google "
                           "Chrome из .deb/.rpm или перенеси данные: export EXPENSE_TRACKER_HOME=~/expense-tracker")
    return _spawn(binary)


def launch():
    config.ensure_home()
    PROFILE.mkdir(mode=0o700, exist_ok=True)
    override = os.environ.get("EXPENSE_TRACKER_CHROME", "").strip() or None
    if sys.platform == "darwin":
        proc = _launch_macos(override)
    elif os.name == "nt":
        binary = override or _windows_chrome()
        if not binary:
            raise BrowserError("chrome.exe не найден. Установи Google Chrome или укажи EXPENSE_TRACKER_CHROME.")
        proc = _spawn(binary)
    else:
        proc = _launch_linux(override)

    deadline = time.time() + LAUNCH_TIMEOUT
    while time.time() < deadline:
        if cdp_up():
            return
        if proc is not None and proc.poll() is not None and proc.returncode != 0:
            raise BrowserError(f"Chrome завершился с кодом {proc.returncode} сразу после запуска.")
        time.sleep(0.25)
    raise BrowserError(
        f"Chrome не открыл CDP-порт {PORT} за {LAUNCH_TIMEOUT} с. Частые причины: профиль {PROFILE} "
        "уже открыт в Chrome без отладочного порта (закрой это окно и повтори) или порт занят другой "
        "программой (задай другой через EXPENSE_TRACKER_CDP_PORT).")


def set_window(page, state):
    """state: "minimized" | "normal". Не везде работает (например, Wayland) — тогда молча пропускаем."""
    try:
        s = page.context.new_cdp_session(page)
        wid = s.send("Browser.getWindowForTarget")["windowId"]
        s.send("Browser.setWindowBounds", {"windowId": wid, "bounds": {"windowState": state}})
        s.detach()
        return True
    except Exception:
        return False


@contextmanager
def session(show=False):
    """(context, page) свёрнутого Chrome; show=True разворачивает окно для входа."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise BrowserError("нет пакета playwright — запускай скрипт через `uv run <скрипт>`") from None
    if not cdp_up():
        launch()
    with sync_playwright() as p:
        try:
            br = p.chromium.connect_over_cdp(CDP)
        except Exception as e:
            raise BrowserError(f"не удалось подключиться к Chrome по CDP ({CDP}): "
                               f"{str(e).splitlines()[0][:200]}") from None
        ctx = br.contexts[0] if br.contexts else br.new_context()
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        if show:
            set_window(page, "normal")
            page.bring_to_front()
        else:
            set_window(page, "minimized")
        yield ctx, page
        # Chrome не закрываем: Playwright только отключается, сессии сайтов живут дальше.


def capture_headers(page, url, match, timeout_ms=8000):
    """Заголовки первого запроса страницы к API (`match` в URL) — нужны, чтобы повторять запросы SPA.

    Значения никогда не печатаем и не сохраняем: среди них сессионные токены.
    """
    drop = {"cookie", "content-length", "host", "connection", "accept-encoding",
            "priority", "traceparent", "x-span-id", "x-trace-id"}
    headers = {}

    def on_request(req):
        if match in req.url and not headers:
            headers.update({k: v for k, v in req.all_headers().items()
                            if not k.startswith((":", "sec-")) and k.lower() not in drop})

    page.on("request", on_request)
    page.goto(url, wait_until="load", timeout=40000)
    waited = 0
    while not headers and waited < timeout_ms:
        page.wait_for_timeout(250)
        waited += 250
    page.remove_listener("request", on_request)
    return headers


def fetch_json(page, path, headers, method="GET", body=None):
    """fetch изнутри страницы (cookies сайта) → [status, json | None]."""
    return page.evaluate(
        """async ([path, headers, method, body]) => {
            const r = await fetch(path, {method, credentials: 'include', headers,
                                         body: body === null ? undefined : JSON.stringify(body)});
            let data = null;
            try { data = await r.json(); } catch (e) {}
            return [r.status, data];
        }""",
        [path, headers, method, body])


def run_cli(main):
    """Понятные сообщения вместо трейсбеков для ошибок браузера; коды выхода скриптов не трогаем."""
    try:
        main()
    except BrowserError as e:
        sys.exit(f"Chrome: {e}")
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as e:
        if type(e).__module__.split(".")[0] == "playwright":
            first = (str(e).splitlines() or [""])[0]
            sys.exit(f"Браузер: {type(e).__name__}: {first[:300]}")
        raise
