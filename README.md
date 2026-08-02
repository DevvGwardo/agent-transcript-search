# agent-transcript-search

One FTS index across **Claude Code**, **Hermes**, **Cursor**, and **Codex** session logs.

> "where did we do that thing three sessions ago?" — now answerable in one command.

## Why

Every AI coding agent keeps its own private session log in its own format:

| agent | on-disk store | format |
|---|---|---|
| Claude Code | `~/.claude/projects/*/*.jsonl` | JSONL events |
| Hermes | `~/.hermes/state.db` | SQLite `messages` table |
| Cursor | `~/.cursor/chats/*/*/store.db` | SQLite `blobs` (JSON) |
| Codex | `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` | JSONL typed events |

Searching one tool finds nothing in the others. `ats` normalizes all four
into a single SQLite FTS5 index and gives you one `search` command.

## Install

```bash
git clone https://github.com/DevGwardo/agent-transcript-search.git
cd agent-transcript-search
pip install -e .
```

Requires Python 3.10+. Zero runtime dependencies (stdlib `sqlite3` FTS5).

## Usage

```bash
ats init                 # create the index (~/.agent-transcript-search/ats.db)
ats index                # scan all agent stores + index (~250k msgs in ~40s)
ats search "prisma migrate"          # all sources, newest first
ats search "cloudflared tunnel" --source hermes
ats search "deploy sha" --limit 10 -v
ats stats                # counts per source
ats clear                # wipe and reindex from scratch
```

## How it works

- `src/agent_transcript_search/extractors/` — one extractor per CLI. Each
  reads its store **read-only** (SQLite opened `mode=ro`), normalizes to a
  common session/message shape, and yields it.
- `src/agent_transcript_search/index.py` — SQLite FTS5 external-content
  index. Incremental: sessions are upserted by `(source, session_id)` and
  skipped when a content hash is unchanged, so `ats index` after a day of
  work only touches new/changed sessions.
- `src/agent_transcript_search/cli.py` — the `ats` CLI.

## Roadmap

- [ ] `ats open <session_id>` — jump to the raw source path (already in output with `-v`)
- [ ] `ats tail` — live-index mode watching for new session files
- [ ] MCP server wrapper (expose `ats` as an MCP tool for agents themselves)
- [ ] More extractors: Cline, Windsurf, Gemini CLI, aider
- [ ] `--json` output for scripting
- [ ] Relevance-tuned ranking (boost title + user-role matches)

## Privacy

Everything stays local. The index lives at
`~/.agent-transcript-search/ats.db` and never leaves your machine. No
telemetry, no network calls. API keys and tokens inside transcripts are
indexed like any other text — same trust model as the source logs
themselves.

## License

MIT
