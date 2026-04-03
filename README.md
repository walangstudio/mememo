# mememo 🧠

[![Python Version](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)
[![Tests](https://img.shields.io/badge/tests-passing-brightgreen.svg)](tests/)
[![MCP](https://img.shields.io/badge/MCP-compatible-purple.svg)](https://modelcontextprotocol.io/)

**Git-aware code memory for AI assistants** - MCP server that understands your codebase's structure, not just its text. Private, local, and built for developers.

## 🚀 Features

### Core Capabilities
- **🎯 Code-Aware**: Understands code structure (functions, classes, methods)
- **🌳 Multi-Language**: 15+ file extensions supported
- **🔍 Semantic Search**: Vector embeddings with FAISS similarity search
- **🔐 Security-First**: Secrets detection with auto-sanitization
- **📂 Git-Aware**: Automatic branch isolation
- **⚡ Incremental**: Only re-index changed files (Merkle DAG)
- **🤖 Passive Hooks**: Auto-capture memories and inject context without any user action
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

## 🤔 Why mememo?

Unlike general-purpose AI memory solutions, mememo is **purpose-built for code**:

| Feature | mememo | General Memory Tools |
|---------|--------|---------------------|
| **Code Structure Awareness** | ✅ AST + tree-sitter for 15+ languages | ❌ Text-only indexing |
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

### Manual Install

```bash
python3 -m venv .venv
source .venv/bin/activate      # Linux/macOS
# or: .venv\Scripts\activate   # Windows

pip install -e .               # Production
pip install -e ".[dev]"        # Development
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
| `index_repository` | Batch-index a codebase (incremental by default) |
| `sync_commits` | Sync recent git commits to update stale code memories |
| `end_session` | Close the session and persist indexes |

#### Smart Context

| Tool | Purpose |
|------|---------|
| `manage_skill` | Create, list, get, or delete skill prompt templates |

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

## 🔧 Architecture

```
mememo/
├── server.py              # FastMCP server (18 MCP tools)
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
