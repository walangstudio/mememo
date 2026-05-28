"""
Base chunker interface and data models.

Defines the abstract interface for all code chunkers.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal

ChunkType = Literal["function", "method", "class", "module", "import", "text", "heading"]

# v0.5 typed-edge taxonomy (FR-013, FR-014). Stored in the relations table.
# v0.7 adds DOCUMENTS (doc section -> code symbol). Edge-type values are no
# longer constrained at the DB layer (the relations.type CHECK was dropped);
# this Literal + the RelationType Literal in types/memory.py are the source of
# truth, enforced by Pydantic before every insert.
EdgeType = Literal[
    "IMPORTS",
    "CALLS",
    "EXTENDS",
    "IMPLEMENTS",
    "USES",
    "DECORATED_BY",
    "DOCUMENTS",
    # v0.8: REFERENCES (doc section -> external URL). target_label IS the URL;
    # the resolver naturally leaves it as target_symbol with target_memory_id NULL.
    "REFERENCES",
]

# Edge confidence — every edge is born EXTRACTED. Resolver may downgrade
# to INFERRED (fuzzy match) or AMBIGUOUS (no resolution).
EdgeConfidence = Literal["EXTRACTED", "INFERRED", "AMBIGUOUS"]


@dataclass
class RawEdge:
    """A single typed edge emitted by a chunker, pre-resolution.

    `source_qualname` is the dotted-path identifier of the source chunk
    (e.g. ``foo.bar.MyClass.method``). `target_label` is the unresolved
    target — a name token from the AST. The symbol_resolver pass turns
    target_label into target_memory_id where possible.
    """

    source_qualname: str
    target_label: str
    edge_type: EdgeType
    confidence: EdgeConfidence = "EXTRACTED"


@dataclass
class Chunk:
    """
    Represents a code chunk with rich metadata.

    This is the fundamental unit of code-aware chunking.
    """

    # Core content
    text: str
    start_line: int
    end_line: int
    chunk_type: ChunkType

    # Code-aware metadata (NEW in v0.3.0)
    function_name: str | None = None
    class_name: str | None = None
    docstring: str | None = None
    decorators: list[str] | None = None
    parent_class: str | None = None
    language: str | None = None

    # Additional context
    file_path: str | None = None
    complexity: int | None = None  # Cyclomatic complexity (future)

    # Explicit symbol qualname for this chunk. When set, the indexer registers
    # the chunk's symbol under this exact string (instead of deriving it from
    # module/class/function). Lets a chunker keep its emitted edge
    # source_qualnames in sync with the registered symbol — required for
    # Markdown doc sections, whose slug-path qualname doesn't match the
    # module.heading-text derivation. None = derive as before.
    qualname: str | None = None

    def __repr__(self) -> str:
        if self.function_name:
            return f"Chunk(function={self.function_name}, lines={self.start_line}-{self.end_line})"
        elif self.class_name:
            return f"Chunk(class={self.class_name}, lines={self.start_line}-{self.end_line})"
        else:
            return f"Chunk({self.chunk_type}, lines={self.start_line}-{self.end_line})"


class BaseChunker(ABC):
    """
    Abstract base class for all code chunkers.

    Implementations:
    - PythonASTChunker: Uses Python's ast module
    - TreeSitterChunker: Uses tree-sitter for multi-language support
    - TextChunker: Fallback for unsupported files
    """

    @abstractmethod
    def chunk(self, code: str, file_path: str) -> list[Chunk]:
        """
        Chunk code into semantic units.

        Args:
            code: Source code content
            file_path: Path to file (for context)

        Returns:
            List of code chunks with metadata

        Raises:
            SyntaxError: If code cannot be parsed (should fallback to text)
        """
        pass

    def chunk_with_fallback(self, code: str, file_path: str) -> list[Chunk]:
        """
        Chunk code with automatic fallback to text chunking on error.

        Args:
            code: Source code content
            file_path: Path to file

        Returns:
            List of chunks (code-aware or text-based)
        """
        try:
            return self.chunk(code, file_path)
        except Exception:
            # Fallback to text chunking
            from .text_chunker import TextChunker

            text_chunker = TextChunker()
            return text_chunker.chunk(code, file_path)


class ChunkingConfig:
    """Configuration for chunking behavior."""

    def __init__(
        self,
        max_tokens: int = 500,
        overlap_tokens: int = 50,
        preserve_structure: bool = True,
        extract_docstrings: bool = True,
        extract_decorators: bool = True,
    ):
        """
        Initialize chunking configuration.

        Args:
            max_tokens: Maximum tokens per chunk
            overlap_tokens: Overlap between chunks
            preserve_structure: Try to keep logical units together
            extract_docstrings: Extract docstrings from functions/classes
            extract_decorators: Extract decorators from functions/classes
        """
        self.max_tokens = max_tokens
        self.overlap_tokens = overlap_tokens
        self.preserve_structure = preserve_structure
        self.extract_docstrings = extract_docstrings
        self.extract_decorators = extract_decorators
