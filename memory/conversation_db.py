"""
MARK XL — Conversation database (SQLite).

Persists every conversation turn, tool call, and tool result so history
survives restarts and can be searched/exported.

Schema:
    conversations  — one row per user session
    messages       — individual messages (user/assistant/tool)
    tool_calls     — tool invocations tied to an assistant message
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator

from core.paths import BASE_DIR
from core.logger import get_logger

log = get_logger("conversation_db")

_DB_PATH = BASE_DIR / "memory" / "conversations.db"
_lock = threading.Lock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    title       TEXT    DEFAULT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role            TEXT    NOT NULL CHECK(role IN ('user', 'assistant', 'tool', 'system')),
    content         TEXT    NOT NULL DEFAULT '',
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS tool_calls (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id  INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    tool_name   TEXT    NOT NULL,
    arguments   TEXT    NOT NULL DEFAULT '{}',
    result      TEXT    DEFAULT NULL,
    duration_ms INTEGER DEFAULT NULL,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_tool_calls_msg ON tool_calls(message_id);
"""


def _get_conn() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH), timeout=5)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def _transaction() -> Generator[sqlite3.Connection, None, None]:
    conn = _get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Create tables if they don't exist."""
    with _transaction() as conn:
        conn.executescript(_SCHEMA)
    log.info("Conversation DB initialized at %s", _DB_PATH)


# ── Conversations ──────────────────────────────────────────────────────────

def create_conversation(title: str | None = None) -> int:
    """Create a new conversation and return its ID."""
    with _lock:
        with _transaction() as conn:
            cur = conn.execute(
                "INSERT INTO conversations (title) VALUES (?)",
                (title,),
            )
            return cur.lastrowid  # type: ignore[return-value]


def update_conversation_title(conv_id: int, title: str) -> None:
    with _lock:
        with _transaction() as conn:
            conn.execute(
                "UPDATE conversations SET title=?, updated_at=datetime('now') WHERE id=?",
                (title, conv_id),
            )


def list_conversations(limit: int = 50) -> list[dict]:
    """List recent conversations with message counts."""
    conn = _get_conn()
    try:
        rows = conn.execute(
            """
            SELECT c.id, c.title, c.created_at, c.updated_at,
                   COUNT(m.id) as message_count
            FROM conversations c
            LEFT JOIN messages m ON m.conversation_id = c.id
            GROUP BY c.id
            ORDER BY c.updated_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_conversation(conv_id: int) -> dict | None:
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM conversations WHERE id=?", (conv_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def delete_conversation(conv_id: int) -> None:
    with _lock:
        with _transaction() as conn:
            conn.execute("DELETE FROM conversations WHERE id=?", (conv_id,))


# ── Messages ───────────────────────────────────────────────────────────────

def add_message(conv_id: int, role: str, content: str) -> int:
    """Add a message to a conversation and return its ID."""
    with _lock:
        with _transaction() as conn:
            cur = conn.execute(
                "INSERT INTO messages (conversation_id, role, content) VALUES (?, ?, ?)",
                (conv_id, role, content),
            )
            conn.execute(
                "UPDATE conversations SET updated_at=datetime('now') WHERE id=?",
                (conv_id,),
            )
            return cur.lastrowid  # type: ignore[return-value]


def get_messages(conv_id: int, limit: int = 100) -> list[dict]:
    """Get messages for a conversation, oldest first."""
    conn = _get_conn()
    try:
        rows = conn.execute(
            """
            SELECT m.id, m.role, m.content, m.created_at
            FROM messages m
            WHERE m.conversation_id = ?
            ORDER BY m.id ASC
            LIMIT ?
            """,
            (conv_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_messages_as_dicts(conv_id: int, limit: int = 100) -> list[dict]:
    """Get messages formatted for LLM API (role + content only)."""
    conn = _get_conn()
    try:
        rows = conn.execute(
            """
            SELECT role, content FROM messages
            WHERE conversation_id = ? AND role IN ('user', 'assistant')
            ORDER BY id ASC LIMIT ?
            """,
            (conv_id, limit),
        ).fetchall()
        return [{"role": r["role"], "content": r["content"]} for r in rows]
    finally:
        conn.close()


# ── Tool calls ─────────────────────────────────────────────────────────────

def add_tool_call(
    message_id: int,
    tool_name: str,
    arguments: dict | str,
    result: str | None = None,
    duration_ms: int | None = None,
) -> int:
    """Record a tool call tied to an assistant message."""
    args_str = json.dumps(arguments, ensure_ascii=False) if isinstance(arguments, dict) else arguments
    with _lock:
        with _transaction() as conn:
            cur = conn.execute(
                "INSERT INTO tool_calls (message_id, tool_name, arguments, result, duration_ms) "
                "VALUES (?, ?, ?, ?, ?)",
                (message_id, tool_name, args_str, result, duration_ms),
            )
            return cur.lastrowid  # type: ignore[return-value]


def get_tool_calls(message_id: int) -> list[dict]:
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM tool_calls WHERE message_id=? ORDER BY id",
            (message_id,),
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            try:
                d["arguments"] = json.loads(d["arguments"])
            except (json.JSONDecodeError, TypeError):
                pass
            result.append(d)
        return result
    finally:
        conn.close()


# ── Search ─────────────────────────────────────────────────────────────────

def search_messages(query: str, limit: int = 20) -> list[dict]:
    """Full-text search across all messages."""
    conn = _get_conn()
    try:
        rows = conn.execute(
            """
            SELECT m.id, m.role, m.content, m.created_at,
                   c.id as conversation_id, c.title
            FROM messages m
            JOIN conversations c ON c.id = m.conversation_id
            WHERE m.content LIKE ?
            ORDER BY m.created_at DESC
            LIMIT ?
            """,
            (f"%{query}%", limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ── Stats ──────────────────────────────────────────────────────────────────

def get_stats() -> dict:
    """Return database statistics."""
    conn = _get_conn()
    try:
        conv_count = conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
        msg_count = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        tool_count = conn.execute("SELECT COUNT(*) FROM tool_calls").fetchone()[0]
        return {
            "conversations": conv_count,
            "messages": msg_count,
            "tool_calls": tool_count,
            "db_path": str(_DB_PATH),
            "db_size_kb": round(_DB_PATH.stat().st_size / 1024, 1) if _DB_PATH.exists() else 0,
        }
    finally:
        conn.close()
