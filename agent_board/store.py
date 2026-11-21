import json
import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Mark:
    kind: str              # "done" | "snooze"
    until: float | None    # для snooze — epoch, до якого ховати
    at_activity: float     # last_activity розмови в момент позначки


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        os.chmod(path, 0o600)
        self._lock = threading.Lock()
        with self._lock, self._db:
            self._db.executescript(
                """
                CREATE TABLE IF NOT EXISTS summaries(
                    session_id TEXT PRIMARY KEY, key INTEGER NOT NULL,
                    json TEXT NOT NULL, created_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS marks(
                    session_id TEXT PRIMARY KEY, kind TEXT NOT NULL,
                    until REAL, at_activity REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS calls(ts REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS projects(path TEXT PRIMARY KEY, name TEXT NOT NULL);
                """
            )

    def get_summary(self, session_id: str) -> tuple[int, dict] | None:
        with self._lock:
            row = self._db.execute(
                "SELECT key, json FROM summaries WHERE session_id = ?", (session_id,)
            ).fetchone()
        return (row[0], json.loads(row[1])) if row else None

    def put_summary(self, session_id: str, key: int, summary: dict) -> bool:
        """Пише підсумок, лише якщо він не старший за збережений. True — якщо записано."""
        with self._lock, self._db:
            cur = self._db.execute(
                "INSERT INTO summaries VALUES (?, ?, ?, ?) "
                "ON CONFLICT(session_id) DO UPDATE SET key = excluded.key, json = excluded.json, "
                "created_at = excluded.created_at WHERE excluded.key >= summaries.key",
                (session_id, key, json.dumps(summary, ensure_ascii=False), time.time()),
            )
            return cur.rowcount > 0

    def marks(self) -> dict[str, Mark]:
        with self._lock:
            rows = self._db.execute("SELECT session_id, kind, until, at_activity FROM marks").fetchall()
        return {sid: Mark(kind, until, at) for sid, kind, until, at in rows}

    def set_mark(self, session_id: str, mark: Mark) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO marks VALUES (?, ?, ?, ?)",
                (session_id, mark.kind, mark.until, mark.at_activity),
            )

    def clear_mark(self, session_id: str) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM marks WHERE session_id = ?", (session_id,))

    def record_call(self, ts: float) -> None:
        """Час виклику Haiku: годинний ліміт переживає перезапуск сервісу."""
        with self._lock, self._db:
            self._db.execute("INSERT INTO calls VALUES (?)", (ts,))
            self._db.execute("DELETE FROM calls WHERE ts < ?", (ts - 86400,))

    def calls_since(self, ts: float) -> list[float]:
        with self._lock:
            rows = self._db.execute("SELECT ts FROM calls WHERE ts >= ? ORDER BY ts", (ts,)).fetchall()
        return [row[0] for row in rows]

    def projects(self) -> list[tuple[str, str]]:
        """Ваші проєкти з панелі «Проєкти»: (тека, назва)."""
        with self._lock:
            return list(self._db.execute("SELECT path, name FROM projects ORDER BY name"))

    def save_project(self, path: str, name: str, old_path: str | None = None) -> None:
        with self._lock, self._db:
            if old_path and old_path != path:
                self._db.execute("DELETE FROM projects WHERE path = ?", (old_path,))
            self._db.execute("INSERT OR REPLACE INTO projects VALUES (?, ?)", (path, name))

    def delete_project(self, path: str) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM projects WHERE path = ?", (path,))

    def close(self) -> None:
        with self._lock:
            self._db.close()
