# Changelog

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
