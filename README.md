# mememo 🧠

[![Python Version](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)
[![Tests](https://img.shields.io/badge/tests-passing-brightgreen.svg)](tests/)
[![MCP](https://img.shields.io/badge/MCP-compatible-purple.svg)](https://modelcontextprotocol.io/)

**Git-aware code memory for AI assistants** - MCP server that understands your codebase's structure, not just its text. Private, local, and built for developers.

## ⚡ TL;DR — 60-second install

```bash
pip install -e .                                       # or: pip install -e '.[web,graph]'
claude mcp add mememo -- python -m mememo              # Claude Code
```

That's it. Open Claude Code in any git repo and it can call `store_memory`,
`search_similar`, `recall_context`, the v0.6 graph tools, etc. Optional:
`export ANTHROPIC_API_KEY=...` to enable LLM-driven `capture`. For other MCP
clients (Cursor, Windsurf, Cline, etc.) see [Installation](#-installation).

## 🚀 Features

### Core Capabilities
- **🎯 Code-Aware**: Understands code structure (functions, classes, methods)
- **🌳 Multi-Language**: Python (AST) + 13 languages via tree-sitter (TS, JS, Go, Rust, Java, C/C++, C#, Kotlin, Ruby, PHP, Swift, Scala) + Markdown
- **🔍 Semantic Search**: Vector embeddings with FAISS similarity search
- **🔐 Security-First**: Secrets detection with auto-sanitization (covers cross-branch memory copies too, v0.4+)
- **📂 Git-Aware**: Automatic branch isolation + linked-worktree-canonical `repo_id` (v0.6)
- **⏱ Commit-Aware** *(v0.4)*: Every memory carries the SHA it was minted at; append-only event log enables time-travel recall and branch-merge unions
- **🕸 Memory Graph** *(v0.5)*: Typed edges (`IMPORTS` / `CALLS` / `EXTENDS` / `IMPLEMENTS` / `USES` / `DECORATED_BY`) across Python and all 13 tree-sitter languages; intra-class `self`/`this` calls resolve to the owning method (v0.16+); Louvain communities; symbol resolver with bounded fuzzy match
- **📐 Diagrams** *(v0.13+)*: `generate_diagram` emits Mermaid straight from the code graph — deterministic class / call / module diagrams, plus LLM-synthesized sequence / use-case / state / ERD (passthrough-aware, rendered in chat). Also a `/diagram` panel in the web UI
- **🌐 Web UI** *(v0.6, optional)*: Localhost-only D3-force graph + paginated table + time-travel slider via `mememo serve`
- **⚡ Incremental**: Only re-index changed files (Merkle DAG)
- **🤖 Passive Hooks**: Auto-capture memories, inject context, and (v0.6) augment Grep/Glob/Bash results — no manual invocation
- **🧠 Smart Context**: Intent-aware injection with dynamic token budgets (22-43% savings)
- **🧹 Manual Cleanup**: Controlled memory cleanup with dry-run preview (no silent auto-expiry)
- **🔗 Orchestrator Support**: Works as a subprocess for external tools (borch, mageNT) via `repo_path` override and non-git directory fallback
- **📦 Batch Operations**: Bulk memory storage with single-pass embedding and indexing

### Passive Hooks (Claude Code)

mememo integrates with Claude Code hooks to make memory fully automatic:

**Stop hook** — fires asynchronously after every Claude response. Reads the conversation transcript, extracts memorable facts via LLM, and stores them. No `capture` call needed.

**UserPromptSubmit hook** — fires synchronously before Claude processes each message. Runs a semantic search against your memory store and injects relevant results as a system message, within a configurable token budget (800 tokens by default). Nothing is injected if no results exceed the similarity threshold.

See [hooks/README.md](hooks/README.md) for setup instructions.

### Smart Context Selection

When `MEMEMO_SMART_CONTEXT_ENABLED=true` (default), the inject hook uses intent-aware context selection instead of fixed-budget injection:

- **Intent classification**: Classifies each prompt into one of 6 categories (coding, debugging, architecture, testing, review, general) using embedding cosine similarity against pre-computed centroids. No LLM call, ~0.01ms latency.
- **Adaptive budget**: Token budget dynamically scales based on result quality. High-relevance matches get more context, low-relevance queries get less noise.
- **Progressive compression**: Memories are formatted at three tiers based on relevance — full text, one-line summary with location, or one-line summary only.
- **Skill injection**: Reusable prompt templates stored as YAML files are automatically injected based on the detected intent. Manage skills via the `manage_skill` MCP tool.
- **Response compression**: The capture hook preprocesses transcripts to strip tool blocks, progress bars, and redundant content before extraction — 55% token reduction on typical transcripts.

Set `MEMEMO_SMART_CONTEXT_ENABLED=false` to revert to the legacy fixed-budget behavior.

### Supported Languages

| Language | Extensions | Chunker | Features |
|----------|-----------|---------|----------|
| Python | `.py`, `.pyi`, `.pyx` | AST | Functions, classes, methods, decorators, docstrings |
| TypeScript | `.ts`, `.tsx` | Tree-sitter | Functions, classes, interfaces |
| JavaScript | `.js`, `.jsx` | Tree-sitter | Functions, classes |
| Go | `.go` | Tree-sitter | Functions, methods, structs, interfaces |
| Rust | `.rs` | Tree-sitter | Functions, impl blocks, structs, traits |
| Java | `.java` | Tree-sitter | Classes, methods, interfaces |
| C/C++ | `.c`, `.cpp`, `.h`, `.hpp` | Tree-sitter | Functions, classes, structs |
| C# | `.cs` | Tree-sitter | Classes, methods, interfaces |
| Kotlin | `.kt`, `.kts` | Tree-sitter | Classes, methods, objects |
| Ruby | `.rb` | Tree-sitter | Classes, modules, methods, mixins |
| PHP | `.php` | Tree-sitter | Classes, interfaces, methods |
| Swift | `.swift` | Tree-sitter | Classes, structs, protocols, methods |
| Scala | `.scala` | Tree-sitter | Classes, traits, objects, methods |

All tree-sitter languages emit the typed-edge graph (CALLS / EXTENDS / IMPLEMENTS / IMPORTS / USES) that powers semantic recall, call graphs, and diagrams.

### Diagrams

`generate_diagram` turns the indexed code graph into [Mermaid](https://mermaid.js.org/):

| Type | Source | Where |
|------|--------|-------|
| `class` | deterministic (graph) | chat + web `/diagram` |
| `call` | deterministic (graph) | chat + web `/diagram` |
| `module` | deterministic (graph) | chat + web `/diagram` |
| `sequence` / `usecase` / `state` / `erd` | LLM-synthesized from the graph + source | chat (passthrough-aware) |

```
generate_diagram(type="class", scope="auth/service.py")   # class diagram for a file
generate_diagram(type="call",  scope="login")             # call graph rooted at a function
generate_diagram(type="sequence", scope="checkout")       # host model renders the Mermaid
```

The deterministic types are exact (read straight from the edge graph); the LLM types return a ready-to-render prompt when no API model is configured, so the host model (e.g. Claude Code) draws them inline. The web UI (`mememo serve`) renders the deterministic types in a `/diagram` panel. Index a repo first (`mememo index <path>`).

## 🤔 Why mememo?

Unlike general-purpose AI memory solutions, mememo is **purpose-built for code**:

| Feature | mememo | General Memory Tools |
|---------|--------|---------------------|
| **Code Structure Awareness** | ✅ AST + tree-sitter for 14 languages | ❌ Text-only indexing |
| **Git Branch Isolation** | ✅ Automatic per-branch context | ❌ No version control awareness |
| **Deployment** | ✅ Local-first, zero external dependencies | ☁️ Cloud-based or complex setup |
| **Incremental Indexing** | ✅ Merkle DAG (5-10x faster re-indexing) | ❌ Full corpus re-indexing |
| **MCP Native** | ✅ Built for Claude Desktop, Cursor, Cline | ⚙️ Requires API adapters |
| **Privacy** | ✅ 100% local, your data stays on your machine | ❌ Cloud storage or hybrid |
| **Passive Memory** | ✅ Auto-capture + inject via Claude Code hooks | ❌ Manual invocation required |

**mememo is ideal if you:**
- 👨‍💻 Use AI assistants (Claude, Cursor, Cline) for coding
- 🔐 Need private, local code memory without cloud dependencies
- 🌳 Work on git repositories with multiple branches
- ⚡ Want fast incremental updates (not full re-indexing)
- 🎯 Need AI that understands code structure (functions, classes), not just keywords

## 💡 Use Cases

### For Individual Developers
- **Context retention**: AI remembers your project architecture across sessions
- **Branch awareness**: Different context for feature branches vs main
- **Fast iteration**: Re-index only changed files when you edit code

### For Teams
- **Shared knowledge**: Team members get consistent code context
- **Onboarding**: New developers get instant codebase understanding
- **Documentation**: Semantic search through undocumented legacy code

### For AI Tool Builders
- **Code search**: Power semantic code discovery features
- **Context injection**: Provide relevant code snippets to LLMs
- **Codebase Q&A**: Answer questions about architecture and patterns

## 📦 Installation

**Prerequisites**: Python 3.10+

### Quick Install

```bash
# Production
bash install.sh                 # Linux/macOS (Claude Desktop)
install.bat                     # Windows (Claude Desktop)

# Development (includes testing tools)
bash install.sh --dev           # Linux/macOS
install.bat --dev               # Windows

# Other clients
bash install.sh -c claude                    # Claude Code (workspace-local)
bash install.sh -c claude --global           # Claude Code (global)
bash install.sh -c cursor                    # Cursor (workspace-local)
bash install.sh -c cursor --global           # Cursor (global)
bash install.sh -c windsurf                  # Windsurf (global only)
bash install.sh -c vscode                    # VS Code (.vscode/mcp.json)
bash install.sh -c gemini                    # Gemini CLI (workspace-local)
bash install.sh -c gemini --global           # Gemini CLI (global)
bash install.sh -c codex                     # OpenAI Codex CLI (workspace-local)
bash install.sh -c codex --global            # OpenAI Codex CLI (global)
bash install.sh -c zed                       # Zed (global)
bash install.sh -c kilo                      # Kilo Code
bash install.sh -c opencode                  # OpenCode (workspace-local)
bash install.sh -c opencode --global         # OpenCode (global)
bash install.sh -c goose                     # Goose
bash install.sh -c all                       # all detected clients
```

This creates a virtual environment at `.venv` and installs all dependencies.

The bundled `install.sh` / `install.bat` auto-detect [`uv`](https://docs.astral.sh/uv/):
when present they create the venv with uv (falling back to a pinned CPython 3.12
when the system Python is outside the supported 3.10–3.14 range) and install with
`uv pip`. Plain `python -m venv` + `pip` is used when uv is absent.

### Manual Install

With uv (recommended — downloads CPython if the system one is unsupported):

```bash
uv venv --python 3.12          # or any 3.10-3.14; omit --python to use system
uv pip install -e ".[web,graph]"
```

Or with stock venv + pip:

```bash
python3 -m venv .venv
source .venv/bin/activate      # Linux/macOS
# or: .venv\Scripts\activate   # Windows

pip install -e .                       # Production (MCP server, hooks, CLI)
pip install -e ".[dev]"                # Development (testing + linting)
pip install -e ".[web]"                # + FastAPI web UI (`mememo serve`)
pip install -e ".[graph]"              # + Louvain clustering + entity dedup
pip install -e ".[web,graph,dev]"      # Everything
```

The base install ships the MCP server, every CLI subcommand except `serve`,
the passive hooks, and the graph traversal tools (`graph_neighbors`,
`graph_path`, `graph_impact`, `cypher_query`). The `[web]` extra is only
needed for `mememo serve`. The `[graph]` extra is only needed if you call
`cluster_relations` or `dedup_entities` — the graph traversal tools work
without it.

Installer flags:

```bash
bash install.sh --with-web              # base + [web]
bash install.sh --with-graph            # base + [graph]
bash install.sh --dev --with-web --with-graph   # everything
```

### Configuration

Set environment variables (optional):

```bash
# Storage location (default: ~/.mememo)
export MEMEMO_STORAGE_DIR="$HOME/.mememo"

# Embedding model (default: minilm)
export MEMEMO_EMBEDDING_MODEL="minilm"  # or "gemma"

# Device (default: auto-detect)
export MEMEMO_EMBEDDING_DEVICE="auto"  # or "cuda", "mps", "cpu"

# Route the first-run model download through the OS trust store (default: on)
# so it works behind a TLS-intercepting corporate proxy. Set to 0 to keep the
# stock certifi CA bundle.
export MEMEMO_USE_SYSTEM_CA="1"

# Security settings
export MEMEMO_SECRETS_DETECTION="true"
export MEMEMO_AUTO_SANITIZE="false"
export MEMEMO_ENABLE_AUDIT_LOG="false"   # appends to ~/.mememo/audit.jsonl

# Indexing (auto-reindex when snapshot is stale)
export MEMEMO_AUTO_REINDEX_AGE_MINUTES="5.0"
export MEMEMO_ENABLE_INCREMENTAL="true"
```

### Passive Hook Configuration

```bash
# Token budget for injected context per prompt (default: 800)
export MEMEMO_HOOK_INJECT_TOKEN_BUDGET="800"

# Min similarity to include a memory in the injected block (default: 0.25)
export MEMEMO_HOOK_INJECT_MIN_SIMILARITY="0.25"

# Broader search floor — candidates fetched at this threshold,
# then filtered down to INJECT_MIN_SIMILARITY (default: 0.2)
export MEMEMO_HOOK_INJECT_SEARCH_FLOOR="0.2"

# Transcript tail lines read by the Stop hook (default: 100)
export MEMEMO_HOOK_CAPTURE_LINES="100"

# Disable individual hooks
export MEMEMO_HOOK_CAPTURE_ENABLED="true"
export MEMEMO_HOOK_INJECT_ENABLED="true"

# TTL for conversation memories in days — 0 = no expiry (default: 0)
export MEMEMO_TTL_CONVERSATION_DAYS="0"

# TTL for context memories in days — 0 = no expiry (default: 0)
export MEMEMO_TTL_CONTEXT_DAYS="0"

# decision, analysis, and summary memories never auto-expire
# Use the cleanup_memory MCP tool for manual, controlled cleanup
```

### Smart Context Configuration

```bash
# Enable intent-aware adaptive context selection (default: true)
export MEMEMO_SMART_CONTEXT_ENABLED="true"

# Min confidence for intent classification; below this, falls back to 'general' (default: 0.3)
export MEMEMO_INTENT_CONFIDENCE_THRESHOLD="0.3"

# Dynamic token budget bounds (default: 200-1200, base 800)
export MEMEMO_HOOK_INJECT_TOKEN_BUDGET_MIN="200"
export MEMEMO_HOOK_INJECT_TOKEN_BUDGET_MAX="1200"

# Skill prompt injection (default: true, 200 token budget)
export MEMEMO_SKILL_INJECTION_ENABLED="true"
export MEMEMO_SKILL_TOKEN_BUDGET="200"

# Response compression before capture (default: true)
export MEMEMO_RESPONSE_COMPRESSION_ENABLED="true"

# Similarity threshold for dedup during capture (default: 0.85)
export MEMEMO_CAPTURE_DEDUP_SIMILARITY="0.85"
```

### Encryption (optional)

SQLite encryption requires an extra dependency:

```bash
pip install mememo[encryption]
```

Then set:

```bash
export MEMEMO_ENABLE_ENCRYPTION="true"
export MEMEMO_ENCRYPTION_KEY="your-key"
```

Without `mememo[encryption]`, setting `MEMEMO_ENABLE_ENCRYPTION=true` will silently fall back to plain SQLite.

### Similarity Scores

`search_similar` returns scores in the range 0.0–1.0, computed as `exp(-L2_distance)`. Practical guide:

| Score | Meaning |
|-------|---------|
| ≥ 0.9 | Near-identical content |
| 0.7–0.9 | Strongly related (default threshold) |
| 0.5–0.7 | Loosely related |
| < 0.5 | Unlikely to be relevant |

The default `min_similarity=0.7` (`MEMEMO_SEARCH_MIN_SIMILARITY`) filters out low-signal results. Lower it if you're getting too few results on a small repo.

### Incremental Indexing

mememo uses a Merkle DAG to track file changes between indexing runs:

- Each file's SHA-256 hash is stored in `~/.mememo/merkle/file_hashes.json`
- On `index_repository` with `incremental=true`, only files whose hash has changed since the last run are re-chunked and re-embedded
- If the snapshot is older than `MEMEMO_AUTO_REINDEX_AGE_MINUTES` (default: 5 min), a full re-index is forced automatically even when `incremental=true`
- The FAISS index is sharded at 50,000 vectors per shard; LRU eviction kicks in when `max_total_memories` is reached

## 🎮 Quick Start

### Step 1: Install

```bash
bash install.sh                        # Linux/macOS
install.bat                            # Windows
```

### Step 2: Connect to your AI assistant

#### Claude Desktop (auto-configure)

```bash
bash install.sh -c claudedesktop     # Linux/macOS
install.bat -c claudedesktop         # Windows
```

Then **restart Claude Desktop** — mememo is launched automatically, no manual server start needed.

#### Claude Code CLI (auto-configure)

```bash
bash install.sh -c claude        # Linux/macOS
install.bat -c claude            # Windows
```

Or manually:
```bash
claude mcp add mememo -- /path/to/mememo/.venv/bin/python -m mememo
# Windows:
claude mcp add mememo -- C:\path\to\mememo\.venv\Scripts\python.exe -m mememo
```

Add for all projects (user scope):
```bash
claude mcp add --scope user mememo -- /path/to/mememo/.venv/bin/python -m mememo
```

Verify it's registered: `claude mcp list`

#### Step 3 (Claude Code only): Enable passive hooks

Copy `hooks/hooks.json` into your Claude Code hooks config and replace the path placeholder:

```bash
# Update the path in hooks.json
sed -i 's|/path/to/mememo|/absolute/path/to/mememo|g' hooks/hooks.json
```

Then merge the contents into `~/.claude/settings.json` under the `hooks` key. See [hooks/README.md](hooks/README.md) for full instructions including Windows setup.

#### Cursor (auto-configure)

```bash
bash install.sh -c cursor        # workspace-local
bash install.sh -c cursor --global  # global
```

#### Windsurf (auto-configure)

```bash
bash install.sh -c windsurf      # global only
```

#### VS Code (auto-configure)

```bash
bash install.sh -c vscode        # workspace-local .vscode/mcp.json
```

#### Gemini CLI (auto-configure)

```bash
bash install.sh -c gemini        # workspace-local
bash install.sh -c gemini --global  # global
```

#### OpenAI Codex CLI (auto-configure)

```bash
bash install.sh -c codex         # workspace-local
bash install.sh -c codex --global   # global
```

#### Zed (auto-configure)

```bash
bash install.sh -c zed           # global only
```

#### Kilo Code (auto-configure)

```bash
bash install.sh -c kilo        # Linux/macOS
install.bat -c kilo            # Windows
```

Writes `.kilocode/mcp.json` in the parent workspace directory.

#### OpenCode (auto-configure)

```bash
bash install.sh -c opencode              # workspace-local
bash install.sh -c opencode --global     # global (~/.config/opencode/opencode.json)
```

#### Goose (auto-configure)

```bash
bash install.sh -c goose       # Linux/macOS
install.bat -c goose           # Windows
```

Writes to `~/.config/goose/config.yaml` (global). Requires PyYAML (included in mememo's dependencies).

#### All detected clients

```bash
bash install.sh -c all         # Linux/macOS
install.bat -c all             # Windows
```

Configures all clients whose config files already exist. Desktop and Code are always attempted.

## Supported MCP Clients

| Client | `-c TYPE` | Config written | Notes |
|--------|-----------|----------------|-------|
| Claude Desktop | `claudedesktop` | OS-specific `claude_desktop_config.json` | Restart required |
| Claude Code | `claude` | `.mcp.json` (workspace) or user scope via `claude mcp add` | Use `--global` for user scope |
| Cursor | `cursor` | `.cursor/mcp.json` or `~/.cursor/mcp.json` (global) | Use `--global` for global |
| Windsurf | `windsurf` | `~/.codeium/windsurf/mcp_config.json` | Global only |
| VS Code | `vscode` | `.vscode/mcp.json` | Workspace-local; global via VS Code settings UI |
| Gemini CLI | `gemini` | `.gemini/settings.json` or `~/.gemini/settings.json` (global) | Use `--global` for global |
| Codex CLI | `codex` | `.codex/config.toml` or `~/.codex/config.toml` (global) | TOML; use `--global` for global |
| Zed | `zed` | `~/.config/zed/settings.json` | Global only |
| Kilo Code | `kilo` | `.kilocode/mcp.json` | Workspace-local only |
| OpenCode | `opencode` | `opencode.json` / `~/.config/opencode/opencode.json` | Use `--global` for global |
| Goose | `goose` | `~/.config/goose/config.yaml` | Global only |
| pi.dev | `pidev` | n/a | Prints manual instructions; no auto-config |
| All above | `all` | All detected existing configs | Skips clients not yet installed |

**Backward-compatible aliases** (still work):
- `--configure=claude` → same as `-c claudedesktop`
- `--configure=claudecli` → same as `-c claude`

### Installer Flags

```
  -c, --client TYPE   claudedesktop, claude, cursor, windsurf, vscode, gemini, codex,
                      zed, kilo, opencode, goose, pidev, all  (default: none)
  -f, --force         Skip prompts, overwrite existing config
  -u, --uninstall     Remove from MCP client config and virtual environment
      --upgrade       Upgrade existing installation (alias: --update)
      --status        Show where this server is currently installed
      --global        Use global config path (claude, cursor, gemini, codex, opencode)
      --skip-test     Skip warmup validation step
      --dev           Install dev/test dependencies
  -h, --help          Show this help
```

Check install status:
```bash
bash install.sh --status
```

Upgrade (pull the latest source first, or re-download and extract, then):
```bash
bash install.sh --upgrade
bash install.sh --upgrade -c all   # also reconfigure all clients
```

> **Upgrading to v0.16.0+ from an older index:** Python method→class linkage and
> `self.x()` call resolution changed how methods are indexed. Existing indexes
> won't show methods in class diagrams or intra-class call edges until you
> re-index: `mememo index <repo> --full`.

**First-run download behind a corporate proxy?** If the initial model download
fails with `CERTIFICATE_VERIFY_FAILED`, mememo routes SSL through the OS trust
store (which holds your proxy's root CA) automatically. If you need to disable
that and use the stock CA bundle, set `MEMEMO_USE_SYSTEM_CA=0`.

### Manual MCP Config

Use absolute paths. Linux/macOS python: `/path/to/mememo/.venv/bin/python` — Windows: `C:\path\to\mememo\.venv\Scripts\python.exe`

#### Claude Desktop

**Windows:** `%APPDATA%\Claude\claude_desktop_config.json`
**macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "mememo": {
      "command": "/absolute/path/to/mememo/.venv/bin/python",
      "args": ["-m", "mememo"]
    }
  }
}
```

#### Cursor

`.cursor/mcp.json` (workspace) or `~/.cursor/mcp.json` (global):
```json
{
  "mcpServers": {
    "mememo": {
      "command": "/absolute/path/to/mememo/.venv/bin/python",
      "args": ["-m", "mememo"]
    }
  }
}
```

#### Windsurf

`~/.codeium/windsurf/mcp_config.json`:
```json
{
  "mcpServers": {
    "mememo": {
      "command": "/absolute/path/to/mememo/.venv/bin/python",
      "args": ["-m", "mememo"]
    }
  }
}
```

#### VS Code

`.vscode/mcp.json` in your workspace root (uses `servers`, not `mcpServers`):
```json
{
  "servers": {
    "mememo": {
      "type": "stdio",
      "command": "/absolute/path/to/mememo/.venv/bin/python",
      "args": ["-m", "mememo"]
    }
  }
}
```

#### VS Code — Cline

Open Cline extension settings → MCP Servers → Add:
```json
{
  "mememo": {
    "command": "/absolute/path/to/mememo/.venv/bin/python",
    "args": ["-m", "mememo"]
  }
}
```

#### VS Code — Continue.dev

`~/.continue/config.json` under `mcpServers`:
```json
{
  "mcpServers": [
    {
      "name": "mememo",
      "command": "/absolute/path/to/mememo/.venv/bin/python",
      "args": ["-m", "mememo"]
    }
  ]
}
```

#### Gemini CLI

`.gemini/settings.json` (workspace) or `~/.gemini/settings.json` (global):
```json
{
  "mcpServers": {
    "mememo": {
      "command": "/absolute/path/to/mememo/.venv/bin/python",
      "args": ["-m", "mememo"]
    }
  }
}
```

#### OpenAI Codex CLI

`.codex/config.toml` (workspace) or `~/.codex/config.toml` (global):
```toml
[mcp_servers.mememo]
command = "/absolute/path/to/mememo/.venv/bin/python -m mememo"
startup_timeout_sec = 30
tool_timeout_sec = 300
enabled = true
```

#### Zed

`~/.config/zed/settings.json`:
```json
{
  "context_servers": {
    "mememo": {
      "command": {
        "path": "/absolute/path/to/mememo/.venv/bin/python",
        "args": ["-m", "mememo"],
        "env": {}
      }
    }
  }
}
```

#### Kilo Code

`.kilocode/mcp.json` in your workspace root:
```json
{
  "mcpServers": {
    "mememo": {
      "command": "/absolute/path/to/mememo/.venv/bin/python",
      "args": ["-m", "mememo"]
    }
  }
}
```

#### OpenCode

`opencode.json` (workspace) or `~/.config/opencode/opencode.json` (global):
```json
{
  "mcp": {
    "mememo": {
      "command": "/absolute/path/to/mememo/.venv/bin/python",
      "args": ["-m", "mememo"]
    }
  }
}
```

#### Goose

`~/.config/goose/config.yaml`:
```yaml
extensions:
  mememo:
    type: stdio
    cmd: /absolute/path/to/mememo/.venv/bin/python
    args:
      - -m
      - mememo
    enabled: true
```

### Available MCP Tools

#### Memory Storage

| Tool | Purpose |
|------|---------|
| `store_memory` | Store code snippets, decisions, context, or analysis |
| `store_decision` | Store an architectural decision with rationale |
| `batch_store` | Store multiple memories in one call (batch embedding + indexing) |
| `capture` | Extract and store memorable facts from raw text via LLM; accepts pre-extracted memories; deduplicates at 0.97 similarity |
| `refresh_memory` | Update an existing memory's content |
| `delete_memory` | Delete a memory by ID |
| `cleanup_memory` | Manual cleanup: age-based, stale, or dedup (dry-run by default) |

#### Memory Retrieval

| Tool | Purpose |
|------|---------|
| `retrieve_memory` | Fetch a single memory by ID |
| `search_similar` | Semantic vector search with optional tag filtering (AND logic) |
| `list_memories` | List memories with filters (type, language, file, tags with AND logic) |
| `recall_context` | Search persistent memories (decisions, analysis, context) with optional tag filtering |
| `recent_context` | Fetch the most recently stored memories |
| `summarize_context` | Summarize stored memories by ID or raw text directly |
| `check_memory` | Show memory statistics |

#### Repository Indexing

| Tool | Purpose |
|------|---------|
| `index_repository` | Batch-index a codebase (incremental by default); v0.5+ also extracts typed edges into the memory graph |
| `sync_commits` | Sync recent git commits — marks affected code memories stale with risk grade and emits STALED events |
| `end_session` | Close the session and persist indexes |

#### Commit-Aware Time-Travel *(v0.4)*

| Tool | Purpose |
|------|---------|
| `detect_changes` | Map `git diff base..head` to affected memories with `WILL_BREAK` / `LIKELY_AFFECTED` / `MAY_NEED_TESTING` risk grades. Read-only — does not mutate `stale` flags |
| `recall_at_commit` | Time-travel semantic search: resolves a target SHA to its commit timestamp, replays the event log, and filters FAISS results to memories alive at that SHA |
| `merge_branch` | Union a source branch's alive memories into the target. Dedup by content checksum, emits RESTORED events at the merge SHA, runs secrets detection on every cross-branch copy |

#### Memory Graph *(v0.5)*

| Tool | Purpose |
|------|---------|
| `graph_neighbors` | Depth-limited BFS over typed edges with direction (`out` / `in` / `both`) and edge-type filter |
| `graph_path` | Shortest directed edge path between two memories within `max_depth`, or `null` if unreachable |
| `graph_impact` | Blast-radius BFS with `min_confidence` floor; each reached memory decorated with current `risk_grade` + file metadata. `direction='upstream'` finds callers / dependents |
| `cypher_query` | Documented Cypher subset (`MATCH (a)-[r:TYPE]->(b)`, `WHERE` with `=`/`<>`/`=~`/`AND`/`OR`, `RETURN`, `LIMIT`). Unsupported constructs raise a structured `error_kind="unsupported"` response |
| `search_similar` (extended) | New `cluster_id` parameter restricts results to memories whose relations live in the named community |

#### Smart Context

| Tool | Purpose |
|------|---------|
| `manage_skill` | Create, list, get, or delete skill prompt templates |

### MCP Resources *(v0.6)*

Four read-only resources that summarise the memory store without burning a tool call. Each payload is bounded to ≤4 KB; over-budget lists truncate and set a `truncated` marker.

| Resource | Content |
|----------|---------|
| `mememo://repo/{id}/stats` | Total + stale memory counts, stale fraction, relation count, community count, per-branch `last_indexed_sha` |
| `mememo://repo/{id}/stale` | Up to 50 most-recently-stale memories with `risk_grade` + `stale_reason` |
| `mememo://repo/{id}/branch/{name}/summary` | Per-branch memory + relation + event counts; current `last_indexed_sha` + `parent_sha` |
| `mememo://repo/{id}/community/{cid}` | Member memory ids + top-degree nodes for a clustering community |

### Web UI *(v0.6, optional)*

`pip install 'mememo[web]'` + `python -m mememo serve [--port 5757]` launches a localhost-only FastAPI app with:

- **D3-force graph view** colored by community, with drag + zoom
- **Paginated memories table** with risk-grade highlighting
- **Time-travel slider** — paste a SHA prefix and both views filter server-side to memories alive at that commit

Refuses to bind to anything other than `127.0.0.1` / `localhost`. The MCP server runs independently without these extras.

### CLI Subcommands

| Command | Purpose |
|---------|---------|
| `python -m mememo` | Start the MCP server (stdio) |
| `python -m mememo serve [--port 5757]` | Launch the localhost web UI (requires `[web]` extra) |
| `python -m mememo install-git-hooks --repo-path <p> [--with-pretool] [--force]` | Install opt-in post-merge / post-commit / (optional) PreToolUse hooks |
| `python -m mememo migrate-worktrees --repo-path <p> [--dry-run]` | Re-key legacy per-worktree `repo_id`s onto the canonical one |
| `python -m mememo merge-branch --repo-path <p> --source <b> --target <b>` | CLI shim over the `merge_branch` MCP tool — invoked by the post-merge hook |
| `python -m mememo sync-commits --repo-path <p>` | CLI shim over the `sync_commits` MCP tool — invoked by the post-commit hook |
| `python -m mememo capture --hook` / `inject --hook` / `pre-tool --hook` | Hook entry points consumed by Claude Code |
| `python -m mememo session-start --hook` | SessionStart hook: recall memories at session open (see [SessionStart hook](#sessionstart-hook)) |
| `python -m mememo import-md <dir> [--repo <path>] [--dry-run]` | Ingest existing markdown memory files (see [Importing existing memories](#importing-existing-memories)) |
| `python -m mememo reindex-identity [--dry-run]` | Recompute repo IDs via the live resolver; move FAISS dirs to match |

#### Orchestrator Integration

All tools accept an optional `repo_path` parameter to override cwd-based git detection. When mememo runs as a subprocess (spawned by borch, mageNT, or other orchestrators), `repo_path` ensures the correct repository context is used regardless of the process working directory. mememo also gracefully handles non-git directories by falling back to a default context.

#### Example usage

```python
# Store a decision
store_decision({
  "problem": "Vector search backend",
  "alternatives": ["FAISS", "ChromaDB", "Pinecone"],
  "chosen": "FAISS",
  "rationale": "Local, no network dependency, supports sharding",
  "tags": ["architecture", "search"]
})

# Semantic search with tag filtering (AND logic)
search_similar({
  "query": "function that processes data",
  "top_k": 5,
  "min_similarity": 0.7,
  "tags": ["architecture"]
})

# Index a repo
index_repository({
  "repo_path": "/path/to/repo",
  "file_patterns": ["**/*.py", "**/*.ts"],
  "incremental": true
})

# Batch store multiple memories at once
batch_store({
  "memories": [
    {"content": "API rate limiting added", "type": "context", "tags": ["api"]},
    {"content": "Error handler middleware", "type": "context", "tags": ["middleware"]}
  ]
})

# Capture with pre-extracted memories (no LLM needed)
capture({
  "pre_extracted": [
    {"type": "decision", "content": "Chose Redis for caching", "tags": ["infra"]},
    {"type": "context", "content": "CI runs in 3 minutes", "tags": ["ci"]}
  ]
})

# Summarize raw text directly
summarize_context({
  "text": "Long agent output to summarize...",
  "max_tokens": 500
})

# List memories by filter with tag AND logic
list_memories({
  "language": "python",
  "tags": ["api"],
  "limit": 50
})
```

## Portable Project Identity

mememo derives a stable 16-char `repo_id` for every repository. The id is
host-agnostic: it is based on the normalized `owner/repo` path, not the full
remote URL, so the same repository resolves to the same id regardless of how
it was cloned.

### Precedence (highest to lowest)

1. `MEMEMO_REPO_ID` env var — hard override, useful in CI.
2. `project_id` field in `.mememo/project.yaml` — checked into the repo.
3. SHA-256 of the normalized remote URL — handles SSH/HTTPS equivalence.
4. SHA-256 of the local repo path — fallback when no remote is configured.

### SSH and HTTPS resolve to the same id

Both of these point to the same index:

```
git@github.com:owner/repo.git   →  owner/repo  →  <same sha-256[:16]>
https://github.com/owner/repo   →  owner/repo  →  <same sha-256[:16]>
```

SSH host aliases defined in `~/.ssh/config` (e.g. `git@gh-kitty:`) are
resolved to their canonical hostname before normalization.

### Escape hatches

**Monorepo / multiple remotes**: set `project_id` in `.mememo/project.yaml`:

```yaml
# .mememo/project.yaml
project_id: "my-monorepo-service-a"
```

**Cross-host collision** (same `owner/repo` on GitHub and a private GitLab):
use `project_id` or `MEMEMO_REPO_ID` to assign distinct ids.

### Global lane

Memories stored without a git context (non-repo directories, process/project
notes, cross-cutting decisions) go into the **global lane** (`repo_id =
"__global__"`). The SessionStart hook and workspace recall always include the
global lane so these memories surface regardless of which repo is active.

Store a memory in the global lane by calling any storage tool from a
non-git directory, or pass `repo_path` pointing at a non-git path.

## Workspace Recall

Run mememo from a **parent directory** that contains multiple repos and it
recalls memories across all of them in one shot.

### How it works

`discover_workspace` checks the current working directory:

- If `cwd` is itself a git repo, it returns `[cwd]` immediately.
- Otherwise it scans immediate children for `.git` dirs, capped at
  `MEMEMO_WORKSPACE_MAX_REPOS` (default: 8).

Cross-location dependencies (repos not under the parent dir) are declared in
`.mememo/workspace.yaml`:

```yaml
# .mememo/workspace.yaml  — place this in the parent/workspace dir
projects:
  - /absolute/path/to/other-repo
  - ../relative/path/to/sibling-repo
```

The global lane is always included, regardless of workspace size.

### Environment variables

```bash
# Max repos scanned per workspace (default: 8)
export MEMEMO_WORKSPACE_MAX_REPOS=8

# Token budget for recalled memories at session start (default: 600)
export MEMEMO_HOOK_SESSION_START_TOKEN_BUDGET=600

# Min similarity for session-start recall (default: 0.2)
export MEMEMO_HOOK_SESSION_START_MIN_SIMILARITY=0.2

# Disable session-start recall entirely
export MEMEMO_HOOK_SESSION_START_ENABLED=false
```

## SessionStart Hook

The SessionStart hook recalls relevant memories from the workspace at the
start of every Claude Code session and injects them via `additionalContext`.
It runs asynchronously — it does not delay Claude Code from opening the
session.

**Important**: CLAUDE.md injection is controlled by the Claude Code harness.
mememo AUGMENTS the session context via the hook; it does not replace or
compete with harness-injected CLAUDE.md content.

### Register the hook

**Automatic** (recommended):

```bash
python -m mememo install-git-hooks --repo-path <repo> --with-session-start
```

**Manual** — add this to `~/.claude/settings.json` under `hooks`:

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python -m mememo session-start --hook",
            "async": true
          }
        ]
      }
    ]
  }
}
```

`register_claude_session_start_hook(repo_path)` from
`mememo.hooks.installer` writes this block programmatically. It is
idempotent: running it twice does not duplicate the entry. If a
`SessionStart` block already exists from another tool, pass `force=True` to
append instead of skipping.

### What gets injected

The hook searches the current workspace repos plus the global lane, then
formats the results as:

```
Memories from previous sessions:
- [decision] Chose FAISS over ChromaDB — local, no network dep
- [global] [context] Deploy pipeline requires manual approval on main
- [myother-repo] [context] API rate limit is 1000 req/min per token
```

Global-lane memories are prefixed `[global]`; memories from repos outside
the current workspace are prefixed with the repo name.

## Importing Existing Memories

`import-md` ingests a directory tree of `.md` files (e.g. the
`~/.claude/projects/.../memory/` files written by Claude's built-in memory
feature) into the mememo store.

```bash
# Ingest global process/project notes (no git context — goes to global lane)
python -m mememo import-md ~/.claude/projects/my-project/memory/

# Scope to a specific repo
python -m mememo import-md ~/.claude/projects/my-project/memory/ \
  --repo /path/to/my-repo

# Dry run: parse and check without writing
python -m mememo import-md ~/.claude/projects/my-project/memory/ --dry-run
```

### Type mapping

The `type:` field in YAML frontmatter controls the mememo `content_type`:

| Frontmatter `type` | mememo `content_type` |
|--------------------|-----------------------|
| `decision`         | `decision`            |
| `project`          | `context`             |
| `user`             | `context`             |
| `feedback`         | `context`             |
| `reference`        | `relationship`        |
| (anything else)    | `context`             |

The original `type` value is preserved as a `source_type:<value>` tag.

### Idempotency

A file is skipped when a memory with the same `file_path` AND content
checksum already exists in the store. Re-running `import-md` on an unchanged
directory is always a no-op. Editing a file produces a new memory; the old
one is not auto-staled (use `cleanup_memory` for dedup).

`[[wikilink]]` references in the body are stored as `REFERENCES` edges
between memories.

## Auto-Migration (Portable Identity Upgrade)

On first startup after upgrading to this version, mememo runs a one-time
background migration that recomputes every stored `repo_id` using the new
host-agnostic resolver and moves FAISS vector-index directories to match.

- **Non-blocking**: runs in a daemon thread; the server is fully usable while
  migration is in progress.
- **Guarded**: the `schema_meta.identity_migrated` flag ensures the migration
  runs exactly once. Subsequent startups skip it immediately.
- **FAISS conflicts**: if a target FAISS dir already exists (e.g. you had two
  clones with different old ids that now converge), embedding pointers are
  cleared so the affected repo is re-embedded on next use.

### Manual escape hatch

```bash
# Inspect what would change without mutating anything
python -m mememo reindex-identity --dry-run

# Apply the migration (normally automatic, but useful after a path change)
python -m mememo reindex-identity
```

### Durability note

Keep your `.md` memory files (e.g. `~/.claude/projects/.../memory/*.md`)
until Phase 2 confirms the migration is stable. They serve as an offline
fallback and can be re-imported via `import-md` if needed.

## 🔧 Architecture

```
mememo/
├── server.py              # FastMCP server (26 MCP tools + 6 resources)
├── cli.py                 # Hook CLI (capture --hook, inject --hook)
├── core/                  # Core managers
│   ├── memory_manager.py  # Orchestrates all memory ops
│   ├── llm_adapter.py     # Multi-provider LLM abstraction
│   ├── storage_manager.py # SQLite + JSON blob storage
│   ├── vector_index.py    # FAISS vector index (sharded)
│   └── git_manager.py     # Git context detection
├── context/               # Smart context selection
│   ├── intent_classifier.py   # Embedding-based intent classification
│   ├── adaptive_builder.py    # Dynamic budget context assembly
│   ├── skill_store.py         # YAML skill template management
│   └── response_compressor.py # Transcript preprocessing for capture
├── chunking/              # Code-aware chunking (AST + tree-sitter)
├── embeddings/            # Sentence transformers (MiniLM / Gemma)
├── indexing/              # Merkle DAG for incremental indexing
├── tools/                 # MCP tool implementations
├── types/                 # Pydantic models (config, memory)
├── utils/                 # Token counter, secrets detector, hashing
└── hooks/                 # Claude Code passive hook scripts
    ├── stop.sh            # Stop hook wrapper
    ├── user-prompt.sh     # UserPromptSubmit hook wrapper
    ├── hooks.json         # Hook config template
    └── README.md          # Hook setup instructions
```

## 📊 Performance

- Startup: ~100ms
- Embedding: ~20ms/chunk
- Search: <10ms for 10k memories
- Index 1000 files: ~2-5 min
- Intent classification: ~0.01ms (cached centroids)
- Adaptive context build: ~0.3ms
- Skill store query: ~0.07ms
- Response compression: ~0.13ms
- Token savings (inject): 22-43% vs legacy fixed-budget
- Token savings (capture): ~55% via transcript preprocessing

## 🧪 Testing

```bash
# Install dev dependencies
bash install.sh --dev

# Run tests
pytest tests/ -v
```

## 📄 License

MIT License

---

**mememo** - Code-aware memory for AI Assistants 🧠
