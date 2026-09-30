"""Local SQLite database. No credentials are stored here (see SecretStore)."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, name TEXT, created_at REAL);
CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, user_id INTEGER, created_at REAL, last_seen REAL);
CREATE TABLE IF NOT EXISTS conversations(id TEXT PRIMARY KEY, session_id TEXT, title TEXT, created_at REAL);
CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id TEXT, role TEXT,
  content TEXT, lang TEXT, created_at REAL);
CREATE TABLE IF NOT EXISTS memories(id INTEGER PRIMARY KEY AUTOINCREMENT, category TEXT NOT NULL, key TEXT,
  content TEXT NOT NULL, tags TEXT DEFAULT '', embedding TEXT, created_at REAL, updated_at REAL);
CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY, command TEXT, status TEXT, outcome TEXT, progress REAL,
  current_action TEXT, result TEXT, error TEXT, created_at REAL, started_at REAL, completed_at REAL,
  conversation_id TEXT);
CREATE TABLE IF NOT EXISTS task_steps(id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, idx INTEGER,
  title TEXT, status TEXT, detail TEXT, created_at REAL);
CREATE TABLE IF NOT EXISTS tool_calls(id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, task_id TEXT, tool TEXT,
  arguments_redacted TEXT, status TEXT, duration_ms INTEGER, result TEXT, error TEXT, verified INTEGER);
CREATE TABLE IF NOT EXISTS connected_accounts(id INTEGER PRIMARY KEY AUTOINCREMENT, provider TEXT, label TEXT,
  scopes TEXT, created_at REAL);
CREATE TABLE IF NOT EXISTS preferences(key TEXT PRIMARY KEY, value TEXT, updated_at REAL);
CREATE TABLE IF NOT EXISTS devices(mac TEXT PRIMARY KEY, ip TEXT, hostname TEXT, vendor TEXT, device_type TEXT,
  connection TEXT, status TEXT, methods TEXT, first_seen REAL, last_seen REAL, extra TEXT);
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, type TEXT, data TEXT);
CREATE TABLE IF NOT EXISTS drafts(id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, provider TEXT, payload TEXT,
  status TEXT, created_at REAL, sent_at REAL);
CREATE INDEX IF NOT EXISTS idx_tool_calls_task ON tool_calls(task_id);
CREATE INDEX IF NOT EXISTS idx_steps_task ON task_steps(task_id);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id);
"""


class Database:
    def __init__(self, path: Path | str):
        self.path = str(path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def execute(self, sql: str, params: Iterable[Any] = ()) -> int:
        with self._lock:
            cur = self._conn.execute(sql, tuple(params))
            self._conn.commit()
            return cur.lastrowid or cur.rowcount

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, tuple(params)).fetchall()]

    def one(self, sql: str, params: Iterable[Any] = ()) -> dict | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # convenience -----------------------------------------------------------------------------
    def pref_get(self, key: str, default: Any = None) -> Any:
        r = self.one("SELECT value FROM preferences WHERE key=?", (key,))
        return json.loads(r["value"]) if r else default

    def pref_set(self, key: str, value: Any) -> None:
        self.execute("INSERT INTO preferences(key,value,updated_at) VALUES(?,?,?) "
                     "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                     (key, json.dumps(value), time.time()))
