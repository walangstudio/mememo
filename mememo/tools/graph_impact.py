"""graph_impact MCP tool (FR-022).

Blast-radius reasoning: starting from a memory, walk the relations graph
in a direction (downstream = follow CALLS outward; upstream = invert) up
to a max depth, filter edges by confidence floor, and decorate each
reached memory with its current ``risk_grade`` if any.

Re-uses the v0.4 risk_grade column written by sync_commits / detect_changes.
"""

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from ..types import RelationConfidence, RelationType
from ..types.memory import RiskGrade

if TYPE_CHECKING:
    from ..core.memory_manager import MemoryManager


Direction = Literal["downstream", "upstream"]

_CONFIDENCE_RANK = {"AMBIGUOUS": 0, "INFERRED": 1, "EXTRACTED": 2}


class GraphImpactParams(BaseModel):
    memory_id: str = Field(min_length=1)
    direction: Direction = "downstream"
    min_confidence: RelationConfidence = "INFERRED"
    max_depth: int = Field(default=4, ge=1, le=8)
    edge_types: list[RelationType] | None = None


class ImpactedMemory(BaseModel):
    memory_id: str
    depth: int
    via_edge_type: RelationType
    via_confidence: RelationConfidence
    risk_grade: RiskGrade | None = None
    file_path: str | None = None
    function_name: str | None = None
    class_name: str | None = None


class GraphImpactResponse(BaseModel):
    success: bool
    message: str
    direction: Direction
    impacted: list[ImpactedMemory] = Field(default_factory=list)


def _passes_confidence(edge_conf: str, floor: RelationConfidence) -> bool:
    return _CONFIDENCE_RANK.get(edge_conf, 0) >= _CONFIDENCE_RANK[floor]


async def graph_impact(
    params: GraphImpactParams, memory_manager: "MemoryManager"
) -> GraphImpactResponse:
    """Batched BFS over outbound (downstream) or inbound (upstream) edges.

    One SQL per level via an IN clause on the frontier; risk_grade + file
    metadata are joined in a single trailing query.
    """
    conn = memory_manager.storage_manager.conn
    visited: set[str] = {params.memory_id}
    frontier: set[str] = {params.memory_id}
    impacted: list[ImpactedMemory] = []
    type_filter = set(params.edge_types) if params.edge_types else None
    join_col = "source_memory_id" if params.direction == "downstream" else "target_memory_id"
    other_col = "target_memory_id" if params.direction == "downstream" else "source_memory_id"

    for depth in range(1, params.max_depth + 1):
        if not frontier:
            break
        next_frontier: set[str] = set()
        for f_ids in _chunked(sorted(frontier), 500):
            placeholders = ",".join("?" * len(f_ids))
            rows = conn.execute(
                f"SELECT {other_col} AS other, type, confidence "
                f"FROM relations WHERE {join_col} IN ({placeholders})",
                f_ids,
            ).fetchall()
            for r in rows:
                if not _passes_confidence(r["confidence"], params.min_confidence):
                    continue
                if type_filter and r["type"] not in type_filter:
                    continue
                other = r["other"]
                if not other or other in visited:
                    continue
                visited.add(other)
                next_frontier.add(other)
                impacted.append(
                    ImpactedMemory(
                        memory_id=other, depth=depth,
                        via_edge_type=r["type"], via_confidence=r["confidence"],
                    )
                )
        frontier = next_frontier

    # Enrich with risk_grade + file metadata in one query.
    if impacted:
        ids = [m.memory_id for m in impacted]
        meta: dict[str, dict] = {}
        for batch in _chunked(ids, 500):
            placeholders = ",".join("?" * len(batch))
            rows = conn.execute(
                f"SELECT id, risk_grade, file_path, function_name, class_name "
                f"FROM memories WHERE id IN ({placeholders})",
                batch,
            ).fetchall()
            for row in rows:
                meta[row["id"]] = dict(row)
        for m in impacted:
            info = meta.get(m.memory_id)
            if info:
                m.risk_grade = info.get("risk_grade")
                m.file_path = info.get("file_path")
                m.function_name = info.get("function_name")
                m.class_name = info.get("class_name")

    will_break = sum(1 for m in impacted if m.risk_grade == "WILL_BREAK")
    return GraphImpactResponse(
        success=True,
        message=(
            f"{len(impacted)} memories impacted {params.direction} "
            f"(depth<={params.max_depth}, min_confidence={params.min_confidence}; "
            f"{will_break} graded WILL_BREAK)"
        ),
        direction=params.direction,
        impacted=impacted,
    )


def _chunked(items, size):
    for i in range(0, len(items), size):
        yield items[i : i + size]
