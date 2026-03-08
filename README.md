# mememo 🧠

[![Python Version](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)
[![Tests](https://img.shields.io/badge/tests-passing-brightgreen.svg)](tests/)
[![MCP](https://img.shields.io/badge/MCP-compatible-purple.svg)](https://modelcontextprotocol.io/)
[![Built with AI](https://img.shields.io/badge/built%20with-AI%20assistance-blueviolet.svg)](https://claude.ai)

**Git-aware code memory for AI assistants** - MCP server that understands your codebase's structure, not just its text. Private, local, and built for developers.

## 🚀 Features

### Core Capabilities
- **🎯 Code-Aware**: Understands code structure (functions, classes, methods)
- **🌳 Multi-Language**: 15+ file extensions supported
- **🔍 Semantic Search**: Vector embeddings with FAISS similarity search
- **🔐 Security-First**: Secrets detection with auto-sanitization
- **📂 Git-Aware**: Automatic branch isolation
- **⚡ Incremental**: Only re-index changed files (Merkle DAG)

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
```

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

#### 1. `store_memory` - Store code snippets

```python
{
  "content": "def example():\n    return 42",
  "type": "code_snippet",
  "language": "python",
  "file_path": "src/example.py",
  "tags": ["example"]
}
```

#### 2. `search_similar` - Semantic search

```python
{
  "query": "function that processes data",
  "top_k": 5,
  "min_similarity": 0.7
}
```

#### 3. `list_memories` - List with filters

```python
{
  "language": "python",
  "function_name": "process_data",
  "limit": 50
}
```

#### 4. `index_repository` - Batch indexing

```python
{
  "repo_path": "/path/to/repo",
  "file_patterns": ["**/*.py", "**/*.ts"],
  "incremental": true
}
```

#### 5-9. Other tools

- `retrieve_memory` - Get by ID
- `delete_memory` - Delete with confirmation
- `summarize_context` - Hierarchical summaries
- `check_memory` - Statistics
- `refresh_memory` - Update existing

## 🔧 Architecture

```
mememo/
├── server.py              # FastMCP server
├── core/                  # Core managers
├── chunking/              # Code-aware chunking
├── embeddings/            # Sentence transformers
├── indexing/              # Merkle DAG
└── tools/                 # 9 MCP tools
```

## 📊 Performance

- Startup: ~100ms
- Embedding: ~20ms/chunk
- Search: <10ms for 10k memories
- Index 1000 files: ~2-5 min

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
