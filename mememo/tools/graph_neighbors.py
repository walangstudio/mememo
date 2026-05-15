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
    params: GraphNeighborsParams, memory_manager: MemoryManager
) -> GraphNeighborsResponse:
    """Batched BFS: one SQL per level (not per-node) using IN-clause filters."""
    conn = memory_manager.storage_manager.conn
    visited: set[str] = {params.memory_id}
    frontier: set[str] = {params.memory_id}
    edges_out: list[EdgeSummary] = []
    type_filter = set(params.edge_types) if params.edge_types else None

    for _ in range(params.depth):
        if not frontier:
            break
        next_frontier: set[str] = set()
        # One SQL per direction per level — IN-clause batch over the frontier.
        for f_ids in _chunked(sorted(frontier), 500):
            placeholders = ",".join("?" * len(f_ids))
            if params.direction in ("out", "both"):
                rows = conn.execute(
                    f"SELECT source_memory_id, target_memory_id, target_symbol, "
                    f"       type, confidence FROM relations "
                    f"WHERE source_memory_id IN ({placeholders})",
                    f_ids,
                ).fetchall()
                _consume(rows, edges_out, visited, next_frontier, type_filter)
            if params.direction in ("in", "both"):
                rows = conn.execute(
                    f"SELECT source_memory_id, target_memory_id, target_symbol, "
                    f"       type, confidence FROM relations "
                    f"WHERE target_memory_id IN ({placeholders})",
                    f_ids,
                ).fetchall()
                _consume(rows, edges_out, visited, next_frontier, type_filter)
        visited |= next_frontier
        frontier = next_frontier

    return GraphNeighborsResponse(
        success=True,
        message=f"Visited {len(visited)} memories via {len(edges_out)} edges",
        visited=sorted(visited),
        edges=edges_out,
    )


def _chunked(items, size):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _consume(rows, edges_out, visited, next_frontier, type_filter):
    for r in rows:
        if type_filter and r["type"] not in type_filter:
            continue
        edges_out.append(
            EdgeSummary(
                source_memory_id=r["source_memory_id"],
                target_memory_id=r["target_memory_id"],
                target_symbol=r["target_symbol"],
                type=r["type"],
                confidence=r["confidence"],
            )
        )
        for candidate in (r["target_memory_id"], r["source_memory_id"]):
            if candidate and candidate not in visited and candidate not in next_frontier:
                next_frontier.add(candidate)
