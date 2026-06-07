"""generate_diagram MCP tool.

Phase 1 — deterministic Mermaid from the code graph (no LLM):
    type in {class, call, module, overview}.

Phase 2 — LLM-synthesized, passthrough-aware:
    type in {sequence, usecase, state, erd, flow}.
    The deterministic subgraph + the scope's source are assembled as grounding,
    then either (a) returned as a passthrough_prompt for the host model to
    synthesize the Mermaid in chat (default, no API key), or (b) completed
    directly when an LLM provider is configured. Mirrors tools/capture.py.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from ..core.llm_adapter import LLMAdapter
    from ..core.memory_manager import MemoryManager

logger = logging.getLogger(__name__)


DiagramType = Literal[
    "class", "call", "module", "overview", "sequence", "usecase", "state", "erd", "flow"
]
_PHASE1_TYPES = frozenset({"class", "call", "module", "overview"})
_PHASE2_TYPES = frozenset({"sequence", "usecase", "state", "erd", "flow"})

# Grounding budget: how much scope source text to feed the model. Generous
# enough to capture a handful of function bodies, bounded so the prompt stays
# within a single cache-friendly request.
_GROUNDING_CHAR_BUDGET = 8000


class GenerateDiagramParams(BaseModel):
    type: str = Field(
        description=(
            "class | call | module | overview (deterministic). "
            "sequence | usecase | state | erd | flow (LLM/passthrough)."
        )
    )
    scope: str | None = Field(
        default=None,
        description=(
            "class/state: file_path or class_name. call/sequence: memory_id or "
            "function_name. module/usecase/erd: optional file_path or dir prefix; "
            "None = whole repo."
        ),
    )
    repo_path: str | None = Field(
        default=None,
        description="Repo path to detect repo_id/branch from. Uses current working dir if None.",
    )
    repo_id: str | None = Field(default=None, description="Override repo_id (skips detection).")
    branch: str | None = Field(default=None, description="Override branch (skips detection).")
    depth: int = Field(default=2, ge=1, le=6, description="BFS depth for call/sequence graphs.")
    max_nodes: int = Field(default=60, ge=1, le=500, description="Node cap for graph diagrams.")


class GenerateDiagramResponse(BaseModel):
    success: bool
    type: str
    mermaid: str = ""
    truncated: bool = False
    message: str = ""
    # Phase 2 passthrough: when no LLM is configured, the host model completes
    # passthrough_prompt to produce the Mermaid (passthrough=True).
    passthrough: bool = False
    passthrough_prompt: str = ""


async def generate_diagram(
    params: GenerateDiagramParams,
    memory_manager: MemoryManager,
    llm_adapter: LLMAdapter,
) -> GenerateDiagramResponse:
    if params.type not in _PHASE1_TYPES and params.type not in _PHASE2_TYPES:
        return GenerateDiagramResponse(
            success=False,
            type=params.type,
            message=(
                f"Unknown diagram type {params.type!r}. Choose one of: "
                "class, call, module, overview, sequence, usecase, state, erd, flow."
            ),
        )

    repo_id, branch = await _resolve_repo_branch(params, memory_manager)

    if params.type in _PHASE1_TYPES:
        return await _phase1(params, memory_manager, repo_id, branch)

    return await _phase2(params, memory_manager, llm_adapter, repo_id, branch)


async def _resolve_repo_branch(
    params: GenerateDiagramParams, memory_manager: MemoryManager
) -> tuple[str, str]:
    repo_id = params.repo_id
    branch = params.branch
    if repo_id and branch:
        return repo_id, branch
    try:
        ctx = await memory_manager.git_manager.detect_context(params.repo_path or ".")
        return repo_id or ctx.repo.id, branch or ctx.branch.name
    except Exception as exc:
        logger.warning("generate_diagram: git context detection failed: %s", exc)
        return repo_id or "", branch or ""


# --------------------------------------------------------------------------
# Phase 1 — deterministic
# --------------------------------------------------------------------------


async def _phase1(
    params: GenerateDiagramParams, memory_manager: MemoryManager, repo_id: str, branch: str
) -> GenerateDiagramResponse:
    from ..diagrams import (
        call_graph,
        class_diagram,
        is_empty_diagram,
        module_dependency,
        overview_diagram,
    )

    conn = memory_manager.storage_manager.conn
    base_dir = memory_manager.storage_manager.base_dir
    mermaid = ""

    if params.type == "class":
        mermaid = class_diagram(conn, repo_id, branch, scope=params.scope, base_dir=base_dir)
        truncated = False
    elif params.type == "module":
        mermaid = module_dependency(conn, repo_id, branch, max_nodes=params.max_nodes)
        truncated = "%% truncated" in mermaid
    elif params.type == "overview":
        mermaid = overview_diagram(
            conn, repo_id, branch, max_nodes=params.max_nodes, depth=params.depth
        )
        truncated = "%% truncated" in mermaid
    else:  # call
        root_id = await _resolve_call_root(conn, repo_id, branch, params.scope)
        if root_id is None:
            return GenerateDiagramResponse(
                success=False,
                type=params.type,
                message=(
                    f"Could not resolve call graph root from scope={params.scope!r}. "
                    "Pass a memory_id or a function_name that exists in the indexed graph."
                ),
            )
        mermaid = call_graph(conn, root_id, depth=params.depth, max_nodes=params.max_nodes)
        truncated = "%% truncated" in mermaid

    # An empty (header + "%% no data") diagram can't be rendered — mermaid raises
    # a parse error. Surface it as a clear message instead.
    if is_empty_diagram(mermaid):
        return GenerateDiagramResponse(
            success=False,
            type=params.type,
            message=(
                f"No {params.type} data for scope={params.scope!r}. The repo may not be "
                "indexed, the scope may not exist, or it has no classes/calls/imports/subsystems."
            ),
        )

    return GenerateDiagramResponse(
        success=True, type=params.type, mermaid=mermaid, truncated=truncated
    )


# --------------------------------------------------------------------------
# Phase 2 — LLM / passthrough
# --------------------------------------------------------------------------

_PHASE2_PROMPTS = {
    "sequence": (
        "You generate Mermaid sequenceDiagram syntax. Given a call flow and the "
        "source of the involved functions, emit a sequenceDiagram that traces the "
        "runtime interaction starting at the entry point. Participants are the "
        "classes/modules; messages are the actual calls in execution order. Use "
        "activations and note return values where the source makes them clear."
    ),
    "usecase": (
        "You generate a Mermaid flowchart that models the use cases / workflows a "
        "user can perform. Given the public entry points (functions, classes, "
        "routes/tools) and the module structure, emit a 'flowchart LR' where actors "
        "point to the use cases they trigger, and use cases that compose others are "
        "linked. Group related use cases with subgraphs."
    ),
    "state": (
        "You generate Mermaid stateDiagram-v2 syntax. Given a class and its methods, "
        "emit a state machine of the object's lifecycle: states are the distinct "
        "conditions the object can be in, transitions are the methods that move it "
        "between them. Mark [*] for initial/terminal states."
    ),
    "erd": (
        "You generate Mermaid erDiagram syntax. Given the classes/data models and "
        "their source, emit an entity-relationship diagram: entities are the models, "
        "attributes are their fields with types, and relationships are the references "
        "between them (||--o{ etc.) inferred from the field types and the call/import "
        "edges. Only include entities present in the provided source."
    ),
    "flow": (
        "You generate a Mermaid flowchart TD that explains end-to-end how this system "
        "works for a NON-DEVELOPER (product or business reader). Use friendly plain-English "
        "labels that describe what each part DOES — avoid raw function names, class names, "
        "or technical jargon where possible. Group related steps with subgraphs. Keep the "
        "diagram to the main happy-path flow; omit error branches and internal implementation "
        "details. Do not invent components not present in the grounding."
    ),
}


async def _phase2(
    params: GenerateDiagramParams,
    memory_manager: MemoryManager,
    llm_adapter: LLMAdapter,
    repo_id: str,
    branch: str,
) -> GenerateDiagramResponse:
    grounding, truncated, has_data = await _gather_grounding(
        params, memory_manager, repo_id, branch
    )
    if not has_data:
        return GenerateDiagramResponse(
            success=False,
            type=params.type,
            message=(
                f"No indexed symbols found for scope={params.scope!r}. Index the repo "
                "first (index_repository), then pass a scope (function/class/file) that "
                "exists in the graph."
            ),
        )

    system_prompt = (
        _PHASE2_PROMPTS[params.type]
        + " Output ONLY the Mermaid code block — no prose, no explanation, no fences "
        "other than the diagram. Ground every node strictly in the provided symbols; "
        "do not invent names."
    )
    user_prompt = (
        f"Diagram type: {params.type}\nScope: {params.scope or '(whole repo)'}\n\n"
        f"=== Grounding (deterministic subgraph + source) ===\n{grounding}\n\n"
        f"Now produce the Mermaid {params.type} diagram."
    )

    # Passthrough (default): hand the prompt back so the host model synthesizes.
    if llm_adapter.is_passthrough():
        return GenerateDiagramResponse(
            success=True,
            type=params.type,
            truncated=truncated,
            passthrough=True,
            passthrough_prompt=f"{system_prompt}\n\n{user_prompt}",
            message=(
                "Passthrough — no LLM configured. Synthesize the Mermaid from "
                "passthrough_prompt (host model)."
            ),
        )

    raw = await llm_adapter.complete(system_prompt, user_prompt)
    mermaid = _strip_fences(raw) if raw else ""
    # raw=None (call failed) or empty output (model returned only fences/prose)
    # both fall back to passthrough — we still have a usable prompt, and an empty
    # mermaid string would make the renderer (mermaid.run) choke.
    if not mermaid.strip():
        return GenerateDiagramResponse(
            success=True,
            type=params.type,
            truncated=truncated,
            passthrough=True,
            passthrough_prompt=f"{system_prompt}\n\n{user_prompt}",
            message="LLM returned no diagram — falling back to passthrough.",
        )

    return GenerateDiagramResponse(
        success=True,
        type=params.type,
        mermaid=mermaid,
        truncated=truncated,
    )


def _strip_fences(text: str) -> str:
    """Drop a wrapping ```mermaid / ``` fence if the model added one.

    Truncates at the first closing fence so trailing prose after the diagram
    (the model occasionally adds a "This shows…" note) doesn't leak in.
    """
    t = text.strip()
    if not t.startswith("```"):
        return t
    out: list[str] = []
    for line in t.splitlines()[1:]:  # drop the opening ```lang line
        if line.strip() == "```":
            break
        out.append(line)
    return "\n".join(out).strip()


async def _gather_grounding(
    params: GenerateDiagramParams, memory_manager: MemoryManager, repo_id: str, branch: str
) -> tuple[str, bool, bool]:
    """Assemble (deterministic skeleton + source excerpts) for the scope.

    Returns (grounding_text, truncated, has_data). The skeleton anchors the LLM
    to real symbols; the source lets it infer ordering / fields / states.
    has_data is False when nothing real was found (no source AND a "%% no data"
    skeleton) — the caller turns that into an error instead of prompting the
    model with an empty graph.
    """
    from ..diagrams import call_graph, class_diagram, module_dependency

    conn = memory_manager.storage_manager.conn
    storage = memory_manager.storage_manager
    dtype = params.type

    skeleton = ""
    source_ids: list[str] = []

    if dtype == "sequence":
        root_id = await _resolve_call_root(conn, repo_id, branch, params.scope)
        if root_id is None:
            return "", False, False
        skeleton = call_graph(conn, root_id, depth=params.depth, max_nodes=params.max_nodes)
        source_ids = _calls_subgraph_ids(conn, repo_id, branch, root_id, params.depth)
        # Method calls (self.fetch()) frequently aren't resolved into CALLS
        # edges, so the BFS subgraph is just the entry point. Add the root's
        # sibling methods/functions (same class, else same file) so the model
        # can trace into the methods the entry point calls.
        if len(source_ids) <= 2:
            for sib in _siblings_of(conn, repo_id, branch, root_id):
                if sib not in source_ids:
                    source_ids.append(sib)
    elif dtype == "state":
        skeleton = class_diagram(
            conn, repo_id, branch, scope=params.scope, base_dir=storage.base_dir
        )
        source_ids = _scope_member_ids(conn, repo_id, branch, params.scope, only_classes=False)
    elif dtype == "erd":
        # Fields in the deterministic skeleton give the model real attributes to
        # build the ERD from instead of guessing them from the source text.
        skeleton = class_diagram(
            conn, repo_id, branch, scope=params.scope, base_dir=storage.base_dir
        )
        source_ids = _scope_member_ids(conn, repo_id, branch, params.scope, only_classes=True)
    elif dtype == "flow":
        from ..diagrams import overview_diagram

        skeleton = overview_diagram(
            conn, repo_id, branch, max_nodes=params.max_nodes, depth=params.depth
        )
        source_ids = _public_entry_ids(conn, repo_id, branch, params.scope)
    else:  # usecase
        skeleton = module_dependency(conn, repo_id, branch, max_nodes=params.max_nodes)
        source_ids = _public_entry_ids(conn, repo_id, branch, params.scope)

    skeleton_has_data = bool(skeleton.strip()) and "%% no data" not in skeleton

    parts: list[str] = []

    # For the flow type, prepend README context so the model has plain-language
    # project description to derive friendly labels from.
    if dtype == "flow" and params.repo_path:
        try:
            readme_path = Path(params.repo_path) / "README.md"
            readme_text = readme_path.read_text(encoding="utf-8")
            parts.append("# README:\n" + readme_text[:2000])
        except (OSError, TypeError):
            pass  # no README or unreadable — degrade gracefully

    if skeleton.strip():
        parts.append("# Deterministic subgraph (real symbols):\n" + skeleton)

    source_blocks: list[str] = []
    used = sum(len(p) for p in parts) + len("\n# Source:")
    truncated = False
    for mid in source_ids:
        text, label = _read_source(storage, conn, mid)
        if not text:
            continue
        block = f"\n## {label}\n{text}\n"
        if used + len(block) > _GROUNDING_CHAR_BUDGET:
            truncated = True
            break
        source_blocks.append(block)
        used += len(block)

    if source_blocks:
        parts.append("\n# Source:")
        parts.extend(source_blocks)

    has_data = bool(source_blocks) or skeleton_has_data
    return "".join(parts), truncated, has_data


def _read_source(storage, conn, memory_id: str) -> tuple[str, str]:
    """Return (source_text, label) for a memory id, or ("", "") if unreadable."""
    import json

    row = conn.execute(
        "SELECT content_ref, file_path, class_name, function_name FROM memories WHERE id = ?",
        (memory_id,),
    ).fetchone()
    if row is None:
        return "", ""
    try:
        blob = json.loads((storage.base_dir / row["content_ref"]).read_text(encoding="utf-8"))
        text = blob.get("text", "")
    except Exception as exc:
        logger.debug("generate_diagram: could not read content for %s: %s", memory_id, exc)
        return "", ""
    label_bits = [b for b in (row["file_path"], row["class_name"], row["function_name"]) if b]
    return text, " / ".join(label_bits) or memory_id


def _calls_subgraph_ids(conn, repo_id: str, branch: str, root_id: str, depth: int) -> list[str]:
    """BFS over CALLS edges from root, returning involved memory ids (root first)."""
    seen = [root_id]
    seen_set = {root_id}
    frontier = [root_id]
    for _ in range(depth):
        if not frontier:
            break
        placeholders = ",".join("?" for _ in frontier)
        rows = conn.execute(
            f"SELECT DISTINCT target_memory_id FROM relations "
            f"WHERE repo_id = ? AND branch = ? AND type = 'CALLS' AND stale = 0 "
            f"AND source_memory_id IN ({placeholders}) AND target_memory_id IS NOT NULL",
            (repo_id, branch, *frontier),
        ).fetchall()
        nxt = []
        for r in rows:
            tid = r["target_memory_id"]
            if tid and tid not in seen_set:
                seen_set.add(tid)
                seen.append(tid)
                nxt.append(tid)
        frontier = nxt
    return seen


def _like_escape(s: str) -> str:
    """Escape LIKE wildcards so a scope containing % or _ doesn't over-match.

    Pairs with ``ESCAPE '\\'`` on the LIKE clause.
    """
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _siblings_of(conn, repo_id: str, branch: str, root_id: str) -> list[str]:
    """Other code chunks in the root's class (preferred) or file.

    Lets a sequence diagram trace into method calls the edge resolver didn't
    turn into CALLS edges (a common gap for self.method() dispatch).
    """
    row = conn.execute(
        "SELECT file_path, class_name FROM memories WHERE id = ?", (root_id,)
    ).fetchone()
    if row is None:
        return []
    if row["class_name"]:
        rows = conn.execute(
            "SELECT id FROM memories WHERE repo_id = ? AND branch_name = ? AND stale = 0 "
            "AND class_name = ? AND id != ? ORDER BY line_start LIMIT 20",
            (repo_id, branch, row["class_name"], root_id),
        ).fetchall()
    elif row["file_path"]:
        rows = conn.execute(
            "SELECT id FROM memories WHERE repo_id = ? AND branch_name = ? AND stale = 0 "
            "AND file_path = ? AND id != ? ORDER BY line_start LIMIT 20",
            (repo_id, branch, row["file_path"], root_id),
        ).fetchall()
    else:
        return []
    return [r["id"] for r in rows]


def _scope_member_ids(
    conn, repo_id: str, branch: str, scope: str | None, only_classes: bool
) -> list[str]:
    """Memories matching a class/file scope. only_classes → just class chunks."""
    sql = (
        "SELECT id FROM memories WHERE repo_id = ? AND branch_name = ? AND stale = 0 "
        "AND content_type = 'code_snippet'"
    )
    args: list = [repo_id, branch]
    if scope:
        sql += " AND (class_name = ? OR file_path = ? OR file_path LIKE ? ESCAPE '\\')"
        args += [scope, scope, f"%{_like_escape(scope)}%"]
    if only_classes:
        sql += " AND class_name IS NOT NULL"
    sql += " ORDER BY file_path, line_start LIMIT 40"
    return [r["id"] for r in conn.execute(sql, args).fetchall()]


def _public_entry_ids(conn, repo_id: str, branch: str, scope: str | None) -> list[str]:
    """Public (non-underscore) functions/classes — candidate use cases."""
    sql = (
        "SELECT id FROM memories WHERE repo_id = ? AND branch_name = ? AND stale = 0 "
        "AND content_type = 'code_snippet' "
        "AND (function_name IS NOT NULL OR class_name IS NOT NULL) "
        "AND COALESCE(function_name, class_name) NOT LIKE '\\_%' ESCAPE '\\'"
    )
    args: list = [repo_id, branch]
    if scope:
        sql += " AND file_path LIKE ? ESCAPE '\\'"
        args.append(f"%{_like_escape(scope)}%")
    sql += " ORDER BY file_path, line_start LIMIT 40"
    return [r["id"] for r in conn.execute(sql, args).fetchall()]


async def resolve_call_root(conn, repo_id: str, branch: str, scope: str | None) -> str | None:
    """Public: resolve a memory_id to root a call/sequence graph from ``scope``.

    Reused by the ``mememo diagram call`` CLI.
    """
    return await _resolve_call_root(conn, repo_id, branch, scope)


async def _resolve_call_root(conn, repo_id: str, branch: str, scope: str | None) -> str | None:
    """Return a memory_id to use as BFS root for call graphs.

    Priority:
    1. scope looks like a memory UUID -> use directly.
    2. scope matches a function_name in the repo/branch.
    3. scope is None -> pick any CALLS source in the repo.
    """
    if scope is None:
        row = conn.execute(
            "SELECT source_memory_id FROM relations "
            "WHERE repo_id = ? AND branch = ? AND type = 'CALLS' AND stale = 0 LIMIT 1",
            (repo_id, branch),
        ).fetchone()
        return row["source_memory_id"] if row else None

    # UUID detection: a real uuid4 string only. The old `"-" in scope` check
    # misread any kebab-case function name (e.g. get-user-profile) as a UUID,
    # skipping the function lookup and always returning "not found".
    import re

    if re.fullmatch(
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
        scope,
    ):
        # Verify it exists.
        row = conn.execute("SELECT id FROM memories WHERE id = ?", (scope,)).fetchone()
        if row:
            return scope

    # Match by function_name.
    row = conn.execute(
        "SELECT id FROM memories WHERE repo_id = ? AND branch_name = ? "
        "AND function_name = ? AND stale = 0 LIMIT 1",
        (repo_id, branch, scope),
    ).fetchone()
    if row:
        return row["id"]

    # Substring match on function_name.
    row = conn.execute(
        "SELECT id FROM memories WHERE repo_id = ? AND branch_name = ? "
        "AND function_name LIKE ? AND stale = 0 LIMIT 1",
        (repo_id, branch, f"%{scope}%"),
    ).fetchone()
    if row:
        return row["id"]
    # Last resort: scope as a literal memory id (non-uuid ids / raw id callers).
    row = conn.execute("SELECT id FROM memories WHERE id = ?", (scope,)).fetchone()
    return row["id"] if row else None
