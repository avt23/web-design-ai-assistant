#!/usr/bin/env python3
"""MCP-сервер памяти дизайн-диалогов.

Сохраняет содержательные реплики, решения и артефакты из Telegram-диалогов
в SQLite (design_memory.db в корне проекта). Агент вызывает инструменты
осознанно через MCP.

Tools:
  save_message(role, text, task_id?, tags?) — сохранить реплику диалога
  save_decision(task_id, summary, files?, refs?, tags?) — сохранить решение/вывод
  search_memory(query, tags?, limit?) — полнотекстовый поиск по памяти
  get_task_context(task_id) — всё по конкретной задаче
  list_tasks(limit?) — последние задачи

Переменные окружения:
  DESIGN_MEMORY_DB — путь к sqlite (по умолчанию <project_root>/design_memory.db)
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Загружаем .env из корня проекта (для DESIGN_MEMORY_DB, если задано)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_env_file = _PROJECT_ROOT / ".env"
if _env_file.exists():
    with open(_env_file, "r", encoding="utf-8") as _f:
        for _line in _f:
            _line = _line.strip()
            if _line.startswith("#") or not _line or "=" not in _line:
                continue
            if _line.startswith("export "):
                _line = _line[7:]
            _k, _, _v = _line.partition("=")
            _k = _k.strip()
            _v = _v.strip().strip('"').strip("'")
            if _k and not os.environ.get(_k):
                os.environ[_k] = _v

from mcp.server import Server, NotificationOptions
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("design-memory-mcp")

DB_PATH = Path(os.environ.get("DESIGN_MEMORY_DB", str(_PROJECT_ROOT / "design_memory.db")))
_lock = threading.Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _lock, sqlite3.connect(DB_PATH) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slug TEXT UNIQUE NOT NULL,
                title TEXT,
                created_ts TEXT NOT NULL,
                last_active_ts TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                task_id INTEGER,
                role TEXT NOT NULL,           -- 'user' | 'assistant' | 'system'
                text TEXT NOT NULL,
                tags TEXT                      -- JSON array string
            );
            CREATE TABLE IF NOT EXISTS decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                task_id INTEGER,
                summary TEXT NOT NULL,
                files TEXT,                    -- JSON array of paths
                refs TEXT,                     -- JSON array of URLs/refs
                tags TEXT                      -- JSON array
            );
            CREATE INDEX IF NOT EXISTS idx_msg_task ON messages(task_id);
            CREATE INDEX IF NOT EXISTS idx_dec_task ON decisions(task_id);
            CREATE INDEX IF NOT EXISTS idx_msg_ts ON messages(ts);
            CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
                text, content='messages', content_rowid='id'
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS decisions_fts USING fts5(
                text, content='decisions', content_rowid='id'
            );
            """
        )
        conn.commit()


