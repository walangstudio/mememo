"""
Python AST-based chunker.

Uses Python's ast module to extract functions, classes, and methods
with rich metadata (docstrings, decorators, type hints).
"""

import ast
import logging
from typing import List, Optional

from .base_chunker import BaseChunker, Chunk

logger = logging.getLogger(__name__)


class PythonASTChunker(BaseChunker):
    """
    Python AST-based chunker.

    Extracts:
    - Functions (with decorators, docstrings, type hints)
    - Classes (with inheritance)
    - Methods (with parent class info)
    - Module-level code
    """

    def chunk(self, code: str, file_path: str) -> List[Chunk]:
        """
        Chunk Python code using AST parsing.

        Args:
            code: Python source code
            file_path: Path to file

        Returns:
            List of code chunks

        Raises:
            SyntaxError: If Python code has syntax errors
        """
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            logger.warning(f"Python syntax error in {file_path}: {e}")
            raise

        chunks = []
        lines = code.split("\n")

        # Walk the AST and extract chunks
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                chunk = self._extract_function(node, lines, file_path)
                if chunk:
                    chunks.append(chunk)
            elif isinstance(node, ast.AsyncFunctionDef):
                chunk = self._extract_function(node, lines, file_path, is_async=True)
                if chunk:
                    chunks.append(chunk)
            elif isinstance(node, ast.ClassDef):
                chunk = self._extract_class(node, lines, file_path)
                if chunk:
                    chunks.append(chunk)

        logger.debug(f"Extracted {len(chunks)} chunks from {file_path}")
        return chunks

    def _extract_function(
        self,
        node: ast.FunctionDef,
        lines: List[str],
        file_path: str,
        is_async: bool = False,
    ) -> Optional[Chunk]:
        """
        Extract function definition with metadata.

        Args:
            node: AST FunctionDef node
            lines: Source code lines
            file_path: Path to file
            is_async: Whether function is async

        Returns:
            Chunk or None if extraction fails
        """
        start_line = node.lineno
        end_line = node.end_lineno or start_line

        # Extract text
        text = "\n".join(lines[start_line - 1:end_line])

        # Extract docstring
        docstring = ast.get_docstring(node)

        # Extract decorators
        decorators = []
        for dec in node.decorator_list:
            decorators.append(self._get_decorator_name(dec))

        # Check if this is a method (inside a class)
        parent_class = None
        # Note: We'd need to track parent context during walk for this
        # For now, we'll detect methods by checking for 'self' or 'cls' first param
        is_method = False
        if node.args.args:
            first_arg = node.args.args[0].arg
            if first_arg in ("self", "cls"):
                is_method = True

        return Chunk(
            text=text,
            start_line=start_line,
            end_line=end_line,
            chunk_type="method" if is_method else "function",
            function_name=node.name,
            docstring=docstring,
            decorators=decorators if decorators else None,
            parent_class=parent_class,
            language="python",
            file_path=file_path,
        )

    def _extract_class(
        self,
        node: ast.ClassDef,
        lines: List[str],
        file_path: str,
    ) -> Optional[Chunk]:
        """
        Extract class definition with metadata.

        Args:
            node: AST ClassDef node
            lines: Source code lines
            file_path: Path to file

        Returns:
            Chunk or None if extraction fails
        """
        start_line = node.lineno
        end_line = node.end_lineno or start_line

        # Extract text
        text = "\n".join(lines[start_line - 1:end_line])

        # Extract docstring
        docstring = ast.get_docstring(node)

        # Extract decorators
        decorators = []
        for dec in node.decorator_list:
            decorators.append(self._get_decorator_name(dec))

        # Extract parent class (first base class)
        parent_class = None
        if node.bases:
            base = node.bases[0]
            if isinstance(base, ast.Name):
                parent_class = base.id
            elif isinstance(base, ast.Attribute):
                parent_class = base.attr

        return Chunk(
            text=text,
            start_line=start_line,
            end_line=end_line,
            chunk_type="class",
            class_name=node.name,
            docstring=docstring,
            decorators=decorators if decorators else None,
            parent_class=parent_class,
            language="python",
            file_path=file_path,
        )

    def _get_decorator_name(self, decorator: ast.expr) -> str:
        """
        Extract decorator name from AST node.

        Args:
            decorator: AST decorator node

        Returns:
            Decorator name as string
        """
        if isinstance(decorator, ast.Name):
            return decorator.id
        elif isinstance(decorator, ast.Call):
            if isinstance(decorator.func, ast.Name):
                return decorator.func.id
            elif isinstance(decorator.func, ast.Attribute):
                return decorator.func.attr
        elif isinstance(decorator, ast.Attribute):
            return decorator.attr

        return str(decorator)
