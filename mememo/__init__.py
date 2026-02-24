"""
mememo - Code-aware memory for Claude

All-Python MCP server with:
- Multi-language code parsing (Python, TypeScript, Go, Rust, Java, C/C++, C#)
- Git-aware branch isolation
- Semantic vector search (FAISS)
- Security-first (secrets detection)
- Incremental indexing (Merkle DAG)
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("mememo")
except PackageNotFoundError:
    __version__ = "unknown"

from .server import mcp, run

__all__ = ["mcp", "run", "__version__"]
