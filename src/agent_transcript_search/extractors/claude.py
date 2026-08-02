"""Claude Code — ~/.claude/projects/<dir>/<uuid>.jsonl

Each line is one event with {type, timestamp, sessionId, content, ...}.
The meaningful transcript types carry the conversation:
  - "user"          : user prompt (content is {type:"text"/"tool_result", ...} or str)
  - "assistant"     : assistant message (content has model/tokens/message)
  - "summary"       : conversation summary (first line of content.text)
Files are per-project folders; each JSONL is one session.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator, Optional

from .base import Extractor, ExtractedMessage, ExtractedSession, walk_jsonl


def _role_from_event(ev: dict) -> Optional[str]:
    t = ev.get("type")
    if t in ("user", "assistant", "summary"):
        return t
    return None


def _text_from_content(content) -> str:
    """Claude Code content field varies: str | dict | list[dict]."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        # assistant event: content has {"type":"text","text":...} or message wrapper
        if "text" in content:
            return str(content["text"])
        msg = content.get("message")
        if isinstance(msg, dict):
            parts = []
            for c in msg.get("content", []):
                if isinstance(c, dict):
                    if c.get("type") == "text":
                        parts.append(c.get("text", ""))
                    elif c.get("type") == "tool_use":
                        parts.append(
                            f"[tool_use {c.get('name','')}] {json_dumps(c.get('input',''))}"
                        )
                    elif c.get("type") == "tool_result":
                        parts.append(f"[tool_result] {c.get('content','')}")
            return "\n".join(p for p in parts if p)
        return str(content)
    if isinstance(content, list):
        parts = []
        for c in content:
            if isinstance(c, dict):
                if c.get("type") == "text":
                    parts.append(str(c.get("text", "")))
                elif c.get("type") == "tool_result":
                    parts.append(str(c.get("content", "")))
                elif c.get("type") == "tool_use":
                    parts.append(f"[tool_use {c.get('name','')}]")
            elif isinstance(c, str):
                parts.append(c)
        return "\n".join(p for p in parts if p)
    return str(content)


def _text_from_message(msg) -> str:
    """Some claude events put text in message.content instead of content."""
    if not isinstance(msg, dict):
        return ""
    c = msg.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        parts = []
        for item in c:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(str(item.get("text", "")))
                elif item.get("type") == "tool_use":
                    parts.append(f"[tool_use {item.get('name','')}]")
                elif item.get("type") == "tool_result":
                    parts.append(f"[tool_result] {item.get('content','')}")
                elif item.get("type") == "thinking":
                    parts.append(f"[thinking] {item.get('thinking','')}")
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(p for p in parts if p)
    return str(c) if c else ""


def json_dumps(o) -> str:
    import json

    try:
        return json.dumps(o, ensure_ascii=False)[:500]
    except Exception:
        return str(o)[:500]


class ClaudeExtractor(Extractor):
    source = "claude"

    def __init__(self, root: Optional[Path] = None):
        self.root = root or Path.home() / ".claude" / "projects"

    def iter_sessions(self) -> Iterator[ExtractedSession]:
        if not self.root.is_dir():
            return
        for proj_dir in sorted(self.root.iterdir()):
            if not proj_dir.is_dir():
                continue
            for jl in sorted(proj_dir.glob("*.jsonl")):
                sess = self._read_file(jl)
                if sess and sess.messages:
                    yield sess

    def _read_file(self, jl: Path) -> Optional[ExtractedSession]:
        sess: Optional[ExtractedSession] = None
        sid: Optional[str] = None
        cwd = None
        title = None
        for ev in walk_jsonl(jl):
            role = _role_from_event(ev)
            if role is None:
                continue
            sid = sid or ev.get("sessionId") or jl.stem
            content = _text_from_content(ev.get("content"))
            if not content and ev.get("message"):
                content = _text_from_message(ev.get("message"))
            if not content:
                continue
            ts = self._ts(ev.get("timestamp"))
            if sess is None:
                sess = ExtractedSession(
                    source=self.source,
                    session_id=sid,
                    cwd=cwd,
                    started_at=ts,
                    updated_at=ts,
                    path=str(jl),
                )
            if ts is not None:
                if sess.started_at is None or ts < sess.started_at:
                    sess.started_at = ts
                if sess.updated_at is None or ts > sess.updated_at:
                    sess.updated_at = ts
            # first user text is a decent title
            if title is None and role == "user":
                title = content.splitlines()[0][:120]
                sess.title = title
            sess.messages.append(ExtractedMessage(role=role, content=content, ts=ts))
        return sess
