#!/usr/bin/env python3
"""
Telegram MCP Server для OpenCode.
Однонаправленный мост: агент → Telegram.

Tools:
  - telegram_send_message(text) → отправить текст
  - telegram_send_file(path, caption?) → отправить файл
  - telegram_send_photo(path, caption?) → отправить фото

Установка:
  pip install mcp python-telegram-bot

Переменные окружения:
  TELEGRAM_BOT_TOKEN — токен бота от @BotFather
  TELEGRAM_CHAT_ID   — chat_id получателя
"""

import os
import sys
import asyncio
import logging
from pathlib import Path
from typing import Optional

# Подгружаем .env из корня проекта вручную (opencode может не передавать env)
_project_root = Path(__file__).resolve().parent.parent
_env_file = _project_root / ".env"
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

# MCP SDK от Anthropic
from mcp.server import Server, NotificationOptions
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

# Telegram
from telegram import Bot

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stderr
)
logger = logging.getLogger("telegram-mcp")

# === Конфигурация из env ===
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

if not BOT_TOKEN or not CHAT_ID:
    logger.error("TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID обязательны!")
    sys.exit(1)

bot = Bot(token=BOT_TOKEN)
server = Server("telegram-mcp")


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="telegram_send_message",
            description=(
                "Отправить текстовое сообщение пользователю в Telegram. "
                "Используй для уведомлений о завершении задачи, "
                "вопросов к пользователю или отправки результатов."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "Текст сообщения (поддерживает Markdown и HTML).",
                    },
                    "parse_mode": {
                        "type": "string",
                        "enum": ["Markdown", "HTML", "Plain"],
                        "default": "Plain",
                        "description": "Форматирование текста.",
                    },
                },
                "required": ["text"],
            },
        ),
        Tool(
            name="telegram_send_file",
            description=(
                "Отправить файл пользователю в Telegram. "
                "Используй для отправки сгенерированных документов, "
                "скриптов, результатов работы."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Абсолютный путь к файлу.",
                    },
                    "caption": {
                        "type": "string",
                        "description": "Подпись к файлу (необязательно).",
                    },
                },
                "required": ["path"],
            },
        ),
        Tool(
            name="telegram_send_photo",
            description=(
                "Отправить изображение пользователю в Telegram. "
                "Поддерживает локальные файлы и URL."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Путь к файлу или URL изображения.",
                    },
                    "caption": {
                        "type": "string",
                        "description": "Подпись к фото (необязательно).",
                    },
                },
                "required": ["path"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    try:
        if name == "telegram_send_message":
            text = arguments.get("text", "")
            parse_mode = arguments.get("parse_mode", "Plain")
            if parse_mode == "Plain":
                parse_mode = None

            await bot.send_message(
                chat_id=CHAT_ID,
                text=text[:4096],  # лимит Telegram
                parse_mode=parse_mode,
            )
            return [TextContent(type="text", text="Сообщение отправлено в Telegram.")]

        elif name == "telegram_send_file":
            path = arguments.get("path", "")
            caption = arguments.get("caption")

            if not Path(path).exists():
                return [TextContent(type="text", text=f"Файл не найден: {path}")]

            with open(path, "rb") as f:
                await bot.send_document(
                    chat_id=CHAT_ID,
                    document=f,
                    caption=caption[:1024] if caption else None,
                )
            return [TextContent(type="text", text=f"Файл отправлен: {path}")]

        elif name == "telegram_send_photo":
            path = arguments.get("path", "")
            caption = arguments.get("caption")

            if path.startswith("http"):
                await bot.send_photo(
                    chat_id=CHAT_ID,
                    photo=path,
                    caption=caption[:1024] if caption else None,
                )
            else:
                if not Path(path).exists():
                    return [TextContent(type="text", text=f"Файл не найден: {path}")]
                with open(path, "rb") as f:
                    await bot.send_photo(
                        chat_id=CHAT_ID,
                        photo=f,
                        caption=caption[:1024] if caption else None,
                    )
            return [TextContent(type="text", text=f"Фото отправлено: {path}")]

        else:
            return [TextContent(type="text", text=f"Неизвестный tool: {name}")]

    except Exception as e:
        logger.error(f"Ошибка в {name}: {e}", exc_info=True)
        return [TextContent(type="text", text=f"Ошибка: {e}")]


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="telegram-mcp",
                server_version="1.0.0",
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )


if __name__ == "__main__":
    asyncio.run(main())