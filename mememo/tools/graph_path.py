"""graph_path MCP tool (FR-021).

Shortest edge path between two memories. BFS over the relations table.
Returns the ordered list of memory_ids along the path, or null if there
is no path within max_depth.
"""

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from ..core.memory_manager import MemoryManager


class GraphPathParams(BaseModel):
    source_id: str = Field(min_length=1)
    target_id: str = Field(min_length=1)
    max_depth: int = Field(default=6, ge=1, le=12)


class GraphPathResponse(BaseModel):
    success: bool
    message: str
    path: list[str] | None = None
    length: int | None = None


async def graph_path(
    params: GraphPathParams, memory_manager: "MemoryManager"
) -> GraphPathResponse:
    if params.source_id == params.target_id:
        return GraphPathResponse(
            success=True, message="source == target", path=[params.source_id], length=0
        )

    storage = memory_manager.storage_manager
    parents: dict[str, str] = {}
    visited: set[str] = {params.source_id}
    queue: deque[tuple[str, int]] = deque([(params.source_id, 0)])

    while queue:
        node, depth = queue.popleft()
        if depth >= params.max_depth:
            continue
        # Outbound edges only — paths are directed.
        for r in storage.list_relations(source_memory_id=node):
            if not r.target_memory_id or r.target_memory_id in visited:
                continue
            parents[r.target_memory_id] = node
            if r.target_memory_id == params.target_id:
                # Reconstruct path.
                path = [params.target_id]
                cur = params.target_id
                while cur in parents:
                    cur = parents[cur]
                    path.append(cur)
                path.reverse()
                return GraphPathResponse(
                    success=True,
                    message=f"Path of length {len(path) - 1}",
                    path=path,
                    length=len(path) - 1,
                )
            visited.add(r.target_memory_id)
            queue.append((r.target_memory_id, depth + 1))

    return GraphPathResponse(
        success=True,
        message=f"No path within max_depth={params.max_depth}",
        path=None,
        length=None,
    )