def _ensure_task(conn: sqlite3.Connection, task_id: Optional[int], task_slug: Optional[str], task_title: Optional[str]) -> Optional[int]:
    if task_id:
        row = conn.execute("SELECT id FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row:
            conn.execute("UPDATE tasks SET last_active_ts=? WHERE id=?", (_now_iso(), task_id))
            return task_id
    if task_slug:
        row = conn.execute("SELECT id FROM tasks WHERE slug=?", (task_slug,)).fetchone()
        if row:
            conn.execute("UPDATE tasks SET last_active_ts=? WHERE id=?", (_now_iso(), row[0]))
            return row[0]
        if task_slug:
            cur = conn.execute(
                "INSERT INTO tasks (slug, title, created_ts, last_active_ts) VALUES (?, ?, ?, ?)",
                (task_slug, task_title, _now_iso(), _now_iso()),
            )
            return cur.lastrowid
    return task_id if task_id else None


def _sync_fts(conn: sqlite3.Connection, table: str, row_id: int, text: str) -> None:
    # FTS-таблицы созданы с полем 'text', в него пишем поисковый контент
    conn.execute(f"INSERT INTO {table}_fts (rowid, text) VALUES (?, ?)", (row_id, text))


_init_db()
server = Server("design-memory")


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="design_memory_save_message",
            description=(
                "Сохранить содержательную реплику диалога (вопрос дизайнера или ответ агента). "
                "Не сохраняй технические логи (/ping, статусы, ошибки). "
                "Используй role='user' для реплик дизайнера, role='assistant' для своих ответов."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "role": {"type": "string", "enum": ["user", "assistant", "system"], "description": "Кто сказал"},
                    "text": {"type": "string", "description": "Текст реплики"},
                    "task_id": {"type": "integer", "description": "ID задачи (если известна)", "default": None},
                    "task_slug": {"type": "string", "description": "Slug задачи (напр. 2026-07-21__unikma-landing). Создаётся автоматически если нет.", "default": None},
                    "task_title": {"type": "string", "description": "Человекочитаемое название задачи", "default": None},
                    "tags": {"type": "array", "items": {"type": "string"}, "description": "Теги для поиска", "default": []},
                },
                "required": ["role", "text"],
            },
        ),
        Tool(
            name="design_memory_save_decision",
            description=(
                "Сохранить решение, вывод или артефакт по задаче: что решили, какие файлы создал, "
                "какие референсы нашёл. Это выжимка, не лог переписки. "
                "Вызывай в конце каждой задачи или когда принял важное решение."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "description": "Краткое описание решения/вывода (1-5 предложений)"},
                    "task_id": {"type": "integer", "default": None},
                    "task_slug": {"type": "string", "default": None},
                    "task_title": {"type": "string", "default": None},
                    "files": {"type": "array", "items": {"type": "string"}, "description": "Пути к созданным/изменённым файлам", "default": []},
                    "refs": {"type": "array", "items": {"type": "string"}, "description": "URL референсов, источников, макетов", "default": []},
                    "tags": {"type": "array", "items": {"type": "string"}, "description": "Теги: figma, landing, trend, ...", "default": []},
                },
                "required": ["summary"],
            },
        ),
        Tool(
            name="design_memory_search",
            description=(
                "Полнотекстовый поиск по памяти: репликам и решениям. "
                "Используй в начале новой задачи, чтобы найти похожие прошлые решения и контекст."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Поисковый запрос (ключевые слова)"},
                    "limit": {"type": "integer", "description": "Макс. число результатов", "default": 10},
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="design_memory_get_task_context",
            description="Получить всю память (реплики + решения) по конкретной задаче по её task_id.",
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer", "description": "ID задачи"},
                },
                "required": ["task_id"],
            },
        ),
        Tool(
            name="design_memory_list_tasks",
            description="Список последних задач (с количеством реплик и решений).",
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "default": 20},
                },
                "required": [],
            },
        ),
    ]


