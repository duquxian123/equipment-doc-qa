"""Small persistent memory store for user-owned agent sessions."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
_db_lock = threading.Lock()


class SessionAccessError(PermissionError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def database_path() -> Path:
    return Path(
        os.environ.get(
            "EQUIPMENT_QA_AGENT_DB_PATH",
            str(ROOT / "output" / "agent_memory.db"),
        )
    )


def _connect() -> sqlite3.Connection:
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def initialize() -> None:
    with _db_lock, closing(_connect()) as connection, connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS agent_sessions (
                id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_agent_sessions_user
                ON agent_sessions(user_id, updated_at DESC);

            CREATE TABLE IF NOT EXISTS agent_turns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                user_query TEXT NOT NULL,
                answer TEXT NOT NULL,
                mode TEXT NOT NULL,
                status TEXT NOT NULL,
                tools_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(session_id) REFERENCES agent_sessions(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_agent_turns_session
                ON agent_turns(session_id, id DESC);
            """
        )


def ensure_session(session_id: str | None, user_id: int) -> str:
    initialize()
    candidate = (session_id or uuid.uuid4().hex).strip()
    if not 8 <= len(candidate) <= 64 or not all(c.isalnum() or c in "_-" for c in candidate):
        raise ValueError("会话标识格式不正确")
    now = utc_now()
    with _db_lock, closing(_connect()) as connection, connection:
        row = connection.execute(
            "SELECT user_id FROM agent_sessions WHERE id = ?",
            (candidate,),
        ).fetchone()
        if row and int(row["user_id"]) != int(user_id):
            raise SessionAccessError("该会话不属于当前用户")
        if not row:
            connection.execute(
                "INSERT INTO agent_sessions(id, user_id, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (candidate, int(user_id), now, now),
            )
        else:
            connection.execute(
                "UPDATE agent_sessions SET updated_at = ? WHERE id = ?",
                (now, candidate),
            )
    return candidate


def _assert_owner(connection: sqlite3.Connection, session_id: str, user_id: int) -> None:
    row = connection.execute(
        "SELECT user_id FROM agent_sessions WHERE id = ?",
        (session_id,),
    ).fetchone()
    if not row:
        raise FileNotFoundError("会话不存在")
    if int(row["user_id"]) != int(user_id):
        raise SessionAccessError("该会话不属于当前用户")


def recent_context(session_id: str, user_id: int, limit: int = 6) -> str:
    initialize()
    with _db_lock, closing(_connect()) as connection:
        _assert_owner(connection, session_id, user_id)
        rows = connection.execute(
            """
            SELECT user_query, answer, tools_json, created_at
            FROM agent_turns
            WHERE session_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (session_id, limit),
        ).fetchall()
    if not rows:
        return "无历史会话。"
    lines: list[str] = []
    for row in reversed(rows):
        tools = json.loads(row["tools_json"] or "[]")
        tool_text = "、".join(str(item) for item in tools) or "无工具"
        answer = str(row["answer"]).replace("\n", " ")
        lines.append(
            f"- 用户：{str(row['user_query'])[:140]} | 工具：{tool_text} | 答复：{answer[:180]}"
        )
    return "\n".join(lines)


def add_turn(
    session_id: str,
    user_id: int,
    query: str,
    answer: str,
    mode: str,
    status: str,
    tools: list[str],
) -> None:
    initialize()
    now = utc_now()
    with _db_lock, closing(_connect()) as connection, connection:
        _assert_owner(connection, session_id, user_id)
        connection.execute(
            """
            INSERT INTO agent_turns(
                session_id, user_query, answer, mode, status, tools_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                query[:2000],
                answer[:12000],
                mode,
                status,
                json.dumps(tools, ensure_ascii=False),
                now,
            ),
        )
        connection.execute(
            "UPDATE agent_sessions SET updated_at = ? WHERE id = ?",
            (now, session_id),
        )


def get_history(session_id: str, user_id: int) -> dict[str, Any]:
    initialize()
    with _db_lock, closing(_connect()) as connection:
        _assert_owner(connection, session_id, user_id)
        rows = connection.execute(
            """
            SELECT user_query, answer, mode, status, tools_json, created_at
            FROM agent_turns
            WHERE session_id = ?
            ORDER BY id ASC
            LIMIT 100
            """,
            (session_id,),
        ).fetchall()
    turns = [
        {
            "query": row["user_query"],
            "answer": row["answer"],
            "mode": row["mode"],
            "status": row["status"],
            "tools": json.loads(row["tools_json"] or "[]"),
            "created_at": row["created_at"],
        }
        for row in rows
    ]
    return {
        "session_id": session_id,
        "turns": turns,
        "memory_summary": f"已保存最近 {min(len(turns), 6)} 轮会话摘要。",
    }


def clear_session(session_id: str, user_id: int) -> None:
    initialize()
    with _db_lock, closing(_connect()) as connection, connection:
        _assert_owner(connection, session_id, user_id)
        connection.execute("DELETE FROM agent_sessions WHERE id = ?", (session_id,))
