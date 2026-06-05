"""Tree-sitter edge emission for TS / JS / Go / Rust / Java / C / C++ / C# (FR-014).

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


# Class-field node types across the grammars we extract attributes from, and
# the nested-type nodes whose own fields must NOT be pulled into the outer class.
_FIELD_NODE_TYPES = frozenset(
    {
        "field_declaration",  # java / c / c++ / c# / rust / go
        "public_field_definition",  # typescript
        "field_definition",  # javascript
        "property_declaration",  # c# / kotlin / swift / php
        "val_definition",  # scala
        "var_definition",  # scala
    }
)
_NESTED_TYPE_NODES = frozenset(
    {
        "class_declaration",
        "class_specifier",
        "struct_specifier",
        "struct_item",
        "interface_declaration",
        "enum_declaration",
        "enum_variant",  # a Rust enum variant's struct fields aren't enum fields
        "record_declaration",
        "trait_item",
        "impl_item",
        "object_declaration",
    }
)

# Declarator wrappers to descend through to reach the bare field identifier
# (C/C++ ``int* p`` / ``int arr[4]`` / ``int& r``).
_DECLARATOR_WRAPPERS = frozenset(
    {"pointer_declarator", "array_declarator", "reference_declarator", "init_declarator"}
)
_NAME_NODE_TYPES = frozenset(
    {"identifier", "property_identifier", "private_property_identifier", "field_identifier"}
)
# Method/function/lambda bodies hold *locals*, never class fields — and in some
# grammars (Scala ``val_definition``) a local uses the same node type as a field,
# so the field walk must not descend into them.
_SCOPE_BODY_NODES = frozenset(
    {
        "function_declaration",
        "function_definition",
        "function_item",
        "method_declaration",
        "method_definition",
        "constructor_declaration",
        "local_function_statement",
        "lambda",
        "lambda_literal",
        "anonymous_function",
        # Property accessors hold locals too (Swift computed/observed props,
        # Kotlin custom get/set), and Swift/Kotlin locals reuse the field node.
        "computed_property",
        "getter",
        "setter",
    }
)


def _field_names(node, code_bytes: bytes) -> list[str]:
    """All field names a single field/property declaration declares.

    Returns a list because one declaration can declare several (``int a, b;``).
    Handles, across grammars: a ``name``/``property`` field (Rust/Go/TS/JS field,
    C# property); Java/C# ``variable_declarator`` (incl. multiple); C++ bare
    ``field_identifier`` and pointer/array/reference declarators; C#'s
    ``variable_declaration`` nesting; Kotlin ``variable_declaration`` → identifier;
    Swift ``pattern`` → simple_identifier; PHP ``property_element`` → variable_name;
    Scala ``val_definition``/``var_definition`` → identifier.

    Tuple-destructured fields (Swift ``var (x, y)`` / Scala ``val (a, b)``) are
    not extracted — uncommon, and the single-name shortcut would be misleading.
    """
    direct = node.child_by_field_name("name") or node.child_by_field_name("property")
    if direct is not None and direct.type in _NAME_NODE_TYPES:
        return [_text(direct, code_bytes)]

    names: list[str] = []

    def add(n) -> None:
        if n is not None:
            t = _text(n, code_bytes)
            if t:
                names.append(t)

    # Scala val/var definition: name is the leading identifier (type is a
    # type_identifier, so no collision).
    if node.type in ("val_definition", "var_definition"):
        add(next((c for c in node.named_children if c.type == "identifier"), None))
        return names

    def collect(n) -> None:
        for ch in n.children:
            ct = ch.type
            if ct == "field_identifier":
                add(ch)
            elif ct == "variable_declarator":  # java / c#
                add(ch.child_by_field_name("name") or ch)
            elif ct == "variable_name":  # php $field
                t = _text(ch, code_bytes).lstrip("$")
                if t:
                    names.append(t)
            elif ct == "variable_declaration":
                # c# wraps variable_declarator(s); kotlin puts the bare name
                # identifier first (followed by a user_type for the type).
                if any(s.type == "variable_declarator" for s in ch.named_children):
                    collect(ch)
                else:
                    add(next((s for s in ch.named_children if s.type == "identifier"), None))
            elif ct == "pattern":  # swift property pattern
                add(next((s for s in ch.named_children if s.type == "simple_identifier"), None))
            elif ct == "property_element" or ct in _DECLARATOR_WRAPPERS:
                collect(ch)

    collect(node)
    return names


# Type nodes a wrapper (C# / Kotlin ``variable_declaration``) can hold when it
# exposes no ``type`` field of its own.
_TYPE_CHILD_NODES = frozenset(
    {"user_type", "type_identifier", "nullable_type", "generic_type", "qualified_name"}
)


def _decl_type_text(node, code_bytes: bytes) -> str | None:
    """Best-effort declared type of a field/property node, as a single line.

    Reaches the type via, in order: a ``type`` field (java/c/c++/c#-property/
    rust/scala/php/go, and typescript's ``type_annotation``); a ``type_annotation``
    child (swift); or a wrapping ``variable_declaration`` (c#-field / kotlin).
    Strips a leading ``:`` (type annotations carry one), collapses whitespace,
    and drops anything over 60 chars. Returns None when the grammar exposes no
    type (javascript, ruby) or none is found — the caller then stores a bare
    name. The class diagram independently gates the type against a safe charset,
    so a messy type here can never break rendering.
    """
    t = node.child_by_field_name("type")
    if t is None:
        for ch in node.children:
            if ch.type == "type_annotation":  # swift (typescript hits the field above)
                t = ch
                break
            if ch.type == "variable_declaration":  # c# / kotlin wrapper
                t = ch.child_by_field_name("type") or next(
                    (s for s in ch.named_children if s.type in _TYPE_CHILD_NODES), None
                )
                if t is not None:
                    break
    if t is None:
        return None
    txt = " ".join(_text(t, code_bytes).split()).lstrip(": ").strip()
    return txt if txt and len(txt) <= 60 else None


def _class_fields(class_node, code_bytes: bytes) -> list[str]:
    """Fields declared directly on a class/struct (not its nested types).

    Walks the class subtree collecting ``"name"`` or ``"name: type"`` entries
    from field/property declarations, skipping any nested type declaration so an
    inner class's (or enum variant's) fields don't leak into the outer one.
    Method/function bodies hold locals under different node types, so they're
    naturally excluded. Deduped by name, capped at 30.
    """
    entries: list[str] = []
    seen: set[str] = set()

    def walk(node) -> None:
        for ch in node.children:
            if ch.type in _NESTED_TYPE_NODES or ch.type in _SCOPE_BODY_NODES:
                continue
            if ch.type in _FIELD_NODE_TYPES:
                type_text = _decl_type_text(ch, code_bytes)
                for nm in _field_names(ch, code_bytes):
                    if nm and nm not in seen:
                        seen.add(nm)
                        entries.append(f"{nm}: {type_text}" if type_text else nm)
            walk(ch)

    walk(class_node)
    return entries[:30]


def _go_struct_fields(struct_type_node, code_bytes: bytes) -> list[str]:
    """Named fields of a Go ``struct_type`` as ``"name"`` / ``"name: type"``.

    ``field_declaration_list`` holds ``field_declaration`` nodes; each carries
    one or more ``field_identifier`` names (``A, B int`` → both) plus one shared
    type. Embedded fields (``Logger``) have a type but no ``field_identifier``,
    so they contribute no name. Capped at 30.

    Deliberately does NOT go through ``_field_names``: that helper's
    ``child_by_field_name("name")`` shortcut returns only the *first* name of a
    Go ``field_declaration``, dropping ``b`` from ``a, b int`` — so collect the
    ``field_identifier`` children directly here.
    """
    entries: list[str] = []
    seen: set[str] = set()
    fdl = next((c for c in struct_type_node.children if c.type == "field_declaration_list"), None)
    if fdl is None:
        return entries
    for fd in fdl.children:
        if fd.type != "field_declaration":
            continue
        type_text = _decl_type_text(fd, code_bytes)
        for ch in fd.children:
            if ch.type == "field_identifier":
                nm = _text(ch, code_bytes)
                if nm and nm not in seen:
                    seen.add(nm)
                    entries.append(f"{nm}: {type_text}" if type_text else nm)
    return entries[:30]


def _ruby_instance_var_fields(class_node, code_bytes: bytes) -> list[str]:
    """Instance-variable "fields" of a Ruby class/module.

    Ruby has no field declarations; instance state lives in ``@ivars`` assigned
    inside methods (usually ``#initialize``). Collect every ``@ivar`` referenced
    in the class body — an ivar is class-scoped whether read or written — minus
    nested class/module bodies, strip the leading ``@``, dedupe. Capped at 30.
    """
    names: list[str] = []
    seen: set[str] = set()

    def walk(node) -> None:
        for ch in node.children:
            if ch.type in ("class", "module", "singleton_class"):
                continue
            if ch.type == "instance_variable":
                nm = _text(ch, code_bytes).lstrip("@")
                if nm and nm not in seen:
                    seen.add(nm)
                    names.append(nm)
            walk(ch)

    walk(class_node)
    return names[:30]


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
                    attributes=_class_fields(node, code_bytes) or None,
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
                    # Owning class is the SQL-queryable context: with
                    # function_name it forms the module.Class.method qualname the
                    # edge source uses, so intra-method calls + class diagrams
                    # resolve. Top-level functions keep class_name=None.
                    class_name=parent,
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
    tree, code_bytes: bytes, module: str, file_path: str, language: str | None = None
) -> tuple[list[Chunk], list[RawEdge]]:
    """Walk a Go AST and emit chunks + edges.

    Go is structurally simpler (no class inheritance, no decorators). Edges:
    - import_declaration / import_spec -> IMPORTS
    - function_declaration / method_declaration -> CALLS (inside body)
    - method receiver type -> USES (the method binds to that struct)
    A ``recv.method()`` call (recv = the method's own receiver var) is rewritten
    to the struct's fully-qualified ``module.Struct.method`` so the resolver
    binds it — Go's receiver is a named var, not ``self``. ``type X struct {...}``
    becomes a class chunk (with field names) so methods attach in class diagrams.
    """
    chunks: list[Chunk] = []
    edges: list[RawEdge] = []
    scope_stack: list[tuple[str, str]] = [("module", module)]
    # (receiver var name, receiver struct) for the enclosing method, or None for
    # a plain function. Lets a ``recv.method()`` call inside the body be rewritten
    # to the struct's fully-qualified method so the resolver binds it (Go's
    # receiver is a named var like ``a``, not ``self``, so the resolver's
    # self-receiver rebind can't catch it — only the walker knows the name).
    recv_stack: list[tuple[str, str] | None] = []

    def cur() -> str:
        return ".".join(part for _, part in scope_stack)

    def _rewrite_receiver_call(tgt: str) -> str:
        recv = recv_stack[-1] if recv_stack else None
        if recv is None:
            return tgt
        rname, rstruct = recv
        head, dot, tail = tgt.partition(".")
        # ``_`` is the blank receiver — it can't be referenced, so never a call head.
        if rname and rname != "_" and dot and tail and "." not in tail and head == rname:
            return f"{module}.{rstruct}.{tail}"
        return tgt

    def visit(node) -> None:
        if node.type == "type_declaration":
            # ``type X struct {...}`` → a class chunk so methods (bound via their
            # receiver as parent_class/class_name) can attach in the class diagram.
            # A single declaration can group several specs: ``type ( A struct{}; ... )``.
            for spec in node.children:
                if spec.type != "type_spec":
                    continue
                name_node = spec.child_by_field_name("name")
                type_node = spec.child_by_field_name("type")
                if name_node is None or type_node is None or type_node.type != "struct_type":
                    continue
                name = _text(name_node, code_bytes)
                start, end = _line_range(spec)
                chunks.append(
                    Chunk(
                        text=_text(spec, code_bytes),
                        start_line=start,
                        end_line=end,
                        chunk_type="class",
                        class_name=name,
                        attributes=_go_struct_fields(type_node, code_bytes) or None,
                        language="go",
                        file_path=file_path,
                    )
                )
            return

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
            recv_stack.append(None)  # a plain function has no receiver
            body = node.child_by_field_name("body")
            if body is not None:
                for child in body.children:
                    visit(child)
            recv_stack.pop()
            scope_stack.pop()
            return

        if node.type == "method_declaration":
            name_node = node.child_by_field_name("name")
            name = _text(name_node, code_bytes) if name_node else "(anonymous)"
            # Receiver type binds the method to a struct — record as USES.
            receiver_struct: str | None = None
            receiver_name: str | None = None
            recv = node.child_by_field_name("receiver")
            if recv is not None:
                for p in recv.children:
                    # parameter_declaration with type identifier or pointer_type
                    if p.type == "parameter_declaration":
                        nm = p.child_by_field_name("name")
                        if nm is not None:
                            receiver_name = _text(nm, code_bytes)
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
                    # Owning struct is the SQL-queryable context: with
                    # function_name it forms the module.Struct.method qualname the
                    # edge source uses, so intra-method calls + class diagrams
                    # resolve (mirrors the OO walkers).
                    class_name=parent_class,
                    parent_class=parent_class,
                    language="go",
                    file_path=file_path,
                )
            )
            if receiver_struct:
                edges.append(RawEdge(qual, receiver_struct, "USES"))
            # Push the struct-qualified name so CALLS emitted in the body match
            # the method's module.Struct.method symbol qualname (mirrors Rust).
            scope_stack.append(
                ("function", f"{receiver_struct}.{name}" if receiver_struct else name)
            )
            recv_stack.append((receiver_name, receiver_struct) if receiver_struct else None)
            body = node.child_by_field_name("body")
            if body is not None:
                for child in body.children:
                    visit(child)
            recv_stack.pop()
            scope_stack.pop()
            return

        if node.type == "call_expression":
            fn = node.child_by_field_name("function")
            if fn is not None:
                tgt = _flatten_member_expression(fn, code_bytes)
                if tgt:
                    edges.append(RawEdge(cur(), _rewrite_receiver_call(tgt), "CALLS"))

        for child in node.children:
            visit(child)

    for child in tree.root_node.children:
        visit(child)
    return chunks, edges


def _rust_callee(node, code_bytes: bytes) -> str:
    """Flatten a Rust ``call_expression`` callee to a path/dotted string.

    Rust callees come as ``identifier`` (``f()``), ``scoped_identifier``
    (``Foo::bar()``, joined with ``::``), or ``field_expression``
    (``x.method()``, joined with ``.``). ``_flatten_member_expression``
    doesn't know these node shapes, hence a Rust-specific flattener.
    """
    t = node.type
    if t in ("identifier", "type_identifier", "field_identifier"):
        return _text(node, code_bytes)
    if t == "scoped_identifier":
        path = node.child_by_field_name("path")
        name = node.child_by_field_name("name")
        left = _rust_callee(path, code_bytes) if path is not None else ""
        right = _text(name, code_bytes) if name is not None else ""
        return "::".join(p for p in (left, right) if p)
    if t == "field_expression":
        value = node.child_by_field_name("value")
        field = node.child_by_field_name("field")
        left = _rust_callee(value, code_bytes) if value is not None else ""
        right = _text(field, code_bytes) if field is not None else ""
        return f"{left}.{right}" if left and right else (left or right)
    return _text(node, code_bytes)


def walk_rust(
    tree, code_bytes: bytes, module: str, file_path: str, language: str | None = None
) -> tuple[list[Chunk], list[RawEdge]]:
    """Walk a Rust AST and emit chunks + edges.

    Edges:
    - ``use_declaration`` (argument path) -> IMPORTS
    - ``call_expression`` inside fn/method bodies -> CALLS
    - ``impl Trait for Type`` -> Type IMPLEMENTS Trait
    - method in an ``impl Type`` block -> method USES Type (binding, like Go receivers)
    Chunks: ``function_item`` (function/method), ``struct_item`` / ``enum_item``
    / ``trait_item`` (class).
    """
    chunks: list[Chunk] = []
    edges: list[RawEdge] = []
    scope_stack: list[tuple[str, str]] = [("module", module)]

    def cur() -> str:
        return ".".join(part for _, part in scope_stack)

    def visit(node, impl_type: str | None = None) -> None:
        t = node.type

        if t == "use_declaration":
            arg = node.child_by_field_name("argument")
            if arg is not None:
                path = _text(arg, code_bytes).strip()
                if path:
                    edges.append(RawEdge(module, path, "IMPORTS"))
            return

        if t == "function_item":
            name_node = node.child_by_field_name("name")
            name = _text(name_node, code_bytes) if name_node else "(anonymous)"
            start, end = _line_range(node)
            if impl_type:
                qual = f"{cur()}.{impl_type}.{name}"
                chunks.append(
                    Chunk(
                        text=_text(node, code_bytes),
                        start_line=start,
                        end_line=end,
                        chunk_type="method",
                        function_name=name,
                        class_name=impl_type,
                        parent_class=impl_type,
                        language="rust",
                        file_path=file_path,
                    )
                )
                edges.append(RawEdge(qual, impl_type, "USES"))
                scope_stack.append(("function", f"{impl_type}.{name}"))
            else:
                chunks.append(
                    Chunk(
                        text=_text(node, code_bytes),
                        start_line=start,
                        end_line=end,
                        chunk_type="function",
                        function_name=name,
                        language="rust",
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

        if t in ("struct_item", "enum_item", "trait_item"):
            name_node = node.child_by_field_name("name")
            name = _text(name_node, code_bytes) if name_node else "(anonymous)"
            start, end = _line_range(node)
            chunks.append(
                Chunk(
                    text=_text(node, code_bytes),
                    start_line=start,
                    end_line=end,
                    chunk_type="class",
                    class_name=name,
                    attributes=_class_fields(node, code_bytes) or None,
                    language="rust",
                    file_path=file_path,
                )
            )
            if t == "trait_item":
                # Default-method bodies (function_item) can still emit CALLS.
                body = node.child_by_field_name("body")
                if body is not None:
                    for child in body.children:
                        if child.type == "function_item":
                            visit(child, impl_type=name)
            return

        if t == "impl_item":
            type_node = node.child_by_field_name("type")
            trait_node = node.child_by_field_name("trait")
            impl_t = _text(type_node, code_bytes) if type_node is not None else None
            if trait_node is not None and impl_t:
                edges.append(RawEdge(impl_t, _text(trait_node, code_bytes), "IMPLEMENTS"))
            body = node.child_by_field_name("body")
            if body is not None:
                for child in body.children:
                    if child.type == "function_item":
                        visit(child, impl_type=impl_t)
                    else:
                        visit(child)
            return

        if t == "call_expression":
            fn = node.child_by_field_name("function")
            if fn is not None:
                tgt = _rust_callee(fn, code_bytes)
                if tgt:
                    edges.append(RawEdge(cur(), tgt, "CALLS"))

        for child in node.children:
            visit(child)

    for child in tree.root_node.children:
        visit(child)
    return chunks, edges


def _java_callee(node, code_bytes: bytes) -> str:
    """Flatten a Java ``method_invocation`` / ``field_access`` callee to dotted form.

    Shapes: bare ``helper()`` (name only), ``this.legs()`` (object=``this``),
    ``System.out.println()`` (object=``field_access``). ``scoped_identifier``
    text is already dotted.
    """
    t = node.type
    if t == "this":
        return "this"
    if t in ("identifier", "type_identifier", "field_identifier", "scoped_identifier"):
        return _text(node, code_bytes)
    if t in ("field_access", "method_invocation"):
        obj = node.child_by_field_name("object")
        tail = node.child_by_field_name("field") or node.child_by_field_name("name")
        left = _java_callee(obj, code_bytes) if obj is not None else ""
        right = _text(tail, code_bytes) if tail is not None else ""
        return f"{left}.{right}" if left and right else (left or right)
    return _text(node, code_bytes)


def walk_java(
    tree, code_bytes: bytes, module: str, file_path: str, language: str | None = None
) -> tuple[list[Chunk], list[RawEdge]]:
    """Walk a Java AST and emit chunks + edges.

    Verified against tree-sitter-java 0.23.5. Edges:
    - ``import_declaration`` -> IMPORTS (dotted path; wildcard keeps ``.*``)
    - ``class_declaration`` ``superclass`` -> EXTENDS
    - ``class_declaration`` ``interfaces`` (super_interfaces > type_list) -> IMPLEMENTS
    - ``method_invocation`` inside bodies -> CALLS (callee flattened)
    - ``field_access`` on ``this`` -> USES
    Chunks: class/interface (class), method/constructor (method).
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
        t = node.type

        if t == "import_declaration":
            path = ""
            for child in node.children:
                if child.type in ("scoped_identifier", "identifier"):
                    path = _text(child, code_bytes)
                    break
            if path:
                if any(c.type == "asterisk" for c in node.children):
                    path = f"{path}.*"
                edges.append(RawEdge(module, path, "IMPORTS"))
            return

        if t in ("class_declaration", "interface_declaration", "enum_declaration"):
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
                    attributes=_class_fields(node, code_bytes) or None,
                    language="java",
                    file_path=file_path,
                )
            )
            superclass = node.child_by_field_name("superclass")
            if superclass is not None:
                for sub in superclass.children:
                    if sub.type == "type_identifier":
                        edges.append(RawEdge(qual, _text(sub, code_bytes), "EXTENDS"))
            interfaces = node.child_by_field_name("interfaces")
            if interfaces is not None:
                for tlist in interfaces.children:
                    if tlist.type == "type_list":
                        for sub in tlist.children:
                            if sub.type == "type_identifier":
                                edges.append(RawEdge(qual, _text(sub, code_bytes), "IMPLEMENTS"))
            scope_stack.append(("class", name))
            body = node.child_by_field_name("body")
            if body is not None:
                for child in body.children:
                    visit(child)
            scope_stack.pop()
            return

        if t in ("method_declaration", "constructor_declaration"):
            name_node = node.child_by_field_name("name")
            name = _text(name_node, code_bytes) if name_node else "(anonymous)"
            parent = enclosing_class()
            start, end = _line_range(node)
            chunks.append(
                Chunk(
                    text=_text(node, code_bytes),
                    start_line=start,
                    end_line=end,
                    chunk_type="method" if parent else "function",
                    function_name=name,
                    # Owning class is the SQL-queryable context: with
                    # function_name it forms the module.Class.method qualname the
                    # edge source uses, so intra-method calls + class diagrams
                    # resolve. Top-level functions keep class_name=None.
                    class_name=parent,
                    parent_class=parent,
                    language="java",
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

        if t == "method_invocation":
            tgt = _java_callee(node, code_bytes)
            if tgt:
                edges.append(RawEdge(cur(), tgt, "CALLS"))

        if t == "field_access":
            obj = node.child_by_field_name("object")
            field = node.child_by_field_name("field")
            if obj is not None and field is not None and _text(obj, code_bytes) == "this":
                edges.append(RawEdge(cur(), _text(field, code_bytes), "USES"))

        for child in node.children:
            visit(child)

    for child in tree.root_node.children:
        visit(child)
    return chunks, edges


def _c_callee(node, code_bytes: bytes) -> str:
    """Flatten a C/C++ ``call_expression`` callee to dotted/scoped form.

    Shapes: ``identifier`` (``f()``), ``field_expression`` (``p.x`` / ``this->run``,
    joined with ``.``), ``qualified_identifier`` (``std::sort``, joined with ``::``).
    """
    t = node.type
    if t == "this":
        return "this"
    if t in ("identifier", "field_identifier", "type_identifier", "namespace_identifier"):
        return _text(node, code_bytes)
    if t == "field_expression":
        arg = node.child_by_field_name("argument")
        field = node.child_by_field_name("field")
        left = _c_callee(arg, code_bytes) if arg is not None else ""
        right = _text(field, code_bytes) if field is not None else ""
        return f"{left}.{right}" if left and right else (left or right)
    if t == "qualified_identifier":
        scope = node.child_by_field_name("scope")
        name = node.child_by_field_name("name")
        left = _c_callee(scope, code_bytes) if scope is not None else ""
        right = _c_callee(name, code_bytes) if name is not None else ""
        return "::".join(p for p in (left, right) if p)
    return _text(node, code_bytes)


def _c_decl_name(declarator, code_bytes: bytes) -> str | None:
    """Pull the function name out of a (possibly pointer/reference-wrapped) declarator."""
    n = declarator
    while n is not None and n.type in (
        "pointer_declarator",
        "reference_declarator",
        "array_declarator",
    ):
        n = n.child_by_field_name("declarator")
    if n is not None and n.type == "function_declarator":
        inner = n.child_by_field_name("declarator")
        if inner is not None:
            if inner.type == "qualified_identifier":
                name = inner.child_by_field_name("name")
                return _text(name, code_bytes) if name is not None else _text(inner, code_bytes)
            return _text(inner, code_bytes)
    return None


def walk_c_family(
    tree, code_bytes: bytes, module: str, file_path: str, language: str | None = None
) -> tuple[list[Chunk], list[RawEdge]]:
    """Walk a C or C++ AST and emit chunks + edges (shared; C++ adds inheritance).

    Verified against tree-sitter-c 0.24.2 / tree-sitter-cpp 0.23.4. Edges:
    - ``preproc_include`` -> IMPORTS (header path, brackets/quotes stripped)
    - ``class_specifier`` ``base_class_clause`` -> EXTENDS (C++; multiple bases all EXTENDS)
    - ``call_expression`` -> CALLS (bare / ``this->m`` / ``Ns::f`` callees)
    - method in a class body -> USES the class (binding); ``this->field`` read -> USES
    Chunks: struct/class definition (class), function/method definition.
    """
    lang = language or "c"
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
        t = node.type

        if t == "preproc_include":
            path_node = node.child_by_field_name("path")
            if path_node is not None:
                path = _text(path_node, code_bytes).strip().strip('<>"')
                if path:
                    edges.append(RawEdge(module, path, "IMPORTS"))
            return

        if t in ("class_specifier", "struct_specifier"):
            body = node.child_by_field_name("body")
            if body is None:  # forward decl or type reference, not a definition
                return
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
                    attributes=_class_fields(node, code_bytes) or None,
                    language=lang,
                    file_path=file_path,
                )
            )
            base = node.child_by_field_name("base_class_clause")
            if base is None:
                for child in node.children:
                    if child.type == "base_class_clause":
                        base = child
                        break
            if base is not None:
                for sub in base.children:
                    if sub.type == "type_identifier":
                        edges.append(RawEdge(qual, _text(sub, code_bytes), "EXTENDS"))
            scope_stack.append(("class", name))
            for child in body.children:
                visit(child)
            scope_stack.pop()
            return

        if t == "function_definition":
            declarator = node.child_by_field_name("declarator")
            name = _c_decl_name(declarator, code_bytes) if declarator is not None else None
            name = name or "(anonymous)"
            parent = enclosing_class()
            start, end = _line_range(node)
            qual = f"{cur()}.{name}"  # cur() already carries the enclosing class
            chunks.append(
                Chunk(
                    text=_text(node, code_bytes),
                    start_line=start,
                    end_line=end,
                    chunk_type="method" if parent else "function",
                    function_name=name,
                    # Owning class is the SQL-queryable context: with
                    # function_name it forms the module.Class.method qualname the
                    # edge source uses, so intra-method calls + class diagrams
                    # resolve. Top-level functions keep class_name=None.
                    class_name=parent,
                    parent_class=parent,
                    language=lang,
                    file_path=file_path,
                )
            )
            if parent:
                edges.append(RawEdge(qual, parent, "USES"))
            scope_stack.append(("function", name))
            body = node.child_by_field_name("body")
            if body is not None:
                for child in body.children:
                    visit(child)
            scope_stack.pop()
            return

        if t == "call_expression":
            fn = node.child_by_field_name("function")
            if fn is not None:
                tgt = _c_callee(fn, code_bytes)
                if tgt:
                    edges.append(RawEdge(cur(), tgt, "CALLS"))

        if t == "field_expression":
            arg = node.child_by_field_name("argument")
            field = node.child_by_field_name("field")
            if arg is not None and field is not None and _text(arg, code_bytes) == "this":
                edges.append(RawEdge(cur(), _text(field, code_bytes), "USES"))

        for child in node.children:
            visit(child)

    for child in tree.root_node.children:
        visit(child)
    return chunks, edges


def walk_csharp(
    tree, code_bytes: bytes, module: str, file_path: str, language: str | None = None
) -> tuple[list[Chunk], list[RawEdge]]:
    """Walk a C# AST and emit chunks + edges.

    Verified against tree-sitter-c-sharp 0.23.5. Edges:
    - ``using_directive`` -> IMPORTS (namespace/alias target, dotted)
    - ``base_list`` on class/interface -> EXTENDS (C# does not separate base
      class from interfaces in the grammar, so every base type is EXTENDS)
    - ``invocation_expression`` -> CALLS (callee text: ``Helper`` / ``this.Run`` / ``Console.WriteLine``)
    - method in a class -> USES the class; ``this.<member>`` -> USES
    Chunks: class/interface/struct/enum/record (class), method/constructor.
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
        t = node.type

        if t == "using_directive":
            target = ""
            for child in node.children:
                if child.type in ("identifier", "qualified_name"):
                    target = _text(child, code_bytes)  # last wins (alias target)
            if target:
                edges.append(RawEdge(module, target, "IMPORTS"))
            return

        if t == "namespace_declaration":
            name_node = node.child_by_field_name("name")
            name = _text(name_node, code_bytes) if name_node else "(global)"
            scope_stack.append(("namespace", name))
            body = node.child_by_field_name("body")
            if body is not None:
                for child in body.children:
                    visit(child)
            scope_stack.pop()
            return

        if t in (
            "class_declaration",
            "interface_declaration",
            "struct_declaration",
            "enum_declaration",
            "record_declaration",
        ):
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
                    attributes=_class_fields(node, code_bytes) or None,
                    language="csharp",
                    file_path=file_path,
                )
            )
            for child in node.children:
                if child.type == "base_list":
                    for sub in child.children:
                        if sub.type in ("identifier", "qualified_name"):
                            edges.append(RawEdge(qual, _text(sub, code_bytes), "EXTENDS"))
            scope_stack.append(("class", name))
            body = node.child_by_field_name("body")
            if body is not None:
                for child in body.children:
                    visit(child)
            scope_stack.pop()
            return

        if t in ("method_declaration", "constructor_declaration", "local_function_statement"):
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
                    # Owning class is the SQL-queryable context: with
                    # function_name it forms the module.Class.method qualname the
                    # edge source uses, so intra-method calls + class diagrams
                    # resolve. Top-level functions keep class_name=None.
                    class_name=parent,
                    parent_class=parent,
                    language="csharp",
                    file_path=file_path,
                )
            )
            if parent:
                edges.append(RawEdge(qual, parent, "USES"))
            scope_stack.append(("function", name))
            body = node.child_by_field_name("body")
            if body is not None:
                for child in body.children:
                    visit(child)
            scope_stack.pop()
            return

        if t == "invocation_expression":
            fn = node.child_by_field_name("function")
            if fn is not None:
                tgt = _text(fn, code_bytes).strip()
                if tgt:
                    edges.append(RawEdge(cur(), tgt, "CALLS"))

        if t == "member_access_expression":
            name = node.child_by_field_name("name")
            expr = node.child_by_field_name("expression")
            is_this = (
                _text(expr, code_bytes).strip() == "this"
                if expr is not None
                else _text(node, code_bytes).startswith("this.")
            )
            if is_this and name is not None:
                edges.append(RawEdge(cur(), _text(name, code_bytes), "USES"))

        for child in node.children:
            visit(child)

    for child in tree.root_node.children:
        visit(child)
    return chunks, edges


def walk_kotlin(
    tree, code_bytes: bytes, module: str, file_path: str, language: str | None = None
) -> tuple[list[Chunk], list[RawEdge]]:
    """Walk a Kotlin AST and emit chunks + edges.

    Verified against tree-sitter-kotlin. Edges:
    - ``import`` (qualified_identifier) -> IMPORTS
    - ``class_declaration`` delegation_specifiers -> EXTENDS (first base) / IMPLEMENTS (remaining)
    - ``object_declaration`` -> class chunk (companion/singleton objects)
    - ``function_declaration`` inside class_body -> method chunks + CALLS
    - ``call_expression`` -> CALLS (bare identifier or navigation_expression)
    Chunks: class_declaration / object_declaration (class), function_declaration (function/method).
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

    def _flatten_navigation(node) -> str:
        """Flatten a Kotlin navigation_expression (a.b.c) to dotted string.

        tree-sitter-kotlin uses positional children [LHS, '.', RHS] — there
        are no named fields 'target' / 'suffix' on navigation_expression.
        """
        if node.type in ("identifier", "simple_identifier", "this_expression"):
            return _text(node, code_bytes)
        if node.type == "navigation_expression":
            named = [c for c in node.children if c.is_named]
            if len(named) >= 2:
                left = _flatten_navigation(named[0])
                right = _text(named[-1], code_bytes)
                return f"{left}.{right}" if left else right
            if named:
                return _flatten_navigation(named[0])
            return _text(node, code_bytes)
        return _text(node, code_bytes)

    def _emit_delegation_edges(class_qual: str, node) -> None:
        """Emit EXTENDS (first base) and IMPLEMENTS (rest) from delegation_specifiers."""
        for child in node.children:
            if child.type != "delegation_specifiers":
                continue
            first = True
            for ds in child.children:
                if ds.type != "delegation_specifier":
                    continue
                base_name: str | None = None
                for sub in ds.children:
                    if sub.type == "constructor_invocation":
                        ut = None
                        for s2 in sub.children:
                            if s2.type == "user_type":
                                ut = s2
                                break
                        if ut is not None:
                            for s2 in ut.children:
                                if s2.type == "identifier":
                                    base_name = _text(s2, code_bytes)
                                    break
                    elif sub.type == "user_type":
                        for s2 in sub.children:
                            if s2.type == "identifier":
                                base_name = _text(s2, code_bytes)
                                break
                if base_name:
                    kind = "EXTENDS" if first else "IMPLEMENTS"
                    edges.append(RawEdge(class_qual, base_name, kind))
                    first = False

    def visit(node) -> None:
        t = node.type

        if t == "import":
            # children: 'import' keyword, then qualified_identifier, optional '.*'
            qi = None
            star = False
            for ch in node.children:
                if ch.type == "qualified_identifier":
                    qi = _text(ch, code_bytes)
                elif ch.type == "*":
                    star = True
            if qi:
                target = f"{qi}.*" if star else qi
                edges.append(RawEdge(module, target, "IMPORTS"))
            return

        if t in ("class_declaration", "object_declaration"):
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
                    attributes=_class_fields(node, code_bytes) or None,
                    language="kotlin",
                    file_path=file_path,
                )
            )
            _emit_delegation_edges(qual, node)
            scope_stack.append(("class", name))
            # Kotlin class_declaration has no named 'body' field — find class_body by type
            class_body = next((c for c in node.children if c.type == "class_body"), None)
            if class_body is not None:
                for child in class_body.children:
                    visit(child)
            scope_stack.pop()
            return

        if t == "function_declaration":
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
                    # Owning class is the SQL-queryable context: with
                    # function_name it forms the module.Class.method qualname the
                    # edge source uses, so intra-method calls + class diagrams
                    # resolve. Top-level functions keep class_name=None.
                    class_name=parent,
                    parent_class=parent,
                    language="kotlin",
                    file_path=file_path,
                )
            )
            scope_stack.append(("function", name))
            # function_body wraps the block; find block inside function_body
            fn_body = next((c for c in node.children if c.type == "function_body"), None)
            if fn_body is not None:
                block = next((c for c in fn_body.children if c.type == "block"), None)
                if block is not None:
                    for child in block.children:
                        visit(child)
            scope_stack.pop()
            return

        if t == "call_expression":
            # children[0] is the callee: identifier or navigation_expression
            if node.children:
                callee = node.children[0]
                tgt = _flatten_navigation(callee)
                if tgt:
                    edges.append(RawEdge(cur(), tgt, "CALLS"))
            # don't recurse into value_arguments to avoid re-emitting nested calls
            # but we must recurse into the body parts for lambda arguments
            for child in node.children:
                visit(child)
            return

        for child in node.children:
            visit(child)

    for child in tree.root_node.children:
        visit(child)
    return chunks, edges


