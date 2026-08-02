"""SQLite FTS5 index — the searchable store.

Schema:
    sessions(id INTEGER PK, source, session_id, title, cwd, started_at,
             updated_at, path, last_indexed)
    messages(id INTEGER PK, session_id → sessions, source, role, content, ts)
    messages_fts — FTS5 external-content over messages(content, role, source)

Incremental by design: an extractor yields full sessions, we upsert by
(source, session_id) and only replace rows whose content hash changed.
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from typing import Iterable

from .extractors.base import ExtractedSession

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    session_id TEXT NOT NULL,
    title TEXT,
    cwd TEXT,
    started_at REAL,
    updated_at REAL,
    path TEXT,
    content_hash TEXT,
    UNIQUE(source, session_id)
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    source TEXT NOT NULL,
    role TEXT,
    content TEXT,
    ts REAL
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
CREATE INDEX IF NOT EXISTS idx_messages_source ON messages(source);
CREATE INDEX IF NOT EXISTS idx_sessions_updated ON sessions(updated_at DESC);
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    content, role, source,
    content='messages', content_rowid='id'
);
"""

# Keep FTS in sync on write. Insert/delete triggers only (updates go
# through delete+insert in the upsert path).
TRIGGERS = """
CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, content, role, source)
    VALUES (new.id, new.content, new.role, new.source);
END;
CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content, role, source)
    VALUES ('delete', old.id, old.content, old.role, old.source);
END;
"""


class Index:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.conn = sqlite3.connect(str(db_path))
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.executescript(SCHEMA)
        self.conn.executescript(TRIGGERS)

    def close(self):
        self.conn.commit()
        self.conn.close()

    # ---- write ----

    def upsert_session(self, s: ExtractedSession) -> int:
        """Replace one session's messages if changed. Returns session row id."""
        cur = self.conn.execute(
            "SELECT id, content_hash FROM sessions WHERE source=? AND session_id=?",
            (s.source, s.session_id),
        )
        row = cur.fetchone()

        # content hash covers title + all message text
        digest = hashlib.sha256()
        digest.update((s.title or "").encode("utf-8", "replace"))
        digest.update((s.cwd or "").encode("utf-8", "replace"))
        for m in s.messages:
            digest.update(f"\x00{m.role}\x01{m.content}".encode("utf-8", "replace"))
        h = digest.hexdigest()

        if row and row[1] == h:
            return int(row[0])

        if row:
            sid = int(row[0])
            self.conn.execute("DELETE FROM messages WHERE session_id=?", (sid,))
            self.conn.execute(
                "UPDATE sessions SET title=?, cwd=?, started_at=?, updated_at=?, "
                "path=?, content_hash=? WHERE id=?",
                (s.title, s.cwd, s.started_at, s.updated_at, s.path, h, sid),
            )
        else:
            cur = self.conn.execute(
                "INSERT INTO sessions (source, session_id, title, cwd, started_at, "
                "updated_at, path, content_hash) VALUES (?,?,?,?,?,?,?,?)",
                (s.source, s.session_id, s.title, s.cwd, s.started_at, s.updated_at,
                 s.path, h),
            )
            sid = int(cur.lastrowid or 0)

        self.conn.executemany(
            "INSERT INTO messages (session_id, source, role, content, ts) "
            "VALUES (?,?,?,?,?)",
            [(sid, s.source, m.role, m.content, m.ts) for m in s.messages],
        )
        self.conn.commit()
        return sid

    def index_sessions(self, sessions: Iterable[ExtractedSession]) -> dict:
        stats = {"new": 0, "updated": 0, "unchanged": 0, "messages": 0}
        for s in sessions:
            cur = self.conn.execute(
                "SELECT content_hash FROM sessions WHERE source=? AND session_id=?",
                (s.source, s.session_id),
            )
            row = cur.fetchone()
            sid = self.upsert_session(s)
            if row is None:
                stats["new"] += 1
            elif row[0] != (self.conn.execute(
                "SELECT content_hash FROM sessions WHERE id=?", (sid,)
            ).fetchone()[0]):
                stats["updated"] += 1
            else:
                stats["unchanged"] += 1
            stats["messages"] += len(s.messages)
        return stats

    # ---- read ----

    def search(self, query: str, source: str | None = None,
               limit: int = 20) -> list[dict]:
        """FTS5 MATCH; returns rows with session context + snippet."""
        fts_query = self._to_fts_query(query)
        sql = (
            "SELECT m.id, m.session_id, m.source, m.role, m.content, m.ts, "
            "       s.title, s.cwd, s.path, "
            "       snippet(messages_fts, 0, '[', ']', '…', 12) AS snip "
            "FROM messages_fts "
            "JOIN messages m ON m.id = messages_fts.rowid "
            "JOIN sessions s ON s.id = m.session_id "
            "WHERE messages_fts MATCH ?"
        )
        params: list = [fts_query]
        if source:
            sql += " AND m.source = ?"
            params.append(source)
        sql += " ORDER BY m.ts DESC LIMIT ?"
        params.append(limit)
        try:
            rows = self.conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError:
            return []
        out = []
        for r in rows:
            out.append({
                "message_id": r[0], "session_id": r[1], "source": r[2],
                "role": r[3], "content": r[4], "ts": r[5], "title": r[6],
                "cwd": r[7], "path": r[8], "snippet": r[9],
            })
        return out

    def stats(self) -> dict:
        total = self.conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        sessions = self.conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        by_source = dict(
            self.conn.execute(
                "SELECT source, COUNT(*) FROM sessions GROUP BY source"
            ).fetchall()
        )
        return {"sessions": sessions, "messages": total, "by_source": by_source}

    def clear(self):
        self.conn.execute("DELETE FROM messages")
        self.conn.execute("DELETE FROM sessions")
        self.conn.commit()

    @staticmethod
    def _to_fts_query(q: str) -> str:
        """Quote free text into a safe FTS5 MATCH expression."""
        q = q.strip()
        if not q:
            return '""'
        # If it already looks like an FTS expression with operators, pass through
        if any(op in q for op in (' AND ', ' OR ', ' NOT ')) or q.startswith('"'):
            return q
        # Multi-word: AND-join quoted terms (phrase-ish, safe)
        terms = q.split()
        return " AND ".join(f'"{t}"' for t in terms)
