"""
mememo v0.1.0 - Code-aware memory for Claude

All-Python MCP server with:
- Multi-language code parsing (Python, TypeScript, Go, Rust, Java, C/C++, C#)
- Git-aware branch isolation
- Semantic vector search (FAISS)
- Security-first (secrets detection)
- Incremental indexing (Merkle DAG)
"""

__version__ = "0.1.0"

from .server import mcp, run

__all__ = ["mcp", "run", "__version__"]
