"""Deterministic Mermaid diagram generators from the mememo relations graph.

Phase 1: class diagram, call graph, module dependency. No LLM.
"""

from __future__ import annotations

import re
import sqlite3


def _mmid(s: str) -> str:
    """Return a safe Mermaid node identifier: strip non-[A-Za-z0-9_], prefix
    with underscore if the result starts with a digit or is empty."""
    clean = re.sub(r"[^A-Za-z0-9_]", "_", s or "unknown")
    if not clean or clean[0].isdigit():
        clean = "_" + clean
    return clean


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
) -> str:
    """Mermaid classDiagram for classes in the given repo/branch.

    scope=None => whole repo; scope=a file_path => only classes in that file;
    scope=a class_name => only that class + its direct supertypes/implementors.
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
        f"SELECT DISTINCT m.id, m.class_name, m.file_path "
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
            classes[cn] = {"id": r["id"], "file_path": r["file_path"]}

    if not classes:
        return "classDiagram\n%% no data"

    lines = ["classDiagram"]

    # Emit class bodies with methods.
    for class_name, info in classes.items():
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
        if methods:
            method_lines = "\n".join(f"    +{m}()" for m in methods)
            lines.append(f"class {mmid} {{\n{method_lines}\n}}")
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
