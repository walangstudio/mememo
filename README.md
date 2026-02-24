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
bash install.sh                 # Linux/macOS
install.bat                     # Windows

# Development (includes testing tools)
bash install.sh --dev          # Linux/macOS
install.bat --dev              # Windows
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
bash install.sh --configure=claude     # Linux/macOS
install.bat --configure=claude         # Windows
```

Then **restart Claude Desktop** — mememo is launched automatically, no manual server start needed.

#### Claude Code CLI (auto-configure)

```bash
bash install.sh --configure=claudecli   # Linux/macOS
install.bat --configure=claudecli       # Windows
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

Add to `~/.continue/config.json` under `mcpServers`:
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

#### Cursor

Add to `~/.cursor/mcp.json` (create if it doesn't exist):
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

---

> **Path reference**:
> - **Linux/macOS**: `/path/to/mememo/.venv/bin/python`
> - **Windows**: `C:\path\to\mememo\.venv\Scripts\python.exe`

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
