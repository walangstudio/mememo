# mememo v2 plan

> Naming (2026-09-25, later): the old repo is archived as `walangstudio/mememo-v1` (local `mememo-v1`, data `~/.mememo-v1`). `mememo-code` is reserved for a from-scratch code-indexing rewrite. Mentions of `mememo-code` below refer to the archive.

Greenfield. Generic, local-first, unlimited, never-forgetting memory for AI coding agents. First job: replace Claude Code auto memory (MEMORY.md). The old repo lives on as `F:\opt\projs\ai\claude\mememo-code` (code indexing, optional). Planned 2026-09-25 by Fable (plan mode).

## Requirements (priority order)

1. Never forget: nothing silently lost, truncated, evicted or deleted. Edits keep history. Buried facts stay retrievable.
2. Fast: per-prompt hook p95 < 300 ms on Windows incl. interpreter start; SessionStart < 500 ms. Offline hot path.
3. Unlimited: injection is budgeted, not proportional to memory count. Holds at 10k memories.
4. Effective: memory reaches the model without it calling a tool.
5. Minimal: zero runtime deps in core, ~1.5k LOC.

## Evidence

- Claude auto memory loads only the first 200 lines / 25KB of MEMORY.md; dir keyed by git repo or launch dir; no user-wide scope.
- Old mememo never injected anything: read `user_prompt` (host sends `prompt`), emitted `systemMessage` (user-only) instead of `hookSpecificOutput.additionalContext`, hooks never wired, TTL expiry ran in the hot path.
- Recall eval, real corpus (54 files, 47 queries, 12 buried):

| setup | buried hit@5 | MRR | p50 | load |
|---|---|---|---|---|
| heading sections + MiniLM | 0.75 | 0.80 | 8 ms | 0.5 s + torch |
| ~700-char chunks + BM25 | 1.00 | 0.92 | 0.3 ms | none |
| ~700-char + MiniLM + BM25 | 1.00 | 0.95 | 9 ms | 0.5 s + torch |
| ~700-char + model2vec + BM25 | 1.00 | 0.94 | 0.6 ms | 0.24 s + numpy |

- Import cost: python start ~95 ms, stdlib ~150 ms, numpy ~230 ms, pydantic stack ~650 ms, torch ~2.7 s.
- Hook contract verified (Claude Code 2.1.282 binary + live pinocchio hook): input `prompt`, output `{"hookSpecificOutput":{"hookEventName":...,"additionalContext":...}}`. No documented output size cap. Codex and Gemini CLI hooks share the shape.

## 0. Tie-breakers

1. Hot path = one stdlib Python process, one SQLite read, always exit 0. No daemon, spawn or network.
2. Files are the memory. The DB is an index plus append-only history. mememo never rewrites or deletes a memory file.
3. Model receives memory via hooks and saves with Write/Edit.
4. Every knob is justified by the committed eval.

## 1. Storage

| Decision | Choice |
|---|---|
| Source of truth | Markdown with Claude-compatible frontmatter (`name`, `description`, `metadata.type` user/feedback/project/reference) |
| Index | SQLite FTS5 `porter unicode61`, ~700-char paragraph chunks, external-content |
| History | Append-only `versions` table (zlib full content per changed hash). No git in core (subprocess, lock races, console flash). Users may `git init` the dir themselves |
| Root | `~/.mememo/` (`MEMEMO_HOME`). Old data moves to `~/.mememo-code/` |
| Layout | `memory/global/*.md`, `memory/repos/<owner>/<repo>/*.md`, `mememo.db`, `sessions/<id>.json`, `hooks.log` |
| Scoping | Lanes boost ranking, never filter. Model picks the lane when saving; cwd lane (from `.git/config`, no subprocess) only sets the boost |
| Identity | memory id = path under `memory/`; chunk id = `path#ord` |
| Supersede | None in v1; chunks carry updated dates, edits create versions |
| Deletion | Detected, chunks dropped, content kept in `versions`, reported in next digest, `mememo restore <path>` |
| Config | Env only: `MEMEMO_HOME`, `MEMEMO_PROMPT_BUDGET=800`, `MEMEMO_SESSION_BUDGET=2000` |

