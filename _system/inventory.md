# Инвентарь системы

Реестр всех инструментов агента: skills, субагентов, MCP, скриптов и промптов.
Обновляет **настройщик** при любом изменении. Дизайнер этот файл не трогает.

Дата последнего обновления: 2026-08-04

---

## Роли

| Роль | Кто | Что делает |
|---|---|---|
| Дизайнер | Пользователь агента | Ставит задачи в `_tasks/`, получает результаты, информируется через Telegram. Не настраивает систему. |
| Настройщик | Администратор агента | Создаёт/правит skills, субагентов, MCP, промпты, ведёт этот реестр и changelog. |

При запросах дизайнера на настройку системы — агент отказывается и направляет к настройщику.

---

## Промпты (AGENTS.md)

| Файл | Назначение | Статус |
|---|---|---|
| `AGENTS.md` (корень) | Глобальный промпт агента: роль, структура проекта, правила работы | Активен |
| `~/.config/opencode/AGENTS.md` | Пользовательский промпт: правила Python (venv и т.п.) | Активен |

---

## Skills (`.opencode/skills/`)

| Skill | Файл | Назначение | Статус |
|---|---|---|---|
| `design-task-router` | `design-task-router/SKILL.md` | Маршрутизация дизайн-задач по агентам (ux-researcher, figma-exporter). Определяет сценарий, запускает агентов, верифицирует результаты | Активен |

---

## Субагенты (`.opencode/agents/`)

| Агент | Файл | Назначение | Статус |
|---|---|---|---|
| `figma-exporter` | `figma-exporter.md` | Экспорт из Figma: токены, структура компонентов, спецификация, ассеты | Активен |
| `telegram-bridge` | `telegram-bridge.md` | Мост между дизайнером и opencode через Telegram-бота | Активен |
| `ux-researcher` | `ux-researcher.md` | UX-исследование паттернов и антипаттернов через Tavily MCP | Активен |

---

## MCP-серверы (`.opencode/opencode.json`)

| MCP | Тип | Назначение | Статус |
|---|---|---|---|
| `tavily-mcp` | local (npx) | Веб-поиск, extract, crawl, research для UX-исследований | Включён |
| `figma-developer-mcp` | local (npx) | Альтернативный Figma-экспортёр через API-ключ | Отключён |
| `figma` | remote (http://127.0.0.1:3845/mcp) | Основной Figma-MCP через локальный мост Figma Desktop | Включён |
| `telegram` | local (python) | Отправка сообщений/файлов/фото пользователю в Telegram | Включён |
| `design-memory` | local (python) | Память по задачам: реплики, решения, поиск | Включён |
| `figma-developer-mcp` | local (npx) | Альтернативный Figma-экспортёр через API-ключ. Отключён после блокировки 429 | Отключён |

---

## Скрипты (`tools/`)

| Скрипт | Назначение | Зависимости | Статус |
|---|---|---|---|
| `telegram-mcp-server.py` | MCP-сервер для Telegram-уведомлений | python-telegram-bot, `.env` (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID) | Активен |
| `design-memory-mcp-server.py` | MCP-сервер памяти задач (SQLite `design_memory.db`) | sqlite3 (стандартная) | Активен |

---

## Хранилища

| Файл/Папка | Назначение |
|---|---|
| `design_memory.db` | SQLite база памяти задач (реплики, решения, поиск) |
| `_tasks/` | Папки задач дизайнера |
| `_system/` | Документация инфраструктуры (этот файл, changelog, todo, boilerplate-analysis) |
| `.env` | Переменные окружения: TAVILY_API_KEY, FIGMA_TOKEN, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID |
| `.venv/` | Виртуальное окружение Python для MCP-серверов |
| `docs/screenshots/` | Скриншоты для README |

---

## Переменные окружения (`.env`)

| Переменная | Используется в | Статус |
|---|---|---|
| `TAVILY_API_KEY` | tavily-mcp | Настроена |
| `FIGMA_TOKEN` | figma-developer-mcp (отключён) | Настроена |
| `TELEGRAM_BOT_TOKEN` | telegram-mcp | Настроена |
| `TELEGRAM_CHAT_ID` | telegram-mcp | Настроена |