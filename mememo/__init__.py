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

__all__ = ["mcp", "run", "__version__"]


def __getattr__(name: str):
    if name in ("mcp", "run"):
        from .server import mcp, run

        globals()["mcp"] = mcp
        globals()["run"] = run
        return globals()[name]
    raise AttributeError(f"module 'mememo' has no attribute {name!r}")
