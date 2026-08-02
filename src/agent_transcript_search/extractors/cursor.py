"""Cursor — ~/.cursor/chats/<chat>/<thread>/store.db (SQLite)

`blobs` table: id (sha256) → data (JSON bytes).
Each blob is a single message: {"role": ..., "content": str|list, ...}.
`meta` table: key/value (session title etc, best-effort).

Also scans ~/.cursor/acp-sessions/<uuid>/store.db (ACP sessions) when present.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Iterator, Optional

from .base import Extractor, ExtractedMessage, ExtractedSession, open_ro


class CursorExtractor(Extractor):
    source = "cursor"

    def __init__(self, root: Optional[Path] = None):
        self.root = root or Path.home() / ".cursor"

    def iter_sessions(self) -> Iterator[ExtractedSession]:
        # classic chats: ~/.cursor/chats/<chatid>/<threadid>/store.db
        chats = self.root / "chats"
        if chats.is_dir():
            for store in sorted(chats.glob("*/*/store.db")):
                sess = self._read_store(store)
                if sess and sess.messages:
                    yield sess
        # ACP sessions: ~/.cursor/acp-sessions/<uuid>/store.db
        acp = self.root / "acp-sessions"
        if acp.is_dir():
            for store in sorted(acp.glob("*/store.db")):
                sess = self._read_store(store, is_acp=True)
                if sess and sess.messages:
                    yield sess

    def _read_store(self, store: Path, is_acp: bool = False) -> Optional[ExtractedSession]:
        try:
            conn = open_ro(store)
        except Exception:
            return None
        try:
            rows = conn.execute("SELECT id, data FROM blobs ORDER BY rowid ASC").fetchall()
            meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
        except Exception:
            return None

        sess = ExtractedSession(source=self.source, session_id=store.parent.name, path=str(store))
        title = None
        cwd = None
        for _bid, data in rows:
            if not data:
                continue
            try:
                msg = json.loads(data.decode("utf-8", "replace"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if not isinstance(msg, dict):
                continue
            role = msg.get("role") or msg.get("type")
            content = msg.get("content")
            ts = self._ts(msg.get("timestamp") or msg.get("ts"))
            text = self._content_to_text(content)
            if not text:
                continue
            if role in ("user", "assistant", "system", "tool"):
                sess.messages.append(
                    ExtractedMessage(role=role, content=text, ts=ts)
                )
            # capture cwd from meta if present
            if cwd is None:
                cwd = meta.get("cwd") or meta.get("workingDirectory") or msg.get("cwd")

        if not sess.messages:
            return None

        sess.cwd = cwd or sess.cwd
        # title from meta or first user message
        title = meta.get("title") or meta.get("summary")
        if not title:
            for m in sess.messages:
                if m.role == "user":
                    title = m.content.splitlines()[0][:120]
                    break
        sess.title = title
        sess.started_at = sess.messages[0].ts
        sess.updated_at = sess.messages[-1].ts or sess.started_at
        return sess

    def _content_to_text(self, content) -> str:
        if content is None:
            return ""
        if isinstance(content, str):
            return self._clean(content)
        if isinstance(content, list):
            parts = []
            for c in content:
                if isinstance(c, dict):
                    if c.get("type") == "text":
                        parts.append(str(c.get("text", "")))
                    elif c.get("type") in ("tool_use", "tool_call"):
                        parts.append(f"[tool_use {c.get('name','')}]")
                    elif c.get("type") in ("tool_result",):
                        parts.append(str(c.get("content", ""))[:1000])
                    else:
                        parts.append(str(c)[:500])
                elif isinstance(c, str):
                    parts.append(c)
            return self._clean("\n".join(parts))
        return self._clean(str(content))
