"""Codex — ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl

Typed event stream:
  - session_meta   : session_id, cwd, cli_version, model_provider, source
  - event_msg      : user/assistant/tool messages
  - response_item  : assistant text / tool calls / reasoning (most common)
  - turn_context   : injected context
  - compacted      : summary events

We flatten user + assistant text (and tool names) into messages.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator, Optional

from .base import Extractor, ExtractedMessage, ExtractedSession, walk_jsonl


class CodexExtractor(Extractor):
    source = "codex"

    def __init__(self, root: Optional[Path] = None):
        self.root = root or Path.home() / ".codex" / "sessions"

    def iter_sessions(self) -> Iterator[ExtractedSession]:
        if not self.root.is_dir():
            return
        # sessions/YYYY/MM/DD/rollout-*.jsonl
        for jl in sorted(self.root.rglob("rollout-*.jsonl")):
            sess = self._read_rollout(jl)
            if sess and sess.messages:
                yield sess

    def _read_rollout(self, jl: Path) -> Optional[ExtractedSession]:
        sess: Optional[ExtractedSession] = None
        for ev in walk_jsonl(jl):
            etype = ev.get("type")
            payload = ev.get("payload") or {}
            if etype == "session_meta":
                sess = ExtractedSession(
                    source=self.source,
                    session_id=str(payload.get("session_id") or jl.stem),
                    cwd=payload.get("cwd"),
                    started_at=self._ts(payload.get("timestamp") or ev.get("timestamp")),
                    path=str(jl),
                )
                continue
            if etype == "event_msg":
                if sess is None:
                    sess = self._new_sess(jl)
                self._event_msg(sess, payload)
            elif etype == "response_item":
                if sess is None:
                    sess = self._new_sess(jl)
                self._response_item(sess, payload)
            elif etype == "compacted":
                if sess is None:
                    sess = self._new_sess(jl)
                self._compacted(sess, payload)

        if sess is None:
            sess = self._new_sess(jl)
        if not sess.messages:
            return None
        if sess.title is None:
            for m in sess.messages:
                if m.role == "user":
                    sess.title = m.content.splitlines()[0][:120]
                    break
        if sess.updated_at is None:
            sess.updated_at = sess.messages[-1].ts or sess.started_at
        return sess

    def _new_sess(self, jl: Path) -> ExtractedSession:
        return ExtractedSession(
            source=self.source, session_id=jl.stem, path=str(jl)
        )

    def _event_msg(self, sess: ExtractedSession, payload: dict):
        role = payload.get("role")
        ts = self._ts(payload.get("timestamp"))
        content = payload.get("content")
        if isinstance(content, str):
            text = self._clean(content)
        elif isinstance(content, list):
            text = self._clean(self._join_content(content))
        else:
            text = ""
        if not text:
            return
        if role not in ("user", "assistant", "system"):
            role = "unknown"
        sess.messages.append(ExtractedMessage(role=role, content=text, ts=ts))

    def _response_item(self, sess: ExtractedSession, payload: dict):
        item_type = payload.get("type")
        ts = self._ts(payload.get("timestamp"))
        if item_type == "message":
            role = payload.get("role")
            text = self._clean(payload.get("content") or "")
            if text:
                sess.messages.append(
                    ExtractedMessage(role=role or "assistant", content=text, ts=ts)
                )
        elif item_type == "function_call":
            name = payload.get("name") or payload.get("tool_name") or ""
            args = payload.get("arguments") or payload.get("input") or ""
            if isinstance(args, dict):
                args = json.dumps(args, ensure_ascii=False)[:500]
            if name:
                sess.messages.append(
                    ExtractedMessage(
                        role="assistant", content=f"[tool_use {name}] {args}", ts=ts
                    )
                )
        elif item_type == "function_call_output":
            out = payload.get("output") or payload.get("content") or ""
            if isinstance(out, dict):
                out = json.dumps(out, ensure_ascii=False)[:2000]
            text = self._clean(str(out))
            if text:
                sess.messages.append(
                    ExtractedMessage(role="tool", content=text, ts=ts)
                )
        elif item_type == "reasoning":
            text = self._clean(str(payload.get("summary") or payload.get("content") or ""))
            if text:
                sess.messages.append(
                    ExtractedMessage(role="reasoning", content=text, ts=ts)
                )

    def _compacted(self, sess: ExtractedSession, payload: dict):
        text = self._clean(payload.get("summary") or payload.get("text") or "")
        if text:
            sess.messages.append(ExtractedMessage(role="summary", content=text))

    def _join_content(self, items: list) -> str:
        parts = []
        for c in items:
            if isinstance(c, str):
                parts.append(c)
            elif isinstance(c, dict):
                if c.get("type") == "text":
                    parts.append(str(c.get("text", "")))
                elif c.get("type") == "input_text":
                    parts.append(str(c.get("text", "")))
                elif c.get("type") == "tool_use":
                    parts.append(f"[tool_use {c.get('name','')}]")
            elif isinstance(c, list):
                parts.append(self._join_content(c))
        return "\n".join(p for p in parts if p)