```
files(path PK, lane, name, description, type, mtime, size, hash, updated, deleted_at, secret)
chunks(id PK, path, ord, hash, heading, text, lane, type, updated)
chunks_fts fts5(text, content='chunks', content_rowid='id', tokenize='porter unicode61')
versions(id PK, path, hash, content BLOB, ts, note)   -- never deleted, not rebuilt
meta(key PK, value)
```

`mememo reindex` rebuilds files/chunks from disk; `versions` is the only non-derived table (back it up with the memory dir).

## 2. Retrieval

| Item | Decision |
|---|---|
| Chunking | Pack paragraphs to ~700 chars; split only paragraphs > 1400 chars; prefix `<stem words>: <description>` + nearest heading |
| Ranking | FTS5 `bm25()`, then current lane x1.25, user/feedback x1.2, updated < 14 d x1.1; max 2 chunks/file; k <= 6 |
| Usage | Per-session dedupe of injected chunk hashes in `sessions/<id>.json`; no DB write in hot path |
| Query | `\w+` lowercase, ~150 stopwords, drop < 2 chars and long hex/numbers, top 40 terms, long prompts use first 1200 + last 400 chars, OR of quoted terms |
| Skip | < 3 content terms, prompt starts with `/`, DB missing/locked |
| Threshold | Keep chunks >= 0.4 x best; absolute floor tuned so negatives inject <= 20% |
| Vectors | Not in v1. Seam: `search.rank(query, lane) -> [(chunk_id, score)]`. Revisit if blind set hit@5 < 0.8 |
| Eval | `eval/`: synthetic ~40-file corpus with buried nonces, dev/blind/negative query sets. Gate: buried hit@5 >= 0.9, MRR >= 0.85, negatives <= 20%, query p95 < 5 ms. `mememo eval --real <dir>` for the private corpus |

## 3. Jev / judges

Jev is a hosted API (bearer key, latency undocumented). Not in the hot path. Not in v1. v2 optional `[judge]` extra: `mememo consolidate` reports near-duplicate / contradiction pairs (one Noul per pair), never writes. Local alternative: model2vec cosine >= 0.9. v1: exact chunk-hash dedupe only.

## 4. Write path

1. Model writes `~/.mememo/memory/<lane>/<topic>.md` per the injected protocol.
2. `mememo remember` (CLI) and `mememo_remember` (MCP) for agents without file tools.
3. Stop hook (`async: true`) runs `sweep`: stat, rehash changed, reindex per file in one transaction, version on hash change, mark deletions. Throttle 20 s.
4. No transcript capture or LLM extraction.
5. Concurrency: file-level last-writer-wins; sweep `BEGIN IMMEDIATE` + 5 s busy_timeout; hot path read-only, 200 ms busy_timeout, empty on lock.
6. Manual edits picked up by next sweep.
7. Secrets: high-precision patterns (from old `mememo/utils/secrets_detector.py:10-38`); flagged chunks excluded from injection; files never rewritten; `remember` refuses secrets.

## 5. Injection

| Event | Input | Config |
|---|---|---|
| SessionStart | `source`, `cwd`, `session_id` | matcher `startup\|clear\|compact`, timeout 5 |
| UserPromptSubmit | `prompt`, `cwd`, `session_id` | timeout 5 |
| Stop | `transcript_path`, `session_id` | async, timeout 120 |

SessionStart digest (<= 2000 tokens, chars/4): protocol ~120, pinned user+feedback descriptions 600, current-lane descriptions 500, 10 most recent 300, housekeeping 150. Overflow says "+N more surface per prompt".

Per prompt (<= 800 tokens, <= 6 whole chunks):

```
<mememo-recall note="auto-recalled from ~/.mememo/memory; dated; read the file for more">
[feedback 2026-06-14 global/feedback_no_merge_without_approval.md] <chunk>
</mememo-recall>
```

