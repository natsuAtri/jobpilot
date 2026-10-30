"""SQLite 连接与迁移。

设计取舍：不用 ORM。表不到 15 张，SQL 直接写在仓储层最可读，
排查问题时不需要猜 ORM 的隐式行为。
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

from .config import DB_PATH, MIGRATIONS_DIR, ensure_dirs


def now_local() -> str:
    """本地时间的文本表示。

    SQLite 的 `datetime('now')` 是 UTC，直接存会让列表里的「创建时间」
    比用户本地时钟早 8 小时。这里统一用本地时间，落库时显式写入。
    """
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


SCHEMA_VERSION_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""


def connect() -> sqlite3.Connection:
    ensure_dirs()
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    """一次事务：正常提交，异常回滚。"""
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def applied_versions(conn: sqlite3.Connection) -> set[str]:
    conn.execute(SCHEMA_VERSION_TABLE)
    return {row["version"] for row in conn.execute("SELECT version FROM schema_migrations")}


def migrate(db_path: Path | None = None) -> list[str]:
    """按文件名顺序执行未应用过的迁移，返回本次应用的版本名。"""
    ensure_dirs()
    applied_now: list[str] = []
    conn = connect() if db_path is None else sqlite3.connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        done = applied_versions(conn)
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if path.name in done:
                continue
            conn.executescript(path.read_text(encoding="utf-8"))
            conn.execute("INSERT INTO schema_migrations(version) VALUES (?)", (path.name,))
            conn.commit()
            applied_now.append(path.name)
    finally:
        conn.close()
    return applied_now


def rows_to_dicts(rows) -> list[dict]:
    return [dict(r) for r in rows]
