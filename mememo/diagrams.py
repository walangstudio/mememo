"""Deterministic Mermaid diagram generators from the mememo relations graph.

Phase 1: class diagram, call graph, module dependency. No LLM.
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path


def _mmid(s: str) -> str:
    """Return a safe Mermaid node identifier: strip non-[A-Za-z0-9_], prefix
    with underscore if the result starts with a digit or is empty."""
    clean = re.sub(r"[^A-Za-z0-9_]", "_", s or "unknown")
    if not clean or clean[0].isdigit():
        clean = "_" + clean
    return clean


def is_empty_diagram(mermaid: str) -> bool:
    """True if the diagram has only a header + comments (e.g. ``%% no data``).

    Mermaid raises a parse error ("Expecting ..., got 'EOF'") on a diagram whose
    body is only a comment, so callers must surface "no data" instead of trying
    to render it.
    """
    lines = (mermaid or "").splitlines()
    for line in lines[1:]:  # skip the header (classDiagram / flowchart ...)
        s = line.strip()
        if s and not s.startswith("%%"):
            return False
    return True


def _esc(s: str | None) -> str:
    """Escape a string for use inside a Mermaid double-quoted label.

    A raw ``"`` or newline in a file/class/function name would break (or inject)
    the surrounding ``["..."]`` node; Mermaid renders the HTML entity ``#quot;``.
    """
    return (s or "").replace("\\", "/").replace('"', "#quot;").replace("\n", " ").strip()


def _method(name: str | None) -> str:
    """Sanitize a method name for a Mermaid class body (drops chars that would
    break the ``{ ... }`` block, keeps Ruby-style ``?!=`` suffixes)."""
    return re.sub(r"[^A-Za-z0-9_?!=]", "", name or "")


def _attr_name(attr: str) -> str:
    """Field name from a stored ``"name"`` / ``"name: type"`` attribute, kept to
    chars that are safe inside a Mermaid ``{ ... }`` class body."""
    name = (attr or "").split(":", 1)[0].strip()
    return re.sub(r"[^A-Za-z0-9_]", "", name)


# A type token is rendered only when it maps to this safe charset (after the
# bracket→``~`` generic rewrite): letters/digits/_/./~, plus commas and spaces
# for multi-arg generics. Anything else (unions ``|``, callables, quotes) is
# dropped so an exotic annotation can never break the Mermaid parse.
_SAFE_TYPE_RE = re.compile(r"^[A-Za-z0-9_~., ]+$")


def _attr_member(attr: str, name: str) -> str:
    """Mermaid class-body line for one field, ``+<type> <name>`` (UML order).

    The stored attribute is ``"name"`` or ``"name: type"``. When a type is
    present and renders to a Mermaid-safe token — generics ``[]``/``<>`` become
    ``~ … ~`` (Mermaid's generic syntax) — it's shown before the name; otherwise
    we fall back to ``+<name>`` so the diagram never fails to parse.
    """
    _, sep, raw_type = attr.partition(":")
    if sep:
        t = raw_type.strip()
        for op, cl in (("[", "]"), ("<", ">"), ("(", ")"), ("{", "}")):
            t = t.replace(op, "~").replace(cl, "~")
        # ``~~`` (from nested generics) or unbalanced markers render oddly, so
        # only accept a single, balanced level of generic nesting.
        if t and "~~" not in t and t.count("~") % 2 == 0 and _SAFE_TYPE_RE.match(t):
            return f"    +{t} {name}"
    return f"    +{name}"


def _load_attributes(base_dir: Path | None, content_ref: str | None) -> list[str]:
    """Read a class memory's ``attributes`` list from its content blob.

    Returns [] when no base_dir is available (caller didn't pass one), the blob
    is missing/unreadable, or the chunk carries no attributes — so class
    diagrams degrade to methods-only rather than failing.
    """
    if not base_dir or not content_ref:
        return []
    try:
        blob = json.loads((base_dir / content_ref).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    attrs = blob.get("attributes")
    return attrs if isinstance(attrs, list) else []


def _label(file_path: str | None, class_name: str | None, fn_name: str | None) -> str:
    """Compact label: file:Class.fn or fallbacks."""
    parts = []
    if file_path:
        parts.append(file_path.split("/")[-1].split("\\")[-1])
    qualified = ""
    if class_name and fn_name:
        qualified = f"{class_name}.{fn_name}"
    elif class_name:
        qualified = class_name
    elif fn_name:
        qualified = fn_name
    if qualified:
        if parts:
            return f"{parts[0]}:{qualified}"
        return qualified
    return parts[0] if parts else "unknown"


def class_diagram(
    conn: sqlite3.Connection,
    repo_id: str,
    branch: str,
    scope: str | None = None,
    base_dir: Path | None = None,
) -> str:
    """Mermaid classDiagram for classes in the given repo/branch.

    scope=None => whole repo; scope=a file_path => only classes in that file;
    scope=a class_name => only that class + its direct supertypes/implementors.
    When ``base_dir`` (the storage base dir) is given, class fields are read from
    each class's content blob and rendered as attribute rows; without it the
    diagram shows methods only.
    """
    # Determine which class memories to include.
    # Class rows: chunk_type='class' or (class_name set and function_name null).
    conditions = [
        "m.repo_id = ?",
        "m.branch_name = ?",
        "m.class_name IS NOT NULL",
        "(m.chunk_type = 'class' OR m.function_name IS NULL)",
    ]
    params: list = [repo_id, branch]

    if scope:
        conditions.append("(m.file_path = ? OR m.class_name = ?)")
        params.extend([scope, scope])

    where = " AND ".join(conditions)
    rows = conn.execute(
        f"SELECT DISTINCT m.id, m.class_name, m.file_path, m.content_ref "
        f"FROM memories m "
        f"WHERE {where} AND m.stale = 0",
        params,
    ).fetchall()

    # Deduplicate by class_name (may have multiple rows with same class_name).
    classes: dict[str, dict] = {}
    for r in rows:
        cn = r["class_name"] or ""
        if not cn:
            continue
        if cn not in classes:
            classes[cn] = {
                "id": r["id"],
                "file_path": r["file_path"],
                "content_ref": r["content_ref"],
            }

    if not classes:
        return "classDiagram\n%% no data"

    lines = ["classDiagram"]

    # Emit class bodies with methods.
    for idx, (class_name, info) in enumerate(classes.items()):
        mmid = _mmid(class_name)
        method_rows = conn.execute(
            "SELECT DISTINCT function_name FROM memories "
            "WHERE repo_id = ? AND branch_name = ? AND class_name = ? "
            "AND function_name IS NOT NULL AND stale = 0 "
            "LIMIT 25",
            (repo_id, branch, class_name),
        ).fetchall()
        methods = [_method(r["function_name"]) for r in method_rows if r["function_name"]]
        methods = [m for m in methods if m]

        # Field rows from the class's stored attributes (deduped, kept order).
        # Cap blob reads so a huge whole-repo diagram doesn't do one file read
        # per class (the diagram is already unusable past a few dozen classes).
        seen_attrs: set[str] = set()
        attr_lines: list[str] = []
        attr_src = _load_attributes(base_dir, info.get("content_ref")) if idx < 80 else []
        for a in attr_src:
            n = _attr_name(a)
            if n and n not in seen_attrs:
                seen_attrs.add(n)
                attr_lines.append(_attr_member(a, n))

        body = attr_lines + [f"    +{m}()" for m in methods]
        if body:
            lines.append(f"class {mmid} {{\n" + "\n".join(body) + "\n}")
        else:
            lines.append(f"class {mmid}")

    # Emit inheritance / implementation edges from EXTENDS/IMPLEMENTS relations.
    class_ids = {info["id"] for info in classes.values()}
    if class_ids:
        placeholders = ",".join("?" * len(class_ids))
        edge_rows = conn.execute(
            f"SELECT r.type, r.target_symbol, "
            f"       sm.class_name AS src_class, tm.class_name AS tgt_class "
            f"FROM relations r "
            f"LEFT JOIN memories sm ON sm.id = r.source_memory_id "
            f"LEFT JOIN memories tm ON tm.id = r.target_memory_id "
            f"WHERE r.source_memory_id IN ({placeholders}) "
            f"AND r.type IN ('EXTENDS', 'IMPLEMENTS') AND r.stale = 0",
            list(class_ids),
        ).fetchall()

        seen_edges: set[tuple[str, str, str]] = set()
        for e in edge_rows:
            src = e["src_class"] or ""
            tgt = e["tgt_class"] or e["target_symbol"] or ""
            if not src or not tgt:
                continue
            key = (e["type"], src, tgt)
            if key in seen_edges:
                continue
            seen_edges.add(key)
            src_id = _mmid(src)
            tgt_id = _mmid(tgt)
            # Ensure target class node exists even if not in scope.
            if tgt not in classes:
                lines.append(f"class {tgt_id}")
            if e["type"] == "EXTENDS":
                lines.append(f"{tgt_id} <|-- {src_id}")
            else:  # IMPLEMENTS
                lines.append(f"{tgt_id} <|.. {src_id}")

    return "\n".join(lines)


def call_graph(
    conn: sqlite3.Connection,
    root_memory_id: str,
    depth: int = 2,
    max_nodes: int = 60,
) -> str:
    """Mermaid flowchart LR BFS over CALLS edges from root_memory_id.

    Resolved callees (target_memory_id) are expanded up to ``depth``; unresolved
    callees (external/stdlib symbols, target_memory_id NULL) are rendered as leaf
    nodes keyed by their ``target_symbol`` so the graph still shows what a
    function calls. ``max_nodes`` caps the node count (BFS halts, not just the
    inner batch).
    """
    visited: set[str] = {root_memory_id}
    frontier: set[str] = {root_memory_id}
    # edge = (src_id, tgt_key, label_for_tgt); tgt_key is the memory id when
    # resolved, else "sym:<symbol>" so external calls become distinct leaves.
    edges_out: list[tuple[str, str, str]] = []
    node_labels: dict[str, str] = {}
    truncated = False

    for _ in range(depth):
        if not frontier or truncated:
            break
        next_frontier: set[str] = set()
        placeholders = ",".join("?" * len(frontier))
        rows = conn.execute(
            f"SELECT r.source_memory_id, r.target_memory_id, r.target_symbol, "
            f"       sm.file_path AS src_file, sm.class_name AS src_class, sm.function_name AS src_fn, "
            f"       tm.file_path AS tgt_file, tm.class_name AS tgt_class, tm.function_name AS tgt_fn "
            f"FROM relations r "
            f"LEFT JOIN memories sm ON sm.id = r.source_memory_id "
            f"LEFT JOIN memories tm ON tm.id = r.target_memory_id "
            f"WHERE r.source_memory_id IN ({placeholders}) AND r.type = 'CALLS' AND r.stale = 0",
            list(frontier),
        ).fetchall()

        for row in rows:
            src_id = row["source_memory_id"]
            tgt_id = row["target_memory_id"]
            tgt_sym = row["target_symbol"]
            if tgt_id:
                tgt_key = tgt_id
                tgt_label = _label(row["tgt_file"], row["tgt_class"], row["tgt_fn"])
            elif tgt_sym:
                tgt_key = f"sym:{tgt_sym}"
                tgt_label = tgt_sym
            else:
                continue  # nothing to point at

            if tgt_key not in visited and len(visited) >= max_nodes:
                truncated = True
                break

            node_labels.setdefault(src_id, _label(row["src_file"], row["src_class"], row["src_fn"]))
            node_labels.setdefault(tgt_key, tgt_label)
            edges_out.append((src_id, tgt_key, tgt_label))

            # Only resolved targets are traversable.
            if tgt_id and tgt_id not in visited:
                visited.add(tgt_id)
                next_frontier.add(tgt_id)

        frontier = next_frontier

    if not edges_out:
        return "flowchart LR\n%% no data"

    lines = ["flowchart LR"]
    if truncated:
        lines.append(f"%% truncated at {max_nodes} nodes")

    seen_edges: set[tuple[str, str]] = set()
    for src_id, tgt_key, _lbl in edges_out:
        key = (src_id, tgt_key)
        if key in seen_edges:
            continue
        seen_edges.add(key)
        s_mmid = _mmid(src_id)
        t_mmid = _mmid(tgt_key)
        s_lbl = _esc(node_labels.get(src_id) or src_id[:8])
        t_lbl = _esc(node_labels.get(tgt_key) or tgt_key[:8])
        lines.append(f'    {s_mmid}["{s_lbl}"] --> {t_mmid}["{t_lbl}"]')

    return "\n".join(lines)


def module_dependency(
    conn: sqlite3.Connection,
    repo_id: str,
    branch: str,
    max_nodes: int = 80,
) -> str:
    """Mermaid flowchart LR of cross-file IMPORTS edges grouped by file."""
    rows = conn.execute(
        "SELECT sm.file_path AS src_file, tm.file_path AS tgt_file, r.target_symbol "
        "FROM relations r "
        "LEFT JOIN memories sm ON sm.id = r.source_memory_id "
        "LEFT JOIN memories tm ON tm.id = r.target_memory_id "
        "WHERE r.repo_id = ? AND r.branch = ? AND r.type = 'IMPORTS' AND r.stale = 0",
        (repo_id, branch),
    ).fetchall()

    # Group to file->file edges (cross-file only).
    seen_edges: set[tuple[str, str]] = set()
    node_files: set[str] = set()
    truncated = False

    for row in rows:
        src_file = row["src_file"] or ""
        tgt_file = row["tgt_file"] or ""
        # For unresolved imports, derive a pseudo-module from target_symbol.
        if not tgt_file and row["target_symbol"]:
            tgt_file = row["target_symbol"]
        if not src_file or not tgt_file:
            continue
        if src_file == tgt_file:
            continue
        key = (src_file, tgt_file)
        if key in seen_edges:
            continue
        # Cap nodes.
        if (
            len(node_files)
            + (0 if src_file in node_files else 1)
            + (0 if tgt_file in node_files else 1)
        ) > max_nodes:
            truncated = True
            continue
        seen_edges.add(key)
        node_files.add(src_file)
        node_files.add(tgt_file)

    if not seen_edges:
        return "flowchart LR\n%% no data"

    lines = ["flowchart LR"]
    if truncated:
        lines.append(f"%% truncated at {max_nodes} nodes")

    def _basename(p: str) -> str:
        return p.split("/")[-1].split("\\")[-1]

    for src_file, tgt_file in sorted(seen_edges):
        s_mmid = _mmid(src_file)
        t_mmid = _mmid(tgt_file)
        s_lbl = _esc(_basename(src_file))
        t_lbl = _esc(_basename(tgt_file))
        lines.append(f'    {s_mmid}["{s_lbl}"] --> {t_mmid}["{t_lbl}"]')

    return "\n".join(lines)