Windows: `mememo-hook` as a gui-script (no console flash), dispatch on `hook_event_name`, imports limited to `sys, os, json, re, sqlite3, time` (test enforces via `-X importtime`). Expected ~160 ms. Fallback: installer writes `pythonw.exe -I <hook.py>` directly.

## 6. Agents

Core is agent-neutral; adapters are installers only. v1: `adapters/claude_code.py` (merge hooks into settings.json with backup, `autoMemoryEnabled: false`). Phase 6: Codex (`~/.codex/hooks.json`), Gemini (`BeforeAgent`/`AfterAgent`). MCP server is stdlib JSON-RPC, 5 tools, not registered for Claude by default (protocol points to `mememo search`/`show` via Bash: zero tool-definition tokens).

## 7. Migration

1. `mememo import-claude`: copy the 54 topic files to `memory/global/`; MEMORY.md becomes `global/claude_memory_index.md`. `mememo mv` moves files to repo lanes later.
2. `mememo import-legacy-db ~/.mememo-code/data/mememo.db`: 21 file-less `context` rows -> `global/legacy/<date>-<slug>.md`. 87 file-backed rows skipped.
3. `mememo install`: back up settings, add 3 hooks, `autoMemoryEnabled: false`, add 2 fallback lines to `~/.claude/CLAUDE.md`.
4. Rollback: `mememo uninstall --restore-claude` + `mememo export-claude` (regenerates a <= 200-line MEMORY.md). Formats identical both ways.

## 8. Layout

```
pyproject.toml   python >=3.10, no runtime deps; scripts: mememo; gui-scripts: mememo-hook, mememo-mcp
src/mememo/
  hook.py        hot path, stdlib allowlist, always exit 0
  store.py       paths, lanes, frontmatter, repo id from .git/config, normalize_remote (old core/identity.py:35-70)
  index.py       schema, chunker, sweep, versions, secrets, reindex
  search.py      query build, rank, boosts, gates, session dedupe
  inject.py      digest + per-prompt block, budgets
  protocol.py    save-protocol text
  cli.py         argparse commands
  mcp.py         stdio JSON-RPC, 5 tools
  adapters/claude_code.py
tests/  eval/  bench/hook_latency.py  .github/workflows/ci.yml
```

Rejected deps: pyyaml, tiktoken, mcp SDK (pydantic), numpy/model2vec, psutil, truststore, torch, faiss, tree-sitter. Dev: pytest, ruff, black.

CI (ubuntu + windows): ruff, black --check, pytest, eval gate, Windows hook bench (soft 600 ms in CI, hard 300 ms locally).

## 9. Rename / archive (order matters)

1. Stop mememo processes and sessions in the old dir.
2. `uv tool uninstall mememo`; remove any editable install of the old package.
3. Move `~/.mememo` -> `~/.mememo-code`; remove `MEMEMO_TOOLS` from settings env.
4. DONE 2026-09-25: local dir renamed to `mememo-code`. Pending in it: `pyproject.toml` + `.claude-plugin/plugin.json` name -> `mememo-code`.
5. HELD (user chose local only): `gh repo rename mememo-code -R walangstudio/mememo`.
6. HELD: update `walangstudio-marketplace/.claude-plugin/marketplace.json:50-56` while the redirect works.
7. HELD: create `walangstudio/mememo` only after 5-6.

## 10. Phases

