"""SQLite: дедуп, очередь pending, вердикты и счётчик вызовов Gemini.

Стандартный sqlite3, без ORM. В базу попадают ТОЛЬКО сообщения,
прошедшие префильтр — отсеянные не сохраняются вообще, иначе база
распухнет на мусоре из живых чатов. Отсев виден только в DEBUG-логе.

Все временные метки — UTC ISO-8601.
"""

import sqlite3
from datetime import datetime, timedelta, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    chat_id          INTEGER NOT NULL,
    message_id       INTEGER NOT NULL,
    chat_title       TEXT,
    author           TEXT,
    text             TEXT,
    status           TEXT    NOT NULL,   -- pending | classified | failed
    is_lead          INTEGER,
    score            INTEGER,
    category         TEXT,
    reason           TEXT,
    suggested_opener TEXT,
    notified         INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT    NOT NULL,
    classified_at    TEXT,
    PRIMARY KEY (chat_id, message_id)
);
CREATE INDEX IF NOT EXISTS idx_messages_status ON messages(status);

CREATE TABLE IF NOT EXISTS api_calls (
    called_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_api_calls_called_at ON api_calls(called_at);
"""


def _now() -> str:
    """Текущее время, UTC, ISO-8601. Никаких naive datetime."""
    return datetime.now(timezone.utc).isoformat()


def init_db(db_path: str) -> sqlite3.Connection:
    """Открыть базу, создать схему, включить WAL, выдавать sqlite3.Row."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def seen(conn: sqlite3.Connection, chat_id: int, message_id: int) -> bool:
    """True, если пара (chat_id, message_id) уже в базе."""
    row = conn.execute(
        "SELECT 1 FROM messages WHERE chat_id = ? AND message_id = ?",
        (chat_id, message_id),
    ).fetchone()
    return row is not None


def save_incoming(
    conn: sqlite3.Connection,
    chat_id: int,
    message_id: int,
    chat_title: str,
    author: str,
    text: str,
) -> None:
    """Записать прошедшее префильтр сообщение со статусом pending.

    INSERT OR IGNORE — повторная доставка того же сообщения не должна падать.
    """
    conn.execute(
        "INSERT OR IGNORE INTO messages "
        "(chat_id, message_id, chat_title, author, text, status, created_at) "
        "VALUES (?, ?, ?, ?, ?, 'pending', ?)",
        (chat_id, message_id, chat_title, author, text, _now()),
    )
    conn.commit()


def save_verdict(
    conn: sqlite3.Connection,
    chat_id: int,
    message_id: int,
    verdict: dict,
) -> None:
    """Проставить результат классификации: status='classified'."""
    conn.execute(
        "UPDATE messages SET status = 'classified', is_lead = ?, score = ?, "
        "category = ?, reason = ?, suggested_opener = ?, classified_at = ? "
        "WHERE chat_id = ? AND message_id = ?",
        (
            int(bool(verdict.get("is_lead"))),
            verdict.get("score"),
            verdict.get("category"),
            verdict.get("reason"),
            verdict.get("suggested_opener"),
            _now(),
            chat_id,
            message_id,
        ),
    )
    conn.commit()


def mark_failed(
    conn: sqlite3.Connection,
    chat_id: int,
    message_id: int,
    reason: str,
) -> None:
    """Классификация не удалась: status='failed', причина в reason."""
    conn.execute(
        "UPDATE messages SET status = 'failed', reason = ?, classified_at = ? "
        "WHERE chat_id = ? AND message_id = ?",
        (reason, _now(), chat_id, message_id),
    )
    conn.commit()


def mark_notified(conn: sqlite3.Connection, chat_id: int, message_id: int) -> None:
    """Отметить, что уведомление в Saved Messages ушло."""
    conn.execute(
        "UPDATE messages SET notified = 1 WHERE chat_id = ? AND message_id = ?",
        (chat_id, message_id),
    )
    conn.commit()


def pending_batch(conn: sqlite3.Connection, limit: int) -> list[sqlite3.Row]:
    """Сообщения в очереди на классификацию, старые первыми."""
    return conn.execute(
        "SELECT * FROM messages WHERE status = 'pending' "
        "ORDER BY created_at ASC LIMIT ?",
        (limit,),
    ).fetchall()


def register_api_call(conn: sqlite3.Connection) -> None:
    """Отметить факт вызова Gemini — для часового лимитера."""
    conn.execute("INSERT INTO api_calls (called_at) VALUES (?)", (_now(),))
    conn.commit()


def api_calls_last_hour(conn: sqlite3.Connection) -> int:
    """Сколько вызовов Gemini сделано за последний час."""
    since = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM api_calls WHERE called_at >= ?", (since,)
    ).fetchone()
    return row["n"]


def purge_old_api_calls(conn: sqlite3.Connection) -> None:
    """Выбросить записи о вызовах старше двух часов."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    conn.execute("DELETE FROM api_calls WHERE called_at < ?", (cutoff,))
    conn.commit()


def close(conn: sqlite3.Connection) -> None:
    """Закрыть соединение (graceful shutdown)."""
    conn.close()
