"""ats — agent transcript search CLI.

Search one FTS index across Claude Code, Hermes, Cursor, and Codex
session logs.

Usage:
    ats init                 create the index at ~/.agent-transcript-search/ats.db
    ats index [--source X]   scan all agent stores and (re)index
    ats search "query"       FTS search, most recent first
    ats stats                index counts
    ats clear                wipe the index
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from .extractors import ALL_EXTRACTORS
from .index import Index

DEFAULT_DB = Path.home() / ".agent-transcript-search" / "ats.db"

SOURCE_COLORS = {
    "claude": "\x1b[36m",   # cyan
    "hermes": "\x1b[35m",   # magenta
    "cursor": "\x1b[33m",   # yellow
    "codex": "\x1b[34m",    # blue
    "reset": "\x1b[0m",
}


def _fmt_ts(ts: float | None) -> str:
    if not ts:
        return "?"
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    except (OverflowError, OSError, ValueError):
        return "?"


def _paint(source: str, text: str) -> str:
    if not sys.stdout.isatty():
        return text
    c = SOURCE_COLORS.get(source)
    return f"{c}{text}{SOURCE_COLORS['reset']}" if c else text


def cmd_init(args) -> int:
    DEFAULT_DB.parent.mkdir(parents=True, exist_ok=True)
    idx = Index(DEFAULT_DB)
    idx.close()
    print(f"index ready: {DEFAULT_DB}")
    print("next: ats index")
    return 0


def cmd_index(args) -> int:
    DEFAULT_DB.parent.mkdir(parents=True, exist_ok=True)
    idx = Index(DEFAULT_DB)
    total = {"sessions": 0, "messages": 0}
    for cls in ALL_EXTRACTORS:
        if args.source and cls.source != args.source:
            continue
        try:
            extractor = cls()
        except Exception:
            continue
        stats = {"new": 0, "updated": 0, "unchanged": 0, "messages": 0}
        try:
            for sess in extractor.iter_sessions():
                stats["unchanged_before"] = stats.get("unchanged_before", 0)
                cur = idx.conn.execute(
                    "SELECT content_hash FROM sessions WHERE source=? AND session_id=?",
                    (sess.source, sess.session_id),
                )
                was = cur.fetchone()
                idx.upsert_session(sess)
                if was is None:
                    stats["new"] += 1
                else:
                    stats["updated"] += 1
                stats["messages"] += len(sess.messages)
        except Exception as e:  # never let one source kill the whole run
            print(f"  {cls.source}: ERROR {e}", file=sys.stderr)
            continue
        total["sessions"] += stats["new"] + stats["updated"]
        total["messages"] += stats["messages"]
        print(
            f"  {cls.source}: {stats['new']} new, {stats['updated']} updated, "
            f"{stats['messages']} messages"
        )
    idx.close()
    print(f"indexed {total['sessions']} sessions, {total['messages']} messages")
    return 0


def cmd_search(args) -> int:
    if not DEFAULT_DB.is_file():
        print("no index yet — run: ats init && ats index", file=sys.stderr)
        return 1
    idx = Index(DEFAULT_DB)
    rows = idx.search(args.query, source=args.source, limit=args.limit)
    if not rows:
        print(f"no matches for: {args.query}")
        return 0
    for r in rows:
        src = _paint(r["source"], r["source"])
        title = str(r["title"] or r["session_id"])
        print(f"[{src}] {title[:80]}")
        print(f"    {_fmt_ts(r['ts'])} · {r['role']} · {r['cwd'] or '?'}")
        snippet = (r["snippet"] or r["content"] or "").replace("\n", " ")
        print(f"    {snippet[:240]}")
        if args.verbose:
            print(f"    path: {r['path']}")
        print()
    idx.close()
    return 0


def cmd_stats(args) -> int:
    if not DEFAULT_DB.is_file():
        print("no index yet — run: ats init && ats index", file=sys.stderr)
        return 1
    idx = Index(DEFAULT_DB)
    s = idx.stats()
    print(f"sessions: {s['sessions']}")
    print(f"messages: {s['messages']}")
    for src, n in sorted(s["by_source"].items()):
        print(f"  {src}: {n}")
    idx.close()
    return 0


def cmd_clear(args) -> int:
    if not DEFAULT_DB.is_file():
        print("no index yet")
        return 0
    idx = Index(DEFAULT_DB)
    idx.clear()
    idx.close()
    print("index cleared")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="ats", description="Search agent CLI session transcripts"
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="create the search index")
    pi = sub.add_parser("index", help="scan + index all agent stores")
    pi.add_argument("--source", choices=["claude", "hermes", "cursor", "codex"])
    ps = sub.add_parser("search", help="FTS search")
    ps.add_argument("query")
    ps.add_argument("--source", choices=["claude", "hermes", "cursor", "codex"])
    ps.add_argument("--limit", type=int, default=20)
    ps.add_argument("-v", "--verbose", action="store_true")
    sub.add_parser("stats", help="index counts")
    sub.add_parser("clear", help="wipe the index")

    args = p.parse_args(argv)
    if args.cmd == "init":
        return cmd_init(args)
    if args.cmd == "index":
        return cmd_index(args)
    if args.cmd == "search":
        return cmd_search(args)
    if args.cmd == "stats":
        return cmd_stats(args)
    if args.cmd == "clear":
        return cmd_clear(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