| Phase | Ships | Verify |
|---|---|---|
| 0 Archive | section 9 | no mememo in `uv tool list`, `~/.mememo-code/data/mememo.db` present |
| 1 Core + CLI | store, index, search, cli, eval gate | `mememo search "the cmd window keeps flashing when hooks run"` ranks `windows-console-popup-mcp-class.md` first; eval gate green in CI |
| 2 Hooks | hook, inject, protocol, claude adapter, install, bench | Nonce E2E: `claude -p` in a fresh session repeats a nonce from a memory file with zero `tool_use`; bench p95 < 300 ms, SessionStart < 500 ms |
| 3 Write path | sweep, versions, deletions, secrets | "remember X" creates a file; 2 edits = 2 versions; deleted file reported and restorable |
| 4 MCP | mcp.py | `/mcp` lists 5 tools; `mememo_recall` returns the nonce |
| 5 Migration + scale | legacy import, export, 10k corpus | 21 legacy files imported; 10k sweep < 60 s, hook p95 < 300 ms, digest <= 2000 tokens |
| 6 Codex/Gemini | installers | nonce test via `codex exec` and `gemini -p` |
| 7 Distribution | PyPI, marketplace plugin | clean Windows VM install + nonce test |

## 11. Risks / unverified

1. Claude saving from an injected protocol with auto memory off: biggest risk, measured in Phase 3.
2. Lexical bias in the eval: blind set.
3. Hook output size cap: none found; self-capped.
4. Async Stop stdout discarded / fires once per turn: confirm in Phase 3 logs.
5. uv gui-script shim latency on Windows: unmeasured; pythonw fallback.
6. 10k-file NTFS stat sweep: measured in Phase 5.
7. Jev latency: unverified, off the v1 path.
8. Codex/Gemini schemas: docs only until Phase 6.
9. Same-file edits by two sessions within one turn: last write wins.
10. `versions` makes the DB non-derived: document backup.
11. Org rights for `gh repo rename`: assumed.
12. Frontmatter parser handles two-level shape only: fallback to first body line.
13. settings.json env contains an API key: installer never logs settings.

## 12. Revision 2026-09-25: research + trial results (supersedes conflicting items above)

Research (LongMemEval ICLR 2025, HaluMem, MemDelta, ConvoMem): extracted facts / summaries lose information and add hallucinations; verbatim text + good retrieval matches or beats Mem0/Zep-style extraction. Decision: store verbatim cards, add an agent-written claim line as the index key, never summarize the body.

Card format (claim is the heading, meta line under it):

```
## <one factual sentence>
id: m-<hex> | date: YYYY-MM-DD | supersedes: m-<hex> | src: <file or commit>
<verbatim body: quotes, exact commands, paths, versions>
```

Changes vs sections 1-10:
- Write path: agent saves with `mememo add` (Bash), not free-form Write/Edit, so ids/dates/format are deterministic. Protocol: search first; supersede with `--supersedes` and carry forward every still-true fact; body records only what was said.
- History: file snapshots in `history/<path>/<stamp>-<sha8>.md`, not a versions table. Removed/edited-away chunks stay in the index with `current=0` and are searchable with `--all`.
- Relevance gate: inject only if >= 3 query terms match, or coverage >= 0.6, or coverage >= 0.4 with BM25 >= 12. Tuned on the real corpus; the ">= 3 terms" clause came from a compound-question trial failure.
- Hook launch: `pythonw.exe -I -S <abs>/hook.py`. Measured entry-point `.exe` shim: p50 1.06 s, p95 14.1 s (likely why old mememo "failed to start").

Measured (real 55-file corpus): normal queries hit@5 1.00, MRR 0.96; buried hit@5 1.00; unrelated prompts injecting noise 1/8; query p50 5 ms; hook spawn p50 110-180 ms, p95 150-410 ms across runs (machine noise); SessionStart p50 120-160 ms.

E2E trials with `claude -p`, isolated settings + MEMEMO_HOME, auto memory off, no tools allowed for recall:
| trial | result |
|---|---|
| nonce recall | exact, 0 tool calls |
| unknown fact (staging port never stored) | "I don't know", no invention |
| superseded fact (region us-east-1 -> eu-west-2) | eu-west-2, notes the move |
| buried fact in 37KB file | correct |
| save then recall in a new session | saved via `mememo add` after a search; recalled |
| update via agent (5433 -> 6543 -> 7000) | superseded correctly; after protocol fix, carried "Postgres 16" forward |
Fixes found by trials: body embellishment (protocol now: record only what was said); supersede dropped a still-true fact (protocol now: carry forward).
