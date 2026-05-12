"""graph_neighbors MCP tool (FR-020).

Depth-limited BFS over the relations table. Returns visited memory ids
and the edges traversed. Read-only.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from ..types import RelationType

if TYPE_CHECKING:
    from ..core.memory_manager import MemoryManager


Direction = Literal["out", "in", "both"]


class GraphNeighborsParams(BaseModel):
    memory_id: str = Field(min_length=1)
    direction: Direction = "both"
    depth: int = Field(default=1, ge=1, le=6)
    edge_types: list[RelationType] | None = None


class EdgeSummary(BaseModel):
    source_memory_id: str
    target_memory_id: str | None = None
    target_symbol: str | None = None
    type: RelationType
    confidence: str


class GraphNeighborsResponse(BaseModel):
    success: bool
    message: str
    visited: list[str] = Field(default_factory=list)
    edges: list[EdgeSummary] = Field(default_factory=list)


async def graph_neighbors(
    params: GraphNeighborsParams, memory_manager: "MemoryManager"
) -> GraphNeighborsResponse:
    storage = memory_manager.storage_manager
    visited: set[str] = {params.memory_id}
    frontier: set[str] = {params.memory_id}
    edges_out: list[EdgeSummary] = []

    for _ in range(params.depth):
        if not frontier:
            break
        next_frontier: set[str] = set()
        for mid in frontier:
            rels: list = []
            if params.direction in ("out", "both"):
                rels += storage.list_relations(source_memory_id=mid)
            if params.direction in ("in", "both"):
                rels += storage.list_relations(target_memory_id=mid)
            for r in rels:
                if params.edge_types and r.type not in params.edge_types:
                    continue
                edges_out.append(
                    EdgeSummary(
                        source_memory_id=r.source_memory_id,
                        target_memory_id=r.target_memory_id,
                        target_symbol=r.target_symbol,
                        type=r.type,
                        confidence=r.confidence,
                    )
                )
                # Frontier extension: follow only edges with a resolved target.
                if r.target_memory_id and r.target_memory_id not in visited:
                    next_frontier.add(r.target_memory_id)
                if r.source_memory_id != mid and r.source_memory_id not in visited:
                    next_frontier.add(r.source_memory_id)
        visited |= next_frontier
        frontier = next_frontier

    return GraphNeighborsResponse(
        success=True,
        message=f"Visited {len(visited)} memories via {len(edges_out)} edges",
        visited=sorted(visited),
        edges=edges_out,
    )
