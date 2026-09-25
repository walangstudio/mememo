# Changelog

## 1.0.0a1 - 2026-09-25

Greenfield rewrite. The previous code-indexing mememo lives on as `mememo-code`.

- Markdown cards as source of truth, SQLite FTS5 index, history snapshots, supersede links.
- Claude Code hooks: SessionStart digest, per-prompt recall with relevance gate and session dedupe, async Stop sweep.
- CLI: add, search, show, status, sweep, import, restore, install.
- Zero runtime dependencies; prompt hook p50 110-180 ms on Windows.
