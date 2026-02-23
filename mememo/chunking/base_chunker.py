"""
Base chunker interface and data models.

Defines the abstract interface for all code chunkers.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional, Literal


ChunkType = Literal["function", "method", "class", "module", "import", "text"]


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
    function_name: Optional[str] = None
    class_name: Optional[str] = None
    docstring: Optional[str] = None
    decorators: Optional[List[str]] = None
    parent_class: Optional[str] = None
    language: Optional[str] = None

    # Additional context
    file_path: Optional[str] = None
    complexity: Optional[int] = None  # Cyclomatic complexity (future)

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
    def chunk(self, code: str, file_path: str) -> List[Chunk]:
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

    def chunk_with_fallback(self, code: str, file_path: str) -> List[Chunk]:
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
        except Exception as e:
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
