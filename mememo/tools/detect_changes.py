"""detect_changes MCP tool (FR-007).

Maps git diff between two refs to affected memories with risk grades.
Read-only: does NOT mutate stale flags or risk_grade on the memories
table — that's sync_commits' job. detect_changes is the dry-run cousin
that returns "if you ran sync_commits right now, here's what would be
graded."
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field, field_validator

from ..core.risk_grader import grade_memory
from ..types.memory import RiskGrade

# Reject leading '-' so git treats the value as a revision, not a flag
# (security audit 2026-05-13).
_REF_RE = re.compile(r"^(?!-)[\w/.:~^@{}-]{1,200}$")

if TYPE_CHECKING:
    from ..core.memory_manager import MemoryManager

logger = logging.getLogger(__name__)


class DetectChangesParams(BaseModel):
    repo_path: str = Field(description="Working directory inside the target git repo")
    base_ref: str = Field(description="Base ref (e.g. 'HEAD~1', 'main', a SHA)")
    head_ref: str = Field(default="HEAD", description="Head ref to diff against")

    @field_validator("base_ref", "head_ref")
    @classmethod
    def _validate_ref(cls, v: str) -> str:
        if not _REF_RE.match(v):
            raise ValueError(f"ref must match [\\w/.:~^@{{}}-]+ without leading '-'; got {v!r}")
        return v


class AffectedMemory(BaseModel):
    memory_id: str
    file_path: str
    line_range: tuple[int, int] | None = None
    function_name: str | None = None
    class_name: str | None = None
    change_kind: str = Field(description="Git --name-status code (D, M, R, T, C)")
    risk_grade: RiskGrade


class DetectChangesResponse(BaseModel):
    success: bool
    message: str
    base_ref: str | None = None
    head_ref: str | None = None
    affected: list[AffectedMemory] = Field(default_factory=list)


async def detect_changes(
    params: DetectChangesParams, memory_manager: MemoryManager
) -> DetectChangesResponse:
    repo_path = Path(params.repo_path)
    if not repo_path.exists() or not repo_path.is_dir():
        return DetectChangesResponse(
            success=False, message=f"Repository path not found: {params.repo_path}"
        )

    try:
        context = await memory_manager.git_manager.detect_context(str(repo_path))
    except RuntimeError as e:
        return DetectChangesResponse(success=False, message=str(e))

    try:
        diff = await memory_manager.git_manager.diff_between(
            params.base_ref, params.head_ref, cwd=str(repo_path)
        )
    except RuntimeError as e:
        return DetectChangesResponse(
            success=False,
            message=f"diff_between failed: {e}",
            base_ref=params.base_ref,
            head_ref=params.head_ref,
        )

    if not diff:
        return DetectChangesResponse(
            success=True,
            message=f"No file changes between {params.base_ref}..{params.head_ref}",
            base_ref=params.base_ref,
            head_ref=params.head_ref,
            affected=[],
        )

    # Pull every memory bound to a file mentioned in the diff, then grade.
    # The diff can be arbitrarily large (e.g. HEAD~N..HEAD with N huge); chunk
    # the IN clause to stay under SQLITE_MAX_VARIABLE_NUMBER on stock builds.
    affected: list[AffectedMemory] = []
    conn = memory_manager.storage_manager.conn
    paths = list(diff.keys())
    chunk_size = 500
    rows: list = []
    for i in range(0, len(paths), chunk_size):
        batch = paths[i : i + chunk_size]
        placeholders = ",".join("?" * len(batch))
        rows.extend(
            conn.execute(
                f"SELECT id, file_path, line_start, line_end, function_name, class_name "
                f"FROM memories WHERE repo_id = ? AND branch_name = ? "
                f"AND file_path IN ({placeholders})",
                (context.repo.id, context.branch.name, *batch),
            ).fetchall()
        )

    for row in rows:
        line_range = (
            (row["line_start"], row["line_end"])
            if row["line_start"] is not None and row["line_end"] is not None
            else None
        )
        grade = grade_memory(
            memory_file=row["file_path"],
            memory_line_range=line_range,
            diff=diff,
        )
        if grade is None:
            continue
        affected.append(
            AffectedMemory(
                memory_id=row["id"],
                file_path=row["file_path"],
                line_range=line_range,
                function_name=row["function_name"],
                class_name=row["class_name"],
                change_kind=diff[row["file_path"]],
                risk_grade=grade,
            )
        )

    return DetectChangesResponse(
        success=True,
        message=(
            f"{len(affected)} memories affected between "
            f"{params.base_ref}..{params.head_ref} ({len(diff)} files changed)"
        ),
        base_ref=params.base_ref,
        head_ref=params.head_ref,
        affected=affected,
    )
