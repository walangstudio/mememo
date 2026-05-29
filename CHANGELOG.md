# Changelog

## [0.9.0] - 2026-05-30

### Added
- **Portable project identity** — `repo_id` is now derived from the
  normalized `owner/repo` path rather than the raw remote URL or local path.
  `git@github.com:owner/repo` and `https://github.com/owner/repo` resolve to
  the same id. SSH host aliases (e.g. `git@gh-kitty:`) are resolved to their
  canonical hostname before normalization. Override precedence: `MEMEMO_REPO_ID`
  env > `.mememo/project.yaml project_id` > normalized remote hash > path hash
  fallback. Monorepo and cross-host-collision escape hatch: set `project_id` in
  `.mememo/project.yaml`.
- **Global lane** — memories stored outside a git repo go to `repo_id =
  "__global__"`. SessionStart hook and workspace recall always include the
  global lane so process/project notes surface regardless of active repo.
- **Workspace recall** — `discover_workspace` scans immediate-child repos from
  a parent directory (capped at `MEMEMO_WORKSPACE_MAX_REPOS`, default 8).
  Cross-location deps declared in `.mememo/workspace.yaml` under `projects:`.
  `recall_workspace` embeds the query once and merges results across all
  discovered repos plus the global lane, ranked by similarity.
- **SessionStart hook** — `python -m mememo session-start --hook` fires
  asynchronously at Claude Code session open, recalls memories from the
  workspace, and injects them via `additionalContext`. Register via
  `install-git-hooks --with-session-start` or manually in
  `~/.claude/settings.json`. `register_claude_session_start_hook()` in
  `mememo.hooks.installer` handles programmatic registration (idempotent).
  CLAUDE.md injection stays harness-controlled — mememo augments, does not
  replace it.
- **`import-md` importer** — `python -m mememo import-md <dir> [--repo <path>]
  [--dry-run]` ingests `.md` files with YAML frontmatter (e.g.
  `~/.claude/projects/.../memory/*.md`). Type mapping: `decision` →
  `decision`; `project`/`user`/`feedback` → `context`; `reference` →
  `relationship`. Idempotent: skips files whose `(file_path, checksum)` pair
  already exists. `[[wikilink]]` references stored as `REFERENCES` edges.
- **`reindex-identity` CLI** — `python -m mememo reindex-identity [--dry-run]`
  recomputes every stored `repo_id` via the live resolver and moves FAISS dirs
  to match. Supports `--dry-run` to inspect changes without writing.
- **Auto-migration** — on first startup after upgrade, a daemon thread
  re-derives every stored `repo_id` via the new resolver and moves FAISS dirs
  accordingly. Guarded by `schema_meta.identity_migrated`; runs exactly once.
  Non-blocking: server is usable immediately. FAISS conflicts (two old ids
  converge) clear embedding pointers for re-embedding.
- **`import-md --allow-secrets`** — opt-in bypass of secret detection for
  trusted local markdown. Memory notes often carry placeholder credentials
  (e.g. `postgres://user:pass@host`) that the scanner otherwise rejects.

### Fixed
- **Migration transaction safety** — `reassign_repo_id` caught
  `OperationalError` but not `IntegrityError`. When two repos normalise to the
  same id (incl. one repo aliased twice, each with a `branch_state` 'main' row)
  the partial `memories` UPDATE was left dirty for the next request to commit,
  producing an inconsistent store. Now wrapped in an explicit transaction
  (rollback + re-raise); the backfill records the colliding repo as skipped and
  keeps going.
- **`normalize_remote` ssh-with-port** — `ssh://user@host:port/path` was misread
  as SCP, making the port the "owner" segment and producing a garbage id. Added
  a negative lookahead so scheme URLs skip the SCP branch.
- **Vector-dir scaffolding** — `VectorIndex.__init__` pre-creates
  `{new_id}/{branch}/` before migration runs, so `move_vector_index` always saw
  a non-empty target → forced re-embed + orphaned `{old_id}/` every upgrade. An
  empty pre-created target is now replaced; only real FAISS files are a conflict.
- **CLI log-rotation contention** — running a mememo CLI command while the server
  holds `~/.mememo/logs/server.log` spammed `WinError 32` on rollover. The file
  handler now tolerates a locked file (skips that rotation, keeps appending).

