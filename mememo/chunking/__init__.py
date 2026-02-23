"""
Code-aware chunking for mememo.

Multi-language support:
- Python: AST-based parsing (functions, classes, decorators, docstrings)
- TypeScript/JavaScript/Go/Rust/Java/C/C++/C#: Tree-sitter parsing
- Other files: Token-based text chunking (fallback)
"""

from .base_chunker import BaseChunker, Chunk, ChunkType, ChunkingConfig
from .factory import ChunkerFactory
from .language_detector import (
    detect_language,
    get_chunker_type,
    get_language_info,
    get_supported_extensions,
    get_supported_languages,
    is_code_file,
)
from .python_ast_chunker import PythonASTChunker
from .text_chunker import TextChunker

# Only import tree-sitter chunker if available
try:
    from .tree_sitter_chunker import TreeSitterChunker, TREE_SITTER_AVAILABLE
except ImportError:
    TreeSitterChunker = None
    TREE_SITTER_AVAILABLE = False

__all__ = [
    # Core interfaces
    "BaseChunker",
    "Chunk",
    "ChunkType",
    "ChunkingConfig",
    # Factory (main entry point)
    "ChunkerFactory",
    # Chunker implementations
    "PythonASTChunker",
    "TreeSitterChunker",
    "TextChunker",
    # Language detection
    "detect_language",
    "get_chunker_type",
    "get_language_info",
    "get_supported_extensions",
    "get_supported_languages",
    "is_code_file",
    # Feature flags
    "TREE_SITTER_AVAILABLE",
]
