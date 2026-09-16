"""SQLite: дедуп, очередь pending, таблица лидов, счётчик вызовов Gemini."""

import sqlite3


def connect(db_path: str) -> sqlite3.Connection:
    """Открыть соединение и создать схему, если её нет."""
    raise NotImplementedError


def init_schema(conn: sqlite3.Connection) -> None:
    """CREATE TABLE IF NOT EXISTS для messages и gemini_calls."""
    raise NotImplementedError


def seen(conn: sqlite3.Connection, chat_id: int, message_id: int) -> bool:
    """True, если пара (chat_id, message_id) уже в базе."""
    raise NotImplementedError


def save_message(
    conn: sqlite3.Connection,
    chat_id: int,
    message_id: int,
    chat_title: str,
    author: str,
    text: str,
    status: str,
) -> None:
    """Записать сообщение. status: skipped | pending | done."""
    raise NotImplementedError


def save_verdict(
    conn: sqlite3.Connection,
    chat_id: int,
    message_id: int,
    score: int,
    category: str,
    reason: str,
    notified: bool,
) -> None:
    """Дописать результат классификации к уже сохранённому сообщению."""
    raise NotImplementedError


def list_pending(conn: sqlite3.Connection, limit: int) -> list[sqlite3.Row]:
    """Сообщения со статусом pending — те, что не влезли в часовой лимит."""
    raise NotImplementedError


def count_gemini_calls_last_hour(conn: sqlite3.Connection) -> int:
    """Сколько вызовов Gemini сделано за последний час."""
    raise NotImplementedError


def log_gemini_call(conn: sqlite3.Connection) -> None:
    """Отметить факт вызова Gemini (для часового лимитера)."""
    raise NotImplementedError


def close(conn: sqlite3.Connection) -> None:
    """Закрыть соединение (graceful shutdown)."""
    raise NotImplementedError
