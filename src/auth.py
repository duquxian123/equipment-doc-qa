"""本地账号、会话、角色权限和操作日志。

使用标准库 SQLite 与 PBKDF2，不依赖外部身份服务。首次启动时没有账号，
由 Web 页面创建第一个系统管理员；之后可通过用户管理页面增加其他账号。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import threading
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = Path(os.environ.get("EQUIPMENT_QA_DB_PATH", str(ROOT / "output" / "app.db")))
SESSION_COOKIE = "equipment_doc_qa_session"
SESSION_SECONDS = 12 * 60 * 60
PBKDF2_ITERATIONS = 240_000

_db_lock = threading.Lock()

PERMISSIONS = {
    "chat.use": "使用文档问答",
    "ops.view": "查看生产运营沙盘",
    "ops.manage": "创建订单、下发工单和提交报工",
    "documents.read": "查看文档和 PDF",
    "documents.manage": "上传、审核与发布文档",
    "versions.manage": "查看和回滚索引版本",
    "users.manage": "管理用户与角色",
    "audit.view": "查看操作日志",
}

ROLE_PERMISSIONS = {
    "user": ["chat.use", "ops.view", "documents.read"],
    "knowledge_admin": [
        "chat.use",
        "ops.view",
        "ops.manage",
        "documents.read",
        "documents.manage",
        "versions.manage",
    ],
    "system_admin": list(PERMISSIONS),
}

ROLE_LABELS = {
    "user": "普通用户",
    "knowledge_admin": "知识管理员",
    "system_admin": "系统管理员",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def initialize() -> None:
    with _db_lock, closing(_connect()) as connection, connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                last_login_at TEXT
            );

            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                expires_at REAL NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);

            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                user_id INTEGER,
                username TEXT NOT NULL,
                role TEXT NOT NULL,
                action TEXT NOT NULL,
                target TEXT,
                details TEXT,
                ip_address TEXT
            );
            """
        )


def _hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        PBKDF2_ITERATIONS,
    )
    return "pbkdf2_sha256${}${}${}".format(
        PBKDF2_ITERATIONS,
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(digest).decode("ascii"),
    )


def _verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt_text, digest_text = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        salt = base64.b64decode(salt_text)
        expected = base64.b64decode(digest_text)
        actual = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt,
            int(iterations),
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def validate_username(username: str) -> str:
    value = username.strip()
    if not 3 <= len(value) <= 32:
        raise ValueError("用户名长度应为 3–32 个字符")
    if not all(char.isalnum() or char in "_-" for char in value):
        raise ValueError("用户名只能包含文字、字母、数字、下划线和短横线")
    return value


def validate_password(password: str) -> None:
    if len(password) < 8:
        raise ValueError("密码至少需要 8 个字符")
    if len(password) > 128:
        raise ValueError("密码不能超过 128 个字符")


def _public_user(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "username": row["username"],
        "role": row["role"],
        "role_label": ROLE_LABELS[row["role"]],
        "active": bool(row["active"]),
        "permissions": ROLE_PERMISSIONS[row["role"]],
        "created_at": row["created_at"],
        "last_login_at": row["last_login_at"],
    }


def initialized() -> bool:
    initialize()
    with _db_lock, closing(_connect()) as connection, connection:
        return connection.execute("SELECT COUNT(*) FROM users").fetchone()[0] > 0


def create_initial_admin(username: str, password: str, ip_address: str = "") -> dict:
    initialize()
    username = validate_username(username)
    validate_password(password)
    with _db_lock, closing(_connect()) as connection, connection:
        if connection.execute("SELECT COUNT(*) FROM users").fetchone()[0] > 0:
            raise ValueError("系统已经初始化")
        now = utc_now()
        cursor = connection.execute(
            """
            INSERT INTO users(username, password_hash, role, active, created_at)
            VALUES (?, ?, 'system_admin', 1, ?)
            """,
            (username, _hash_password(password), now),
        )
        user_id = int(cursor.lastrowid)
        row = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        _audit_with_connection(
            connection,
            row=row,
            action="auth.setup",
            target=f"user:{user_id}",
            details={"role": "system_admin"},
            ip_address=ip_address,
        )
        return _public_user(row)


def authenticate(username: str, password: str, ip_address: str = "") -> dict | None:
    initialize()
    with _db_lock, closing(_connect()) as connection, connection:
        row = connection.execute(
            "SELECT * FROM users WHERE username = ?",
            (username.strip(),),
        ).fetchone()
        if not row or not row["active"] or not _verify_password(password, row["password_hash"]):
            return None
        now = utc_now()
        connection.execute(
            "UPDATE users SET last_login_at = ? WHERE id = ?",
            (now, row["id"]),
        )
        row = connection.execute("SELECT * FROM users WHERE id = ?", (row["id"],)).fetchone()
        _audit_with_connection(
            connection,
            row=row,
            action="auth.login",
            target=f"user:{row['id']}",
            details=None,
            ip_address=ip_address,
        )
        return _public_user(row)


def create_session(user_id: int) -> tuple[str, int]:
    initialize()
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    expires_at = time.time() + SESSION_SECONDS
    with _db_lock, closing(_connect()) as connection, connection:
        connection.execute("DELETE FROM sessions WHERE expires_at <= ?", (time.time(),))
        connection.execute(
            "INSERT INTO sessions(token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (token_hash, user_id, utc_now(), expires_at),
        )
    return token, SESSION_SECONDS


def destroy_session(token: str | None) -> None:
    if not token:
        return
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    initialize()
    with _db_lock, closing(_connect()) as connection, connection:
        connection.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))


