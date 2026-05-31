"""generate_diagram MCP tool — Phase 1 deterministic Mermaid generators.

Supports type in {class, call, module}. LLM types (erd/sequence/state/usecase)
are deferred to Phase 2 and return a descriptive error.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from ..core.llm_adapter import LLMAdapter
    from ..core.memory_manager import MemoryManager

logger = logging.getLogger(__name__)


DiagramType = Literal["class", "call", "module"]
_PHASE2_TYPES = frozenset({"erd", "sequence", "state", "usecase"})


class GenerateDiagramParams(BaseModel):
    type: str = Field(
        description="Diagram type: class | call | module (Phase 1). erd/sequence/state/usecase are Phase 2."
    )
    scope: str | None = Field(
        default=None,
        description="For class: file_path or class_name. For call: memory_id or function_name. For module: ignored.",
    )
    repo_path: str | None = Field(
        default=None,
        description="Repo path to detect repo_id/branch from. Uses current working dir if None.",
    )
    repo_id: str | None = Field(default=None, description="Override repo_id (skips detection).")
    branch: str | None = Field(default=None, description="Override branch (skips detection).")
    depth: int = Field(default=2, ge=1, le=6, description="BFS depth for call graph.")
    max_nodes: int = Field(default=60, ge=1, le=500, description="Node cap for call/module graphs.")


class GenerateDiagramResponse(BaseModel):
    success: bool
    type: str
    mermaid: str = ""
    truncated: bool = False
    message: str = ""


async def generate_diagram(
    params: GenerateDiagramParams,
    memory_manager: MemoryManager,
    llm_adapter: LLMAdapter,
) -> GenerateDiagramResponse:
    from ..diagrams import call_graph, class_diagram, module_dependency

    if params.type in _PHASE2_TYPES:
        return GenerateDiagramResponse(
            success=False,
            type=params.type,
            message=(
                f"Phase 2: {params.type!r} diagrams are generated via the upcoming LLM path "
                "(erd/sequence/state/usecase). Use type=class, call, or module for Phase 1."
            ),
        )

    if params.type not in ("class", "call", "module"):
        return GenerateDiagramResponse(
            success=False,
            type=params.type,
            message=f"Unknown diagram type {params.type!r}. Choose class, call, or module.",
        )

    conn = memory_manager.storage_manager.conn

    # Resolve repo_id + branch.
    repo_id = params.repo_id
    branch = params.branch
    if not repo_id or not branch:
        try:
            ctx = await memory_manager.git_manager.detect_context(params.repo_path or ".")
            repo_id = repo_id or ctx.repo.id
            branch = branch or ctx.branch.name
        except Exception as exc:
            logger.warning("generate_diagram: git context detection failed: %s", exc)
            repo_id = repo_id or ""
            branch = branch or ""

    if params.type == "class":
        mermaid = class_diagram(conn, repo_id, branch, scope=params.scope)
        truncated = False
    elif params.type == "module":
        mermaid = module_dependency(conn, repo_id, branch, max_nodes=params.max_nodes)
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

    return GenerateDiagramResponse(
        success=True,
        type=params.type,
        mermaid=mermaid,
        truncated=truncated,
        message="",
    )


async def _resolve_call_root(conn, repo_id: str, branch: str, scope: str | None) -> str | None:
    """Return a memory_id to use as BFS root for call graphs.

    Priority:
    1. scope looks like a memory UUID (contains hyphen or is 32+ hex chars) -> use directly.
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

    # UUID detection: contains '-' (standard uuid4 format) or 32+ lowercase hex.
    import re

    if "-" in scope or re.fullmatch(r"[0-9a-f]{32,}", scope):
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
    return row["id"] if row else None
