"""Extractor interface — each agent CLI implements one.

An extractor walks a source's on-disk store and yields normalized
sessions. A session is:

    {
        "source": "claude" | "hermes" | "cursor" | "codex",
        "session_id": str,
        "title": str | None,
        "cwd": str | None,
        "started_at": float | None,   # epoch seconds
        "updated_at": float | None,   # epoch seconds
        "path": str,                  # origin file/db for provenance
        "messages": [
            {"role": "user"|"assistant"|"system"|"tool", "content": str, "ts": float|None},
        ],
    }
"""

from __future__ import annotations

import json
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Optional


@dataclass
class ExtractedMessage:
    role: str
    content: str
    ts: Optional[float] = None


@dataclass
class ExtractedSession:
    source: str
    session_id: str
    title: Optional[str] = None
    cwd: Optional[str] = None
    started_at: Optional[float] = None
    updated_at: Optional[float] = None
    path: str = ""
    messages: list[ExtractedMessage] = field(default_factory=list)


class Extractor:
    """Base class. Subclasses implement iter_sessions()."""

    source: str = "?"

    def iter_sessions(self) -> Iterator[ExtractedSession]:
        raise NotImplementedError

    # --- helpers shared by subclasses ---

    def _ts(self, value) -> Optional[float]:
        """Accept epoch-float, epoch-string, or ISO-8601; return epoch-float."""
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            v = value.strip()
            if not v:
                return None
            try:
                return float(v)
            except ValueError:
                pass
            # ISO-8601 with Z / +00:00 → datetime.fromisoformat handles most
            try:
                from datetime import datetime

                dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
                return dt.timestamp()
            except ValueError:
                return None
        return None

    def _clean(self, text: Optional[str], limit: int = 200_000) -> str:
        """Normalize content for indexing; cap runaway blobs."""
        if not text:
            return ""
        if not isinstance(text, str):
            text = str(text)
        text = text.strip()
        if len(text) > limit:
            text = text[:limit]
        return text


def walk_jsonl(path: Path) -> Iterator[dict]:
    """Yield parsed JSON objects from a .jsonl file, skipping bad lines."""
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def open_ro(db_path: Path) -> sqlite3.Connection:
    """Open a SQLite db read-only — never take a write lock on a live app store."""
    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.execute("PRAGMA query_only = ON")
    return conn