## [0.8.1] - 2026-05-28

### Fixed
- **Hooks now actually reuse the running daemon** (PR #38, merged into v0.8.0 but missed in that entry). `cmd_capture` / `cmd_inject` / `cmd_pre_tool` were calling `initialize_mememo()` directly, which re-ran full boot every fire — defeating the v0.8.0 hooks-sidecar. They now call `ensure_initialized()` (the idempotent gate). Live log evidence pre-fix showed ~10 fresh "Initializing mememo" blocks per minute with the sidecar up; post-fix it's once per server process lifetime.
- **Hookd cold-init race serialized across handler threads** (PR #40). Each hookd request spins a fresh asyncio loop in its own thread, so the existing is-None check in `ensure_initialized` let multiple threads through and ran full init in parallel. Wrapped the init with a module-level `threading.Lock` using a double-checked-locking pattern; fast path stays a cheap pointer compare, only cold-init contention pays the lock.

### Tests
- Real `cmd_capture` integration test driven through hookd's default factories (PR #40), closing the wiring gap that hid the #38 bug behind a trivial `_echo` factory in earlier tests.

## [0.8.0] - 2026-05-28

### Added
- **FTS5 content search + `content_type` filter** (PR #36). New `memories_fts` virtual table populated by `save_memory` and kept in sync via an `AFTER DELETE ON memories` trigger, with a one-shot startup backfill for existing stores. `/memories?q=` now ORs the existing file/fn/class LIKE with an FTS prefix-match subquery, so `decision`/`context`/`summary` memories (NULL file/fn/class, body lives in a blob) are finally findable. New `/memories?content_type=` filter + frontend dropdown.
- **Hook sidecar** (PR #35). The MCP server boots a stdlib loopback HTTP listener (127.0.0.1, bearer-token, discovery file at `~/.mememo/.daemon.json`). `mememo capture|inject|pre-tool --hook` first POSTs into the running daemon, falling back transparently to the in-process slow path when the daemon isn't reachable (e.g. git hooks fired outside Claude Code). New modules: `mememo/hookd.py`, `mememo/hookclient.py`. Opt-outs: `MEMEMO_NO_HOOK_DAEMON=1` (server), `MEMEMO_NO_HOOK_CLIENT=1` (CLI).
- **`REFERENCES` URL edge type** (PR #37). Markdown doc sections emit a typed edge per outbound `https?://…` (trailing sentence punctuation stripped, dedup per section, fenced URLs ignored). The resolver naturally leaves the URL as `target_symbol` with `target_memory_id` NULL. Added to both `EdgeType` (chunking) and `RelationType` (types/memory) Literals; web graph renders REFERENCES with a distinct color.

### Fixed
- **MCP cold-start regression** (PR #34). `Embedder.dimension` was triggering a SentenceTransformer load (~6s cold on Windows) just to read a number `MODEL_REGISTRY` already publishes. `VectorIndex(..., dimension=embedder.dimension)` at `initialize_mememo` forced the load even though the model isn't used until the first `embed()` call. The property now reads from the registry first; cold init drops from ~9s → ~2.5s. Verified by `~/.mememo/logs/server.log` — "Loading embedding model" no longer appears at server boot.

### Notes
- **Editable installs need a refresh**: `pip install -e .` (or `uv pip install -e .`) so `importlib.metadata.version("mememo")` agrees with this bump. The daemon discovery file embeds the reported version, so the refresh is also how the sidecar advertises the new build.

## [0.7.1] - 2026-05-27

### Fixed
- **Indexing is ~90× faster — the multi-hour hang is gone.** `count_tokens` called `tiktoken.get_encoding("cl100k_base")` on every invocation; with no cached BPE file it attempted an HTTPS download that failed after ~0.8s, and the failure was never memoized. Run per chunk (and again inside `truncate_to_tokens`' binary search for summaries), this dominated indexing time. The tokenizer load is now attempted once and the failure cached, falling back to a fast offline heuristic. Indexing mememo on itself dropped from ~2030s to ~23s for the same 138 files / 1145 chunks.
- **Repository indexing now batches chunk creation.** `index_repository` created memories one chunk at a time, so every chunk spawned a `git` subprocess (context detection), ran a single-item embed, and committed on its own. It now accumulates chunks and calls `create_memories_batch` (one git-detect + one batched embed + one vector add per flush of 256).
- **File discovery no longer descends ignored trees.** The `glob("**/*")` walk enumerated `target/`, `.git/`, `node_modules/` etc. and filtered afterward (minutes on a large Rust `target/`); it now uses `os.walk` with in-place directory pruning, plus a max-file-size guard and a binary-extension skip list. Progress is logged every 100 files.

### Changed
- **MCP server starts fast and logs to a file.** `sentence-transformers`/`torch` and `faiss` are now imported lazily instead of at module load, so `python -m mememo` reaches the stdio handshake in ~2s (was a 5–15s cold import that could exceed Claude Code's connect window and show the server as failed). Logs are also written to `~/.mememo/logs/server.log` (rotating) since stderr is invisible once spawned.

## [0.7.0] - 2026-05-24

### Added
- **Rust edge extraction** `walk_rust` emits the full edge taxonomy for Rust: IMPORTS (`use` paths), CALLS (bare/scoped/field callees), IMPLEMENTS (`impl Trait for Type`), and USES (method-to-`impl` type binding), plus function/method/struct/enum/trait chunks. Previously Rust produced chunks but zero edges.
- **Java edge extraction** `walk_java` emits IMPORTS (dotted paths, wildcard preserved), EXTENDS (`superclass`), IMPLEMENTS (`super_interfaces`), CALLS (bare/`this`-qualified/field-access callees), and USES (`this` field reads), plus class/interface/method/constructor chunks. Verified against tree-sitter-java 0.23.5. Previously Java produced chunks but zero edges.
- **C / C++ edge extraction** `walk_c_family` (shared) emits IMPORTS (`#include`, brackets/quotes stripped), CALLS (bare/`this->m`/`Ns::f` callees), and for C++ EXTENDS (`base_class_clause`, multiple bases) + USES (method-to-class binding and `this->field` reads), plus struct/class and function/method chunks. Verified against tree-sitter-c 0.24.2 / tree-sitter-cpp 0.23.4. Previously C/C++ produced chunks but zero edges.
- **C# edge extraction** `walk_csharp` emits IMPORTS (`using`, dotted; alias resolves to its target), EXTENDS (`base_list` — C# does not separate base class from interfaces in-grammar, so every base type is EXTENDS), CALLS (bare/`this.M`/member-access callees), and USES (method-to-class binding and `this.<member>` reads), plus class/interface/struct/enum/record and method/constructor chunks, namespace-aware qualnames. Verified against tree-sitter-c-sharp 0.23.5. This closes the edge-graph gap — every language mememo chunks now emits edges.
- **Markdown docs in the graph** `MarkdownChunker` (pure-Python, heading-scoped) chunks `.md` files into one chunk per heading section (parent/child hierarchy), and emits `DOCUMENTS` edges from a doc section to the code symbols it names — backtick-quoted identifiers and bare `path/to/file.ext` mentions, resolved against the indexed symbol table at INFERRED confidence. Doc qualnames are namespace-aware (`file.heading-slug`). New `heading` chunk type; new `DOCUMENTS` edge type.

### Changed
- **Edge-type values are no longer constrained at the DB layer.** The `relations.type` `CHECK` was dropped; edge types are validated by the `RelationType`/`EdgeType` literals (Pydantic) before insert. Existing databases are migrated in place (table rebuild, rows preserved). Adding a future edge type now needs no schema migration.

### Fixed
- **Edge pass now runs for every walker language.** `index_repository` hardcoded the edge-pass language list to the original five (python/typescript/tsx/javascript/go), so the Rust/Java/C/C++/C# walkers added in v0.7 never ran during real indexing. The list is now driven by `ts_edges.EDGE_WALKERS`, so a new walker is picked up automatically.
- **Markdown `DOCUMENTS` edges now actually persist.** The chunker emitted edges with a slug-path `source_qualname` while the indexer registered the heading chunk under `module.<heading-text>`, so the resolver (which drops edges with an unknown source) discarded every doc→code edge. `Chunk` gained an explicit `qualname` the indexer registers verbatim, keeping both sides in sync; an end-to-end resolve test guards it. Also fixed heading-chunk `end_line` off-by-one and stopped URLs being emitted as bare-path edge targets. The `relations.type` migration is now re-runnable (`DROP TABLE IF EXISTS relations_new`).

## [0.6.2] - 2026-05-20

### Changed
- **tree-sitter dependency migrated** `tree-sitter-languages` (unmaintained, no wheels past Python 3.12) replaced with the per-language `tree-sitter-typescript/-javascript/-go/-rust/-java/-c/-cpp/-c-sharp` wheels. Each is self-contained and fully offline (no runtime grammar download), so mememo now installs and parses natively on Python 3.10–3.14 with plain pip. Core `tree-sitter` pin moved `>=0.20,<0.22` → `>=0.25.2,<0.26`
- **tree-sitter chunker query API** updated for tree-sitter 0.25 (`Query(lang, src)` + `QueryCursor.captures()` returning `dict`). A version-compat shim keeps the legacy ≤0.21 path working too
- **Installers are uv-aware** `install.sh` / `install.bat` detect `uv` and create the venv with it (falling back to pinned CPython 3.12 when the system Python is unsupported), then `uv pip install`. Plain `python -m venv` + `pip` remain the fallback when uv is absent

## [0.6.0] - 2026-05-13

### Added
- **Worktree-canonical repo_id** (FR-024): `GitManager.canonical_repo_root` uses `git rev-parse --git-common-dir` so linked worktrees (`git worktree add ...`) collapse to the same SHA-256 `repo_id` as the primary checkout instead of fragmenting per worktree
- **`mememo migrate-worktrees`** (FR-025): one-shot CLI that re-keys legacy per-worktree `repo_id`s onto the canonical one across `memories`, `relations`, `branch_state`, and the existing index_state tables. Idempotent, supports `--dry-run`
- **Four read-only MCP resources** (FR-026/FR-027): `mememo://repo/{id}/stats`, `mememo://repo/{id}/stale`, `mememo://repo/{id}/branch/{name}/summary`, `mememo://repo/{id}/community/{cid}` — each payload bounded to ≤4 KB with a `truncated` marker
- **PreToolUse hook** (FR-028/FR-029): `python -m mememo pre-tool --hook` reads the Claude Code hook payload for Grep/Glob/Bash calls and emits up to 3 related memories totalling at most 300 tokens. Never blocks the tool call; failures log to stderr and return an empty continue response
- **FastAPI web UI** (FR-030/FR-031): `mememo serve` (optional extras) launches a localhost-only app on 127.0.0.1:5757 with read-only routes (`/repos`, `/memories?as_of_sha=<sha>`, `/relations`, `/communities`, `/snapshots/{sha}`) plus a single-page D3-force graph + paginated memories table + time-travel slider. Binding refused for any host other than localhost
- **`cypher_query` MCP tool** (FR-032): hand-rolled parser for a documented Cypher subset (`MATCH (a)-[r:TYPE]->(b)`, `WHERE` with `=` / `<>` / `=~` / `AND` / `OR`, `RETURN ident.prop [AS alias]`, `LIMIT`). Any other construct raises `UnsupportedCypherError` which the tool layer turns into `error_kind="unsupported"`
- **`mememo install-git-hooks --with-pretool`**: installer extension that registers the PreToolUse hook into `<repo>/.claude/settings.json`. Idempotent; refuses to clobber a user-customised block unless `--force` is passed
- **`mememo merge-branch` / `mememo sync-commits` CLI shims**: dispatch arms that the opt-in post-merge / post-commit hooks shell out to. Previously the hook scripts called these subcommands but the dispatch was missing — fixed in the simplify pass
- **Optional install extras**: `pip install 'mememo[web]'` for the FastAPI UI, `pip install 'mememo[graph]'` for `cluster_relations` (networkx) + `dedup_entities` (rapidfuzz). The MCP server, hooks, and every other CLI subcommand work without either. Installer flags `--with-web` and `--with-graph` available

### Changed
- Console script entry-point repointed from `mememo.server:main` (non-existent) to `mememo.__main__:main`
- `__main__.py` rewritten as a `{cmd: handler}` dispatch table
- `translate_to_sql` returns `(sql, params, projection_keys)` so callers no longer reach into private parser internals
- Snapshot filter pushed server-side: `/memories?as_of_sha=<sha>` filters before paginating, replacing a buggy client-side filter that corrupted `total` and pagination math

### Performance
- `graph_neighbors` and `graph_impact` BFS rewritten as batched per-level `IN (...)` queries — one SQL per level instead of one per visited node (N+1 elimination)
- `merge_branch` switched to `executemany` + a single transaction commit; per-row `append_event` + per-row commit replaced. `content_ref` secrets-scan results cached within one merge call
- `detect_changes` chunks its `WHERE file_path IN (...)` to 500 ids per batch — defends against `SQLITE_MAX_VARIABLE_NUMBER` failures on large diffs
- `index_repository._run_edge_pass` consumes `(rel_path, content, lang)` triples + a pre-built `SymbolEntry` list threaded from the main indexing loop instead of re-walking the tree with 6 separate `rglob` patterns + re-reading every file + re-querying SQLite for the symbol table. Also no longer descends into `.venv` / `node_modules`

### Reuse
- New `coerce_sha(value) -> str` helper in `mememo.types` replaces 9 hand-rolled `commit_hash or NULL_SHA` / length-check sites across `memory_manager`, `storage_manager`, `index_repository`, `merge_branch`
- `SHA_PATTERN` and `SHA_PREFIX_PATTERN` exported from `mememo.types`; duplicate regexes dropped from `recall_at_commit.py` and `web/app.py`

### Security
- `mememo.web.app.run()` refuses to bind to anything other than 127.0.0.1 / localhost — defence in depth on top of the FastAPI route guards
- `/snapshots/{sha}` and `/memories?as_of_sha=` reject anything that isn't 4-40 hex chars, matching the v0.4 option-injection guards on `recall_at_commit`

## [0.5.0] - 2026-05-13

### Added
- **Typed-edge memory graph** (FR-013, FR-014, FR-017): every indexed Python / TypeScript / JavaScript / Go file emits `IMPORTS`, `CALLS`, `EXTENDS`, `IMPLEMENTS`, `USES`, `DECORATED_BY` edges into a new `relations` table. Edge confidence (`EXTRACTED` / `INFERRED` / `AMBIGUOUS`) recorded per row. CHECK constraints reject unknown types + non-40-char SHAs
- **`mememo/chunking/python_ast_chunker.py`** gains `chunk_with_edges()` — a single scope-aware walk that emits chunks with proper `parent_class` AND raw edges in one pass. Legacy `chunk()` unchanged
- **`mememo/chunking/ts_edges.py`** (FR-014): tree-sitter walkers for TypeScript / JavaScript / Go that share the same scope-stack pattern as the Python walker. Handles JS-vs-TS class_heritage grammar differences and Go method receivers
- **`mememo/core/symbol_resolver.py`** (FR-015, FR-016): turns `(rel_path, raw_edges)` into resolved `Relation` rows. Exact qualname match → `EXTRACTED`; suffix match via a precomputed tail index → `EXTRACTED`; single Jaro-Winkler match ≥ 0.95 → `INFERRED`; otherwise `AMBIGUOUS` with the raw `target_symbol` preserved. Fuzzy match auto-disables when the symbol set exceeds `fuzzy_max_symbols=2000` so the resolver stays O(E) on large corpora
- **`mememo/core/graph_analysis.py`** (FR-018, FR-019): `cluster_relations()` runs deterministic-with-seed Louvain (networkx) over the relations graph and stamps `community` per edge. `dedup_entities()` chains Jaro-Winkler (rapidfuzz) and a path-compressed union-find; writes `(canonical_memory_id, alias_label, similarity)` to a new `entity_aliases` table. Both raise a clear `ImportError` when their optional dep is missing
- **`graph_neighbors`** MCP tool (FR-020): depth-limited BFS over typed edges with direction (`out` / `in` / `both`) and edge-type filter
- **`graph_path`** MCP tool (FR-021): shortest directed edge path between two memories or `null` if unreachable within `max_depth`
- **`graph_impact`** MCP tool (FR-022): blast-radius BFS with `min_confidence` floor; each reached memory decorated with its current `risk_grade` (from v0.4 sync_commits) + file/class/function metadata. `direction='upstream'` inverts the walk to find callers / dependents
- **`search_similar` `cluster_id` filter** (FR-023): restrict semantic-search results to memories whose relations live in the named community
- **Resolver perf gate** (FR-016): `benchmarks/resolver_perf.py` + a pytest budget gate that asserts ≤1.0s per 10k chunks
- **Index-time perf gate** (FR-035): `benchmarks/index_corpus_perf.py` asserts that edge extraction adds at most 30% over chunk-only baseline

### Changed
- `index_repository` now runs a best-effort edge post-pass after chunking that emits + resolves + persists relations. Fails open: chunk-only indexing still produces a usable store when tree-sitter or other optional deps are missing
- `Chunk.parent_class` is now populated correctly for Python methods via the unified scope-aware walk (legacy walk left `parent_class=None` per a pre-v0.5 TODO)

### Performance
- Resolver tail-index precomputation: suffix lookup is O(1) per edge instead of O(N). Resolved 5k symbols in <0.5s where the original implementation took >6s

## [0.4.0] - 2026-05-13

### Added
- **Commit-aware memory layer**: every memory now carries the git SHA it was created at (`created_at_sha`) and last updated at (`updated_at_sha`); risk-graded staleness (`WILL_BREAK` / `LIKELY_AFFECTED` / `MAY_NEED_TESTING`) lives on `memories.risk_grade`
- **`memory_events` table**: append-only event log (CREATED / UPDATED / STALED / DELETED / RESTORED) with a `CHECK(length(commit_sha)=40)` guard and a UNIQUE index against duplicate CREATED events under concurrent startup
- **`branch_state` table**: per-(repo, branch) last_indexed_sha + parent_sha (merge-base with default branch); upserted by `index_repository` on every run
- **`detect_changes` MCP tool**: read-only diff → affected memories with risk grades; backs the post-commit hook
- **`recall_at_commit` MCP tool**: time-travel semantic search; resolves a target SHA to its commit timestamp, replays events, filters FAISS search to the alive set
- **`merge_branch` MCP tool**: unions alive source-branch memories into target, dedup by `content_sha`, emits RESTORED events tagged at the merge SHA
- **GitManager extensions**: `merge_base`, `is_merge_commit`, `diff_between`; whitelist extended with `merge-base` and `cat-file`
- **Risk grader** (`mememo/core/risk_grader.py`): pure function turning a `--name-status` diff + memory line range into a risk grade; FR-009 SHOULD-clause hunk-overlap downgrade implemented behind an optional `hunk_ranges` arg
- **Opt-in git hooks**: `mememo install-git-hooks --repo-path <repo>` copies `post-merge` (triggers `mememo merge-branch`) and `post-commit` (triggers `mememo sync-commits`) into `.git/hooks/`; refuses to clobber existing hooks unless `--force` is passed (FR-033)
- **`NULL_SHA` / `BACKFILL_SHA` sentinels**: 40-char hex sentinels for "no git context" and "legacy pre-v0.4 row"; replace the empty-string SHA flaw flagged by the v0.4 security audit

### Changed
- `mark_memories_stale_for_file(file, repo, branch, reason, commit_sha=None)` now emits a STALED event per affected memory; the new `commit_sha` parameter is the SHA that caused the staling (defaults to `NULL_SHA`)
- `index_repository` upserts `BranchState` alongside the legacy `repo_index_metadata.last_indexed_commit` so commit-aware tools can read the canonical per-branch SHA
- `server.py` tolerates a missing pip-install (`PackageNotFoundError`) and falls back to `"0.0.0+local"` so the module loads from a source checkout during dev/CI

### Migration
- v0.3 → v0.4 backfill runs idempotently on every startup: rows with a valid 40-char `commit_hash` get their `created_at_sha` / `updated_at_sha` backfilled and receive one synthetic CREATED event; legacy rows without a valid SHA get `BACKFILL_SHA` so the replay path can distinguish them from live non-repo writes
- A down-migration helper `StorageManager.downgrade_v04_to_v03()` is available for emergency rollback — it drops the v0.4 tables and columns; SQLite `ALTER TABLE DROP COLUMN` requires SQLite 3.35+

### Security
- `memory_events.commit_sha` has a DB-level CHECK constraint requiring length 40; combined with the Pydantic field validator on `MemoryEvent.commit_sha`, empty-string SHA writes are rejected at both layers
- Backfill uses `INSERT OR IGNORE` + a UNIQUE index on `(memory_id, commit_sha) WHERE op='CREATED'` so concurrent startups cannot duplicate synthetic events

## [0.3.0] - 2026-03-21

### Added
- **Passive hooks**: `UserPromptSubmit` hook injects relevant memories as a system message before each prompt; `Stop` hook asynchronously captures memorable facts from the conversation transcript after each response — both fully automatic, no user action required
- **`capture` tool**: LLM-based extraction of decisions, analysis, and context from raw text; falls back to a self-extract prompt in passthrough mode (no LLM configured)
- **`store_decision` tool**: Store architectural decisions with rationale in a single call
- **`recall_context` tool**: Search persistent memories only (decisions, analysis, context) — excludes code snippets
- **`recent_context` tool**: Fetch the most recently stored memories by creation time
- **`end_session` tool**: Flush and persist indexes at session end
- **`llm_adapter`**: Multi-provider LLM abstraction (Anthropic, OpenAI, Ollama) used by the capture tool for autonomous extraction
- **`cli.py`**: `python -m mememo capture --hook` and `python -m mememo inject --hook` entry points consumed by the hook scripts
- **Benchmark suite** (`benchmarks/hooks_perf.py`): Reproducible token-savings benchmark comparing inject hook vs naive file-read baseline; reports per-prompt breakdown, mean savings, and full JSON output

### Fixed
- Tree-sitter incompatibility with `tree-sitter >= 0.22`: pinned to `<0.22` so `tree_sitter_languages` (which uses the old `Language(path, name)` API) works correctly; Go, Rust, Java, C, C#, TypeScript parsers now load without errors
- Tree-sitter parser failures now log a single `WARNING` per language on first failure and cache it — previously logged `ERROR` on every file processed for that language
- `capture` deduplication: before storing an LLM-extracted memory, a similarity search at threshold 0.97 skips near-identical content from previous sessions; dedup check fails open so a search error never silently drops a memory
- Persistent memory accumulation: type-differentiated TTL expires `conversation` memories after 30 days and `context` memories after 90 days; `decision`, `analysis`, and `summary` are durable and never auto-expired. Cleanup runs lazily at inject time. Configurable via `MEMEMO_TTL_CONVERSATION_DAYS` and `MEMEMO_TTL_CONTEXT_DAYS` (set to 0 to disable)

### Changed
- Hook inject uses a two-stage similarity filter: broad `inject_search_floor` (default 0.2) fetches candidates; `inject_min_similarity` (default 0.25) filters the final injected block — reduces noise without sacrificing recall

## [0.2.0] - 2026-02-24

### Fixed
- Pydantic V2 deprecations: replaced `class Config` with `ConfigDict`, `.dict()` with `.model_dump()`
- MCP cold-start timeout: added `warmup.py` to pre-compile bytecode and cache embedding model at install time
- `claude mcp add` failing on re-install: install scripts now remove existing entry before re-adding
- Batch script `! was unexpected` error caused by `enabledelayedexpansion` and exclamation marks in echo strings

### Changed
- Minimum Python version bumped to 3.10 (required by fastmcp)
- Version is now a single source of truth in `pyproject.toml`; all code reads it via `importlib.metadata`
- Install scripts read version dynamically from the installed package
- Type annotations modernised: `Optional[X]` → `X | None`, `List[X]` → `list[X]`, `Tuple` → `tuple`
- GitHub Actions CI simplified: single Ubuntu job, Python 3.10 + 3.12 only, removed CodeQL and security jobs

## [0.1.0] - Unreleased

Initial development version.

### Features
- FastMCP server with 9 tools + 2 resources
- Code-aware chunking (Python AST, tree-sitter for 8+ languages)
- Semantic search with FAISS vector indexing
- Git-aware branch isolation
- Incremental indexing with Merkle DAG
- Secrets detection and sanitization

### Requirements
- Python 3.10+
- Dependencies: FastMCP, Pydantic, sentence-transformers, FAISS, tree-sitter
