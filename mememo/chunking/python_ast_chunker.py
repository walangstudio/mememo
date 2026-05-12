"""
Python AST-based chunker.

Uses Python's ast module to extract functions, classes, and methods
with rich metadata (docstrings, decorators, type hints).

v0.5 (FR-013): adds chunk_with_edges() which returns a tuple of
(chunks, raw_edges). raw_edges carry the IMPORTS / CALLS / EXTENDS /
USES / DECORATED_BY taxonomy and feed the symbol_resolver pass.
"""

import ast
import logging
from pathlib import PurePosixPath

from .base_chunker import BaseChunker, Chunk, RawEdge

logger = logging.getLogger(__name__)


def file_path_to_module(file_path: str) -> str:
    """Map ``mememo/core/storage_manager.py`` -> ``mememo.core.storage_manager``.

    Drops the .py extension and replaces path separators with dots. The result
    is used as the module prefix when building qualnames for chunks emitted
    from that file.
    """
    p = PurePosixPath(file_path.replace("\\", "/"))
    parts = list(p.with_suffix("").parts)
    return ".".join(parts)


class PythonASTChunker(BaseChunker):
    """
    Python AST-based chunker.

    Extracts:
    - Functions (with decorators, docstrings, type hints)
    - Classes (with inheritance)
    - Methods (with parent class info)
    - Module-level code
    """

    def chunk(self, code: str, file_path: str) -> list[Chunk]:
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

    # ----- v0.5 edge emission (FR-013) ------------------------------------

    def chunk_with_edges(
        self, code: str, file_path: str
    ) -> tuple[list[Chunk], list[RawEdge]]:
        """Return ``(chunks, raw_edges)``.

        Chunks are produced by the existing AST walk; raw edges are emitted
        in a second pass that walks the same tree visiting Import, ImportFrom,
        Call, ClassDef.bases, Attribute, and decorator nodes.

        Resolution into target memory IDs is the symbol_resolver's job.
        """
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            logger.warning(f"Python syntax error in {file_path}: {e}")
            raise
        chunks = self.chunk(code, file_path)
        module = file_path_to_module(file_path)
        edges = _emit_edges(tree, module)
        return chunks, edges

    def _extract_function(
        self,
        node: ast.FunctionDef,
        lines: list[str],
        file_path: str,
        is_async: bool = False,
    ) -> Chunk | None:
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
        text = "\n".join(lines[start_line - 1 : end_line])

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
        lines: list[str],
        file_path: str,
    ) -> Chunk | None:
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
        text = "\n".join(lines[start_line - 1 : end_line])

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


def _name_from_attr_chain(node: ast.expr) -> str:
    """Render an ast.Name / ast.Attribute chain back to dotted form.

    ``ast.Attribute(ast.Name('os'), 'path')`` -> ``"os.path"``. Used by the
    edge emitter to recover qualified targets like ``module.submod.func``
    from Call.func and ClassDef.bases nodes.
    """
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _name_from_attr_chain(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return ""


def _emit_edges(tree: ast.AST, module: str) -> list[RawEdge]:
    """Second-pass walker that emits IMPORTS / CALLS / EXTENDS / USES /
    DECORATED_BY edges. The source_qualname of every edge is the qualified
    path of the enclosing function/class/module, never just ``module``."""
    edges: list[RawEdge] = []

    # Track the enclosing scope stack so we know which function / class
    # produces each edge. Pure DFS — simple parents-during-walk pattern.
    scope_stack: list[str] = [module]  # module is the implicit outer scope

    def cur() -> str:
        return ".".join(scope_stack)

    def walk(node: ast.AST) -> None:
        # Imports always sit at the module scope of THIS file.
        if isinstance(node, ast.Import):
            for alias in node.names:
                edges.append(RawEdge(module, alias.name, "IMPORTS"))
            return
        if isinstance(node, ast.ImportFrom):
            base = node.module or ""
            for alias in node.names:
                target = f"{base}.{alias.name}" if base else alias.name
                edges.append(RawEdge(module, target, "IMPORTS"))
            return

        if isinstance(node, ast.ClassDef):
            qual = f"{cur()}.{node.name}"
            # EXTENDS edges per base class.
            for base in node.bases:
                tgt = _name_from_attr_chain(base)
                if tgt:
                    edges.append(RawEdge(qual, tgt, "EXTENDS"))
            # DECORATED_BY on the class itself.
            for dec in node.decorator_list:
                tgt = _name_from_attr_chain(
                    dec.func if isinstance(dec, ast.Call) else dec
                )
                if tgt:
                    edges.append(RawEdge(qual, tgt, "DECORATED_BY"))
            scope_stack.append(node.name)
            for child in ast.iter_child_nodes(node):
                walk(child)
            scope_stack.pop()
            return

        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            qual = f"{cur()}.{node.name}"
            for dec in node.decorator_list:
                tgt = _name_from_attr_chain(
                    dec.func if isinstance(dec, ast.Call) else dec
                )
                if tgt:
                    edges.append(RawEdge(qual, tgt, "DECORATED_BY"))
            scope_stack.append(node.name)
            for child in ast.iter_child_nodes(node):
                walk(child)
            scope_stack.pop()
            return

        if isinstance(node, ast.Call):
            tgt = _name_from_attr_chain(node.func)
            if tgt:
                edges.append(RawEdge(cur(), tgt, "CALLS"))
            for child in ast.iter_child_nodes(node):
                walk(child)
            return

        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            # ``self.foo`` access inside a method — emit a USES edge to ``foo``.
            if node.value.id in ("self", "cls"):
                edges.append(RawEdge(cur(), node.attr, "USES"))

        for child in ast.iter_child_nodes(node):
            walk(child)

    walk(tree)
    return edges
