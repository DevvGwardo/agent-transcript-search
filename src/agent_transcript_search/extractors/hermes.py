"""Hermes Agent — ~/.hermes/state.db (SQLite)

`messages` table (FTS5 external-content index already exists):
    id, session_id, role, content, tool_name, tool_calls, timestamp(REAL), ...
`sessions` table: id, title, cwd/working_dir, started/updated timestamps.

We read read-only, never take a lock on the live DB.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator, Optional

from .base import Extractor, ExtractedMessage, ExtractedSession, open_ro


class HermesExtractor(Extractor):
    source = "hermes"

    def __init__(self, db_path: Optional[Path | str] = None):
        self.db_path = Path(db_path) if db_path else Path.home() / ".hermes" / "state.db"

    def iter_sessions(self) -> Iterator[ExtractedSession]:
        if not self.db_path.is_file():
            return
        try:
            conn = open_ro(self.db_path)
        except Exception:
            return

        # figure out the sessions table columns defensively
        cols = {
            r[1]
            for r in conn.execute("PRAGMA table_info(sessions)").fetchall()
        }

        def pick(cols_set, *candidates):
            for c in candidates:
                if c in cols_set:
                    return c
            return None

        id_col = pick(cols, "id", "session_id")
        title_col = pick(cols, "title", "name", "summary")
        cwd_col = pick(cols, "cwd", "working_dir", "workdir", "path")
        ts_col = pick(cols, "created_at", "started_at", "timestamp")

        try:
            if id_col is None:
                return
            rows = conn.execute(
                f'SELECT {id_col}'
                + (f", {title_col}" if title_col else "")
                + (f", {cwd_col}" if cwd_col else "")
                + (f", {ts_col}" if ts_col else "")
                + " FROM sessions ORDER BY " + (ts_col or "rowid") + " DESC LIMIT 20000"
            ).fetchall()
        except Exception:
            return

        # messages table may or may not have tool_name/tool_calls columns
        msg_cols = {
            r[1]
            for r in conn.execute("PRAGMA table_info(messages)").fetchall()
        }
        has_tool_name = "tool_name" in msg_cols
        # keep timestamp LAST so tuple unpacking is stable either way
        msg_select = (
            "SELECT role, content, timestamp FROM messages"
            if not has_tool_name
            else "SELECT role, content, tool_name, timestamp FROM messages"
        )

        for row in rows:
            sid = row[0]
            title = row[1] if title_col else None
            cwd = row[2] if cwd_col else None
            started = self._ts(row[3]) if ts_col else None
            try:
                mrows = conn.execute(
                    msg_select + " WHERE session_id = ? ORDER BY timestamp ASC",
                    (str(sid),),
                ).fetchall()
            except Exception:
                continue
            msgs = []
            for mrow in mrows:
                role, content = mrow[0], mrow[1]
                ts = mrow[2] if not has_tool_name else mrow[3]
                text = self._clean(content)
                if has_tool_name and mrow[2]:
                    text = f"[tool:{mrow[2]}] {text}" if text else f"[tool:{mrow[2]}]"
                if not text:
                    continue
                msgs.append(ExtractedMessage(role=role or "unknown", content=text, ts=self._ts(ts)))
            if not msgs:
                continue
            updated = msgs[-1].ts or started
            yield ExtractedSession(
                source=self.source,
                session_id=str(sid),
                title=title,
                cwd=cwd,
                started_at=started,
                updated_at=updated,
                path=str(self.db_path),
                messages=msgs,
            )
