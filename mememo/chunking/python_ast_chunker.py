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
from .url_extract import scan_urls

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

        Delegates to the scope-aware ``chunk_with_edges`` walk and drops the
        edges. This is the single source of truth for chunk metadata, so memory
        chunks carry the same ``parent_class`` / ``class_name`` linkage the edge
        pass relies on (a method's owning class must be queryable for class
        diagrams and for qualname-based call resolution).

        Args:
            code: Python source code
            file_path: Path to file

        Returns:
            List of code chunks

        Raises:
            SyntaxError: If Python code has syntax errors
        """
        chunks, _edges = self.chunk_with_edges(code, file_path)
        logger.debug(f"Extracted {len(chunks)} chunks from {file_path}")
        return chunks

    # ----- v0.5 edge emission (FR-013) ------------------------------------

    def chunk_with_edges(self, code: str, file_path: str) -> tuple[list[Chunk], list[RawEdge]]:
        """Return ``(chunks, raw_edges)``.

        Single scope-aware traversal: every FunctionDef / ClassDef produces a
        chunk with ``parent_class`` populated when the enclosing scope is a
        class, and every Import / Call / ClassDef.bases / Attribute /
        decorator emits a RawEdge whose ``source_qualname`` reflects the
        enclosing scope (module / class / function).

        Resolution into target memory IDs is the symbol_resolver's job.
        """
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            logger.warning(f"Python syntax error in {file_path}: {e}")
            raise
        module = file_path_to_module(file_path)
        lines = code.split("\n")
        chunks: list[Chunk] = []
        edges: list[RawEdge] = []
        scope_stack: list[tuple[str, str]] = [("module", module)]

        def cur_qualname() -> str:
            return ".".join(part for _, part in scope_stack)

        def enclosing_class() -> str | None:
            for kind, name in reversed(scope_stack):
                if kind == "class":
                    return name
            return None

        def enclosing_class_qualname() -> str | None:
            """Dotted path up to and including the nearest enclosing class,
            e.g. ``pkg.mod.Outer.Inner`` — used to bind ``self.x()`` calls."""
            last = -1
            for i, (kind, _name) in enumerate(scope_stack):
                if kind == "class":
                    last = i
            if last < 0:
                return None
            return ".".join(name for _, name in scope_stack[: last + 1])

        def visit(node: ast.AST) -> None:
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
                qual = f"{cur_qualname()}.{node.name}"
                # Chunk for the class itself.
                start = node.lineno
                end = node.end_lineno or start
                docstring = ast.get_docstring(node)
                chunks.append(
                    Chunk(
                        text="\n".join(lines[start - 1 : end]),
                        start_line=start,
                        end_line=end,
                        chunk_type="class",
                        class_name=node.name,
                        docstring=docstring,
                        decorators=[self._get_decorator_name(d) for d in node.decorator_list]
                        or None,
                        parent_class=enclosing_class(),
                        language="python",
                        file_path=file_path,
                    )
                )
                if docstring:
                    seen_urls: set[str] = set()
                    for url in scan_urls(docstring):
                        if url not in seen_urls:
                            seen_urls.add(url)
                            edges.append(RawEdge(qual, url, "REFERENCES", "INFERRED"))
                for base in node.bases:
                    tgt = _name_from_attr_chain(base)
                    if tgt:
                        edges.append(RawEdge(qual, tgt, "EXTENDS"))
                for dec in node.decorator_list:
                    tgt = _name_from_attr_chain(dec.func if isinstance(dec, ast.Call) else dec)
                    if tgt:
                        edges.append(RawEdge(qual, tgt, "DECORATED_BY"))
                scope_stack.append(("class", node.name))
                for child in ast.iter_child_nodes(node):
                    visit(child)
                scope_stack.pop()
                return

            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # A method's owning class is the *direct* enclosing scope, not any
                # ancestor class: a helper closure nested inside a method must stay
                # a plain function (class_name=None) so it doesn't pollute the
                # class diagram's member list. scope_stack[-1] is the immediate
                # named scope (control-flow blocks like `if` don't push).
                parent = scope_stack[-1][1] if scope_stack[-1][0] == "class" else None
                qual = f"{cur_qualname()}.{node.name}"
                start = node.lineno
                end = node.end_lineno or start
                docstring = ast.get_docstring(node)
                chunks.append(
                    Chunk(
                        text="\n".join(lines[start - 1 : end]),
                        start_line=start,
                        end_line=end,
                        chunk_type="method" if parent else "function",
                        function_name=node.name,
                        # class_name is the SQL-queryable owning class: with
                        # function_name it forms the module.Class.method qualname
                        # that call resolution and class diagrams key on.
                        class_name=parent,
                        docstring=docstring,
                        decorators=[self._get_decorator_name(d) for d in node.decorator_list]
                        or None,
                        parent_class=parent,
                        language="python",
                        file_path=file_path,
                    )
                )
                if docstring:
                    seen_urls: set[str] = set()
                    for url in scan_urls(docstring):
                        if url not in seen_urls:
                            seen_urls.add(url)
                            edges.append(RawEdge(qual, url, "REFERENCES", "INFERRED"))
                for dec in node.decorator_list:
                    tgt = _name_from_attr_chain(dec.func if isinstance(dec, ast.Call) else dec)
                    if tgt:
                        edges.append(RawEdge(qual, tgt, "DECORATED_BY"))
                scope_stack.append(("function", node.name))
                for child in ast.iter_child_nodes(node):
                    visit(child)
                scope_stack.pop()
                return

            if isinstance(node, ast.Call):
                tgt = _call_target(node.func, enclosing_class_qualname())
                if tgt:
                    edges.append(RawEdge(cur_qualname(), tgt, "CALLS"))
                for child in ast.iter_child_nodes(node):
                    visit(child)
                return

            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                if node.value.id in ("self", "cls"):
                    edges.append(RawEdge(cur_qualname(), node.attr, "USES"))

            for child in ast.iter_child_nodes(node):
                visit(child)

        for child in ast.iter_child_nodes(tree):
            visit(child)
        return chunks, edges

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


def _call_target(func: ast.expr, class_qualname: str | None) -> str:
    """Resolve a call's target label.

    ``self.x()`` / ``cls.x()`` inside a class bind to that class's member
    (``pkg.mod.Class.x``) so the resolver links intra-class calls to the right
    method instead of leaving a dangling ``self.x`` symbol. Everything else
    falls back to the dotted name chain (``foo``, ``mod.bar``).
    """
    if (
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Name)
        and func.value.id in ("self", "cls")
    ):
        return f"{class_qualname}.{func.attr}" if class_qualname else func.attr
    return _name_from_attr_chain(func)
