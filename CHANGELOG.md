# Changelog

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
