# claude-expense-tracker

Плагин-маркетплейс Claude Code: учёт трат (Яндекс Пэй, чеки Ленты, Яндекс Еда) в базе Notion и отчёт за месяц.

- Плагин: `plugins/expense-tracker/` — скиллы `expense-tracker` (сверка и запись), `expense-tracker-setup` (настройка), `expense-report` (отчёт)
- Скрипты и справочники: `plugins/expense-tracker/skills/expense-tracker/{scripts,references}/`
- Проверка манифестов: `claude plugin validate --strict . && claude plugin validate --strict ./plugins/expense-tracker`
- Проверка скриптов: `python3 -m py_compile plugins/expense-tracker/skills/expense-tracker/scripts/*.py plugins/expense-tracker/skills/expense-tracker/scripts/report/*.py`
- Stdlib-скрипты (`config.py`, `guess_combo.py`, `report/build_report.py`) должны работать на Python 3.9 (системный на macOS): `/usr/bin/python3 -m py_compile` для них; скрипты с браузером — 3.10+ через uv
- Демо-отчёт: `python3 plugins/expense-tracker/skills/expense-tracker/scripts/report/build_report.py --csv examples/demo-month.csv --title "Демо-месяц" --findings examples/demo-findings.html --config plugins/expense-tracker/skills/expense-tracker/scripts/report/config.example.json --out docs/demo/report.html`; `docs/demo/report.png` — скриншот этого файла (ширина 1100, светлая тема, полная страница), `report-top.png` — его верх для README. Поменяли CSV — пересчитайте числа в `examples/demo-findings.html`
- Личные данные пользователя — только в `~/.expense-tracker/`, в репо не попадают; примеры — только выдуманные
- Временные файлы: `tmp/` (в .gitignore)
- Подробнее: [README.md](README.md), [docs/how-it-works.md](docs/how-it-works.md), [docs/privacy.md](docs/privacy.md), [docs/troubleshooting.md](docs/troubleshooting.md)
