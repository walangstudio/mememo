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

import json
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from pydantic import BaseModel, Field

from ..types.memory import MemoryEvent, coerce_sha

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
    skipped_secrets_count: int = 0


def _load_blob_text(storage_base: Path, content_ref: str) -> str:
    """Best-effort: read the text blob for a memory.

    Returns "" if the blob is missing or malformed — callers treat that as
    "nothing to scan" rather than failing the whole merge.
    """
    try:
        path = storage_base / content_ref
        blob = json.loads(path.read_text(encoding="utf-8"))
        return blob.get("text", "") or ""
    except (OSError, json.JSONDecodeError):
        return ""


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
    merge_sha = coerce_sha(params.merge_sha or context.branch.commit_hash)

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
    skipped_secrets = 0
    detector = memory_manager.secrets_detector if memory_manager.secrets_detection else None
    # Cache blob scans by content_ref — multiple memories can share a blob.
    blob_secrets_cache: dict[str, bool] = {}

    def _blob_has_secrets(content_ref: str) -> bool:
        cached = blob_secrets_cache.get(content_ref)
        if cached is not None:
            return cached
        text = _load_blob_text(storage.base_dir, content_ref)
        verdict = bool(text and detector.has_secrets(text))
        blob_secrets_cache[content_ref] = verdict
        return verdict

    # Collect rows + events; insert in one transaction at the end so we
    # don't fsync per row.
    memory_rows: list[tuple] = []
    new_events: list[MemoryEvent] = []
    now = int(time.time())
    for row in source_rows:
        if row["checksum"] in target_shas:
            skipped_dup += 1
            continue
        if detector is not None and _blob_has_secrets(row["content_ref"]):
            logger.warning(
                "merge_branch: skipping memory %s — secrets detected", row["id"]
            )
            skipped_secrets += 1
            continue
        new_id = str(uuid4())
        memory_rows.append((
            new_id, row["repo_id"], row["repo_name"], row["repo_path"],
            params.target_branch, merge_sha,
            row["content_type"], row["file_path"], row["line_start"], row["line_end"],
            row["function_name"], row["class_name"], row["language"], row["chunk_type"],
            row["checksum"], row["content_ref"], row["token_count"],
            row["created_at"], now,
            None, None,  # embeddings stay per-branch
            0, None,
            merge_sha, merge_sha, None,
        ))
        new_events.append(MemoryEvent(
            commit_sha=merge_sha, memory_id=new_id, op="RESTORED",
            content_sha=row["checksum"], branch=params.target_branch,
        ))
        target_shas.add(row["checksum"])  # avoid duplicating within this run
        merged += 1

    if memory_rows:
        cursor = conn.cursor()
        cursor.executemany(
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
            memory_rows,
        )
        cursor.executemany(
            "INSERT INTO memory_events (commit_sha, memory_id, op, content_sha, branch, ts) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [(e.commit_sha, e.memory_id, e.op, e.content_sha, e.branch,
              int(e.ts.timestamp())) for e in new_events],
        )
        conn.commit()

    secrets_note = f", {skipped_secrets} secret-skipped" if skipped_secrets else ""
    return MergeBranchResponse(
        success=True,
        message=(
            f"Merged {merged} memories from {params.source_branch} into "
            f"{params.target_branch} ({skipped_dup} dup-skipped{secrets_note})"
        ),
        source_branch=params.source_branch,
        target_branch=params.target_branch,
        merged_count=merged,
        skipped_dup_count=skipped_dup,
        skipped_secrets_count=skipped_secrets,
    )
