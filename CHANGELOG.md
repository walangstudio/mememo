# Changelog

## 2.0.0 - 2026-09-25

mememo 2.0: greenfield rewrite as a MEMORY.md replacement. mememo 1.x (code-aware memory MCP, code graph, embeddings) is archived at [walangstudio/mememo-code](https://github.com/walangstudio/mememo-code).

- Markdown cards as source of truth, SQLite FTS5 index, history snapshots, supersede links.
- Claude Code hooks: SessionStart digest, per-prompt recall with relevance gate and session dedupe, async Stop sweep.
- CLI: add, search, show, status, sweep, import, restore, install.
- Zero runtime dependencies; prompt hook p50 110-180 ms on Windows.
- Hooks and the agent-facing CLI call the base interpreter directly (`hook.py <args>` is the CLI), bypassing `.exe` launchers that cost up to 8 s per call on Windows.
