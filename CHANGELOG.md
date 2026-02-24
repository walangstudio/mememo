# Changelog

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
