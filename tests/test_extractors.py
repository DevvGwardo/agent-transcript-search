"""Fixture-based tests — no real session stores needed."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from agent_transcript_search.extractors.claude import ClaudeExtractor
from agent_transcript_search.extractors.codex import CodexExtractor
from agent_transcript_search.extractors.cursor import CursorExtractor
from agent_transcript_search.extractors.hermes import HermesExtractor
from agent_transcript_search.index import Index


@pytest.fixture()
def claude_dir(tmp_path: Path) -> Path:
    proj = tmp_path / ".claude" / "projects" / "-Users-test-proj"
    proj.mkdir(parents=True)
    jl = proj / "sess1.jsonl"
    lines = [
        {"type": "user", "sessionId": "sess1", "timestamp": "2026-07-01T10:00:00Z",
         "message": {"role": "user", "content": "fix the auth flow in login.ts"}},
        {"type": "assistant", "sessionId": "sess1", "timestamp": "2026-07-01T10:01:00Z",
         "message": {"role": "assistant", "content": [
             {"type": "text", "text": "Found the bug: the token wasn't being refreshed."},
             {"type": "tool_use", "name": "Bash", "input": {"command": "npm test"}},
         ]}},
        {"type": "assistant", "sessionId": "sess1", "timestamp": "2026-07-01T10:02:00Z",
         "message": {"role": "assistant", "content": "Tests pass now."}},
    ]
    with open(jl, "w") as fh:
        for line in lines:
            fh.write(json.dumps(line) + "\n")
    return tmp_path / ".claude" / "projects"


def test_claude_extractor(claude_dir: Path):
    ex = ClaudeExtractor(root=claude_dir)
    sessions = list(ex.iter_sessions())
    assert len(sessions) == 1
    s = sessions[0]
    assert s.session_id == "sess1"
    assert len(s.messages) == 3
    roles = [m.role for m in s.messages]
    assert roles == ["user", "assistant", "assistant"]
    assert "auth flow" in s.messages[0].content
    assert "token wasn't being refreshed" in s.messages[1].content
    assert "tool_use Bash" in s.messages[1].content
    assert s.title and "auth flow" in s.title


@pytest.fixture()
def hermes_db(tmp_path: Path) -> Path:
    db = tmp_path / "state.db"
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY, title TEXT, cwd TEXT, created_at REAL
        );
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT,
            timestamp REAL NOT NULL
        );
        INSERT INTO sessions VALUES ('s1', 'debug ws reconnect', '/proj', 1780000000.0);
        INSERT INTO messages (session_id, role, content, timestamp)
        VALUES ('s1', 'user', 'websocket keeps dropping after 5 minutes', 1780000000.0),
               ('s1', 'assistant', 'Added a keepalive ping every 30s', 1780000005.0);
    """)
    conn.commit()
    conn.close()
    return db


def test_hermes_extractor(hermes_db: Path):
    ex = HermesExtractor(db_path=hermes_db)
    sessions = list(ex.iter_sessions())
    assert len(sessions) == 1
    s = sessions[0]
    assert s.session_id == "s1"
    assert s.title == "debug ws reconnect"
    assert len(s.messages) == 2
    assert s.messages[0].role == "user"


@pytest.fixture()
def cursor_store(tmp_path: Path) -> Path:
    store = tmp_path / ".cursor" / "chats" / "chat1" / "thread1" / "store.db"
    store.parent.mkdir(parents=True)
    conn = sqlite3.connect(store)
    conn.executescript("""
        CREATE TABLE blobs (id TEXT PRIMARY KEY, data BLOB);
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
        INSERT INTO meta VALUES ('title', 'fix the deploy sha check');
    """)
    conn.execute("INSERT INTO blobs VALUES ('a', ?)",
                 (json.dumps({"role": "user", "content": "deploy went out but sha mismatched"}).encode(),))
    conn.execute("INSERT INTO blobs VALUES ('b', ?)",
                 (json.dumps({"role": "assistant",
                              "content": [{"type": "text", "text": "Check the bundle hash against git HEAD"}]}).encode(),))
    conn.commit()
    conn.close()
    return tmp_path / ".cursor"


def test_cursor_extractor(cursor_store: Path):
    ex = CursorExtractor(root=cursor_store)
    sessions = list(ex.iter_sessions())
    assert len(sessions) == 1
    s = sessions[0]
    assert s.title == "fix the deploy sha check"
    assert len(s.messages) == 2
    assert "sha mismatched" in s.messages[0].content


@pytest.fixture()
def codex_dir(tmp_path: Path) -> Path:
    d = tmp_path / ".codex" / "sessions" / "2026" / "07" / "01"
    d.mkdir(parents=True)
    jl = d / "rollout-test.jsonl"
    lines = [
        {"type": "session_meta", "payload": {
            "session_id": "rollout-test", "cwd": "/proj",
            "timestamp": "2026-07-01T09:00:00Z"}},
        {"type": "event_msg", "payload": {
            "role": "user", "timestamp": "2026-07-01T09:00:01Z",
            "content": [{"type": "input_text", "text": "profile the slow query"}]}},
        {"type": "response_item", "payload": {
            "type": "message", "role": "assistant",
            "timestamp": "2026-07-01T09:00:05Z",
            "content": "The query was missing an index on user_id"}},
        {"type": "response_item", "payload": {
            "type": "function_call", "name": "shell",
            "timestamp": "2026-07-01T09:00:06Z",
            "arguments": {"command": "psql -c EXPLAIN"}}},
    ]
    with open(jl, "w") as fh:
        for line in lines:
            fh.write(json.dumps(line) + "\n")
    return tmp_path / ".codex" / "sessions"


def test_codex_extractor(codex_dir: Path):
    ex = CodexExtractor(root=codex_dir)
    sessions = list(ex.iter_sessions())
    assert len(sessions) == 1
    s = sessions[0]
    assert len(s.messages) == 3
    assert s.messages[0].role == "user"
    assert "missing an index" in s.messages[1].content
    assert "tool_use shell" in s.messages[2].content
    assert s.cwd == "/proj"


def test_index_roundtrip(tmp_path: Path, claude_dir: Path):
    idx = Index(tmp_path / "ats.db")
    ex = ClaudeExtractor(root=claude_dir)
    sessions = list(ex.iter_sessions())
    stats = idx.index_sessions(sessions)
    assert stats["new"] == 1
    assert stats["messages"] == 3

    # unchanged re-index = no new
    stats2 = idx.index_sessions(list(ex.iter_sessions()))
    assert stats2["new"] == 0
    assert stats2["unchanged"] == 1

    # search finds it
    rows = idx.search("auth flow")
    assert len(rows) >= 1
    assert rows[0]["source"] == "claude"

    # source filter
    rows = idx.search("auth flow", source="codex")
    assert len(rows) == 0

    # stats
    st = idx.stats()
    assert st["sessions"] == 1
    assert st["messages"] == 3
    assert st["by_source"] == {"claude": 1}
    idx.close()
