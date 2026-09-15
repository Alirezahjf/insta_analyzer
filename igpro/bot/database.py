"""دیتابیس SQLite ربات: کاربران/مدیر/دسترسی‌ها + هیزتوری.

ساده، بدون وابستگی خارجی. هر عملیات زیر قفل است و از رشته‌های مختلف هم امن است.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

FOREVER_SECONDS = 3600 * 24 * 365 * 100  # « دائمی » به‌عنوان ۱۰۰ سال

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    telegram_id INTEGER PRIMARY KEY,
    username    TEXT,
    first_name  TEXT,
    is_admin    INTEGER NOT NULL DEFAULT 0,
    access_until REAL,                 -- epoch؛ NULL = بدون دسترسی
    created_at  REAL NOT NULL,
    last_seen   REAL
);
CREATE TABLE IF NOT EXISTS access_requests (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    requester_id INTEGER NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending',
    created_at   REAL NOT NULL,
    resolved_at  REAL
);
CREATE TABLE IF NOT EXISTS history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    kind        TEXT NOT NULL,
    target      TEXT NOT NULL,
    status      TEXT NOT NULL,
    result_html TEXT,
    meta        TEXT,
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_history_user ON history(user_id, id DESC);
"""


class Database:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------- کاربران
    def ensure_user(
        self,
        telegram_id: int,
        username: Optional[str],
        first_name: Optional[str],
        forced_admin_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """کاربر را اضافه/به‌روزرسانی می‌کند و ردیفش برمی‌گرداند.

        قاعده‌ی مدیریت:
          - اگر forced_admin_id ست شده باشد (env BOT_ADMIN_ID): فقط همان آیدی مدیر است.
          - وگرنه: اولین نفری که در دیتابیس ثبت می‌شود، مدیر می‌شود.
        """
        now = time.time()
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)
            ).fetchone()
            if row is None:
                total = self._conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
                if forced_admin_id is not None:
                    is_admin = 1 if forced_admin_id == telegram_id else 0
                else:
                    is_admin = 1 if total == 0 else 0
                self._conn.execute(
                    "INSERT INTO users (telegram_id, username, first_name, is_admin, access_until, created_at, last_seen)"
                    " VALUES (?, ?, ?, ?, NULL, ?, ?)",
                    (telegram_id, username, first_name, is_admin, now, now),
                )
                self._conn.commit()
                row = self._conn.execute(
                    "SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)
                ).fetchone()
            else:
                updates = ["username = ?", "first_name = ?", "last_seen = ?"]
                params: List[Any] = [username, first_name, now]
                if forced_admin_id is not None and forced_admin_id == telegram_id and not row["is_admin"]:
                    updates.append("is_admin = 1")
                self._conn.execute(
                    f"UPDATE users SET {', '.join(updates)} WHERE telegram_id = ?",
                    (*params, telegram_id),
                )
                self._conn.commit()
                row = self._conn.execute(
                    "SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)
                ).fetchone()
            return dict(row)

    def get_user(self, telegram_id: int) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)
            ).fetchone()
            return dict(row) if row else None

    def list_users(self) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM users ORDER BY is_admin DESC, created_at ASC"
            ).fetchall()
            return [dict(r) for r in rows]

    def grant_access(self, telegram_id: int, hours: Optional[float]) -> float:
        """دسترسی تا hours ساعت دیگر؛ hours=None یعنی دائمی.

        اگر کاربر هنوز ردیف نداشته باشد (مثلاً /start نزده)، ردیفش ساخته می‌شود.
        """
        until = time.time() + (hours * 3600 if hours is not None else FOREVER_SECONDS)
        now = time.time()
        with self._lock:
            cur = self._conn.execute(
                "UPDATE users SET access_until = ? WHERE telegram_id = ?",
                (until, telegram_id),
            )
            if cur.rowcount == 0:
                self._conn.execute(
                    "INSERT OR IGNORE INTO users"
                    " (telegram_id, username, first_name, is_admin, access_until, created_at, last_seen)"
                    " VALUES (?, NULL, NULL, 0, ?, ?, ?)",
                    (telegram_id, until, now, now),
                )
            self._conn.commit()
        return until

    def revoke_access(self, telegram_id: int) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE users SET access_until = NULL WHERE telegram_id = ?",
                (telegram_id,),
            )
            self._conn.commit()

    def is_authorized(self, telegram_id: int, now: Optional[float] = None) -> bool:
        user = self.get_user(telegram_id)
        if not user:
            return False
        if user["is_admin"]:
            return True
        until = user["access_until"]
        return bool(until and until > (now if now is not None else time.time()))

    def admin_id(self) -> Optional[int]:
        with self._lock:
            row = self._conn.execute(
                "SELECT telegram_id FROM users WHERE is_admin = 1 ORDER BY created_at ASC LIMIT 1"
            ).fetchone()
            return row["telegram_id"] if row else None

    # -------------------------------------------------------- درخواست دسترسی
    def has_pending_request(self, requester_id: int) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT id FROM access_requests WHERE requester_id = ? AND status = 'pending'",
                (requester_id,),
            ).fetchone()
            return row is not None

    def add_access_request(self, requester_id: int) -> int:
        """درخواست جدید؛ اگر قبلاً در انتظار است، همان شناسه برمی‌گردد."""
        with self._lock:
            row = self._conn.execute(
                "SELECT id FROM access_requests WHERE requester_id = ? AND status = 'pending'",
                (requester_id,),
            ).fetchone()
            if row:
                return row["id"]
            cur = self._conn.execute(
                "INSERT INTO access_requests (requester_id, status, created_at) VALUES (?, 'pending', ?)",
                (requester_id, time.time()),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def resolve_access_request(self, requester_id: int, status: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE access_requests SET status = ?, resolved_at = ?"
                " WHERE requester_id = ? AND status = 'pending'",
                (status, time.time(), requester_id),
            )
            self._conn.commit()

    # ------------------------------------------------------------- هیزتوری
    def add_history(
        self,
        user_id: int,
        kind: str,
        target: str,
        status: str,
        result_html: Optional[str],
        meta: Optional[Dict[str, Any]],
    ) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO history (user_id, kind, target, status, result_html, meta, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    user_id,
                    kind,
                    target,
                    status,
                    (result_html or "")[:60000] or None,
                    json.dumps(meta or {}, ensure_ascii=False),
                    time.time(),
                ),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def get_history(self, history_id: int) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM history WHERE id = ?", (history_id,)).fetchone()
            return dict(row) if row else None

    def list_history(
        self, user_id: int, offset: int = 0, limit: int = 10
    ) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, kind, target, status, created_at FROM history"
                " WHERE user_id = ? ORDER BY id DESC LIMIT ? OFFSET ?",
                (user_id, limit, offset),
            ).fetchall()
            return [dict(r) for r in rows]

    def count_history(self, user_id: int) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS c FROM history WHERE user_id = ?", (user_id,)
            ).fetchone()
            return int(row["c"])
