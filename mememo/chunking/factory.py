"""
Chunker factory for auto-selecting the appropriate chunker.

Routes to the best chunker based on file type:
- Python → PythonASTChunker (ast module)
- TypeScript/Go/Rust/etc → TreeSitterChunker (tree-sitter)
- Unsupported files → TextChunker (fallback)
"""

import logging

from .base_chunker import BaseChunker, Chunk
from .language_detector import detect_language, get_chunker_type
from .python_ast_chunker import PythonASTChunker
from .text_chunker import TextChunker
from .tree_sitter_chunker import TREE_SITTER_AVAILABLE, TreeSitterChunker

logger = logging.getLogger(__name__)


class ChunkerFactory:
    """
    Factory for selecting and instantiating the appropriate chunker.

    Auto-selects based on file extension:
    - Python (.py) → PythonASTChunker
    - TypeScript/JavaScript/Go/Rust/Java/C/C++/C# → TreeSitterChunker
    - Other files → TextChunker (fallback)
    """

    def __init__(self):
        """Initialize chunker factory with lazy-loaded chunkers."""
        self._python_chunker: PythonASTChunker | None = None
        self._tree_sitter_chunker: TreeSitterChunker | None = None
        self._text_chunker: TextChunker | None = None

    def get_chunker(self, file_path: str) -> BaseChunker:
        """
        Get the appropriate chunker for a file.

        Args:
            file_path: Path to file

        Returns:
            BaseChunker instance (PythonASTChunker, TreeSitterChunker, or TextChunker)
        """
        # Detect language from file extension
        language = detect_language(file_path)

        if language is None:
            # Unknown file type - use text chunker
            logger.debug(f"Unknown language for {file_path}, using text chunker")
            return self._get_text_chunker()

        # Get recommended chunker type
        chunker_type = get_chunker_type(language)

        if chunker_type == "python_ast":
            # Python AST chunker
            logger.debug(f"Using Python AST chunker for {file_path}")
            return self._get_python_chunker()

        elif chunker_type == "tree_sitter":
            # Tree-sitter multi-language chunker
            if TREE_SITTER_AVAILABLE:
                logger.debug(f"Using tree-sitter chunker for {file_path} ({language})")
                return self._get_tree_sitter_chunker()
            else:
                # Tree-sitter not available - fallback to text
                logger.warning(f"Tree-sitter not available for {file_path}, using text chunker")
                return self._get_text_chunker()

        else:
            # Text chunker fallback
            logger.debug(f"Using text chunker for {file_path}")
            return self._get_text_chunker()

    def chunk_file(self, code: str, file_path: str, language: str | None = None) -> list[Chunk]:
        """
        Chunk a file with automatic chunker selection.

        This is the main entry point for chunking.

        Args:
            code: Source code content
            file_path: Path to file
            language: Optional language override (auto-detected if not provided)

        Returns:
            List of code chunks with metadata

        Raises:
            Exception: If chunking fails (caught and logged, returns text chunks)
        """
        # Get the appropriate chunker
        chunker = self.get_chunker(file_path)

        try:
            # For tree-sitter, we need to pass language parameter
            if isinstance(chunker, TreeSitterChunker):
                # Auto-detect language if not provided
                if language is None:
                    language = detect_language(file_path)

                return chunker.chunk(code, file_path, language=language)
            else:
                # Python AST and Text chunkers don't need language parameter
                return chunker.chunk(code, file_path)

        except Exception as e:
            # Chunking failed - fallback to text chunker
            logger.warning(f"Chunking failed for {file_path} with {type(chunker).__name__}: {e}")
            logger.debug("Falling back to text chunker")

            try:
                text_chunker = self._get_text_chunker()
                return text_chunker.chunk(code, file_path)
            except Exception as fallback_error:
                logger.error(f"Text chunker fallback also failed: {fallback_error}")
                # Return empty list as last resort
                return []

    def _get_python_chunker(self) -> PythonASTChunker:
        """Get or create Python AST chunker (lazy loading)."""
        if self._python_chunker is None:
            self._python_chunker = PythonASTChunker()
        return self._python_chunker

    def _get_tree_sitter_chunker(self) -> TreeSitterChunker:
        """Get or create tree-sitter chunker (lazy loading)."""
        if self._tree_sitter_chunker is None:
            self._tree_sitter_chunker = TreeSitterChunker()
        return self._tree_sitter_chunker

    def _get_text_chunker(self) -> TextChunker:
        """Get or create text chunker (lazy loading)."""
        if self._text_chunker is None:
            self._text_chunker = TextChunker()
        return self._text_chunker

    def get_supported_languages(self) -> list[str]:
        """
        Get list of all supported languages.

        Returns:
            List of language names
        """
        from .language_detector import get_supported_languages

        return get_supported_languages()

    def get_supported_extensions(self) -> list[str]:
        """
        Get list of all supported file extensions.

        Returns:
            List of file extensions (including dot)
        """
        from .language_detector import get_supported_extensions

        return get_supported_extensions()

    def is_code_file(self, file_path: str) -> bool:
        """
        Check if file is a supported code file.

        Args:
            file_path: Path to file

        Returns:
            True if supported code file
        """
        from .language_detector import is_code_file

        return is_code_file(file_path)