def _ok(msg: str) -> list[TextContent]:
    return [TextContent(type="text", text=msg)]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    try:
        if name == "design_memory_save_message":
            role = arguments["role"]
            text = arguments["text"]
            tags = arguments.get("tags") or []
            task_slug = arguments.get("task_slug")
            task_title = arguments.get("task_title")
            task_id = arguments.get("task_id")
            with _lock, sqlite3.connect(DB_PATH) as conn:
                tid = _ensure_task(conn, task_id, task_slug, task_title)
                cur = conn.execute(
                    "INSERT INTO messages (ts, task_id, role, text, tags) VALUES (?, ?, ?, ?, ?)",
                    (_now_iso(), tid, role, text, json.dumps(tags, ensure_ascii=False)),
                )
                _sync_fts(conn, "messages", cur.lastrowid, text)
                conn.commit()
                return _ok(f"saved message id={cur.lastrowid} task_id={tid}")

        if name == "design_memory_save_decision":
            summary = arguments["summary"]
            files = arguments.get("files") or []
            refs = arguments.get("refs") or []
            tags = arguments.get("tags") or []
            task_slug = arguments.get("task_slug")
            task_title = arguments.get("task_title")
            task_id = arguments.get("task_id")
            with _lock, sqlite3.connect(DB_PATH) as conn:
                tid = _ensure_task(conn, task_id, task_slug, task_title)
                cur = conn.execute(
                    "INSERT INTO decisions (ts, task_id, summary, files, refs, tags) VALUES (?, ?, ?, ?, ?, ?)",
                    (_now_iso(), tid, summary, json.dumps(files, ensure_ascii=False), json.dumps(refs, ensure_ascii=False), json.dumps(tags, ensure_ascii=False)),
                )
                _sync_fts(conn, "decisions", cur.lastrowid, summary)
                conn.commit()
                return _ok(f"saved decision id={cur.lastrowid} task_id={tid}")

        if name == "design_memory_search":
            query = arguments["query"]
            limit = int(arguments.get("limit", 10))
            # FTS5 без стемминга: строим OR-запрос по словам и префиксам
            # напр. "память дизайнера" -> "память OR дизайнера OR память* OR дизайнера*"
            words = [w for w in query.split() if w.strip()]
            terms = []
            for w in words:
                terms.append(w)
                terms.append(w + "*")  # prefix match для морфологии
            fts_query = " OR ".join(terms) if terms else query
            with _lock, sqlite3.connect(DB_PATH) as conn:
                conn.row_factory = sqlite3.Row
                rows_m = conn.execute(
                    """SELECT m.id, m.ts, m.task_id, m.role, m.text, m.tags, t.slug, t.title,
                              bm25(messages_fts) AS rank
                       FROM messages_fts JOIN messages m ON m.id = messages_fts.rowid
                       LEFT JOIN tasks t ON t.id = m.task_id
                       WHERE messages_fts MATCH ? ORDER BY rank LIMIT ?""",
                    (fts_query, limit),
                ).fetchall()
                rows_d = conn.execute(
                    """SELECT d.id, d.ts, d.task_id, d.summary, d.files, d.refs, d.tags, t.slug, t.title,
                              bm25(decisions_fts) AS rank
                       FROM decisions_fts JOIN decisions d ON d.id = decisions_fts.rowid
                       LEFT JOIN tasks t ON t.id = d.task_id
                       WHERE decisions_fts MATCH ? ORDER BY rank LIMIT ?""",
                    (fts_query, limit),
                ).fetchall()
            results = []
            for r in rows_m:
                results.append({"type": "message", "id": r["id"], "ts": r["ts"], "task_id": r["task_id"],
                                "task_slug": r["slug"], "task_title": r["title"], "role": r["role"],
                                "text": r["text"], "tags": json.loads(r["tags"] or "[]")})
            for r in rows_d:
                results.append({"type": "decision", "id": r["id"], "ts": r["ts"], "task_id": r["task_id"],
                                "task_slug": r["slug"], "task_title": r["title"], "summary": r["summary"],
                                "files": json.loads(r["files"] or "[]"), "refs": json.loads(r["refs"] or "[]"),
                                "tags": json.loads(r["tags"] or "[]")})
            return _ok(json.dumps(results, ensure_ascii=False, indent=2))

        if name == "design_memory_get_task_context":
            task_id = int(arguments["task_id"])
            with _lock, sqlite3.connect(DB_PATH) as conn:
                conn.row_factory = sqlite3.Row
                task = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
                msgs = conn.execute("SELECT id, ts, role, text, tags FROM messages WHERE task_id=? ORDER BY ts", (task_id,)).fetchall()
                decs = conn.execute("SELECT id, ts, summary, files, refs, tags FROM decisions WHERE task_id=? ORDER BY ts", (task_id,)).fetchall()
            out = {
                "task": dict(task) if task else None,
                "messages": [dict(m) for m in msgs],
                "decisions": [dict(d) for d in decs],
            }
            return _ok(json.dumps(out, ensure_ascii=False, indent=2))

        if name == "design_memory_list_tasks":
            limit = int(arguments.get("limit", 20))
            with _lock, sqlite3.connect(DB_PATH) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    """SELECT t.id, t.slug, t.title, t.created_ts, t.last_active_ts,
                              (SELECT COUNT(*) FROM messages WHERE task_id=t.id) AS msg_count,
                              (SELECT COUNT(*) FROM decisions WHERE task_id=t.id) AS dec_count
                       FROM tasks t ORDER BY t.last_active_ts DESC LIMIT ?""",
                    (limit,),
                ).fetchall()
            return _ok(json.dumps([dict(r) for r in rows], ensure_ascii=False, indent=2))

        return _ok(f"unknown tool: {name}")
    except Exception as e:
        logger.exception("tool error")
        return _ok(f"ERROR: {type(e).__name__}: {e}")


async def main() -> None:
    async with stdio_server() as (read, write):
        await server.run(read, write, InitializationOptions(
            server_name="design-memory",
            server_version="0.1.0",
            capabilities=server.get_capabilities(
                notification_options=NotificationOptions(),
                experimental_capabilities={},
            ),
        ))


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())