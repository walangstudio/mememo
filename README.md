# mememo 2.0

Unlimited, fast, local memory for coding agents. Replaces Claude Code's `MEMORY.md`, which only loads the first 200 lines / 25KB and shares one file per launch directory.

> mememo 2.0 is a rewrite. The 1.x code-aware memory MCP server (code graph, embeddings, comprehension tools) is archived at [walangstudio/mememo-code](https://github.com/walangstudio/mememo-code).

- **Never forgets.** Markdown files are the source of truth. Edits are snapshotted to `history/`, superseded and deleted facts stay searchable (`--all`) and restorable.
- **No hallucinated recall.** Memories are stored verbatim, never summarized. Every recalled note carries its card id, date and path, and the block tells the model to say "don't know" when memory is silent.
- **Fast.** One stdlib Python process per prompt: p50 110-180 ms, p95 150-410 ms end to end on a loaded Windows dev box; the SQLite FTS5 query is 5-10 ms of that.
- **Nothing to fail at startup.** No daemon, no MCP server, no dependencies, no `.exe` launcher shim. The hook fails open: a broken index never blocks a prompt.

## How it works

| Event | What mememo does |
|---|---|
| SessionStart | Sweeps changed files into the index, injects the save protocol plus a budgeted index (rules first, then this project, then recent) |
| UserPromptSubmit | BM25 search over ~700-char chunks, injects up to 6 relevant notes (top 3 with verbatim body), skips notes already shown this session, injects nothing for unrelated prompts |
| Stop (async) | Sweeps files the agent edited |

A memory is a card inside a topic file:

```
## The lighthouse staging database is on port 7000 (moved from 6543 on 2026-09-25).
id: m-d7eba12ad | date: 2026-09-25 | supersedes: m-d7ae7bb40
User said on 2026-09-25: "the lighthouse staging database moved again, now to port 7000." ...
```

Plain markdown (for example imported Claude topic files) works too: it is chunked by paragraph.

Layout under `~/.mememo` (`MEMEMO_HOME`): `memory/global/`, `memory/repos/<owner>/<repo>/`, `memory/dirs/<slug>/`, `index.db` (rebuildable), `history/`, `hook.log`.

## Install

```
uv tool install git+https://github.com/walangstudio/mememo
mememo import ~/.claude/projects/<project>/memory   # optional: bring existing Claude memory
mememo install                                       # hooks into ~/.claude/settings.json, autoMemoryEnabled=false
```

`install` backs up the settings file and wires hooks to the base interpreter directly (`pythonw.exe -I -S .../hook.py`). The injected save protocol gives the agent the same fast path for CLI calls (`python.exe -I -S .../hook.py add ...`); the `mememo` launcher works for humans but can take seconds per call on Windows.

## Commands

```
mememo add --type feedback --topic ci --claim "Run clippy before push" --body "..." [--lane global|here] [--supersedes m-...]
mememo search "query" [--full] [--all]
mememo show m-d7eba12ad | global/ci.md
mememo status        # counts, lane, hook p50/p95, recent hook errors
mememo sweep [--rebuild]
mememo restore global/ci.md [-n 0]
```

## Development

```
pytest -q tests
python bench/hook_latency.py 40
ruff check src tests bench && black --check src tests bench
```