def walk_ruby(
    tree, code_bytes: bytes, module: str, file_path: str, language: str | None = None
) -> tuple[list[Chunk], list[RawEdge]]:
    """Walk a Ruby AST and emit chunks + edges.

    Verified against tree-sitter-ruby. Edges:
    - ``call`` with method=require/require_relative -> IMPORTS
    - ``class`` superclass -> EXTENDS
    - ``call`` with method=include/prepend -> IMPLEMENTS (mixin)
    - ``call`` inside method bodies -> CALLS
    Chunks: class / module (class), method / singleton_method (function/method).
    Ruby has no static typing so USES is not emitted.
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

    def _call_target(node) -> str:
        """Flatten a Ruby call receiver.method to dotted string."""
        receiver = node.child_by_field_name("receiver")
        method = node.child_by_field_name("method")
        method_text = _text(method, code_bytes) if method is not None else ""
        if receiver is not None:
            rec_text = _text(receiver, code_bytes)
            return f"{rec_text}.{method_text}" if method_text else rec_text
        return method_text

    def visit(node) -> None:
        t = node.type

        if t == "call":
            method_node = node.child_by_field_name("method")
            method_name = _text(method_node, code_bytes) if method_node is not None else ""
            receiver = node.child_by_field_name("receiver")

            if method_name in ("require", "require_relative") and receiver is None:
                args = node.child_by_field_name("arguments")
                if args is not None:
                    for ch in args.children:
                        if ch.type in ("string", "simple_string"):
                            for sub in ch.children:
                                if sub.type in ("string_content", "string_value"):
                                    edges.append(RawEdge(module, _text(sub, code_bytes), "IMPORTS"))
                                    break
                            else:
                                raw = _text(ch, code_bytes).strip("'\"")
                                if raw:
                                    edges.append(RawEdge(module, raw, "IMPORTS"))
                return

            if method_name in ("include", "prepend") and receiver is None:
                args = node.child_by_field_name("arguments")
                if args is not None:
                    cls_qual = cur()
                    for ch in args.children:
                        if ch.type == "constant":
                            edges.append(RawEdge(cls_qual, _text(ch, code_bytes), "IMPLEMENTS"))
                return

            # generic call — emit CALLS
            tgt = _call_target(node)
            if tgt:
                edges.append(RawEdge(cur(), tgt, "CALLS"))
            for child in node.children:
                visit(child)
            return

        if t in ("class", "module"):
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
                    attributes=_ruby_instance_var_fields(node, code_bytes) or None,
                    language="ruby",
                    file_path=file_path,
                )
            )
            if t == "class":
                sup = node.child_by_field_name("superclass")
                if sup is not None:
                    for ch in sup.children:
                        if ch.type == "constant":
                            edges.append(RawEdge(qual, _text(ch, code_bytes), "EXTENDS"))
                            break
            scope_stack.append(("class", name))
            body = node.child_by_field_name("body")
            if body is not None:
                for child in body.children:
                    visit(child)
            scope_stack.pop()
            return

        if t in ("method", "singleton_method"):
            name_node = node.child_by_field_name("name")
            name = _text(name_node, code_bytes) if name_node else "(anonymous)"
            parent = enclosing_class()
            start, end = _line_range(node)
            chunks.append(
                Chunk(
                    text=_text(node, code_bytes),
                    start_line=start,
                    end_line=end,
                    chunk_type="method" if parent else "function",
                    function_name=name,
                    # Owning class is the SQL-queryable context: with
                    # function_name it forms the module.Class.method qualname the
                    # edge source uses, so intra-method calls + class diagrams
                    # resolve. Top-level functions keep class_name=None.
                    class_name=parent,
                    parent_class=parent,
                    language="ruby",
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

        for child in node.children:
            visit(child)

    for child in tree.root_node.children:
        visit(child)
    return chunks, edges


def walk_php(
    tree, code_bytes: bytes, module: str, file_path: str, language: str | None = None
) -> tuple[list[Chunk], list[RawEdge]]:
    """Walk a PHP AST and emit chunks + edges.

    Verified against tree-sitter-php 0.23+. Edges:
    - ``namespace_use_declaration`` -> IMPORTS (each clause)
    - ``class_declaration`` base_clause -> EXTENDS
    - ``class_declaration`` class_interface_clause -> IMPLEMENTS (each name)
    - ``interface_declaration`` -> class chunk
    - ``function_call_expression`` -> CALLS
    - ``member_call_expression`` -> CALLS (object->method)
    - ``scoped_call_expression`` -> CALLS (Class::method)
    Chunks: class_declaration / interface_declaration (class),
    method_declaration / function_definition (method/function).
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

    def _gather_use_targets(node) -> list[str]:
        """Extract import targets from a namespace_use_declaration.

        Handles both simple form:  use App\\Foo;
        and grouped form:          use App\\Http\\{HomeController, UserController};

        For grouped form, tree-sitter-php emits a sibling namespace_name node
        (the common prefix) followed by a namespace_use_group containing the
        individual namespace_use_clause nodes — each clause text is the bare
        name only, so we must prepend the prefix ourselves.
        """
        targets: list[str] = []

        # Detect grouped form: a namespace_use_group sibling is present.
        prefix_node = next((c for c in node.children if c.type == "namespace_name"), None)
        group_node = next((c for c in node.children if c.type == "namespace_use_group"), None)

        if group_node is not None:
            prefix = _text(prefix_node, code_bytes).strip("\\") if prefix_node else ""
            for ch in group_node.children:
                if ch.type == "namespace_use_clause":
                    clause_text = _text(ch, code_bytes).strip("\\")
                    targets.append(f"{prefix}\\{clause_text}" if prefix else clause_text)
        else:
            # Simple form: each namespace_use_clause text is already the full path.
            def _collect(n) -> None:
                if n.type == "namespace_use_clause":
                    targets.append(_text(n, code_bytes).strip("\\"))
                    return
                for ch in n.children:
                    _collect(ch)

            _collect(node)

        return targets

    def visit(node) -> None:
        t = node.type

        if t == "namespace_use_declaration":
            for tgt in _gather_use_targets(node):
                if tgt:
                    edges.append(RawEdge(module, tgt, "IMPORTS"))
            return

        if t in ("class_declaration", "interface_declaration"):
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
                    attributes=_class_fields(node, code_bytes) or None,
                    language="php",
                    file_path=file_path,
                )
            )
            # base_clause and class_interface_clause are unkeyed children in this grammar
            for child in node.children:
                if child.type == "base_clause":
                    for ch in child.children:
                        if ch.type == "name":
                            edges.append(RawEdge(qual, _text(ch, code_bytes), "EXTENDS"))
                            break
                elif child.type == "class_interface_clause":
                    for ch in child.children:
                        if ch.type == "name":
                            edges.append(RawEdge(qual, _text(ch, code_bytes), "IMPLEMENTS"))
            scope_stack.append(("class", name))
            body = node.child_by_field_name("body")
            if body is not None:
                for child in body.children:
                    visit(child)
            scope_stack.pop()
            return

        if t in ("method_declaration", "function_definition"):
            name_node = node.child_by_field_name("name")
            name = _text(name_node, code_bytes) if name_node else "(anonymous)"
            parent = enclosing_class()
            start, end = _line_range(node)
            chunks.append(
                Chunk(
                    text=_text(node, code_bytes),
                    start_line=start,
                    end_line=end,
                    chunk_type="method" if parent else "function",
                    function_name=name,
                    # Owning class is the SQL-queryable context: with
                    # function_name it forms the module.Class.method qualname the
                    # edge source uses, so intra-method calls + class diagrams
                    # resolve. Top-level functions keep class_name=None.
                    class_name=parent,
                    parent_class=parent,
                    language="php",
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

        if t == "function_call_expression":
            fn = node.child_by_field_name("function")
            if fn is not None:
                edges.append(RawEdge(cur(), _text(fn, code_bytes), "CALLS"))
            for child in node.children:
                visit(child)
            return

        if t == "member_call_expression":
            # object->method(args)
            obj = node.child_by_field_name("object")
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                obj_text = _text(obj, code_bytes) if obj is not None else ""
                tgt = (
                    f"{obj_text}.{_text(name_node, code_bytes)}"
                    if obj_text
                    else _text(name_node, code_bytes)
                )
                edges.append(RawEdge(cur(), tgt, "CALLS"))
            for child in node.children:
                visit(child)
            return

        if t == "scoped_call_expression":
            # Class::method(args)
            cls_node = node.child_by_field_name("class_name") or node.child_by_field_name("scope")
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                cls_text = _text(cls_node, code_bytes) if cls_node is not None else ""
                tgt = (
                    f"{cls_text}::{_text(name_node, code_bytes)}"
                    if cls_text
                    else _text(name_node, code_bytes)
                )
                edges.append(RawEdge(cur(), tgt, "CALLS"))
            for child in node.children:
                visit(child)
            return

        for child in node.children:
            visit(child)

    for child in tree.root_node.children:
        visit(child)
    return chunks, edges


def walk_swift(
    tree, code_bytes: bytes, module: str, file_path: str, language: str | None = None
) -> tuple[list[Chunk], list[RawEdge]]:
    """Walk a Swift AST and emit chunks + edges.

    Verified against tree-sitter-swift. Edges:
    - ``import_declaration`` -> IMPORTS (identifier text)
    - ``class_declaration`` / ``struct_declaration`` inheritance_specifier ->
      EXTENDS (first) / IMPLEMENTS (rest)
    - ``protocol_declaration`` -> class chunk
    - ``function_declaration`` / ``protocol_function_declaration`` -> chunks + CALLS
    - ``call_expression`` -> CALLS (simple_identifier or navigation_expression callee)
    - ``navigation_expression`` with self target -> USES
    Chunks: class_declaration / struct_declaration / protocol_declaration (class),
    function_declaration / protocol_function_declaration (function/method).
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

    def _flatten_nav(node) -> str:
        """Flatten a Swift navigation_expression to a dotted string."""
        if node.type in ("simple_identifier", "identifier"):
            return _text(node, code_bytes)
        if node.type == "navigation_expression":
            target = node.child_by_field_name("target")
            suffix = node.child_by_field_name("suffix")
            left = _flatten_nav(target) if target is not None else ""
            if suffix is not None:
                for ch in suffix.children:
                    if ch.type in ("simple_identifier", "identifier"):
                        right = _text(ch, code_bytes)
                        return f"{left}.{right}" if left else right
            return left
        return _text(node, code_bytes)

    def _emit_inheritance_edges(class_qual: str, node) -> None:
        """Emit EXTENDS (first) / IMPLEMENTS (rest) from inheritance_specifier siblings."""
        first = True
        for child in node.children:
            if child.type == "inheritance_specifier":
                base_name: str | None = None
                for sub in child.children:
                    if sub.type == "user_type":
                        for s2 in sub.children:
                            if s2.type == "type_identifier":
                                base_name = _text(s2, code_bytes)
                                break
                        break
                    if sub.type == "type_identifier":
                        base_name = _text(sub, code_bytes)
                        break
                if base_name:
                    kind = "EXTENDS" if first else "IMPLEMENTS"
                    edges.append(RawEdge(class_qual, base_name, kind))
                    first = False

    def visit(node) -> None:
        t = node.type

        if t == "import_declaration":
            for child in node.children:
                if child.type == "identifier":
                    edges.append(RawEdge(module, _text(child, code_bytes), "IMPORTS"))
                    break
            return

        if t in ("class_declaration", "protocol_declaration"):
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
                    attributes=_class_fields(node, code_bytes) or None,
                    language="swift",
                    file_path=file_path,
                )
            )
            _emit_inheritance_edges(qual, node)
            scope_stack.append(("class", name))
            body = node.child_by_field_name("body")
            if body is not None:
                for child in body.children:
                    visit(child)
            scope_stack.pop()
            return

        if t in ("function_declaration", "protocol_function_declaration"):
            name_node = node.child_by_field_name("name")
            name = _text(name_node, code_bytes) if name_node else "(anonymous)"
            parent = enclosing_class()
            start, end = _line_range(node)
            chunks.append(
                Chunk(
                    text=_text(node, code_bytes),
                    start_line=start,
                    end_line=end,
                    chunk_type="method" if parent else "function",
                    function_name=name,
                    # Owning class is the SQL-queryable context: with
                    # function_name it forms the module.Class.method qualname the
                    # edge source uses, so intra-method calls + class diagrams
                    # resolve. Top-level functions keep class_name=None.
                    class_name=parent,
                    parent_class=parent,
                    language="swift",
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

        if t == "call_expression":
            if node.children:
                callee = node.children[0]
                tgt = _flatten_nav(callee)
                if tgt:
                    edges.append(RawEdge(cur(), tgt, "CALLS"))
            for child in node.children:
                visit(child)
            return

        if t == "navigation_expression":
            target = node.child_by_field_name("target")
            suffix = node.child_by_field_name("suffix")
            if target is not None and target.type == "self_expression" and suffix is not None:
                for ch in suffix.children:
                    if ch.type in ("simple_identifier", "identifier"):
                        edges.append(RawEdge(cur(), _text(ch, code_bytes), "USES"))
                        break

        for child in node.children:
            visit(child)

    for child in tree.root_node.children:
        visit(child)
    return chunks, edges


def walk_scala(
    tree, code_bytes: bytes, module: str, file_path: str, language: str | None = None
) -> tuple[list[Chunk], list[RawEdge]]:
    """Walk a Scala AST and emit chunks + edges.

    Verified against tree-sitter-scala. Edges:
    - ``import_declaration`` -> IMPORTS (dotted path, with namespace_selectors expanded)
    - ``class_definition`` / ``trait_definition`` extends_clause -> EXTENDS (first) /
      IMPLEMENTS (rest, after ``with`` keyword)
    - ``object_definition`` -> class chunk (companion objects / singletons)
    - ``function_definition`` / ``function_declaration`` -> chunks + CALLS
    - ``call_expression`` -> CALLS (identifier or field_expression callee)
    - ``field_expression`` with ``this`` value -> USES
    Chunks: class_definition / trait_definition / object_definition (class),
    function_definition / function_declaration (function/method).
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

    def _flatten_field(node) -> str:
        """Flatten a Scala field_expression (a.b) or identifier to dotted string."""
        if node.type in ("identifier", "type_identifier"):
            return _text(node, code_bytes)
        if node.type == "field_expression":
            val = node.child_by_field_name("value")
            field = node.child_by_field_name("field")
            left = _flatten_field(val) if val is not None else ""
            right = _text(field, code_bytes) if field is not None else ""
            return f"{left}.{right}" if left and right else (left or right)
        return _text(node, code_bytes)

    def _import_path(node) -> str:
        """Reconstruct import path from import_declaration children."""
        parts: list[str] = []
        for ch in node.children:
            if ch.type == "identifier":
                parts.append(_text(ch, code_bytes))
            elif ch.type == "namespace_selectors":
                # {A, B} -> emit per-selector later; signal with placeholder
                parts.append("{}")
                break
        return ".".join(p for p in parts if p != "{}")

    def _emit_import(node) -> None:
        base = _import_path(node)
        # Check for namespace_selectors
        for ch in node.children:
            if ch.type == "namespace_selectors":
                for sub in ch.children:
                    if sub.type == "identifier":
                        target = (
                            f"{base}.{_text(sub, code_bytes)}" if base else _text(sub, code_bytes)
                        )
                        edges.append(RawEdge(module, target, "IMPORTS"))
                return
        if base:
            edges.append(RawEdge(module, base, "IMPORTS"))

    def _emit_extends_edges(class_qual: str, node) -> None:
        """Emit EXTENDS / IMPLEMENTS from extends_clause."""
        for child in node.children:
            if child.type != "extends_clause":
                continue
            first = True
            for sub in child.children:
                if sub.type in ("type_identifier", "identifier"):
                    kind = "EXTENDS" if first else "IMPLEMENTS"
                    edges.append(RawEdge(class_qual, _text(sub, code_bytes), kind))
                    first = False

    def visit(node) -> None:
        t = node.type

        if t == "import_declaration":
            _emit_import(node)
            return

        if t in ("class_definition", "trait_definition", "object_definition"):
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
                    attributes=_class_fields(node, code_bytes) or None,
                    language="scala",
                    file_path=file_path,
                )
            )
            _emit_extends_edges(qual, node)
            scope_stack.append(("class", name))
            body = node.child_by_field_name("body")
            if body is not None:
                for child in body.children:
                    visit(child)
            scope_stack.pop()
            return

        if t in ("function_definition", "function_declaration"):
            name_node = node.child_by_field_name("name")
            name = _text(name_node, code_bytes) if name_node else "(anonymous)"
            parent = enclosing_class()
            start, end = _line_range(node)
            chunks.append(
                Chunk(
                    text=_text(node, code_bytes),
                    start_line=start,
                    end_line=end,
                    chunk_type="method" if parent else "function",
                    function_name=name,
                    # Owning class is the SQL-queryable context: with
                    # function_name it forms the module.Class.method qualname the
                    # edge source uses, so intra-method calls + class diagrams
                    # resolve. Top-level functions keep class_name=None.
                    class_name=parent,
                    parent_class=parent,
                    language="scala",
                    file_path=file_path,
                )
            )
            scope_stack.append(("function", name))
            body = node.child_by_field_name("body")
            if body is not None:
                if body.type == "block":
                    for child in body.children:
                        visit(child)
                else:
                    # Expression-body: def f() = expr  — body IS the expression node.
                    visit(body)
            scope_stack.pop()
            return

        if t == "call_expression":
            fn = node.child_by_field_name("function")
            if fn is not None:
                tgt = _flatten_field(fn)
                if tgt:
                    edges.append(RawEdge(cur(), tgt, "CALLS"))
            for child in node.children:
                visit(child)
            return

        if t == "field_expression":
            val = node.child_by_field_name("value")
            field = node.child_by_field_name("field")
            if val is not None and field is not None and _text(val, code_bytes) == "this":
                edges.append(RawEdge(cur(), _text(field, code_bytes), "USES"))

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
    "rust": walk_rust,
    "java": walk_java,
    "c": walk_c_family,
    "cpp": walk_c_family,
    "csharp": walk_csharp,
    "kotlin": walk_kotlin,
    "ruby": walk_ruby,
    "php": walk_php,
    "swift": walk_swift,
    "scala": walk_scala,
}
