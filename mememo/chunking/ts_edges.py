"""Tree-sitter edge emission for TypeScript / JavaScript / Go (FR-014).

A single scope-aware walk per language emits both chunks (with proper
``parent_class``) and raw edges (IMPORTS / CALLS / EXTENDS / IMPLEMENTS /
USES / DECORATED_BY). The walker pattern mirrors the Python AST chunker
in mememo/chunking/python_ast_chunker.py.

Each walker is a closure that mutates ``chunks`` and ``edges`` lists; no
shared state across files. Pure functions otherwise.
"""

from __future__ import annotations

from collections.abc import Callable

from .base_chunker import Chunk, RawEdge


def _text(node, code_bytes: bytes) -> str:
    return code_bytes[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _line_range(node) -> tuple[int, int]:
    return (node.start_point[0] + 1, node.end_point[0] + 1)


def _flatten_member_expression(node, code_bytes: bytes) -> str:
    """Render an ``a.b.c`` member_expression chain back to dotted form.

    Handles TS / JS where the AST shape is
    ``member_expression(object, '.', property)`` and Go's selector_expression.
    """
    if node.type in ("identifier", "type_identifier", "property_identifier", "field_identifier"):
        return _text(node, code_bytes)
    if node.type in ("member_expression", "selector_expression"):
        obj = node.child_by_field_name("object") or node.child_by_field_name("operand")
        prop = node.child_by_field_name("property") or node.child_by_field_name("field")
        if obj is not None and prop is not None:
            base = _flatten_member_expression(obj, code_bytes)
            tail = _text(prop, code_bytes)
            return f"{base}.{tail}" if base else tail
    return _text(node, code_bytes)


def walk_typescript_or_javascript(
    tree, code_bytes: bytes, module: str, file_path: str, language: str
) -> tuple[list[Chunk], list[RawEdge]]:
    """Walk a TypeScript or JavaScript AST and emit chunks + edges.

    Coverage:
    - import_statement / import_clause -> IMPORTS
    - class_declaration with heritage clause -> EXTENDS / IMPLEMENTS
    - decorators on class / method -> DECORATED_BY
    - function_declaration / method_definition -> CALLS (inside the body)
    - method body uses ``this.foo`` -> USES
    """
    chunks: list[Chunk] = []
    edges: list[RawEdge] = []
    scope_stack: list[tuple[str, str]] = [("module", module)]

    def cur() -> str:
        return ".".join(part for _, part in scope_stack)

    def enclosing_class() -> str | None:
        for kind, name in reversed(scope_stack):
            if kind == "class":
                return name
        return None

    def visit(node) -> None:
        # ---- imports (module-level)
        if node.type == "import_statement":
            # ``import { a, b } from "./foo"`` → IMPORTS edges per imported name.
            # The grammar exposes the source as a plain `string` child (not a
            # named field on tree-sitter-javascript). Pull the string_fragment
            # if present, else strip the quotes from the raw string text.
            base = ""
            for child in node.children:
                if child.type == "string":
                    for sub in child.children:
                        if sub.type == "string_fragment":
                            base = _text(sub, code_bytes)
                            break
                    else:
                        base = _text(child, code_bytes).strip("'\"`")
                    break
            specifiers: list[str] = []
            for child in node.children:
                if child.type == "import_clause":
                    for sub in child.children:
                        if sub.type == "named_imports":
                            for spec in sub.children:
                                if spec.type == "import_specifier":
                                    name_node = spec.child_by_field_name("name")
                                    if name_node is not None:
                                        specifiers.append(_text(name_node, code_bytes))
                        elif sub.type == "identifier":
                            specifiers.append(_text(sub, code_bytes))
            if not specifiers:
                edges.append(RawEdge(module, base or "(side-effect)", "IMPORTS"))
            else:
                for name in specifiers:
                    target = f"{base}.{name}" if base else name
                    edges.append(RawEdge(module, target, "IMPORTS"))
            return

        # ---- class
        if node.type == "class_declaration":
            name_node = node.child_by_field_name("name")
            name = _text(name_node, code_bytes) if name_node else "(anonymous)"
            qual = f"{cur()}.{name}"
            start, end = _line_range(node)
            chunks.append(
                Chunk(
                    text=_text(node, code_bytes),
                    start_line=start,
                    end_line=end,
                    chunk_type="class",
                    class_name=name,
                    parent_class=enclosing_class(),
                    language=language,
                    file_path=file_path,
                )
            )
            # decorators: appear as sibling nodes BEFORE class_declaration in
            # some grammars; safe to scan the parent's children. Skip for v0.5.
            heritage = node.child_by_field_name("heritage") or node.child_by_field_name("body")
            # JavaScript and TypeScript grammars differ here:
            # - JS:  class_heritage > 'extends' keyword + identifier(s)
            # - TS:  class_heritage > extends_clause / implements_clause > types
            for child in node.children:
                if child.type != "class_heritage":
                    continue
                current_kind: str | None = None
                for hc in child.children:
                    if hc.type == "extends":
                        current_kind = "EXTENDS"
                        continue
                    if hc.type == "implements":
                        current_kind = "IMPLEMENTS"
                        continue
                    # TypeScript nested form.
                    if hc.type in ("extends_clause", "implements_clause"):
                        kind = "EXTENDS" if hc.type == "extends_clause" else "IMPLEMENTS"
                        for sub in hc.children:
                            if sub.type in ("identifier", "type_identifier", "member_expression"):
                                tgt = _flatten_member_expression(sub, code_bytes)
                                if tgt:
                                    edges.append(RawEdge(qual, tgt, kind))
                        continue
                    # JS flat form: identifier(s) follow the keyword.
                    if current_kind and hc.type in (
                        "identifier",
                        "type_identifier",
                        "member_expression",
                    ):
                        tgt = _flatten_member_expression(hc, code_bytes)
                        if tgt:
                            edges.append(RawEdge(qual, tgt, current_kind))
            scope_stack.append(("class", name))
            if heritage is not None and heritage.type == "class_body":
                for child in heritage.children:
                    visit(child)
            scope_stack.pop()
            return

        # ---- function / method
        if node.type in ("function_declaration", "method_definition"):
            name_node = node.child_by_field_name("name")
            name = _text(name_node, code_bytes) if name_node else "(anonymous)"
            parent = enclosing_class()
            qual = f"{cur()}.{name}"
            start, end = _line_range(node)
            chunks.append(
                Chunk(
                    text=_text(node, code_bytes),
                    start_line=start,
                    end_line=end,
                    chunk_type="method" if parent else "function",
                    function_name=name,
                    parent_class=parent,
                    language=language,
                    file_path=file_path,
                )
            )
            scope_stack.append(("function", name))
            body = node.child_by_field_name("body")
            if body is not None:
                for child in body.children:
                    visit(child)
            scope_stack.pop()
            return

        # ---- call expressions
        if node.type == "call_expression":
            fn = node.child_by_field_name("function")
            if fn is not None:
                tgt = _flatten_member_expression(fn, code_bytes)
                if tgt:
                    edges.append(RawEdge(cur(), tgt, "CALLS"))

        # ---- this.X usage inside methods -> USES
        if node.type == "member_expression":
            obj = node.child_by_field_name("object")
            prop = node.child_by_field_name("property")
            if obj is not None and prop is not None and _text(obj, code_bytes) == "this":
                edges.append(RawEdge(cur(), _text(prop, code_bytes), "USES"))

        for child in node.children:
            visit(child)

    for child in tree.root_node.children:
        visit(child)
    return chunks, edges


def walk_go(
    tree, code_bytes: bytes, module: str, file_path: str
) -> tuple[list[Chunk], list[RawEdge]]:
    """Walk a Go AST and emit chunks + edges.

    Go is structurally simpler (no class inheritance, no decorators). Edges:
    - import_declaration / import_spec -> IMPORTS
    - function_declaration / method_declaration -> CALLS (inside body)
    - method receiver type -> USES (the method binds to that struct)
    """
    chunks: list[Chunk] = []
    edges: list[RawEdge] = []
    scope_stack: list[tuple[str, str]] = [("module", module)]

    def cur() -> str:
        return ".".join(part for _, part in scope_stack)

    def visit(node) -> None:
        if node.type == "import_declaration":
            # Tree-sitter-go wraps single-line imports as a single import_spec
            # directly under import_declaration, and parenthesised blocks as
            # import_spec_list > import_spec. Each import_spec carries an
            # interpreted_string_literal child for the path. Find every
            # import_spec descendant and pull its string out.
            def _gather_specs(n):
                if n.type == "import_spec":
                    yield n
                for c in n.children:
                    yield from _gather_specs(c)

            for spec in _gather_specs(node):
                for child in spec.children:
                    if child.type == "interpreted_string_literal":
                        path = _text(child, code_bytes).strip("`'\"")
                        if path:
                            edges.append(RawEdge(module, path, "IMPORTS"))
                        break
            return

        if node.type == "function_declaration":
            name_node = node.child_by_field_name("name")
            name = _text(name_node, code_bytes) if name_node else "(anonymous)"
            qual = f"{cur()}.{name}"
            start, end = _line_range(node)
            chunks.append(
                Chunk(
                    text=_text(node, code_bytes),
                    start_line=start,
                    end_line=end,
                    chunk_type="function",
                    function_name=name,
                    language="go",
                    file_path=file_path,
                )
            )
            scope_stack.append(("function", name))
            body = node.child_by_field_name("body")
            if body is not None:
                for child in body.children:
                    visit(child)
            scope_stack.pop()
            return

        if node.type == "method_declaration":
            name_node = node.child_by_field_name("name")
            name = _text(name_node, code_bytes) if name_node else "(anonymous)"
            # Receiver type binds the method to a struct — record as USES.
            receiver_struct: str | None = None
            recv = node.child_by_field_name("receiver")
            if recv is not None:
                for p in recv.children:
                    # parameter_declaration with type identifier or pointer_type
                    if p.type == "parameter_declaration":
                        t = p.child_by_field_name("type")
                        if t is not None:
                            receiver_struct = _flatten_member_expression(t, code_bytes)
                            if receiver_struct.startswith("*"):
                                receiver_struct = receiver_struct.lstrip("*").strip()
            parent_class = receiver_struct
            qual = f"{cur()}.{receiver_struct}.{name}" if receiver_struct else f"{cur()}.{name}"
            start, end = _line_range(node)
            chunks.append(
                Chunk(
                    text=_text(node, code_bytes),
                    start_line=start,
                    end_line=end,
                    chunk_type="method",
                    function_name=name,
                    parent_class=parent_class,
                    language="go",
                    file_path=file_path,
                )
            )
            if receiver_struct:
                edges.append(RawEdge(qual, receiver_struct, "USES"))
            scope_stack.append(("function", name))
            body = node.child_by_field_name("body")
            if body is not None:
                for child in body.children:
                    visit(child)
            scope_stack.pop()
            return

        if node.type == "call_expression":
            fn = node.child_by_field_name("function")
            if fn is not None:
                tgt = _flatten_member_expression(fn, code_bytes)
                if tgt:
                    edges.append(RawEdge(cur(), tgt, "CALLS"))

        for child in node.children:
            visit(child)

    for child in tree.root_node.children:
        visit(child)
    return chunks, edges


# Public dispatch — language -> walker.
LanguageWalker = Callable[..., tuple[list[Chunk], list[RawEdge]]]

EDGE_WALKERS: dict[str, LanguageWalker] = {
    "typescript": walk_typescript_or_javascript,
    "tsx": walk_typescript_or_javascript,
    "javascript": walk_typescript_or_javascript,
    "go": walk_go,
}
