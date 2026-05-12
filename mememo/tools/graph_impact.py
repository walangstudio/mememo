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
    storage = memory_manager.storage_manager
    visited: set[str] = {params.memory_id}
    queue: deque[tuple[str, int, RelationType | None, RelationConfidence | None]] = deque(
        [(params.memory_id, 0, None, None)]
    )
    impacted: list[ImpactedMemory] = []

    # Pull risk_grade + file/class/function metadata in one shot at the end.
    def _enrich(memory_ids: list[str]) -> dict[str, dict]:
        if not memory_ids:
            return {}
        placeholders = ",".join("?" * len(memory_ids))
        rows = storage.conn.execute(
            f"SELECT id, risk_grade, file_path, function_name, class_name "
            f"FROM memories WHERE id IN ({placeholders})",
            memory_ids,
        ).fetchall()
        return {row["id"]: dict(row) for row in rows}

    while queue:
        node, depth, _, _ = queue.popleft()
        if depth >= params.max_depth:
            continue
        if params.direction == "downstream":
            edges = storage.list_relations(source_memory_id=node)
        else:
            edges = storage.list_relations(target_memory_id=node)
        for r in edges:
            if not _passes_confidence(r.confidence, params.min_confidence):
                continue
            if params.edge_types and r.type not in params.edge_types:
                continue
            other = (
                r.target_memory_id if params.direction == "downstream" else r.source_memory_id
            )
            if not other or other in visited:
                continue
            visited.add(other)
            queue.append((other, depth + 1, r.type, r.confidence))
            impacted.append(
                ImpactedMemory(
                    memory_id=other,
                    depth=depth + 1,
                    via_edge_type=r.type,
                    via_confidence=r.confidence,
                )
            )

    # Enrich with risk_grade + file metadata.
    meta = _enrich([m.memory_id for m in impacted])
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
