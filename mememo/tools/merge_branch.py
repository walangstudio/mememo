"""merge_branch MCP tool (FR-010).

Unions the source branch's alive memories into the target branch.
Dedup is by content_sha (the existing content-addressed checksum):
memories already present on target with the same content are skipped,
not re-stored. New memories are inserted into target with a RESTORED
event tagged at the merge commit SHA (resolved via GitManager.merge_base
or the caller-supplied merge_sha).

Called by:
- the opt-in post-merge git hook (T013)
- a user invoking the MCP tool directly after a manual merge
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from pydantic import BaseModel, Field

from ..types.memory import MemoryEvent, NULL_SHA

if TYPE_CHECKING:
    from ..core.memory_manager import MemoryManager

logger = logging.getLogger(__name__)


class MergeBranchParams(BaseModel):
    repo_path: str
    source_branch: str = Field(description="Branch whose memories should be merged in")
    target_branch: str = Field(description="Branch receiving the union")
    merge_sha: str | None = Field(
        default=None,
        description="SHA to record on the RESTORED events; defaults to current HEAD",
    )


class MergeBranchResponse(BaseModel):
    success: bool
    message: str
    source_branch: str | None = None
    target_branch: str | None = None
    merged_count: int = 0
    skipped_dup_count: int = 0


async def merge_branch(
    params: MergeBranchParams, memory_manager: "MemoryManager"
) -> MergeBranchResponse:
    repo_path = Path(params.repo_path)
    if not repo_path.exists() or not repo_path.is_dir():
        return MergeBranchResponse(
            success=False, message=f"Repository path not found: {params.repo_path}"
        )
    if params.source_branch == params.target_branch:
        return MergeBranchResponse(
            success=False,
            message="source_branch and target_branch must differ",
        )

    try:
        context = await memory_manager.git_manager.detect_context(str(repo_path))
    except RuntimeError as e:
        return MergeBranchResponse(success=False, message=str(e))

    repo_id = context.repo.id
    merge_sha = params.merge_sha or context.branch.commit_hash or NULL_SHA
    if len(merge_sha) != 40:
        merge_sha = NULL_SHA

    storage = memory_manager.storage_manager
    conn = storage.conn

    # Replay current source-branch state from the event log.
    source_alive = storage.alive_memory_ids_at_ts(
        target_ts=int(time.time()),
        repo_id=repo_id,
        branch=params.source_branch,
    )
    if not source_alive:
        return MergeBranchResponse(
            success=True,
            message=f"No alive memories on {params.source_branch}; nothing to merge",
            source_branch=params.source_branch,
            target_branch=params.target_branch,
        )

    # Existing target-branch content_shas (dedup key).
    target_shas = {
        row["checksum"]
        for row in conn.execute(
            "SELECT checksum FROM memories WHERE repo_id = ? AND branch_name = ?",
            (repo_id, params.target_branch),
        ).fetchall()
    }

    # Fetch source rows and copy what's missing.
    placeholders = ",".join("?" * len(source_alive))
    source_rows = conn.execute(
        f"SELECT * FROM memories WHERE repo_id = ? AND branch_name = ? "
        f"AND id IN ({placeholders})",
        (repo_id, params.source_branch, *source_alive),
    ).fetchall()

    merged = 0
    skipped_dup = 0
    cursor = conn.cursor()
    for row in source_rows:
        if row["checksum"] in target_shas:
            skipped_dup += 1
            continue
        new_id = str(uuid4())
        cursor.execute(
            """
            INSERT INTO memories (
                id, repo_id, repo_name, repo_path, branch_name, commit_hash,
                content_type, file_path, line_start, line_end,
                function_name, class_name, language, chunk_type,
                checksum, content_ref, token_count, created_at, updated_at,
                embedding_shard, embedding_index, stale, stale_reason,
                created_at_sha, updated_at_sha, risk_grade
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_id, row["repo_id"], row["repo_name"], row["repo_path"],
                params.target_branch, merge_sha,
                row["content_type"], row["file_path"], row["line_start"], row["line_end"],
                row["function_name"], row["class_name"], row["language"], row["chunk_type"],
                row["checksum"], row["content_ref"], row["token_count"],
                row["created_at"], int(time.time()),
                None, None,  # embeddings stay per-branch; not copied
                0, None,
                merge_sha, merge_sha, None,
            ),
        )
        storage.append_event(
            MemoryEvent(
                commit_sha=merge_sha,
                memory_id=new_id,
                op="RESTORED",
                content_sha=row["checksum"],
                branch=params.target_branch,
            )
        )
        target_shas.add(row["checksum"])  # avoid duplicating within this run
        merged += 1
    conn.commit()

    return MergeBranchResponse(
        success=True,
        message=(
            f"Merged {merged} memories from {params.source_branch} into "
            f"{params.target_branch} ({skipped_dup} dup-skipped)"
        ),
        source_branch=params.source_branch,
        target_branch=params.target_branch,
        merged_count=merged,
        skipped_dup_count=skipped_dup,
    )