def current_user(token: str | None) -> dict | None:
    if not token:
        return None
    initialize()
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    with _db_lock, closing(_connect()) as connection, connection:
        row = connection.execute(
            """
            SELECT users.*
            FROM sessions
            JOIN users ON users.id = sessions.user_id
            WHERE sessions.token_hash = ?
              AND sessions.expires_at > ?
              AND users.active = 1
            """,
            (token_hash, time.time()),
        ).fetchone()
        return _public_user(row) if row else None


def permission_required(path: str, method: str) -> str | None:
    if path.startswith("/agent"):
        return "chat.use"
    if path.startswith("/ops"):
        return "ops.view" if method in {"GET", "HEAD", "OPTIONS"} else "ops.manage"
    if path == "/ask":
        return "chat.use"
    if path == "/documents":
        return "documents.read"
    if path.startswith("/documents/") and path.endswith("/file"):
        return "documents.read"
    if path == "/documents/upload" or path.startswith("/jobs"):
        return "documents.manage"
    if path.startswith("/versions"):
        return "versions.manage"
    if path.startswith("/admin"):
        return "users.manage"
    if path.startswith("/audit"):
        return "audit.view"
    return None


def list_users() -> list[dict]:
    initialize()
    with _db_lock, closing(_connect()) as connection, connection:
        rows = connection.execute("SELECT * FROM users ORDER BY id").fetchall()
        return [_public_user(row) for row in rows]


def create_user(
    username: str,
    password: str,
    role: str,
    actor: dict,
    ip_address: str = "",
) -> dict:
    username = validate_username(username)
    validate_password(password)
    if role not in ROLE_PERMISSIONS:
        raise ValueError("角色无效")
    initialize()
    with _db_lock, closing(_connect()) as connection, connection:
        try:
            cursor = connection.execute(
                """
                INSERT INTO users(username, password_hash, role, active, created_at)
                VALUES (?, ?, ?, 1, ?)
                """,
                (username, _hash_password(password), role, utc_now()),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError("用户名已存在") from exc
        row = connection.execute("SELECT * FROM users WHERE id = ?", (cursor.lastrowid,)).fetchone()
        _audit_with_connection(
            connection,
            actor=actor,
            action="user.create",
            target=f"user:{row['id']}",
            details={"username": username, "role": role},
            ip_address=ip_address,
        )
        return _public_user(row)


def update_user(
    user_id: int,
    payload: dict,
    actor: dict,
    ip_address: str = "",
) -> dict:
    initialize()
    with _db_lock, closing(_connect()) as connection, connection:
        row = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            raise FileNotFoundError("用户不存在")

        role = payload.get("role", row["role"])
        active = bool(payload.get("active", row["active"]))
        if role not in ROLE_PERMISSIONS:
            raise ValueError("角色无效")
        if row["role"] == "system_admin" and (role != "system_admin" or not active):
            admins = connection.execute(
                "SELECT COUNT(*) FROM users WHERE role = 'system_admin' AND active = 1"
            ).fetchone()[0]
            if admins <= 1:
                raise ValueError("不能停用或降级最后一个系统管理员")

        connection.execute(
            "UPDATE users SET role = ?, active = ? WHERE id = ?",
            (role, int(active), user_id),
        )
        if payload.get("password"):
            validate_password(payload["password"])
            connection.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?",
                (_hash_password(payload["password"]), user_id),
            )
            connection.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))

        current = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        _audit_with_connection(
            connection,
            actor=actor,
            action="user.update",
            target=f"user:{user_id}",
            details={
                "role": role,
                "active": active,
                "password_changed": bool(payload.get("password")),
            },
            ip_address=ip_address,
        )
        return _public_user(current)


def _audit_with_connection(
    connection: sqlite3.Connection,
    *,
    action: str,
    target: str | None,
    details: dict | None,
    ip_address: str,
    row: sqlite3.Row | None = None,
    actor: dict | None = None,
) -> None:
    if actor:
        user_id = actor["id"]
        username = actor["username"]
        role = actor["role"]
    elif row:
        user_id = row["id"]
        username = row["username"]
        role = row["role"]
    else:
        user_id = None
        username = "anonymous"
        role = "anonymous"
    connection.execute(
        """
        INSERT INTO audit_logs(
            created_at, user_id, username, role, action, target, details, ip_address
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            utc_now(),
            user_id,
            username,
            role,
            action,
            target,
            json.dumps(details, ensure_ascii=False) if details else None,
            ip_address,
        ),
    )


def audit(
    actor: dict,
    action: str,
    target: str | None = None,
    details: dict | None = None,
    ip_address: str = "",
) -> None:
    initialize()
    with _db_lock, closing(_connect()) as connection, connection:
        _audit_with_connection(
            connection,
            actor=actor,
            action=action,
            target=target,
            details=details,
            ip_address=ip_address,
        )


def list_audit(limit: int = 100) -> list[dict]:
    initialize()
    with _db_lock, closing(_connect()) as connection, connection:
        rows = connection.execute(
            "SELECT * FROM audit_logs ORDER BY id DESC LIMIT ?",
            (max(1, min(limit, 500)),),
        ).fetchall()
        return [
            {
                "id": row["id"],
                "created_at": row["created_at"],
                "username": row["username"],
                "role": row["role"],
                "action": row["action"],
                "target": row["target"],
                "details": json.loads(row["details"]) if row["details"] else None,
                "ip_address": row["ip_address"],
            }
            for row in rows
        ]


def roles_payload() -> dict:
    return {
        "roles": [
            {
                "id": role,
                "label": ROLE_LABELS[role],
                "permissions": permissions,
            }
            for role, permissions in ROLE_PERMISSIONS.items()
        ],
        "permissions": PERMISSIONS,
    }
