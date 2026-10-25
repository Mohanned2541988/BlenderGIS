"""Per-project SQLite storage with versioned schema migrations.

Each project keeps everything in database/project.db:
  meta(key, value)                      - project settings (JSON values)
  records(kind, id, position, data, ..) - scenes, sources, claims, assets, cues, renders
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from . import PROJECT_SCHEMA_VERSION

KINDS = {"scene", "source", "claim", "asset", "cue", "render"}

_MIGRATIONS = {
    1: [
        """CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS records (
               kind TEXT NOT NULL,
               id TEXT NOT NULL,
               position REAL NOT NULL DEFAULT 0,
               data TEXT NOT NULL,
               created REAL NOT NULL,
               updated REAL NOT NULL,
               PRIMARY KEY (kind, id))""",
        """CREATE INDEX IF NOT EXISTS idx_records_kind_pos ON records(kind, position)""",
    ],
}


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


class ProjectDB:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        # WAL + synchronous=NORMAL survives app crashes without losing committed edits.
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self.migrate()

    def close(self):
        with self._lock:
            self._conn.close()

    # -- schema -------------------------------------------------------------
    def schema_version(self) -> int:
        return int(self._conn.execute("PRAGMA user_version").fetchone()[0])

    def migrate(self):
        with self._lock:
            current = self.schema_version()
            if current > PROJECT_SCHEMA_VERSION:
                raise RuntimeError(
                    f"Project was created by a newer app version (schema {current}); please update the app.")
            for version in range(current + 1, PROJECT_SCHEMA_VERSION + 1):
                self._conn.execute("BEGIN")
                try:
                    for stmt in _MIGRATIONS[version]:
                        self._conn.execute(stmt)
                    self._conn.execute(f"PRAGMA user_version={version}")
                    self._conn.execute("COMMIT")
                except Exception:
                    self._conn.execute("ROLLBACK")
                    raise

    # -- meta ---------------------------------------------------------------
    def get_meta(self, key: str, default=None):
        row = self._conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def all_meta(self) -> dict:
        return {r["key"]: json.loads(r["value"]) for r in self._conn.execute("SELECT key, value FROM meta")}

    def set_meta(self, key: str, value):
        with self._lock:
            self._conn.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value, ensure_ascii=False)))

    # -- records ------------------------------------------------------------
    def _check(self, kind):
        if kind not in KINDS:
            raise ValueError(f"unknown record kind {kind}")

    def list(self, kind: str) -> list[dict]:
        self._check(kind)
        rows = self._conn.execute(
            "SELECT id, position, data FROM records WHERE kind=? ORDER BY position, created", (kind,))
        out = []
        for r in rows:
            d = json.loads(r["data"])
            d["id"] = r["id"]
            out.append(d)
        return out

    def get(self, kind: str, rid: str):
        self._check(kind)
        r = self._conn.execute("SELECT data FROM records WHERE kind=? AND id=?", (kind, rid)).fetchone()
        if not r:
            return None
        d = json.loads(r["data"])
        d["id"] = rid
        return d

    def put(self, kind: str, data: dict, position: float | None = None) -> dict:
        self._check(kind)
        with self._lock:
            rid = data.get("id") or new_id(kind[:3])
            data = dict(data, id=rid)
            now = time.time()
            existing = self._conn.execute(
                "SELECT position FROM records WHERE kind=? AND id=?", (kind, rid)).fetchone()
            if position is None:
                if existing:
                    position = existing[0]
                else:
                    mx = self._conn.execute(
                        "SELECT COALESCE(MAX(position), 0) FROM records WHERE kind=?", (kind,)).fetchone()[0]
                    position = mx + 1
            payload = json.dumps({k: v for k, v in data.items() if k != "id"}, ensure_ascii=False)
            self._conn.execute(
                """INSERT INTO records(kind, id, position, data, created, updated) VALUES(?,?,?,?,?,?)
                   ON CONFLICT(kind, id) DO UPDATE SET position=excluded.position, data=excluded.data,
                   updated=excluded.updated""",
                (kind, rid, position, payload, now, now))
            return data

    def delete(self, kind: str, rid: str) -> bool:
        self._check(kind)
        with self._lock:
            cur = self._conn.execute("DELETE FROM records WHERE kind=? AND id=?", (kind, rid))
            return cur.rowcount > 0

    def delete_kind(self, kind: str, where=None):
        """Delete records of a kind; where is an optional predicate over the record dict."""
        with self._lock:
            for rec in self.list(kind):
                if where is None or where(rec):
                    self.delete(kind, rec["id"])

    def reorder(self, kind: str, ids: list[str]):
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                for i, rid in enumerate(ids):
                    self._conn.execute("UPDATE records SET position=? WHERE kind=? AND id=?", (i + 1, kind, rid))
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def insert_after(self, kind: str, after_id: str | None, data: dict) -> dict:
        """Insert a record directly after another one, renumbering positions."""
        with self._lock:
            ids = [r["id"] for r in self.list(kind)]
            rec = self.put(kind, data)
            ids = [i for i in ids if i != rec["id"]]
            idx = ids.index(after_id) + 1 if after_id in ids else len(ids)
            ids.insert(idx, rec["id"])
            self.reorder(kind, ids)
            return rec
